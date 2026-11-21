"""Append-only journal of observed Telegram message versions."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

import fcntl


def data_dir():
    return Path(os.environ.get('SCANGOAL_DATA_DIR', 'data'))


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _records(raw):
    """Yield numbered rows and return the bytes of an incomplete, unterminated tail."""
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (UnicodeError, ValueError):
            if index == len(lines) - 1 and not line.endswith(b'\n'):
                return line
            marker = {'_journal_torn_tail_sha256': _digest(line.removesuffix(b'\n'))}
            if index + 1 < len(lines) and lines[index + 1].strip() == json.dumps(marker, sort_keys=True).encode():
                continue
            raise
        if '_journal_torn_tail_sha256' not in row:
            yield index + 1, row


def message_rows(path):
    if path.exists():
        yield from _records(path.read_bytes())


def load_seen(path):
    seen = {(r['message_id'], r['sha256']) for _, r in message_rows(path)}
    return seen, max((mid for mid, _ in seen), default=0)


def _version(row):
    return row['text'], row.get('buttons'), row.get('edit_date_utc')


def _append_atomically(path, prior, extra):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.messages-', delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(prior)
            file.write(extra)
            file.flush()
            os.fsync(file.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)


def append_versions(rows):
    """Store changes relative to the last observed version of each message."""
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'messages.jsonl'
    lock_path = directory.parent / ('.' + directory.name + '.messages.lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        prior = path.read_bytes() if path.exists() else b''
        latest, records = {}, _records(prior)
        while True:
            try:
                _, row = next(records)
            except StopIteration as end:
                torn_tail = end.value
                break
            latest[row['message_id']] = _version(row)
        new = []
        for row in sorted(rows, key=lambda r: r['message_id']):
            mid, version = row['message_id'], _version(row)
            if latest.get(mid) != version:
                latest[mid] = version
                new.append(dict(row, sha256=_digest(row['text'].encode('utf-8'))))
        if new:
            extra = b'' if not prior or prior.endswith(b'\n') else b'\n'
            trail = [{'_journal_torn_tail_sha256': _digest(torn_tail)}] if torn_tail is not None else []
            extra += ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n'
                             for row in [*trail, *new]).encode('utf-8')
            _append_atomically(path, prior, extra)
        return len(new)
