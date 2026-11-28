"""Durable, incident-scoped authorization alerts on an isolated Git ref."""
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import authorization_events as events
from . import notify

REF = 'refs/heads/goal-roi-telegram-auth-alert-state'
MAX_CAS_ATTEMPTS = 4


class StateUnavailable(RuntimeError):
    pass


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('naive incident timestamp')
    return parsed


def initial_state():
    return {'version': 1, 'active': None, 'recovered_at': None, 'incidents': {}}


def validate(state):
    records = state['incidents']
    active = state['active']
    if state['version'] != 1 or not isinstance(records, dict) or (active is not None and active not in records):
        raise ValueError('invalid incident index')
    if state['recovered_at'] is not None:
        timestamp(state['recovered_at'])
    for key, row in records.items():
        if (not re.fullmatch(r'[0-9a-f]{32}', key)
                or row['status'] not in {'detected', 'reserved', 'attempting', 'confirmed', 'rejected', 'uncertain'}
                or not (row['error'].isascii() and row['error'].isidentifier())
                or ('recovered_at' in row) == (key == active)):
            raise ValueError('invalid incident')
        timestamp(row['detected_at'])
    return state


class GitState:
    """A compare-and-swap on a dedicated ref. Plumbing leaves HEAD and the index alone."""

    def __init__(self, root):
        self.root = root

    def git(self, *args, payload=None, allow_failure=False):
        try:
            result = subprocess.run(
                ['git', '-C', str(self.root), '-c', 'user.name=goal-roi-auth-alert',
                 '-c', 'user.email=actions@users.noreply.github.com', '-c', 'commit.gpgsign=false', *args],
                input=payload, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=8,
                env={**{key: value for key, value in os.environ.items()
                       if key not in {'TG_BOT_TOKEN', 'TG_CHAT_ID', 'TG_SESSION', 'TG_API_HASH'}},
                     'GIT_TERMINAL_PROMPT': '0'},
            )
        except (OSError, subprocess.SubprocessError):
            raise StateUnavailable('incident Git operation unavailable') from None
        if result.returncode and not allow_failure:
            raise StateUnavailable('incident Git operation failed')
        return result

    def read(self):
        found = self.git('ls-remote', '--exit-code', 'origin', REF, allow_failure=True)
        if found.returncode == 2:
            return '', initial_state()
        fields = found.stdout.split()
        if (found.returncode or len(fields) != 2 or fields[1] != REF
                or not re.fullmatch(r'[0-9a-f]{40}', fields[0])):
            raise StateUnavailable('incident ref unavailable')
        tip = fields[0]
        if self.git('cat-file', '-e', tip, allow_failure=True).returncode:
            self.git('fetch', '--no-tags', '--no-write-fetch-head', 'origin', REF)
        raw = self.git('show', f'{tip}:incident.json').stdout
        try:
            return tip, validate(json.loads(raw))
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
            raise StateUnavailable('invalid incident state') from None

    def write(self, tip, state):
        blob = self.git('hash-object', '-w', '--stdin', payload=json.dumps(state, sort_keys=True)).stdout.strip()
        tree = self.git('mktree', payload=f'100644 blob {blob}\tincident.json\n').stdout.strip()
        parent = ('-p', tip) if tip else ()
        commit = self.git('commit-tree', tree, *parent, '-m', 'Telegram authorization incident').stdout.strip()
        # The proposed commit is a child of the observed tip. The explicit lease also
        # guards first creation and prevents recreating a ref deleted in the meantime.
        return self.git('push', f'--force-with-lease={REF}:{tip}', 'origin', f'{commit}:{REF}',
                        allow_failure=True).returncode == 0

    def update(self, change):
        for _ in range(MAX_CAS_ATTEMPTS):
            tip, state = self.read()
            if not change(state) or self.write(tip, state):
                return state
        raise StateUnavailable('incident ref changed or was not published')


class Alerts:
    def __init__(self, storage, token='', chat='', sender=notify.send, clock=events.utc_now):
        self.storage, self.token, self.chat = storage, token, chat
        self.sender, self.clock = sender, clock
        self.pending_outcome = None

    def observe(self, event):
        if event['kind'] == 'loss':
            return self.detect(event['at'], event['error'])
        if event['kind'] != 'recovery':
            raise ValueError('unknown authorization event')
        return self.recover(event['incident'], event['at'])

    def detect(self, at, error):
        detected = timestamp(at)
        if not (error.isascii() and error.isidentifier()):
            raise ValueError('invalid authorization category')
        incident = uuid.uuid4().hex

        def record(state):
            recovered = state['recovered_at']
            if recovered and detected <= timestamp(recovered):
                return False
            if state['active'] is not None:
                row = state['incidents'][state['active']]
                if detected >= timestamp(row['detected_at']):
                    return False
                row.update(detected_at=at, error=error)
                return True
            state['active'] = incident
            state['incidents'][incident] = {'status': 'detected', 'error': error, 'detected_at': at}
            return True

        state = self.storage.update(record)
        if state['active'] == incident:
            print(f'AUTH_ALERT_DETECTED {incident} {at}', flush=True)
        return state

    def recover(self, incident, at):
        recovered = timestamp(at)

        def close(state):
            if incident is None or state['active'] != incident:
                return False
            row = state['incidents'][incident]
            if recovered < timestamp(row['detected_at']):
                return False
            row['recovered_at'] = state['recovered_at'] = at
            state['active'] = None
            return True

        return self.storage.update(close)

    def deliver(self, incident, origin_state):
        if incident is None or origin_state['incidents'][incident]['status'] not in {'detected', 'reserved'}:
            return origin_state
        if not self.token or not self.chat:
            print('AUTH_ALERT_CHANNEL_UNCONFIGURED', flush=True)
            return origin_state

        def reserve(state):
            row = state['incidents'].get(incident)
            if state['active'] != incident or row['status'] != 'detected':
                return False
            row.update(status='reserved', reserved_at=self.clock())
            return True

        reserved = self.storage.update(reserve)
        if reserved['active'] == incident and reserved['incidents'][incident]['status'] == 'reserved':
            print(f'AUTH_ALERT_RESERVED {incident} {self.clock()}', flush=True)
        attempt = uuid.uuid4().hex

        def claim(state):
            row = state['incidents'].get(incident)
            if state['active'] != incident or row['status'] != 'reserved':
                return False
            row.update(status='attempting', attempt_id=attempt, claimed_at=self.clock())
            return True

        state = self.storage.update(claim)
        row = state['incidents'].get(incident, {})
        if state['active'] != incident or row.get('attempt_id') != attempt:
            return state

        attempted_at = self.clock()
        print(f'AUTH_ALERT_ATTEMPT {incident} {attempted_at}', flush=True)
        status = 'uncertain'
        try:
            self.sender(self.token, self.chat, f'A recolha do goal-roi perdeu autorização Telegram ({row["error"]}). '
                        f'Incidente {incident}. Vê as execuções em https://github.com/nunovinhas-creator/goal-roi/actions')
        except notify.DeliveryFailed:
            status = 'rejected'
        except Exception:
            # An unexpected transport failure provides no proof of non-delivery.
            status = 'uncertain'
        else:
            status = 'confirmed'
        finally:
            finished_at = self.clock()
            print(f'AUTH_ALERT_{status.upper()} {incident} {finished_at}', flush=True)
            self.pending_outcome = (incident, attempt, status, attempted_at, finished_at)
            state = self.flush_outcome()
        return state

    def flush_outcome(self):
        if self.pending_outcome is None:
            return None
        incident, attempt, status, attempted_at, finished_at = self.pending_outcome

        def outcome(current):
            record = current['incidents'].get(incident, {})
            if record.get('attempt_id') != attempt or record['status'] != 'attempting':
                return False
            record.update(status=status, attempted_at=attempted_at, finished_at=finished_at)
            return True

        state = self.storage.update(outcome)
        self.pending_outcome = None
        return state


def run(root, alerts, *, pause=time.sleep):
    """Drain events supplied by both readers, independently of report publication."""
    active = None
    boot = True
    while True:
        queue = sorted(root.glob('*.event'))
        try:
            alerts.flush_outcome()
            if boot:
                state = alerts.storage.read()[1]
                active = state['active']
                events.write_atomic(root / 'incident', active)
                alerts.deliver(active, state)
                boot = False
            for path in queue:
                try:
                    state = alerts.observe(json.loads(path.read_text(encoding='utf-8')))
                except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
                    print('AUTH_ALERT_INVALID_EVENT', flush=True)
                    path.rename(path.with_suffix('.invalid'))
                    continue
                active = state['active']
                events.write_atomic(root / 'incident', active)
                alerts.deliver(active, state)
                path.unlink()
        except (StateUnavailable, OSError):
            print('AUTH_ALERT_STATE_UNAVAILABLE', flush=True)
            pause(5)
            continue
        if (root / 'stop').exists() and not list(root.glob('*.event')):
            (root / 'stopped').touch()
            return
        pause(0.5)


def main():
    root = events.spool()
    if root is None or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        return
    root.mkdir(parents=True, exist_ok=True)
    run(root, Alerts(GitState(Path.cwd()), os.getenv('TG_BOT_TOKEN', '').strip(),
                     os.getenv('TG_CHAT_ID', '').strip()))


if __name__ == '__main__':
    main()
