"""Atomic Dropping Odds state on a dedicated Git notes ref."""
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from uuid import uuid4

REF = 'refs/notes/goal-roi-dropping-odds'
NAMES = ('dropping_odds_state.json', 'dropping_odds_log.jsonl')
TRANSITIONS = {
    None: {'received'},
    'received': {'rejected', 'expired', 'eligible'},
    'eligible': {'pending', 'failed', 'expired'},
    'pending': {'sent', 'failed', 'expired', 'uncertain'},
}


class JournalError(RuntimeError):
    """The next transition cannot be proved safe."""


def utc(value):
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError('timezone required')
    return stamp.astimezone(timezone.utc)


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8')


class Journal:
    def __init__(self, initialized_at):
        self.initialized_at = initialized_at
        self.events = []
        self.status = {}

    def add(self, event):
        mid, status = event['message_id'], event['status']
        if (type(mid) is not int or mid <= 0 or utc(event['at']) < self.initialized_at
                or status not in TRANSITIONS.get(self.status.get(mid), ())
                or (status == 'pending' and not re.fullmatch(r'[a-f0-9]{32}', event.get('intent', '')))):
            raise JournalError('invalid event transition')
        self.events.append(event)
        self.status[mid] = status

    def append(self, mid, status, when, **fields):
        self.add(dict(fields, message_id=mid, status=status, at=when.isoformat()))

    def files(self):
        log = b''.join(map(encode, self.events))
        state = encode({'version': 1, 'initialized_at': self.initialized_at.isoformat(),
                        'events': len(self.events), 'log_sha256': hashlib.sha256(log).hexdigest()})
        return state, log

    @classmethod
    def load(cls, state_bytes, log):
        try:
            state = json.loads(state_bytes)
            if (set(state) != {'version', 'initialized_at', 'events', 'log_sha256'}
                    or type(state['version']) is not int or state['version'] != 1
                    or type(state['events']) is not int
                    or hashlib.sha256(log).hexdigest() != state['log_sha256']
                    or (log and not log.endswith(b'\n'))):
                raise ValueError
            journal = cls(utc(state['initialized_at']))
            for line in log.split(b'\n')[:-1]:
                journal.add(json.loads(line))
            last_events = {event['message_id']: event for event in journal.events}
            if (len(journal.events) != state['events']
                    or any(event['status'] == 'received'
                           or (event['status'] == 'eligible' and event.get('mode') != 'shadow')
                           for event in last_events.values())):
                raise ValueError
            return journal
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
            raise JournalError('invalid Dropping Odds state') from exc


class GitJournal:
    """Publish both notes in one compare-and-swap. No worktree, index or main writes."""

    def __init__(self, root='.', remote='origin'):
        self.root, self.remote = root, remote
        self.tip = None
        # Each stable Git object name addresses one note in the shared commit tree.
        self.notes = [self._git('hash-object', '-w', '--stdin', data=name.encode()).decode().strip()
                      for name in NAMES]

    def _run(self, *args, data=None):
        try:
            return subprocess.run(['git', *args], cwd=self.root, input=data, capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise JournalError('Git command unavailable or timed out') from exc

    def _git(self, *args, data=None):
        completed = self._run(*args, data=data)
        if completed.returncode:
            raise JournalError('Git ' + args[0] + ' failed')
        return completed.stdout

    def bootstrap(self, now):
        if self._run('ls-remote', '--exit-code', '--refs', self.remote, REF).returncode != 2:
            raise JournalError('state already exists or remote is unavailable')
        self.publish(Journal(now))

    def read(self):
        self._git('fetch', '--no-tags', '--depth=1', self.remote, REF)
        self.tip = self._git('rev-parse', 'FETCH_HEAD').decode().strip()
        state, log = (self._git('show', f'{self.tip}:{name}') for name in self.notes)
        return Journal.load(state, log)

    def publish(self, journal):
        blobs = [self._git('hash-object', '-w', '--stdin', data=data).decode().strip()
                 for data in journal.files()]
        entries = ''.join(f'100644 blob {blob}\t{name}\n' for name, blob in zip(self.notes, blobs))
        tree = self._git('mktree', data=entries.encode()).decode().strip()
        parents = ['-p', self.tip] if self.tip else []
        commit = self._git('-c', 'user.name=goal-roi-dropping-odds',
                           '-c', 'user.email=actions@users.noreply.github.com',
                           'commit-tree', tree, *parents, '-m',
                           'Dropping Odds journal ' + uuid4().hex).decode().strip()
        # The new commit always descends from the exact snapshot read by this writer.
        # A lost reply or a competing writer leaves this process without authority to send.
        self._git('push', '--porcelain', f'--force-with-lease={REF}:{self.tip or ""}',
                  self.remote, f'{commit}:{REF}')
        self.tip = commit
