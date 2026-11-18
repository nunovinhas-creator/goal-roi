"""Opt-in consumer of the collector's published JSONL, with its own private SQLite outbox."""
import argparse
import fcntl
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import time
from contextlib import closing, contextmanager
from urllib.parse import urlencode

from .stake_alert_text import payloads, timestamp

ORIGIN = 'https://github.com/nunovinhas-creator/goal-roi.git'
POLL_SECONDS = 120
SEND_SPACING = 3.1
RETRY_SECONDS = 300
SCHEMA = (
    'CREATE TABLE cursor (id INTEGER PRIMARY KEY CHECK (id = 1), activation REAL NOT NULL,'
    ' size INTEGER NOT NULL, digest TEXT NOT NULL, send_after REAL NOT NULL)',
    'CREATE TABLE seen (message_id INTEGER PRIMARY KEY)',
    'CREATE TABLE outbox (id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL, part INTEGER NOT NULL,'
    ' attempts INTEGER NOT NULL DEFAULT 0, replayed INTEGER NOT NULL DEFAULT 0,'
    ' status TEXT NOT NULL, text TEXT, UNIQUE (message_id, part))',
)


class SourceChanged(ValueError):
    """Published data no longer has the consumed append-only prefix."""


def git(repo, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith('TG_')}
    env['GIT_TERMINAL_PROMPT'] = '0'
    return subprocess.run(['git', '-C', str(repo), *args], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, check=True, timeout=45).stdout


def published_snapshot(repo):
    if (git(repo, 'rev-parse', '--is-bare-repository').strip() != b'true'
            or git(repo, 'remote', 'get-url', 'origin').decode().strip() != ORIGIN):
        raise ValueError('an independent bare clone of Goal-ROI is required')
    git(repo, 'fetch', '--quiet', '--no-tags', '--no-recurse-submodules', '--depth=1', 'origin', 'refs/heads/main')
    return git(repo, 'show', 'FETCH_HEAD:data/messages.jsonl')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def entries(blob, offset=0):
    if blob and not blob.endswith(b'\n'):
        raise SourceChanged('JSONL ends with an incomplete record')
    reader = io.BytesIO(blob)
    reader.seek(offset)
    for line in reader:
        if not line.strip():
            continue
        row = json.loads(line)
        if (not isinstance(row, dict) or type(row.get('message_id')) is not int or row['message_id'] <= 0
                or not isinstance(row.get('text'), str)):
            raise ValueError('invalid source record')
        yield row


def initialize(db, snapshot, activated_at):
    with db:
        # Start the transaction before DDL so an interrupted init creates no half-schema.
        db.execute('BEGIN IMMEDIATE')
        for schema in SCHEMA:
            db.execute(schema)
        db.executemany('INSERT OR IGNORE INTO seen VALUES (?)', ((row['message_id'],) for row in entries(snapshot)))
        db.execute('INSERT INTO cursor VALUES (1, ?, ?, ?, 0)', (activated_at, len(snapshot), digest(snapshot)))


def ingest(db, snapshot):
    activation, offset, old_digest = db.execute('SELECT activation, size, digest FROM cursor').fetchone()
    if len(snapshot) < offset or digest(memoryview(snapshot)[:offset]) != old_digest:
        raise SourceChanged('JSONL prefix changed')
    if len(snapshot) == offset:
        return 0
    queued = 0
    with db:
        for row in entries(snapshot, offset):
            if not db.execute('INSERT OR IGNORE INTO seen VALUES (?)', (row['message_id'],)).rowcount:
                continue
            collected = timestamp(row.get('collected_at_utc'))
            if collected is None:
                raise ValueError('source record lacks a collection timestamp')
            emitted = timestamp(row.get('date_utc'))
            if collected.timestamp() <= activation or (emitted and emitted.timestamp() <= activation):
                continue
            for part, text in enumerate(payloads(row), 1):
                db.execute("INSERT INTO outbox (message_id, part, status, text) VALUES (?, ?, 'pending', ?)",
                           (row['message_id'], part, text))
                queued += 1
        db.execute('UPDATE cursor SET size = ?, digest = ?', (len(snapshot), digest(snapshot)))
    return queued


def telegram_send(token, chat, text, connect=http.client.HTTPSConnection):
    """One HTTPS request to the existing bot. Return (outcome, retry delay)."""
    body = urlencode({'chat_id': chat, 'text': text, 'link_preview_options': '{"is_disabled":true}'})
    with closing(connect('api.telegram.org', timeout=15)) as conn:
        try:
            conn.connect()
        except (OSError, http.client.HTTPException):
            return 'pending', RETRY_SECONDS
        try:
            conn.request('POST', f'/bot{token}/sendMessage', body, {'Content-Type': 'application/x-www-form-urlencoded'})
            response = conn.getresponse()
        except (socket.gaierror, ConnectionRefusedError):
            return 'pending', RETRY_SECONDS
        except (OSError, http.client.HTTPException):
            return 'uncertain', RETRY_SECONDS

        if response.status == 200 or response.status == 429:
            try:
                answer = json.loads(response.read(200_000))
            except (ValueError, OSError, http.client.HTTPException):
                answer = None
            answer = answer if isinstance(answer, dict) else {}
            if response.status == 200 and answer.get('ok') is True:
                return 'sent', 0
            if response.status == 429:
                parameters = answer.get('parameters')
                delay = parameters.get('retry_after') if isinstance(parameters, dict) else None
                if type(delay) is not int or not 0 < delay < 2**31:
                    delay = RETRY_SECONDS
                return 'pending', delay
        if response.status in (401, 403, 404):
            return 'pending', RETRY_SECONDS
        if 400 <= response.status < 500 and response.status != 408:
            return 'rejected', 0
        return 'uncertain', RETRY_SECONDS


def recover(db):
    with db:
        return db.execute("UPDATE outbox SET status = 'uncertain' WHERE status = 'sending'").rowcount


def next_pending(db):
    return db.execute("SELECT id, text FROM outbox WHERE status = 'pending' ORDER BY id LIMIT 1").fetchone()


def send_next(db, sender, clock=time.time, retry_uncertain=False):
    pending = next_pending(db)
    replay = False
    if pending is None and retry_uncertain:
        pending = db.execute("SELECT id, text FROM outbox WHERE status = 'uncertain' AND replayed = 0 "
                             "AND text IS NOT NULL ORDER BY id LIMIT 1").fetchone()
        replay = True
    if pending is None:
        return None
    now = clock()
    send_after, = db.execute('SELECT send_after FROM cursor').fetchone()
    if now < send_after:
        return None
    item, text = pending
    with db:
        db.execute("UPDATE outbox SET status = 'sending', attempts = attempts + 1, replayed = MAX(replayed, ?) WHERE id = ?", (int(replay), item))
        db.execute('UPDATE cursor SET send_after = ?', (now + SEND_SPACING,))
    # A crash after this durable intent leaves this request uncertain and forbids automatic replay.
    outcome, delay = sender(text)
    with db:
        attempts, = db.execute('SELECT attempts FROM outbox WHERE id = ?', (item,)).fetchone()
        if outcome == 'pending' and attempts >= 5:
            outcome = 'exhausted'
        db.execute('UPDATE outbox SET status = ?, text = ? WHERE id = ?',
                   (outcome, None if outcome == 'sent' else text, item))
        if delay and outcome != 'uncertain':
            db.execute('UPDATE cursor SET send_after = MAX(send_after, ?)', (clock() + delay,))
    return outcome


@contextmanager
def locked_db(path, create=False):
    # One consumer on one persistent local disk. The lock survives SQLite commits and network calls.
    with path.with_suffix(path.suffix + '.lock').open('ab') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        uri = path.resolve().as_uri() + ('?mode=rwc' if create else '?mode=rw')
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.execute('PRAGMA synchronous = FULL')
            db.execute('PRAGMA secure_delete = ON')
            yield db


def supervise(db, load, sender, retry_uncertain=False):
    with db:
        columns = {r[1] for r in db.execute('PRAGMA table_info(outbox)')}
        for field in ('attempts', 'replayed'):
            if field not in columns:
                db.execute(f'ALTER TABLE outbox ADD COLUMN {field} INTEGER NOT NULL DEFAULT 0')
    interrupted = recover(db)
    if interrupted:
        print('STAKE_INTERRUPTED_UNCERTAIN', interrupted, flush=True)
    next_poll = 0
    while True:
        if time.monotonic() >= next_poll:
            next_poll = time.monotonic() + POLL_SECONDS
            try:
                snapshot = load()
            except (OSError, subprocess.SubprocessError):
                print('STAKE_SOURCE_UNAVAILABLE', flush=True)
            else:
                queued = ingest(db, snapshot)
                if queued:
                    print('STAKE_QUEUED', queued, flush=True)
        outcome = send_next(db, sender, retry_uncertain=retry_uncertain)
        if outcome:
            print('STAKE_DELIVERY', outcome.upper(), flush=True)
        wait = next_poll - time.monotonic()
        if next_pending(db):
            send_after, = db.execute('SELECT send_after FROM cursor').fetchone()
            wait = min(wait, send_after - time.time())
        time.sleep(max(0, wait))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-repo', type=Path, required=True, help='independent bare clone')
    parser.add_argument('--state', type=Path, required=True, help='private persistent SQLite path')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--initialize', action='store_true', help='record a baseline without sending')
    mode.add_argument('--enable-send', action='store_true', help='start sending new Stake occurrences')
    parser.add_argument('--retry-uncertain-once', action='store_true')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.initialize:
            with locked_db(args.state, create=True) as db:
                snapshot = published_snapshot(args.source_repo)
                initialize(db, snapshot, time.time())
            print('STAKE_BASELINE_INITIALIZED')
        else:
            token, chat = (os.environ.get(key, '').strip() for key in ('TG_BOT_TOKEN', 'TG_CHAT_ID'))
            if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token) or not chat:
                raise ValueError('bot credentials unavailable')
            with locked_db(args.state) as db:
                supervise(db, lambda: published_snapshot(args.source_repo), lambda text: telegram_send(token, chat, text), args.retry_uncertain_once)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
        # Never print exception text, message payloads, credentials, or chat IDs.
        print('STAKE_STOPPED', type(error).__name__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
