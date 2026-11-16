"""P0 (review #5920565094): persistence across GitHub Actions runners, with real git repositories (offline).

A bare repository plays GitHub; each runner is a FRESH clone (clean checkout), as on a new Actions runner. Runner A dies or
loses its push at every critical point; runner B starts from the remote only. With the publish barrier (NOTIFY_DURABLE=git),
B must never send again what A may have sent. Without it (the current workflow), the duplicate is reproduced.
"""
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from scangoal import notify
from test_telegram_presentation import ENV, NOW, Crash, base_state, entry, migrated, report

DURABLE = dict(ENV, NOTIFY_DURABLE='git', GITHUB_REF_NAME='main')


def git(cwd, *args, check=True):
    r = subprocess.run(['git', '-C', str(cwd), '-c', 'user.name=t', '-c', 'user.email=t@invalid', '-c', 'commit.gpgsign=false', *args],
                       capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(r.stderr)
    return r


@unittest.skipUnless(shutil.which('git'), 'git required')
class AcrossRunners(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.remote = self.tmp / 'github.git'
        git(self.tmp, 'init', '-q', '--bare', '-b', 'main', str(self.remote))
        seed = self.tmp / 'seed'
        git(self.tmp, 'clone', '-q', str(self.remote), str(seed))
        (seed / 'data').mkdir()
        (seed / 'data' / 'notify_state.json').write_text(json.dumps(migrated(base_state())))
        (seed / 'data' / 'report.json').write_text(json.dumps(report(watch=[entry('m1'), entry('m2', minutes=-2)]), ensure_ascii=False))
        (seed / 'README.md').write_text('x\n')
        git(seed, 'add', '-A')
        git(seed, 'commit', '-qm', 'seed')
        git(seed, 'push', '-q', 'origin', 'HEAD:main')
        self.runners = 0

    def runner(self):
        """A fresh Actions runner: clean clone of the remote."""
        self.runners += 1
        path = self.tmp / ('runner%d' % self.runners)
        git(self.tmp, 'clone', '-q', str(self.remote), str(path))
        return path

    def notify(self, clone, sender, env=DURABLE, now=NOW, checkpoint=None):
        with patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(clone / 'data')}), redirect_stdout(io.StringIO()) as out:
            notify.run(sender, now, env, checkpoint)
        return out.getvalue()

    def commit_step(self, clone):
        """The workflow's "Commit data" step (unchanged)."""
        git(clone, 'add', 'data')
        if git(clone, 'diff', '--cached', '--quiet', check=False).returncode:
            git(clone, 'commit', '-qm', 'data: collect [skip ci]')
        git(clone, 'pull', '-q', '--rebase', 'origin', 'main')
        git(clone, 'push', '-q', 'origin', 'HEAD:main')

    def remote_state(self):
        view = self.runner()
        return json.loads((view / 'data' / 'notify_state.json').read_text())

    def reject_pushes(self, on=True):
        hook = self.remote / 'hooks' / 'pre-receive'
        if on:
            hook.write_text('#!/bin/sh\nexit 1\n')
            hook.chmod(0o755)
        elif hook.exists():
            hook.unlink()

    # -----------------------------------------------------------------------------------------------------------------

    def test_without_the_barrier_a_dead_runner_causes_a_duplicate(self):
        """Characterisation of the defect found by the audit (current workflow, NOTIFY_DURABLE unset)."""
        a, sent = self.runner(), []

        def crash_after_send(token, chat, text):
            sent.append(text)
            raise Crash()
        with self.assertRaises(Crash):
            self.notify(a, crash_after_send, env=ENV)
        b = self.runner()                                               # A never committed
        self.notify(b, lambda t, c, text: sent.append(text), env=ENV)
        self.assertEqual(sent[0], sent[1])                              # the same notice twice: the P0 failure

    def test_runner_dies_after_sending(self):
        a, sent = self.runner(), []

        def crash_after_send(token, chat, text):
            sent.append(text)
            raise Crash()
        with self.assertRaises(Crash):
            self.notify(a, crash_after_send)
        self.assertIn('in_flight', [r['state'] for r in self.remote_state()['deliveries'].values()])   # published before the send
        b = self.runner()
        self.notify(b, lambda t, c, text: sent.append(text))
        self.commit_step(b)
        self.assertEqual(len(sent), 2)
        self.assertEqual(len(set(sent)), 2)                             # m1 once (A), m2 once (B): no duplicate
        st = self.remote_state()
        self.assertEqual(sorted(r['state'] for r in st['deliveries'].values()), ['delivered', 'uncertain'])
        self.assertEqual(st['sent_total'], 313)                         # the uncertain one is never counted

    def test_runner_dies_at_every_point_of_a_delivery(self):
        points = ('after_intent_state', 'after_intent', 'after_publish', 'after_response', 'after_record_state', 'after_record')
        for point in points:
            with self.subTest(point):
                self.setUp()
                a, sent = self.runner(), []

                def cp(name):
                    if name == point:
                        raise Crash()
                with self.assertRaises(Crash):
                    self.notify(a, lambda t, c, text: sent.append(text), checkpoint=cp)
                b = self.runner()
                self.notify(b, lambda t, c, text: sent.append(text))
                self.commit_step(b)
                c = self.runner()
                self.notify(c, lambda t, c_, text: sent.append(text), now=NOW + timedelta(minutes=5))
                self.assertEqual(len(sent), len(set(sent)), point)       # never the same text twice, across three runners
                st = self.remote_state()
                delivered = sum(r['state'] == 'delivered' for r in st['deliveries'].values())
                self.assertEqual(st['sent_total'], 312 + delivered, point)

    def test_push_rejected_nothing_is_sent_and_it_goes_out_once_later(self):
        a, sent = self.runner(), []
        self.reject_pushes()
        out = self.notify(a, lambda t, c, text: sent.append(text))
        self.assertIn('NOTIFY_PUBLISH_FAILED', out)
        self.assertEqual(sent, [])                                      # never contacted Telegram
        local = json.loads((a / 'data' / 'notify_state.json').read_text())
        self.assertEqual([r['state'] for r in local['deliveries'].values()], ['pending', 'pending'])
        self.reject_pushes(False)
        self.commit_step(a)                                             # A survives and its final commit goes through
        b = self.runner()
        self.notify(b, lambda t, c, text: sent.append(text))
        self.assertEqual(len(sent), 2)
        self.assertEqual(len(set(sent)), 2)

    def test_push_rejected_and_runner_dies(self):
        a, sent = self.runner(), []
        self.reject_pushes()
        self.notify(a, lambda t, c, text: sent.append(text))
        self.reject_pushes(False)                                       # A dies without its final commit
        b = self.runner()
        self.notify(b, lambda t, c, text: sent.append(text))
        self.assertEqual(len(sent), 2)                                  # sent once, by B

    def test_ambiguous_push_accepted_but_reported_failed(self):
        for survives in (False, True):
            with self.subTest(survives=survives):
                self.setUp()
                a, sent = self.runner(), []
                real = notify.GitPublisher.__call__

                def lost_answer(publisher, key):
                    real(publisher, key)                                # the remote accepted it…
                    return False                                        # …but the answer was lost
                with patch.object(notify.GitPublisher, '__call__', lost_answer):
                    self.notify(a, lambda t, c, text: sent.append(text))
                self.assertEqual(sent, [])
                if survives:
                    self.commit_step(a)
                b = self.runner()
                self.notify(b, lambda t, c, text: sent.append(text))
                self.assertEqual(len(sent), len(set(sent)))             # at most once, whatever happened
                if not survives:
                    st = json.loads((b / 'data' / 'notify_state.json').read_text())
                    self.assertIn('uncertain', [r['state'] for r in st['deliveries'].values()])   # lost, never duplicated

    def test_remote_moved_by_another_commit(self):
        stale = self.runner()                                           # cloned before someone else pushed
        other = self.runner()
        (other / 'README.md').write_text('changed by someone else\n')
        git(other, 'commit', '-qam', 'docs')
        git(other, 'push', '-q', 'origin', 'HEAD:main')
        dirty = (stale / 'data' / 'report.json').read_text() + '\n'
        (stale / 'data' / 'report.json').write_text(dirty)             # a dirty tree, as during a run
        sent = []
        self.notify(stale, lambda t, c, text: sent.append(text))
        self.assertEqual(len(sent), 2)
        st = self.remote_state()
        self.assertEqual(sorted(r['state'] for r in st['deliveries'].values()), ['delivered', 'in_flight'])   # last outcome: local
        self.assertEqual((stale / 'data' / 'report.json').read_text(), dirty)                          # autostash restored it

    def test_conflicting_remote_state_blocks_the_send(self):
        a = self.runner()
        other = self.runner()
        st = json.loads((other / 'data' / 'notify_state.json').read_text())
        st['summary_day'] = '2026-11-16'
        (other / 'data' / 'notify_state.json').write_text(json.dumps(st))
        git(other, 'commit', '-qam', 'someone edits the state')
        git(other, 'push', '-q', 'origin', 'HEAD:main')
        sent = []
        out = self.notify(a, lambda t, c, text: sent.append(text))
        self.assertIn('NOTIFY_PUBLISH_FAILED', out)
        self.assertEqual(sent, [])
        self.assertFalse((a / '.git' / 'rebase-merge').exists() or (a / '.git' / 'rebase-apply').exists())   # no half rebase left

    def test_fast_reads_several_runs_in_one_runner_then_death(self):
        a, sent = self.runner(), []
        self.notify(a, lambda t, c, text: sent.append(text))           # fast read 1: both notices, intents and outcomes local
        rep = report(watch=[entry('m1'), entry('m2', minutes=-2), entry('m3', minutes=-1)])
        (a / 'data' / 'report.json').write_text(json.dumps(rep, ensure_ascii=False))

        def crash_after_send(token, chat, text):
            sent.append(text)
            raise Crash()
        with self.assertRaises(Crash):                                  # fast read 2 sends m3 and the runner dies
            self.notify(a, crash_after_send, now=NOW + timedelta(minutes=2))
        b = self.runner()                                               # the final "fast reads" commit never ran
        (b / 'data' / 'report.json').write_text(json.dumps(rep, ensure_ascii=False))
        self.notify(b, lambda t, c, text: sent.append(text), now=NOW + timedelta(minutes=10))
        self.assertEqual(len(sent), 3)                                  # m1, m2, m3: each once
        self.assertEqual(len(set(sent)), 3)

    def test_misconfiguration_sends_nothing(self):
        a, sent = self.runner(), []
        for env in (dict(ENV, NOTIFY_DURABLE='git'), dict(ENV, NOTIFY_DURABLE='yes', GITHUB_REF_NAME='main')):
            with self.subTest(env=env['NOTIFY_DURABLE']), self.assertRaises(notify.NotifyError):
                self.notify(a, lambda t, c, text: sent.append(text), env=env)
        self.assertEqual(sent, [])

    def test_barrier_commits_only_the_notifier_files(self):
        a, sent = self.runner(), []
        (a / 'README.md').write_text('local, uncommitted\n')
        (a / 'data' / 'other.json').write_text('{}')
        self.notify(a, lambda t, c, text: sent.append(text))
        changed = git(self.tmp, '-C', str(self.remote), 'log', '--name-only', '--format=', 'main', check=True).stdout.split()
        self.assertTrue(set(changed) - {'README.md', 'data/notify_state.json', 'data/report.json'} <= {'data/notify_log.jsonl'})
        self.assertEqual((a / 'README.md').read_text(), 'local, uncommitted\n')     # the working tree is left as it was
        subjects = git(self.tmp, '-C', str(self.remote), 'log', '--format=%s', 'main').stdout.split('\n')
        self.assertTrue(all('[skip ci]' in x for x in subjects if x.startswith('notify: intent')))
        self.assertEqual(sum(x.startswith('notify: intent') for x in subjects), 2)


class WithdrawalCrash(unittest.TestCase):
    """A crash between the withdrawal in the state and its `withdrawn` log line: the notice was never sent, so it must stay
    pending (sent once later), never become uncertain."""

    def test_withdrawn_in_state_but_not_in_log(self):
        from test_telegram_presentation import Data, consistent
        d = Data(self, migrated(base_state()), report(watch=[entry('m1')]))
        real_append = notify.FileStore.append_log

        def append(store, entries):
            if any(e['event'] == 'withdrawn' for e in entries):
                raise Crash()
            return real_append(store, entries)
        with patch.object(notify, 'durability', lambda env, store: (lambda key: False)), \
                patch.object(notify.FileStore, 'append_log', append), self.assertRaises(Crash):
            d.run()
        self.assertEqual(d.sent, [])
        self.assertEqual([e['event'] for e in d.log()], ['pending', 'intent'])      # the log never learnt of the withdrawal
        d.run(now=NOW + timedelta(minutes=2))
        st = consistent(self, d)
        self.assertEqual(len(d.sent), 1)                                             # sent once, not lost as "uncertain"
        self.assertEqual([r['state'] for r in st['deliveries'].values()], ['delivered'])


if __name__ == '__main__':
    unittest.main()
