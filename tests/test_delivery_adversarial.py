"""Adversarial delivery checks for PR #74 (two runners, lost acknowledgements, restarts, A07 with a real clock), written against the public behaviour of the modules, not their internals.

Also runnable against another checkout: GOAL_ROI_ROOT=<checkout> python tests/test_delivery_adversarial.py
Every message, account and send is synthetic; Git remotes are local bare repositories; no network.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.environ.get('GOAL_ROI_ROOT') or Path(__file__).resolve().parents[1]).resolve()
sys.path.insert(0, str(ROOT))
from scangoal import notify, polymarket_alerts, stake_alerts  # noqa: E402

try:
    from scangoal import publication
except ImportError:
    publication = None

NOW = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
MIGRATION = NOW - timedelta(days=7)


def at(minutes=0, *, days=0):
    return NOW + timedelta(minutes=minutes, days=days)


def iso(minutes=0, *, days=0):
    return at(minutes, days=days).isoformat()


def state(watch_sent=(), notified=None, migration=True):
    migrations = {notify.STATUS_MIGRATION: {'at_utc': MIGRATION.isoformat()},
                  polymarket_alerts.ACTIVATION: {'at_utc': MIGRATION.isoformat()}}
    if migration:
        migrations[notify.FORMAT_MIGRATION] = {'at_utc': MIGRATION.isoformat(), 'sent_total': 0}
    return {'sent_total': len(watch_sent), 'watch_sent': sorted(watch_sent), 'statuses': {}, 'hello': True,
            'notified_items': dict(notified or {}), 'migrations': migrations}


def watch(match_id, *, match='Alfa FC — Beta FC', market='Asian Total', selection='Over 2.5', minute=-5,
          kickoff=30, league='Liga Exemplo'):
    return {'date_utc': iso(minute), 'match': match, 'match_id': match_id, 'canonical_match_id': match_id,
            'type': 'BETFAIR', 'phase': 'pre', 'market': market, 'selection': selection, 'odds': 1.9,
            'kickoff_utc': iso(kickoff), 'league': league, 'period': 'regulation', 'finished': False,
            'reasons': [{'id': 'h1', 'status': 'promissora', 'certified': False}]}


def report(*items):
    return {'generated_at_utc': NOW.isoformat(), 'brain': {'watch': list(items)}}


class StakeClock(unittest.TestCase):
    def test_A07_instrumented_clock_crosses_the_limit_during_the_push(self):
        """A07 with the program clock really advanced by the push (the #71 script only wrote a value in a list)."""
        b = watch('fx-a07')
        b.update(type='STAKE_LOAD', phase='live', date_utc=iso(-29), kickoff_utc=iso(-60), after_kickoff_min=31)
        times = {'now': NOW}
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / stake_alerts.STATE_NAME).write_text(json.dumps(
                {'format': stake_alerts.STATE_FORMAT, 'sent': {}, 'sent_total': 0}))
            (data / stake_alerts.LOG_NAME).write_text(json.dumps({'format': stake_alerts.LOG_FORMAT}) + '\n')
            sent = []

            def push(_key):
                times['now'] = at(2)          # the remote acknowledgement arrives two minutes later
                return True

            with patch.object(stake_alerts, 'matches', lambda *_: [{'key': 'fx-a07|m|s', 'lines': [1, 6, 9], 'b': b}]), \
                    patch.dict(os.environ, {'SCANGOAL_DATA_DIR': folder}):
                stake_alerts.deliver([b], NOW, {'TG_BOT_TOKEN': 's', 'TG_CHAT_ID': 's'},
                                     sender=lambda *_: sent.append(True), path=data / stake_alerts.STATE_NAME,
                                     publisher=push, clock=lambda: times['now'])
            self.assertEqual(sent, [], 'sent after the signal reached 31 minutes during publication')


def git(path, *args, env=None):
    result = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, timeout=30, env=env)
    if result.returncode:
        raise RuntimeError('git %s: %s' % (' '.join(args[:2]), result.stderr))
    return result.stdout.strip()


IDENT = ['-c', 'user.name=t', '-c', 'user.email=t@example.invalid']


class TwoRunners(unittest.TestCase):
    """Two independent clones of one bare remote, as two GitHub runners."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.remote = self.tmp / 'remote.git'
        git(self.tmp, 'init', '-q', '--bare', '--initial-branch=main', str(self.remote))
        seed = self.tmp / 'seed'
        git(self.tmp, 'clone', '-q', str(self.remote), str(seed))
        (seed / 'data').mkdir()
        (seed / 'docs').mkdir()
        notify.FileStore(seed / 'data').save_state(state())
        notify.FileStore(seed / 'data').log_path.touch()
        (seed / 'data/messages.jsonl').write_text('')
        (seed / 'docs/report.json').write_text('{}\n')
        git(seed, 'add', '-A')
        git(seed, *IDENT, 'commit', '-qm', 'seed')
        git(seed, 'push', '-q', 'origin', 'main')

    def clone(self, name):
        path = self.tmp / name
        git(self.tmp, 'clone', '-q', str(self.remote), str(path))
        git(path, 'config', 'user.name', 't')
        git(path, 'config', 'user.email', 't@example.invalid')
        return path

    def publisher(self, clone, stamp=None):
        def runner(args, **kw):
            env = dict(os.environ)
            if stamp:
                env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)   # identical content and clock
            return subprocess.run(args, env=env, **kw)
        return notify.GitPublisher(clone, 'main', ['data/notify_state.json', 'data/notify_log.jsonl'], runner=runner)

    def race(self, stamp):
        a, b = self.clone('a'), self.clone('b')
        barrier, sends = threading.Barrier(2), []
        rep = report(watch('fx-race'))

        def run(index, clone):
            local = notify.FileStore(clone / 'data')
            pub = self.publisher(clone, stamp)

            def publish(key):
                barrier.wait(timeout=15)        # both planned before either pushed
                if index == 2:
                    import time
                    time.sleep(0.5)
                return pub(key)

            try:
                notify.deliver(local, rep, local.load_state(), NOW, lambda *_: sends.append(index), 's', 's',
                               publish=publish, clock=lambda: NOW)
            except notify.NotifyError:
                pass

        with ThreadPoolExecutor(2) as pool:
            for f in [pool.submit(run, i, c) for i, c in ((1, a), (2, b))]:
                f.result(timeout=60)
        log, _ = notify.parse_log(git(self.remote, 'show', 'main:data/notify_log.jsonl').encode())
        return sends, [e for e in log if e['event'] == 'intent']

    def test_R01_identical_commits_same_second_only_one_send(self):
        sends, intents = self.race(iso())
        self.assertEqual((len(sends), len(intents)), (1, 1), (sends, intents))

    def test_R02_different_clocks_only_one_send(self):
        sends, intents = self.race(None)
        self.assertEqual((len(sends), len(intents)), (1, 1), (sends, intents))

    def test_R03_lost_push_ack_then_restart_never_resends(self):
        a = self.clone('a')
        sends = []

        def lose(args, **kw):
            r = subprocess.run(args, **kw)
            if 'push' in args and r.returncode == 0:
                return subprocess.CompletedProcess(args, 1, '', 'connection reset after acceptance')
            return r

        local = notify.FileStore(a / 'data')
        try:
            notify.deliver(local, report(watch('fx-ack')), local.load_state(), NOW, lambda *_: sends.append(1), 's', 's',
                           publish=notify.GitPublisher(a, 'main', ['data/notify_state.json', 'data/notify_log.jsonl'],
                                                       runner=lose), clock=lambda: NOW)
        except notify.NotifyError:
            pass
        b = self.clone('b')                           # fresh runner from the remote only
        restarted = notify.FileStore(b / 'data')
        st, _ = notify.deliver(restarted, report(watch('fx-ack')), restarted.load_state(), NOW, lambda *_: sends.append(2),
                               's', 's', publish=self.publisher(b), clock=lambda: NOW)
        self.assertEqual(sends, [])
        self.assertEqual(st['delivery_counts'].get('uncertain'), 1)

    def test_R04_network_down_before_push_sends_nothing_and_stays_pending(self):
        a = self.clone('a')
        git(a, 'remote', 'set-url', 'origin', str(self.tmp / 'missing.git'))
        local, sends = notify.FileStore(a / 'data'), []
        try:
            notify.deliver(local, report(watch('fx-net')), local.load_state(), NOW, lambda *_: sends.append(1), 's', 's',
                           publish=self.publisher(a), clock=lambda: NOW)
        except notify.NotifyError:
            pass
        self.assertEqual(sends, [])


@unittest.skipIf(publication is None, 'no publication module in this version')
class Publication(unittest.TestCase):
    def setUp(self):
        TwoRunners.setUp(self)
        self.env = self.enterContext(patch.dict(os.environ, {k: '' for k in ('GOAL_ROI_DURABLE_URL', 'GOAL_ROI_DURABLE_TOKEN')}))

    clone = TwoRunners.clone

    def spool(self, name):
        return self.tmp / ('spool-' + name)

    def change(self, clone, tag):
        with (clone / 'data/messages.jsonl').open('a') as f:
            f.write(json.dumps({'id': tag}) + '\n')

    def test_P01_publish_without_any_external_journal(self):
        os.environ.pop('GOAL_ROI_DURABLE_REQUIRED', None)
        a = self.clone('a')
        self.change(a, 'p01')
        publication.Publisher(a, spool=self.spool('a')).publish()
        self.assertIn('p01', git(self.remote, 'show', 'main:data/messages.jsonl'))

    def test_P02_runner_lost_after_commit_next_runner_replays_once(self):
        a = self.clone('a')
        self.change(a, 'p02')
        calls = {'n': 0}

        def dies_on_push(args, **kw):
            if 'push' in args:
                calls['n'] += 1
                return subprocess.CompletedProcess(args, 1, '', 'network')
            return subprocess.run(args, **kw)

        with self.assertRaises(publication.PublicationError):
            publication.Publisher(a, spool=self.spool('a'), runner=dies_on_push).publish()
        # The artifact (manifest + bundle) is all the next runner gets.
        b = self.clone('b')
        spool = self.spool('b')
        spool.mkdir()
        for name in ('pending.json', 'pending.bundle'):
            (spool / name).write_bytes((self.spool('a') / name).read_bytes())
        publication.Publisher(b, spool=spool).resume(**self.abandon_kw())
        publication.Publisher(b, spool=spool).resume(**self.abandon_kw())        # idempotent
        log = git(self.remote, 'log', '--format=%s%n%b', 'main')
        self.assertEqual(log.count('Publication-ID:'), 1)
        self.assertEqual(git(self.remote, 'show', 'main:data/messages.jsonl').count('p02'), 1)

    def test_P03_push_accepted_ack_lost_is_not_pushed_twice(self):
        a = self.clone('a')
        self.change(a, 'p03')

        def lose(args, **kw):
            r = subprocess.run(args, **kw)
            if 'push' in args and r.returncode == 0:
                return subprocess.CompletedProcess(args, 1, '', 'reset')
            return r

        publication.Publisher(a, spool=self.spool('a'), runner=lose).publish()
        self.assertEqual(git(self.remote, 'show', 'main:data/messages.jsonl').count('p03'), 1)
        self.assertFalse((self.spool('a') / 'pending.json').exists())

    def test_P04_concurrent_writer_does_not_lose_collector_commits(self):
        a, b = self.clone('a'), self.clone('b')
        self.change(a, 'from-a')
        self.change(b, 'from-b')
        publication.Publisher(b, spool=self.spool('b')).publish()
        try:
            publication.Publisher(a, spool=self.spool('a')).publish()
        except publication.PublicationError:
            pass
        text = git(self.remote, 'show', 'main:data/messages.jsonl')
        self.assertIn('from-b', text)               # never overwritten (no force push)

    def test_P05_exhausted_conflicting_transaction_does_not_block_later_runs(self):
        a, b = self.clone('a'), self.clone('b')
        (a / 'data/messages.jsonl').write_text('{"conflict": "a"}\n')
        (b / 'data/messages.jsonl').write_text('{"conflict": "b"}\n')
        publication.Publisher(b, spool=self.spool('b')).publish()
        spool = self.spool('a')
        for _ in range(3):
            try:
                publication.Publisher(a, spool=spool).publish()
            except publication.PublicationError:
                pass
            if not (spool / 'pending.json').exists():
                break
        c = self.clone('c')
        cspool = self.spool('c')
        cspool.mkdir()
        if (spool / 'pending.json').exists():
            for name in ('pending.json', 'pending.bundle'):
                (cspool / name).write_bytes((spool / name).read_bytes())
        try:
            for _ in range(3):
                publication.Publisher(c, spool=cspool).resume(**self.abandon_kw())
        except publication.PublicationError:
            pass
        self.change(c, 'later')
        publication.Publisher(c, spool=cspool).publish()
        text = git(self.remote, 'show', 'main:data/messages.jsonl')
        self.assertIn('later', text)
        self.assertIn('"conflict": "b"', text)

    @staticmethod
    def abandon_kw():
        import inspect
        return {'may_abandon': True} if 'may_abandon' in inspect.signature(publication.Publisher.resume).parameters else {}


class Workflow(unittest.TestCase):
    def test_W01_collector_has_no_external_journal(self):
        text = (ROOT / '.github/workflows/collect.yml').read_text()
        for word in ('DURABLE_URL', 'DURABLE_TOKEN', 'DURABLE_REQUIRED', 'workers.dev'):
            self.assertNotIn(word, text)

    def test_W02_telegram_read_does_not_depend_on_resume(self):
        text = (ROOT / '.github/workflows/collect.yml').read_text()
        if 'Resume pending publication' not in text:
            return
        block = text.split('- name: Read bot conversation (read-only)\n', 1)[1].split('\n      - ', 1)[0]
        self.assertIn('!cancelled()', block)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, 'w')).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    failed = {t.id().split('.')[-1]: str(e).strip().splitlines()[-1][:220] for t, e in result.failures + result.errors}
    names = sorted(t.id().split('.')[-1] for t in unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]) for t in t)
    for n in names:
        print('%-70s %s' % (n, 'FAIL ' + failed[n] if n in failed else ('SKIP' if any(n == s[0].id().split('.')[-1] for s in result.skipped) else 'ok')))
    print('total', len(names), 'failed', len(failed), 'skipped', len(result.skipped))
    sys.exit(1 if failed else 0)
