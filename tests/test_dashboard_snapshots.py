"""V6 snapshot publication (scangoal.dashboard_snapshots): inactive by default, coherent, atomic, recoverable, concurrent-safe."""

import hashlib
import json
import multiprocessing
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scangoal import dashboard_snapshots as D
from scangoal import prospective as P
from scangoal.publication import Publisher, PublicationError, PATHS

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sandbox():
    """Copy of the inputs the builder reads, with the real V6 core and builder."""
    tmp = Path(tempfile.mkdtemp(prefix='v6-snap-test-'))
    for rel in ('docs/report.json', 'data/prospective_report.json', 'data/prospective_ledger.jsonl', 'scangoal/prospective_protocol.json',
                'scripts/build_dashboard_snapshots.cjs', 'docs/v6/v6-core.js', 'docs/v6/v6-markets.js'):
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, tmp / rel)
    return tmp


def _manifest(root):
    return json.loads((root / D.OUT / D.MANIFEST).read_text(encoding='utf-8'))


def _concurrent_build(root):
    return D.build(root)


@unittest.skipUnless(NODE, 'node indisponível')
class DashboardSnapshots(unittest.TestCase):
    def setUp(self):
        self.root = _sandbox()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_inactive_by_default_writes_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(D.ENV, None)
            self.assertEqual(D.publish_quietly(self.root), 'inactive')
            self.assertEqual(D.main(), 0)
        self.assertFalse((self.root / D.OUT).exists())
        self.assertEqual(D.publish_quietly(self.root, {D.ENV: 'off'}), 'inactive')
        self.assertEqual(D.publish_quietly(self.root, {D.ENV: 'ON '}), 'inactive')

    def test_coherent_content_addressed_publication(self):
        self.assertEqual(D.build(self.root), 'published')
        m = _manifest(self.root)
        self.assertEqual(m['report_sha256'], _sha(self.root / 'docs/report.json'))
        for name, entry in m['files'].items():
            path = self.root / D.OUT / entry['path']
            self.assertRegex(entry['path'], r'^dashboard-%s\.[0-9a-f]{16}\.json$' % name)
            self.assertEqual(_sha(path), entry['sha256'])
        self.assertEqual(D.build(self.root), 'unchanged')

    def test_protected_inputs_untouched(self):
        protected = ('docs/report.json', 'data/prospective_report.json', 'data/prospective_ledger.jsonl', 'scangoal/prospective_protocol.json')
        before = {p: _sha(self.root / p) for p in protected}
        D.build(self.root)
        self.assertEqual({p: _sha(self.root / p) for p in protected}, before)
        # the production modules hashed by the frozen prospective protocol do not include the publication path
        self.assertNotIn('dashboard_snapshots.py', P.CODE_V2)
        self.assertNotIn('publication.py', P.CODE_V2)
        self.assertNotIn('watch.py', P.CODE_V2)

    def test_previous_generation_kept_older_pruned(self):
        manifests = []
        for step in range(3):
            report = json.loads((self.root / 'docs/report.json').read_text(encoding='utf-8'))
            report['messages'] += 1  # a new revision changes the summary snapshot
            (self.root / 'docs/report.json').write_text(json.dumps(report), encoding='utf-8')
            self.assertEqual(D.build(self.root), 'published')
            manifests.append(_manifest(self.root))
        names = {p.name for p in (self.root / D.OUT).iterdir()}
        summaries = [m['files']['summary']['path'] for m in manifests]
        self.assertEqual(len(set(summaries)), 3)
        self.assertNotIn(summaries[0], names, 'the generation before the previous one is pruned')
        self.assertIn(summaries[1], names, 'the previous generation stays for cached manifests')
        self.assertIn(summaries[2], names)

    def test_failure_leaves_previous_manifest(self):
        D.build(self.root)
        before = (self.root / D.OUT / D.MANIFEST).read_bytes()
        files_before = sorted(p.name for p in (self.root / D.OUT).iterdir())
        # broken report: the builder refuses it
        (self.root / 'docs/report.json').write_text('{"generated_at_utc": "x"}', encoding='utf-8')
        with self.assertRaises(D.SnapshotError):
            D.build(self.root)
        self.assertEqual((self.root / D.OUT / D.MANIFEST).read_bytes(), before)
        self.assertEqual(sorted(p.name for p in (self.root / D.OUT).iterdir()), files_before)
        self.assertEqual(D.publish_quietly(self.root, {D.ENV: 'on'}), 'failed')

    def test_report_changed_during_build_is_discarded(self):
        D.build(self.root)
        before = (self.root / D.OUT / D.MANIFEST).read_bytes()
        real = subprocess.run

        def runner(*args, **kwargs):
            out = real(*args, **kwargs)
            report = self.root / 'docs/report.json'
            report.write_bytes(report.read_bytes().replace(b'"messages": ', b'"messages":  '))  # concurrent writer
            return out
        with self.assertRaisesRegex(D.SnapshotError, 'changed while'):
            D.build(self.root, runner=runner)
        self.assertEqual((self.root / D.OUT / D.MANIFEST).read_bytes(), before)

    def test_tampered_builder_output_is_refused(self):
        real = subprocess.run

        def runner(cmd, **kwargs):
            out = real(cmd, **kwargs)
            staging = Path(cmd[cmd.index('--out') + 1])
            target = next(staging.glob('dashboard-learning.*.json'))
            target.write_text('{}', encoding='utf-8')
            return out
        with self.assertRaisesRegex(D.SnapshotError, 'does not match'):
            D.build(self.root, runner=runner)
        self.assertFalse((self.root / D.OUT / D.MANIFEST).exists())

    def test_builder_timeout_and_missing_node(self):
        def slow(*a, **k):
            raise subprocess.TimeoutExpired('node', 1)
        with self.assertRaises(subprocess.TimeoutExpired):
            D.build(self.root, runner=slow)
        with mock.patch.object(D.shutil, 'which', return_value=None):
            self.assertEqual(D.build(self.root), 'skipped_no_node')
        self.assertFalse((self.root / D.OUT / D.MANIFEST).exists())

    def test_concurrent_builds_serialise(self):
        ctx = multiprocessing.get_context('fork')
        with ctx.Pool(4) as pool:
            results = pool.map(_concurrent_build, [self.root] * 4)
        self.assertIn('published', results)
        self.assertTrue(set(results) <= {'published', 'unchanged'})
        m = _manifest(self.root)
        for entry in m['files'].values():
            self.assertEqual(_sha(self.root / D.OUT / entry['path']), entry['sha256'])
        self.assertFalse(list((self.root / D.OUT).glob('*.tmp')))


class PublicationPaths(unittest.TestCase):
    def test_snapshot_paths_allowed_other_docs_refused(self):
        self.assertIn('docs/v6/snapshots', PATHS)
        publisher = Publisher.__new__(Publisher)
        for changed, ok in ((['docs/v6/snapshots/dashboard-manifest.json', 'docs/report.json', 'data/x.json'], True),
                            (['docs/v6/v6-app.js'], False), (['docs/index.html'], False), (['scangoal/report.py'], False),
                            (['docs/v6/snapshots-evil/x.json'], False)):
            publisher.require = lambda *a, c=changed: '\0'.join(c)
            if ok:
                publisher.check_paths('diff')
            else:
                with self.assertRaises(PublicationError):
                    publisher.check_paths('diff')

    def test_workflow_step_is_inactive_by_default(self):
        text = (ROOT / '.github/workflows/collect.yml').read_text(encoding='utf-8')
        step = text[text.index('- name: V6 dashboard snapshots'):]
        step = step[:step.index('\n      - ', 10)]
        self.assertIn("vars.GOAL_ROI_V6_SNAPSHOTS == 'on'", step)
        self.assertIn('continue-on-error: true', step)
        order = [text.index(s) for s in ('- name: Report (fixed rules)', '- name: Prospective ledger', '- name: V6 dashboard snapshots',
                                         '- name: Publish the report before sending notices')]
        self.assertEqual(order, sorted(order), 'snapshots must follow the report and the ledger and precede the publication')


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(NODE, 'node indisponível')
class GitPublication(unittest.TestCase):
    """Report and snapshots of one revision reach `main` in the same commit, including after a concurrent writer."""

    def setUp(self):
        import contextlib
        import io
        self.tmp = Path(tempfile.mkdtemp(prefix='v6-pub-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.remote, self.first, self.second = self.tmp / 'origin.git', self.tmp / 'first', self.tmp / 'second'
        self.git(self.tmp, 'init', '--bare', '--initial-branch=main', str(self.remote))
        seed = _sandbox()
        self.addCleanup(shutil.rmtree, seed, True)
        shutil.copytree(seed, self.first)
        self.git(self.first, 'init', '--initial-branch=main')
        self.git(self.first, 'add', '.')
        self.git(self.first, 'commit', '-q', '-m', 'seed')
        self.git(self.first, 'remote', 'add', 'origin', str(self.remote))
        self.git(self.first, 'push', '-q', '-u', 'origin', 'main')
        self.git(self.tmp, 'clone', '-q', str(self.remote), str(self.second))

    def git(self, repo, *args):
        return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', *args],
                              text=True, capture_output=True, check=True, timeout=30).stdout.strip()

    def new_revision(self, repo, bump):
        report = json.loads((repo / 'docs/report.json').read_text(encoding='utf-8'))
        report['messages'] += bump
        (repo / 'docs/report.json').write_text(json.dumps(report), encoding='utf-8')

    def check_tip(self):
        manifest = json.loads(self.git(self.remote, 'show', 'main:docs/v6/snapshots/dashboard-manifest.json'))
        report = subprocess.run(['git', '-C', str(self.remote), 'show', 'main:docs/report.json'], capture_output=True, check=True).stdout
        self.assertEqual(manifest['report_sha256'], hashlib.sha256(report).hexdigest())
        for entry in manifest['files'].values():
            body = subprocess.run(['git', '-C', str(self.remote), 'show', 'main:docs/v6/snapshots/' + entry['path']], capture_output=True, check=True).stdout
            self.assertEqual(hashlib.sha256(body).hexdigest(), entry['sha256'])
        return manifest

    def test_same_commit_and_concurrent_writer(self):
        self.new_revision(self.first, 1)
        self.assertEqual(D.build(self.first), 'published')
        Publisher(self.first, spool=self.tmp / 'spool1').publish()
        changed = self.git(self.remote, 'show', '--name-only', '--format=', 'main').split('\n')
        self.assertIn('docs/report.json', changed)
        self.assertIn('docs/v6/snapshots/dashboard-manifest.json', changed)
        self.check_tip()
        # a concurrent writer publishes data only; the first runner then publishes a new revision on top
        self.git(self.second, 'pull', '-q')
        (self.second / 'data' / 'notify_log.jsonl').write_text('{"intent":"x"}\n')
        Publisher(self.second, spool=self.tmp / 'spool2').publish()
        self.new_revision(self.first, 2)
        self.assertEqual(D.build(self.first), 'published')
        Publisher(self.first, spool=self.tmp / 'spool1').publish()
        manifest = self.check_tip()
        tip_report = json.loads(self.git(self.remote, 'show', 'main:docs/report.json'))
        self.assertEqual(tip_report['messages'], json.loads((self.first / 'docs/report.json').read_text())['messages'])
        self.assertEqual(self.git(self.remote, 'show', 'main:data/notify_log.jsonl'), '{"intent":"x"}', 'concurrent data kept')
        self.assertTrue(manifest['files'])
