import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scangoal.publication import PublicationError, Publisher


class PublicationRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.remote, self.first, self.second = (self.tmp / name for name in ('origin.git', 'first', 'second'))
        self.git(self.tmp, 'init', '--bare', '--initial-branch=main', str(self.remote))
        self.git(self.tmp, 'clone', str(self.remote), str(self.first))
        (self.first / 'data').mkdir()
        (self.first / 'data' / 'report.json').write_text('old report\n')
        self.git(self.first, 'add', 'data')
        self.git(self.first, 'commit', '-m', 'seed')
        self.git(self.first, 'push', 'origin', 'main')
        self.git(self.tmp, 'clone', str(self.remote), str(self.second))
        self.spool = self.tmp / 'spool'
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def git(self, repo, *args):
        return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', *args],
                              text=True, capture_output=True, check=True, timeout=10).stdout.strip()

    def prepare(self):
        (self.first / 'data' / 'report.json').write_text('new report\n')
        (self.first / 'data' / 'notify_log.jsonl').write_text('{"intent":"once"}\n')

    def test_push_failure_then_restart_on_clean_runner(self):
        self.prepare()
        attempts = []

        def reject(args, **kwargs):
            if 'push' in args:
                attempts.append(args[-1])
                return subprocess.CompletedProcess(args, 1, '', '')
            return subprocess.run(args, **kwargs)

        with self.assertRaises(PublicationError):
            Publisher(self.first, spool=self.spool, runner=reject).publish()
        manifest = json.loads((self.spool / 'pending.json').read_bytes())
        self.assertEqual((manifest['status'], len(attempts), len(set(attempts))), ('degraded', 2, 1))
        shutil.rmtree(self.first)
        saved = shutil.copytree(self.spool, self.tmp / 'second-artifact')
        Publisher(self.second, spool=self.spool).resume()
        Publisher(self.second, spool=saved).resume()
        self.assertEqual(self.git(self.remote, 'show', 'main:data/report.json'), 'new report')
        self.assertEqual(self.git(self.remote, 'show', 'main:data/notify_log.jsonl'), '{"intent":"once"}')
        self.assertEqual(self.git(self.remote, 'rev-list', '--count', 'main'), '2')

    def test_acknowledgement_lost_after_remote_acceptance(self):
        self.prepare()
        accepted = []

        def lose_ack(args, **kwargs):
            result = subprocess.run(args, **kwargs)
            if 'push' in args:
                accepted.append(result.returncode)
                return subprocess.CompletedProcess(args, 1, '', '')
            return result

        Publisher(self.first, spool=self.spool, runner=lose_ack).publish()
        self.assertEqual(accepted, [0])
        self.assertFalse((self.spool / 'pending.json').exists())
        self.assertEqual(self.git(self.remote, 'rev-list', '--count', 'main'), '2')

    def test_concurrent_nonoverlapping_writer_and_replayed_artifact(self):
        self.prepare()
        attempted = []

        def concurrent_update(args, **kwargs):
            if 'push' in args and not attempted:
                (self.second / 'data' / 'concurrent.json').write_text('other writer\n')
                self.git(self.second, 'add', 'data')
                self.git(self.second, 'commit', '-m', 'concurrent')
                self.git(self.second, 'push', 'origin', 'main')
                shutil.copytree(self.spool, self.tmp / 'old-artifact')
                attempted.append(True)
            return subprocess.run(args, **kwargs)

        Publisher(self.first, spool=self.spool, runner=concurrent_update).publish()
        Publisher(self.second, spool=self.tmp / 'old-artifact').resume()
        self.assertEqual(self.git(self.remote, 'show', 'main:data/concurrent.json'), 'other writer')
        self.assertEqual(self.git(self.remote, 'show', 'main:data/report.json'), 'new report')
        self.assertEqual(self.git(self.remote, 'rev-list', '--count', 'main'), '3')

    def test_conflicting_concurrent_writer_leaves_bundle_and_keeps_remote(self):
        self.prepare()
        (self.second / 'data' / 'report.json').write_text('other writer\n')
        self.git(self.second, 'add', 'data')
        self.git(self.second, 'commit', '-m', 'concurrent')
        self.git(self.second, 'push', 'origin', 'main')
        with self.assertRaises(PublicationError):
            Publisher(self.first, spool=self.spool).publish()
        self.assertTrue((self.spool / 'pending.bundle').exists())
        self.assertEqual((self.first / 'data' / 'report.json').read_text(), 'new report\n')
        self.assertEqual(self.git(self.remote, 'show', 'main:data/report.json'), 'other writer')
