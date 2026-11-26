"""V6 snapshot publication (scangoal.dashboard_snapshots): inactive by default, coherent, atomic, recoverable, concurrent-safe."""

import hashlib
import json
import multiprocessing
import re
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
# sha256 of .github/workflows/collect.yml on main at the base of PR #98 (2026-11-25)
MAIN_COLLECT_SHA256 = 'a725e5d28f3cdaf08137c1506b25aeb48f077f8e53895fded1996773533dea82'
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

    def test_coherent_stable_name_publication(self):
        self.assertEqual(D.build(self.root), 'published')
        m = _manifest(self.root)
        self.assertEqual(m['report_sha256'], _sha(self.root / 'docs/report.json'))
        for name, entry in m['files'].items():
            self.assertEqual(entry['path'], 'dashboard-%s.json' % name)
            self.assertEqual(_sha(self.root / D.OUT / entry['path']), entry['sha256'])
        self.assertEqual(sorted(p.name for p in (self.root / D.OUT).iterdir()), sorted(D.FILES + (D.MANIFEST,)))
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

    def test_successive_revisions_keep_seven_files_and_remove_legacy_layout(self):
        legacy = self.root / D.OUT / 'dashboard-summary.0123456789abcdef.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{}')
        (self.root / D.OUT / 'dashboard-signals.json.tmp').write_text('x')
        for step in range(3):
            report = json.loads((self.root / 'docs/report.json').read_text(encoding='utf-8'))
            report['messages'] += 1  # a new revision changes the summary snapshot
            (self.root / 'docs/report.json').write_text(json.dumps(report), encoding='utf-8')
            self.assertEqual(D.build(self.root), 'published')
        self.assertEqual(sorted(p.name for p in (self.root / D.OUT).iterdir()), sorted(D.FILES + (D.MANIFEST,)))

    def test_malformed_manifests_are_refused_not_crashing(self):
        D.build(self.root)
        good = _manifest(self.root)
        sha = good['report_sha256']
        bad = [None, [], {'schema': 'x'}, dict(good, files=None), dict(good, files={}), dict(good, files=dict(good['files'], extra={})),
               dict(good, files=dict(good['files'], summary={'path': 1})), dict(good, files=dict(good['files'], summary=dict(good['files']['summary'], sha256=7))),
               dict(good, files=dict(good['files'], summary=dict(good['files']['summary'], bytes='1'))),
               dict(good, files=dict(good['files'], summary=dict(good['files']['summary'], path='../../etc/passwd'))),
               dict(good, files=dict(good['files'], summary=dict(good['files']['summary'], path='dashboard-signals.json')))]
        for manifest in bad:
            with self.subTest(manifest=str(manifest)[:60]):
                with self.assertRaises(D.SnapshotError):
                    D.verify(manifest, self.root / D.OUT, sha)

        def runner(cmd, **kwargs):  # builder writes an unreadable manifest
            out = subprocess.run(cmd, **kwargs)
            (Path(cmd[cmd.index('--out') + 1]) / D.MANIFEST).write_text('{"files": [1,')
            return out
        with self.assertRaises(D.SnapshotError):
            D.build(self.root, runner=runner)
        with mock.patch.object(D, 'build', side_effect=TypeError('boom')), mock.patch.dict(os.environ, {D.ENV: 'on'}):
            self.assertEqual(D.main(), 3, 'any failure exits with 3, never a traceback')

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
            target = staging / 'dashboard-learning.json'
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
        allowed = ['docs/v6/snapshots/' + n for n in D.FILES + (D.MANIFEST,)]
        for changed, ok in ((allowed + ['docs/report.json', 'data/x.json'], True),
                            (['docs/v6/v6-app.js'], False), (['docs/index.html'], False), (['scangoal/report.py'], False),
                            (['docs/v6/snapshots-evil/x.json'], False), (['docs/v6/snapshots/dashboard-summary.json.tmp'], False),
                            (['docs/v6/snapshots/index.html'], False), (['docs/v6/snapshots/dashboard-summary.0123456789abcdef.json'], False),
                            (['docs/v6/snapshots/sub/dashboard-summary.json'], False), (['docs/v6/snapshots/dashboard-other.json'], False)):
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


class ProductionIsolationV6(unittest.TestCase):
    """Without the gated step, collect.yml is byte-identical to main; every other step behaves identically."""

    STEP = 'V6 dashboard snapshots (inactive by default)'

    def setUp(self):
        from test_polymarket_observer import strip_v6, V6_STEP, V6_ENV
        self.strip, self.V6_STEP, self.V6_ENV = strip_v6, V6_STEP, V6_ENV
        from test_git_persistence import simulate_job
        self.simulate = simulate_job
        self.current = (ROOT / '.github/workflows/collect.yml').read_text(encoding='utf-8')
        self.original = strip_v6(self.current)

    def test_only_the_gated_step_and_its_variable_were_added(self):
        self.assertNotEqual(self.current, self.original)
        self.assertNotIn('GOAL_ROI_V6_SNAPSHOTS', self.original)
        # main's collect.yml at the base of this PR (2026-11-25), unchanged except for the gated additions
        self.assertEqual(hashlib.sha256(self.original.encode()).hexdigest(), MAIN_COLLECT_SHA256)

    def test_simulated_runs_identical_with_step_on_off_or_failing(self):
        names = re.findall(r'^      - name: (.+)$', self.original, re.M)
        strip = lambda xs: [n for n in xs if n != self.STEP]
        for failing in [None, *names, self.STEP]:
            with self.subTest(failing=failing):
                reference = self.simulate(self.original, failing if failing in names else None)
                for v6 in (True, False):
                    state, ran, skipped = self.simulate(self.current, failing, v6=v6)
                    self.assertEqual((state, strip(ran), strip(skipped)), reference)
                    self.assertIn(self.STEP, ran if v6 else skipped)


    def test_every_mention_of_the_variable_is_inside_the_pinned_additions(self):
        self.assertEqual(self.current.count(self.V6_STEP), 1)
        self.assertEqual(self.current.count(self.V6_ENV), 2, 'the step env and the fast-reads env, nowhere else')
        self.assertNotIn('GOAL_ROI_V6_SNAPSHOTS', self.original)

    def test_guard_detects_edits_inside_the_step_or_the_variable_elsewhere(self):
        sha = lambda t: hashlib.sha256(t.encode()).hexdigest()
        mutations = {
            'secret added to the step env': self.current.replace(
                "          GOAL_ROI_V6_SNAPSHOTS: ${{ vars.GOAL_ROI_V6_SNAPSHOTS }}\n        run: python -u -m scangoal.dashboard_snapshots",
                "          GOAL_ROI_V6_SNAPSHOTS: ${{ vars.GOAL_ROI_V6_SNAPSHOTS }}\n          TG_BOT_TOKEN: ${{ secrets.TG_BOT_TOKEN }}\n        run: python -u -m scangoal.dashboard_snapshots"),
            'continue-on-error removed': self.current.replace(
                "vars.GOAL_ROI_V6_SNAPSHOTS == 'on' }}\n        continue-on-error: true\n", "vars.GOAL_ROI_V6_SNAPSHOTS == 'on' }}\n"),
            'extra command in the step': self.current.replace("run: python -u -m scangoal.dashboard_snapshots\n",
                                                               "run: python -u -m scangoal.dashboard_snapshots && git push\n"),
            'variable passed to another step': self.current.replace(
                "        run: timeout --signal=TERM --kill-after=10s 220s python -u -m scangoal.reader",
                self.V6_ENV + "        run: timeout --signal=TERM --kill-after=10s 220s python -u -m scangoal.reader"),
            'new step inside the region': self.current.replace(self.V6_STEP, self.V6_STEP + "      - name: Extra\n        run: echo x\n"),
        }
        for label, text in mutations.items():
            with self.subTest(mutation=label):
                self.assertTrue(text != self.current, 'mutation not applied: ' + label)
                self.assertNotEqual(sha(self.strip(text)), MAIN_COLLECT_SHA256, 'guard did not detect: ' + label)


class ReversalWithPendingPublications(unittest.TestCase):
    """Reverting the code is safe only after a run with the variable off and no pending publication. With the variable
    off nothing is written, so no pending bundle can carry snapshot paths; a deletion of the directory is publishable."""

    def test_variable_off_writes_nothing_and_a_pending_bundle_cannot_carry_snapshots(self):
        root = _sandbox()
        self.addCleanup(shutil.rmtree, root, True)
        self.assertEqual(D.publish_quietly(root, {}), 'inactive')
        self.assertFalse((root / D.OUT).exists())
        from scangoal import publication
        staged = [name for name in publication.PATHS if (root / name).exists()]
        self.assertNotIn('docs/v6/snapshots', staged)

    def test_deleting_the_snapshot_directory_is_publishable(self):
        publisher = Publisher.__new__(Publisher)
        deletions = ['docs/v6/snapshots/' + n for n in D.FILES + (D.MANIFEST,)]
        publisher.require = lambda *a: '\0'.join(deletions + ['docs/report.json'])
        publisher.check_paths('diff')  # no exception: the cleanup commit goes through the same publisher


if __name__ == '__main__':
    unittest.main()
