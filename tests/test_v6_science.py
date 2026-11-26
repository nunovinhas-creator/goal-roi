"""Goal-ROI Intelligence V6: integridade científica da derivação e regressões do dashboard.

Corre os testes do núcleo (node --test) e do navegador (Playwright), e confirma em Python, de forma independente,
que os snapshots copiam o relatório prospetivo sem alterações e que os critérios congelados usados pela interface
coincidem com scangoal/prospective.py. Nada aqui escreve em data/ nem em docs/report.json."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scangoal import prospective as P

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = ('data/report.json', 'docs/report.json', 'data/prospective_report.json', 'data/prospective_ledger.jsonl',
             'scangoal/prospective_protocol.json')


def _node():
    return shutil.which('node') is not None


def _playwright():
    available = _node() and subprocess.run(['node', '-e', "require('playwright')"], cwd=ROOT, capture_output=True).returncode == 0
    # Na CI o Playwright é obrigatório: a sua falta é um erro, nunca um teste ignorado em silêncio.
    if not available and os.environ.get('CI'):
        raise RuntimeError('Playwright em falta na CI: os testes de navegador da V6 não podem ser ignorados')
    return available


def _digest():
    return {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in PROTECTED if (ROOT / p).exists()}


@unittest.skipUnless(_node(), 'node indisponível')
class V6Science(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = _digest()
        cls.out = Path(tempfile.mkdtemp(prefix='v6-science-'))
        r = subprocess.run(['node', 'scripts/build_dashboard_snapshots.cjs', '--out', str(cls.out)], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr
        cls.manifest = json.loads((cls.out / 'dashboard-manifest.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def snap(self, name):
        return json.loads((self.out / f'dashboard-{name}.json').read_text(encoding='utf-8'))

    def test_protected_inputs_unchanged(self):
        self.assertEqual(_digest(), self.before)
        self.assertEqual(self.manifest['report_sha256'], hashlib.sha256((ROOT / 'data/report.json').read_bytes()).hexdigest())

    def test_prospective_copied_verbatim(self):
        published = json.loads((ROOT / 'data/prospective_report.json').read_text(encoding='utf-8'))
        science = self.snap('science')['data']
        for key in ('H1', 'H2', 'D1', 'verdict', 'ledger_entries', 'games_registered', 'signals_registered',
                    'signals_registered_before_result', 'signals_result_known_at_registration',
                    'games_with_independent_result', 'games_with_conflicting_results', 'games_without_result_after_72h'):
            self.assertEqual(science['prospective'][key], published[key], key)
        self.assertTrue(science['validation']['ok'], science['validation'])
        self.assertIn('NÃO DEMONSTRADA', science['verdict'])

    def test_frozen_criteria_match_protocol_code(self):
        frozen = self.snap('science')['data']['frozen']
        self.assertEqual(frozen['min_games'], P.MIN_GAMES)
        self.assertEqual(frozen['z_crit'], P.Z_CRIT)
        self.assertEqual(frozen['min_band'], P.MIN_BAND)
        self.assertEqual(frozen['missing_after_hours'], P.MISSING_AFTER.total_seconds() / 3600)

    def test_no_strategy_presented_as_validated(self):
        report = json.loads((ROOT / 'data/report.json').read_text(encoding='utf-8'))
        summary = self.snap('summary')['data']
        cert = report['brain'].get('validation', {})
        if cert.get('state') != 'active' or not cert.get('ledger_valid'):
            self.assertEqual(summary['certification']['validated'], 0)
        self.assertTrue(all(item['validated'] is False for item in self.snap('signals')['data']['items']))

    def test_reference_numbers_equal_report(self):
        report = json.loads((ROOT / 'data/report.json').read_text(encoding='utf-8'))
        ref = report['questions']['Q7 Todas as apostas (referência)']
        perf = self.snap('performance')['data']
        self.assertEqual(perf['reference'], ref)
        self.assertEqual(perf['groups'], report['groups'])
        by_source = sum(row['units'] for row in perf['periods']['by_source'].values())
        self.assertAlmostEqual(by_source, ref['units'], delta=0.05)

    def test_no_private_fields(self):
        text = ''.join((self.out / f).read_text(encoding='utf-8') for f in (m['path'] for m in self.manifest['files'].values()))
        for forbidden in ('unparsed_samples', '"chat_id"', '"api_key"', '"token"', '"session"', '"text"'):
            self.assertNotIn(forbidden, text)

    def test_core_unit_tests(self):
        r = subprocess.run(['node', '--test', 'tests/v6_core.test.cjs'], cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-2000:])

    def test_market_matrix(self):
        r = subprocess.run(['node', '--test', 'tests/v6_markets.test.cjs'], cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-2000:])

    @unittest.skipUnless(_playwright(), 'playwright indisponível')
    def test_long_open_tab(self):
        r = subprocess.run(['node', 'tests/v6_longrun.cjs'], cwd=ROOT, capture_output=True, text=True, timeout=1200)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-2000:])

    @unittest.skipUnless(_playwright(), 'playwright indisponível')
    def test_browser_regressions(self):
        r = subprocess.run(['node', 'tests/v6_dashboard.cjs'], cwd=ROOT, capture_output=True, text=True, timeout=900)
        self.assertEqual(r.returncode, 0, r.stdout[-6000:] + r.stderr[-2000:])


if __name__ == '__main__':
    unittest.main()
