"""Bounded, recoverable publication of collected data and delivery journals.

Git on `main` is the only durable store. Exclusivity comes from fast-forward pushes; a pending transaction survives a
lost runner only as a GitHub Actions artifact (bundle + manifest). Nothing here depends on an external journal.

Safety argument for abandoning a transaction: delivery intents never travel only in a pending bundle. `notify` and
`stake_alerts` push each intent to `main` before contacting Telegram, so a bundle only carries data that can be read
again and post-send outcomes. Losing those outcomes turns an open intent into `uncertain`, which is never resent."""

import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path


PATHS = ('data', 'docs/report.json', 'docs/bets.csv')
BRANCH = 'refs/heads/main'
TRACKING = 'refs/remotes/origin/main'
MAX_ATTEMPTS = 4  # initial attempt, retry, and at most two more after a restart
IDENTITY = ('-c', 'user.name=scangoal-collector', '-c', 'user.email=actions@users.noreply.github.com')


class PublicationError(RuntimeError):
    pass


class Publisher:
    def __init__(self, root='.', *, spool=None, runner=subprocess.run):
        self.root, self.runner = Path(root).resolve(), runner
        configured = spool or os.environ.get('GOAL_ROI_PUBLICATION_DIR')
        self.spool = Path(configured).resolve() if configured else self.root / self.require('rev-parse', '--git-path', 'goal-roi-publication')
        self.spool.mkdir(parents=True, exist_ok=True)
        self.manifest, self.bundle = self.spool / 'pending.json', self.spool / 'pending.bundle'

    def command(self, *args):
        try:
            return self.runner(['git', '-C', str(self.root), *IDENTITY, *args], capture_output=True, text=True, timeout=35)
        except (OSError, subprocess.TimeoutExpired):
            raise PublicationError('Git unavailable during publication') from None

    def require(self, *args):
        result = self.command(*args)
        if result.returncode:
            raise PublicationError('Git publication step failed: ' + args[0])
        return result.stdout.strip()

    def ancestor(self, base, tip):
        result = self.command('merge-base', '--is-ancestor', base, tip)
        if result.returncode not in (0, 1):
            raise PublicationError('cannot establish publication ancestry')
        return result.returncode == 0

    @contextlib.contextmanager
    def locked(self):
        with (self.spool / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def write(self, pending):
        temporary = self.manifest.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as handle:
            json.dump(pending, handle, sort_keys=True)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.manifest)

    @staticmethod
    def validate(pending):
        try:
            if (not re.fullmatch(r'[0-9a-f]{32}', pending['id'])
                    or any(not re.fullmatch(r'[0-9a-f]{40,64}', pending[k]) for k in ('tip', 'base'))
                    or type(pending['attempts']) is not int or not 0 <= pending['attempts'] <= MAX_ATTEMPTS):
                raise ValueError
            return pending
        except (ValueError, TypeError, KeyError):
            raise PublicationError('invalid pending publication manifest') from None

    def read(self):
        try:
            return self.validate(json.loads(self.manifest.read_bytes()))
        except (OSError, ValueError):
            raise PublicationError('invalid pending publication manifest') from None

    @staticmethod
    def marker(pending):
        return 'Publication-ID: ' + pending['id']

    def check_paths(self, *args):
        changed = self.require(*args).split('\0')
        if any(name and name not in PATHS and not name.startswith('data/') for name in changed):
            raise PublicationError('publication contains paths outside the data and report')

    def queue(self, pending):
        self.check_paths('log', '--format=', '--name-only', '-z', pending['base'] + '..' + pending['tip'])
        temporary = self.bundle.with_suffix('.tmp')
        temporary.unlink(missing_ok=True)
        self.require('bundle', 'create', str(temporary), 'HEAD', '^' + pending['base'])
        with temporary.open('rb') as handle:
            os.fsync(handle.fileno())
        temporary.replace(self.bundle)
        self.write(pending)

    def complete(self, pending, remote):
        self.write(dict(pending, status='completed'))
        self.bundle.unlink(missing_ok=True)
        self.manifest.unlink(missing_ok=True)
        print('PUBLICATION_COMPLETED', pending['id'])

    def complete_if_published(self, pending, remote):
        if not self.published(pending, remote):
            return False
        head = self.require('rev-parse', 'HEAD')
        if head != remote and self.ancestor(head, remote) and not self.require('status', '--porcelain', '--untracked-files=no'):
            self.require('merge', '--ff-only', remote)
        self.complete(pending, remote)
        return True

    def remote(self):
        self.require('fetch', '-q', 'origin', BRANCH)
        return self.require('rev-parse', 'FETCH_HEAD')

    def published(self, pending, remote):
        if self.ancestor(pending['tip'], remote):
            return True
        return bool(self.require('log', '-1', '--format=%H', '--fixed-strings', '--grep=' + self.marker(pending),
                                 pending['base'] + '..' + remote))

    def prepare(self, pending, remote):
        tip, base = pending['tip'], pending['base']
        head = self.require('rev-parse', 'HEAD')
        if self.ancestor(remote, tip):
            if head != tip:
                self.require('merge', '--ff-only', tip)
            return pending
        if not self.ancestor(base, remote):
            raise PublicationError('remote no longer extends the original publication base')
        if self.require('status', '--porcelain', '--untracked-files=no'):
            raise PublicationError('local changes prevent safe publication replay')
        if head == tip:
            result = self.command('rebase', '--onto', remote, base)
            if result.returncode:
                self.require('rebase', '--abort')
                raise PublicationError('concurrent data changes require reconciliation')
        else:
            self.require('merge', '--ff-only', remote)
            commits = self.require('rev-list', '--reverse', base + '..' + tip).splitlines()
            result = self.command('cherry-pick', '--keep-redundant-commits', *commits)
            if result.returncode:
                self.require('cherry-pick', '--abort')
                raise PublicationError('concurrent data changes require reconciliation')
        pending = dict(pending, base=remote, tip=self.require('rev-parse', 'HEAD'))
        self.queue(pending)
        return pending

    def replay(self, pending, *, may_abandon=False):
        if self.command('cat-file', '-e', pending['tip'] + '^{commit}').returncode:
            self.require('bundle', 'verify', str(self.bundle))
            self.require('fetch', '-q', str(self.bundle), 'HEAD')
            if self.require('rev-parse', 'FETCH_HEAD') != pending['tip']:
                raise PublicationError('bundle does not match the publication manifest')
        self.check_paths('log', '--format=', '--name-only', '-z', pending['base'] + '..' + pending['tip'])
        if not self.ancestor(pending['base'], pending['tip']) or self.require('log', '-1', '--format=%b', pending['tip']) != self.marker(pending):
            raise PublicationError('bundle lacks the original publication intent')
        # The immutable intent is recognised even when a push succeeded and its acknowledgement was lost.
        for _ in range(2):
            remote = None
            try:
                remote = self.remote()
                if self.complete_if_published(pending, remote):
                    return
            except PublicationError:
                remote = None
            if pending['attempts'] >= MAX_ATTEMPTS:
                break
            pending['attempts'] = min(pending['attempts'] + 1, MAX_ATTEMPTS)
            self.write(pending)
            if remote is None:
                continue
            try:
                pending = self.prepare(pending, remote)
                if self.command('push', '-q', 'origin', pending['tip'] + ':' + BRANCH).returncode == 0:
                    self.complete(pending, pending['tip'])
                    return
            except PublicationError:
                # A network failure is retriable without manufacturing a second transaction.
                continue
        remote = None
        try:
            remote = self.remote()
            if self.complete_if_published(pending, remote):
                return
        except PublicationError:
            remote = None
        if may_abandon and pending['attempts'] >= MAX_ATTEMPTS and remote is not None and self.abandon(pending, remote):
            return
        self.write(dict(pending, status='degraded'))
        print('PUBLICATION_DEGRADED', pending['id'], 'attempts', pending['attempts'])
        raise PublicationError('data and journal publication is pending reconciliation')

    def abandon(self, pending, remote):
        """Stop retrying a transaction that exhausted its attempts, so the collection chain keeps moving. Only at the start
        of a run (clean checkout): the checkout returns to the remote tip, so no abandoned commit reaches a later
        publication. The files are kept for diagnosis under `abandoned/`; see the module docstring for why this cannot
        cause a second send."""
        if self.require('status', '--porcelain', '--untracked-files=no'):
            return False
        self.require('reset', '-q', '--hard', remote)
        folder = self.spool / 'abandoned'
        folder.mkdir(exist_ok=True)
        for source in (self.manifest, self.bundle):
            if source.exists():
                source.replace(folder / (pending['id'] + source.suffix))
        print('PUBLICATION_ABANDONED', pending['id'], 'attempts', pending['attempts'])
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a', encoding='utf-8') as out:
                out.write('Publication %s abandoned after %d attempts; its intents were already on main.\n'
                          % (pending['id'], pending['attempts']))
        return True

    def resume(self, *, may_abandon=False):
        with self.locked():
            if self.manifest.exists():
                self.replay(self.read(), may_abandon=may_abandon)

    def publish(self):
        with self.locked():
            if self.manifest.exists():
                self.replay(self.read())
            base = self.require('rev-parse', TRACKING)
            self.require('add', '--', *[name for name in PATHS if (self.root / name).exists()])
            self.check_paths('diff', '--cached', '--name-only', '-z')
            staged_changes = self.require('diff', '--cached', '--name-only')
            if not staged_changes and base == self.require('rev-parse', 'HEAD'):
                return
            pending = {'id': uuid.uuid4().hex, 'base': base, 'attempts': 0, 'status': 'pending'}
            self.require('commit', '-q', '--allow-empty', '-m', 'data: collect [skip ci]', '-m', self.marker(pending))
            pending['tip'] = self.require('rev-parse', 'HEAD')
            self.queue(pending)
            print('PUBLICATION_PENDING', pending['id'])
            self.replay(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('resume', 'publish'))
    args = parser.parse_args()
    try:
        publisher = Publisher()
        if args.operation == 'resume':
            publisher.resume(may_abandon=True)  # Only the first step of a run, on a clean checkout.
        else:
            publisher.publish()
    except PublicationError as exc:
        print('PUBLICATION_FAILED', exc)
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
