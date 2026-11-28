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
from .authorization_status import Reporter

REF = 'refs/heads/goal-roi-telegram-auth-alert-state'
WITNESS = 'refs/heads/goal-roi-telegram-auth-alert-witness'
INCIDENT_ID = re.compile(r'[0-9a-f]{32}')
GIT_SHA = re.compile(r'[0-9a-f]{40}')
OUTCOMES = {'confirmed', 'rejected', 'uncertain'}
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


def unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('duplicate state field')
    return result


def validate(state):
    if set(state) != {'version', 'active', 'recovered_at', 'incidents'} or type(state['version']) is not int or state['version'] != 1:
        raise ValueError('invalid state schema')
    records, active = state['incidents'], state['active']
    if not isinstance(records, dict) or (active is not None and active not in records):
        raise ValueError('invalid incident index')
    closed_dates = []
    for key, row in records.items():
        status = row['status']
        if (not INCIDENT_ID.fullmatch(key)
                or status not in {'detected', 'reserved', 'attempting'} | OUTCOMES
                or not (row['error'].isascii() and row['error'].isidentifier())
                or ('recovered_at' in row) == (key == active)):
            raise ValueError('invalid incident')
        dates = {'detected_at'}
        fields = {'status', 'error'}
        if status != 'detected':
            dates.add('reserved_at')
        if status in {'attempting'} | OUTCOMES:
            dates.add('claimed_at')
            fields.add('attempt_id')
            if not INCIDENT_ID.fullmatch(row['attempt_id']):
                raise ValueError('invalid attempt')
        if status in OUTCOMES:
            dates.update(('attempted_at', 'finished_at'))
        if key != active:
            dates.add('recovered_at')
            closed_dates.append(timestamp(row['recovered_at']))
        if set(row) != dates | fields:
            raise ValueError('invalid incident fields')
        for key_date in dates:
            timestamp(row[key_date])
        if 'recovered_at' in row and timestamp(row['recovered_at']) < timestamp(row['detected_at']):
            raise ValueError('invalid incident chronology')
    recovered = state['recovered_at']
    if closed_dates:
        if timestamp(recovered) != max(closed_dates):
            raise ValueError('invalid recovery index')
    elif recovered is not None:
        raise ValueError('invalid recovery index')
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
        found = self.git('ls-remote', '--exit-code', 'origin', REF, WITNESS, allow_failure=True)
        if found.returncode:
            raise StateUnavailable('incident refs missing or inaccessible')
        refs = {}
        for line in found.stdout.splitlines():
            fields = line.split()
            if len(fields) != 2 or not GIT_SHA.fullmatch(fields[0]) or fields[1] in refs:
                raise StateUnavailable('invalid incident refs')
            refs[fields[1]] = fields[0]
        if set(refs) != {REF, WITNESS} or refs[REF] != refs[WITNESS]:
            raise StateUnavailable('incident refs absent or divergent')
        tip = refs[REF]
        if self.git('cat-file', '-e', tip, allow_failure=True).returncode:
            self.git('fetch', '--no-tags', '--no-write-fetch-head', 'origin', REF)
        raw = self.git('show', f'{tip}:incident.json').stdout
        try:
            return tip, validate(json.loads(raw, object_pairs_hook=unique_object))
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
            raise StateUnavailable('invalid incident state') from None

    def write(self, tip, state):
        if not GIT_SHA.fullmatch(tip):
            raise StateUnavailable('explicit provisioning required')
        try:
            validate(state)
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
            raise StateUnavailable('invalid incident state') from None
        blob = self.git('hash-object', '-w', '--stdin', payload=json.dumps(state, sort_keys=True)).stdout.strip()
        tree = self.git('mktree', payload=f'100644 blob {blob}\tincident.json\n').stdout.strip()
        commit = self.git('commit-tree', tree, '-p', tip, '-m', 'Telegram authorization incident').stdout.strip()
        # Both refs advance atomically to a child of the observed tip.
        return self.git('push', '--atomic', f'--force-with-lease={REF}:{tip}', f'--force-with-lease={WITNESS}:{tip}',
                        'origin', f'{commit}:{REF}', f'{commit}:{WITNESS}', allow_failure=True).returncode == 0

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
        self.report = Reporter()

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
            self.report('reserved', incident)
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

        self.report('attempting', incident)
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
            self.report(status, incident)
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
    alerts.report = Reporter(root)
    if not alerts.token or not alerts.chat:
        alerts.report('channel_unconfigured')
    boot = True

    def show(state):
        active = state['active']
        events.write_atomic(root / 'incident', active)
        if active is None:
            alerts.report('healthy')
        else:
            status = state['incidents'][active]['status']
            alerts.report('abandoned' if status == 'attempting' else status, active)

    while True:
        queue = sorted(root.glob('*.event'))
        try:
            alerts.flush_outcome()
            if boot:
                state = alerts.storage.read()[1]
                show(state)
                alerts.deliver(state['active'], state)
                boot = False
            for path in queue:
                try:
                    state = alerts.observe(json.loads(path.read_text(encoding='utf-8')))
                except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
                    print('AUTH_ALERT_INVALID_EVENT', flush=True)
                    path.rename(path.with_suffix('.invalid'))
                    continue
                show(state)
                alerts.deliver(state['active'], state)
                path.unlink()
        except (StateUnavailable, OSError):
            alerts.report('state_unavailable')
            print('AUTH_ALERT_STATE_UNAVAILABLE', flush=True)
            pause(5)
            continue
        if (root / 'stop').exists() and not next(root.glob('*.event'), None):
            (root / 'stopped').touch()
            return
        pause(0.5)


def main():
    root = events.spool()
    if root is None or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        return
    root.mkdir(parents=True, exist_ok=True)
    try:
        run(root, Alerts(GitState(Path.cwd()), os.getenv('TG_BOT_TOKEN', '').strip(),
                         os.getenv('TG_CHAT_ID', '').strip()))
    finally:
        try:
            (root / 'exited').touch()
        except OSError:
            print('AUTH_ALERT_REPORT_FAILED', flush=True)


if __name__ == '__main__':
    main()
