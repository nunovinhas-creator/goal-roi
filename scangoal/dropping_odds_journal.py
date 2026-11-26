"""Atomic Dropping Odds state and a separate Git ref that witnesses its tip."""
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from uuid import uuid4

REF = 'refs/notes/goal-roi-dropping-odds'
WITNESS_REF = 'refs/notes/goal-roi-dropping-odds-witness'
REFS = (REF, WITNESS_REF)
ANCHOR_REF = 'refs/heads/goal-roi-dropping-odds-anchor'
ALL_REFS = (*REFS, ANCHOR_REF)
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
        self.versions = {}

    def add(self, event):
        mid, status = event['message_id'], event['status']
        if type(mid) is not int or mid <= 0 or utc(event['at']) < self.initialized_at:
            raise JournalError('invalid event identity or time')
        versions = event.get('versions', [])
        if (not isinstance(versions, list)
                or any(not isinstance(v, str) or not re.fullmatch(r'[a-f0-9]{64}', v) for v in versions)
                or len(set(versions)) != len(versions)):
            raise JournalError('invalid archived versions')
        if status == 'late_versions':
            if mid not in self.status or not versions or self.versions.get(mid, set()).intersection(versions):
                raise JournalError('invalid late archived versions')
        elif (status not in TRANSITIONS.get(self.status.get(mid), ())
                or (status == 'pending' and not re.fullmatch(r'[a-f0-9]{32}', event.get('intent', '')))):
            raise JournalError('invalid event transition')
        else:
            self.status[mid] = status
        if versions:
            self.versions.setdefault(mid, set()).update(versions)
        self.events.append(event)

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
            last_events = {event['message_id']: event for event in journal.events
                           if event['status'] != 'late_versions'}
            if (len(journal.events) != state['events']
                    or any(event['status'] == 'received'
                           or (event['status'] == 'eligible' and event.get('mode') != 'shadow')
                           for event in last_events.values())):
                raise ValueError
            return journal
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
            raise JournalError('invalid Dropping Odds state') from exc


class GitJournal:
    """Publish both notes and the witness by atomic, leased updates to both refs."""

    def __init__(self, root='.', remote='origin', *, require_anchor=False, guard=None):
        self.root, self.remote = root, remote
        self.tip = None
        self.checkpoint = None
        self.require_anchor = require_anchor
        self.guard = guard
        self.active_refs = REFS
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

    def _remote_tips(self):
        answer = self._git('ls-remote', '--refs', self.remote, *ALL_REFS).decode()
        tips = dict((ref, tip) for tip, ref in (row.split('\t') for row in answer.splitlines()))
        return tips

    def bootstrap(self, now):
        if self._remote_tips():
            raise JournalError('state or witness already exists')
        self.publish(Journal(now))

    def _read_snapshot(self, commit):
        state, log = (self._git('show', f'{commit}:{name}') for name in self.notes)
        journal = Journal.load(state, log)
        return journal, (journal.initialized_at, log)

    def _check_guard(self):
        if self.guard is None:
            raise JournalError('anchor policy cannot be verified')
        self.guard()

    def anchor(self, expected):
        if not re.fullmatch(r'[0-9a-f]{40}', expected):
            raise JournalError('explicit verified checkpoint required')
        self.read()
        if self.active_refs != REFS or self.tip != expected:
            raise JournalError('anchor already present or checkpoint changed')
        self._check_guard()
        self._git('push', '--atomic', '--porcelain',
                  f'--force-with-lease={ANCHOR_REF}:', self.remote, f'{self.tip}:{ANCHOR_REF}')
        self.active_refs = ALL_REFS
        self.assert_current()

    def assert_current(self):
        if self.active_refs != ALL_REFS:
            raise JournalError('protected anchor required')
        self._check_guard()
        if self._remote_tips() != dict.fromkeys(ALL_REFS, self.tip):
            raise JournalError('journal and anchor changed or disagree')

    def read(self):
        tips = self._remote_tips()
        self.active_refs = ALL_REFS if ANCHOR_REF in tips or self.require_anchor else REFS
        if set(tips) != set(self.active_refs) or len(set(tips.values())) != 1:
            raise JournalError('journal and witness missing or disagree')
        if self.active_refs == ALL_REFS:
            self._check_guard()
        tip = tips[REF]
        self._git('fetch', '--no-tags', '--depth=2', self.remote, REF)
        if self._git('rev-parse', 'FETCH_HEAD').decode().strip() != tip:
            raise JournalError('journal changed while reading')
        journal, checkpoint = self._read_snapshot(tip)
        headers = self._git('cat-file', 'commit', tip).split(b'\n\n', 1)[0].splitlines()
        parents = [line.split()[1].decode() for line in headers if line.startswith(b'parent ')]
        if parents:
            if len(parents) != 1:
                raise JournalError('invalid journal ancestry')
            _, parent = self._read_snapshot(parents[0])
            self._verify_append(parent, checkpoint)
        elif journal.events:
            raise JournalError('journal root must be empty')
        if self.checkpoint is not None and self.tip != tip:
            self._verify_append(self.checkpoint, checkpoint)
        self.tip, self.checkpoint = tip, checkpoint
        return journal

    @staticmethod
    def _verify_append(before, after):
        old_origin, old_log = before
        new_origin, new_log = after
        if new_origin != old_origin or len(new_log) <= len(old_log) or not new_log.startswith(old_log):
            raise JournalError('journal history is not append-only')

    def publish(self, journal):
        state, log = journal.files()
        checkpoint = (journal.initialized_at, log)
        if self.checkpoint is not None:
            self._verify_append(self.checkpoint, checkpoint)
        elif log:
            raise JournalError('journal bootstrap must be empty')
        blobs = [self._git('hash-object', '-w', '--stdin', data=data).decode().strip()
                 for data in (state, log)]
        entries = ''.join(f'100644 blob {blob}\t{name}\n' for name, blob in zip(self.notes, blobs))
        tree = self._git('mktree', data=entries.encode()).decode().strip()
        parents = ['-p', self.tip] if self.tip else []
        commit = self._git('-c', 'user.name=goal-roi-dropping-odds',
                           '-c', 'user.email=actions@users.noreply.github.com',
                           'commit-tree', tree, *parents, '-m',
                           'Dropping Odds journal ' + uuid4().hex).decode().strip()
        # The new commit always descends from the exact snapshot read by this writer.
        # A lost reply or a competing writer leaves this process without authority to send.
        if self.active_refs == ALL_REFS:
            self._check_guard()
        leases = [f'--force-with-lease={ref}:{self.tip or ""}' for ref in self.active_refs]
        refspecs = [f'{commit}:{ref}' for ref in self.active_refs]
        self._git('push', '--atomic', '--porcelain', *leases, self.remote, *refspecs)
        self.tip, self.checkpoint = commit, checkpoint
