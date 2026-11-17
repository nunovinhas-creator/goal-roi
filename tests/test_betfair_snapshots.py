import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scangoal.betfair_audit import REQUIRED_INPUTS

CODE = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('git'), 'Git is required for snapshot tests')
class BetfairSnapshots(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.git('init', '-q')
        for name in REQUIRED_INPUTS:
            (self.data / name).write_text('' if name.endswith('.jsonl') else '{}')
        self.previous = self.commit('reference')
        self.assertEqual(self.audit(self.previous, 'reference').returncode, 0)
        (self.data / 'results_hl.json').write_text('{"results": {}}')
        (self.data / 'manual_results.json').write_text('{}')
        self.current = self.commit('current')
        self.assertEqual(self.audit(self.current, 'current').returncode, 0)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True).strip()

    def commit(self, message):
        self.git('add', 'data')
        self.git('-c', 'user.email=test@example.com', '-c', 'user.name=Test', 'commit', '-qm', message)
        return self.git('rev-parse', 'HEAD')

    def cli(self, module, *args):
        return subprocess.run(
            [sys.executable, '-m', 'scangoal.' + module, *args], cwd=self.root,
            env={**os.environ, 'PYTHONPATH': str(CODE), 'SCANGOAL_DATA_DIR': str(self.data)},
            capture_output=True, text=True, timeout=15,
        )

    def audit(self, commit, output):
        return self.cli('betfair_audit', '--snapshot-sha', commit, '--output-dir', output,
                        '--as-of', '2026-11-17T18:32:28Z')

    def reconcile(self, output):
        return self.cli('betfair_reconcile', '--reference', 'reference/audit.json',
                        '--current', 'current/audit.json', '--output', output)

    def test_missing_highlightly_and_optional_files_rejected_before_output(self):
        for name in ('results_hl.json', 'manual_results.json'):
            with self.subTest(name=name):
                path = self.data / name
                saved = path.read_bytes()
                path.unlink()
                run = self.audit(self.current, 'incomplete')
                self.assertNotEqual(run.returncode, 0)
                self.assertIn(name, run.stderr)
                self.assertFalse((self.root / 'incomplete').exists())
                path.write_bytes(saved)

    def test_untracked_optional_and_replaced_input_rejected(self):
        run = self.audit(self.previous, 'mixed')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('results_hl.json', run.stderr)
        (self.data / 'results_hl.json').write_text('{}')
        run = self.audit(self.previous, 'mixed')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('manual_results.json', run.stderr)

    def test_current_inventory_cannot_reconcile_using_reference_inputs(self):
        run = self.reconcile('valid.json')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.git('checkout', self.previous, '--', 'data/results_hl.json')
        (self.data / 'manual_results.json').unlink()
        run = self.reconcile('mixed.json')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('results_hl.json', run.stderr)
        self.assertFalse((self.root / 'mixed.json').exists())

    def test_forged_inventory_rejected_even_with_correct_manifest(self):
        path = self.root / 'current' / 'audit.json'
        audit = json.loads(path.read_text())
        audit['inventory'].append({'bet_id': 'forged', 'match_id': 'g', 'state': 'settled'})
        path.write_text(json.dumps(audit))
        run = self.reconcile('forged.json')
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('factual rows', run.stderr)
        self.assertFalse((self.root / 'forged.json').exists())
