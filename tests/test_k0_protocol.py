"""K0: frozen protocol, isolation boundary of the bank kit, beacon schedule, commitments and scoring rules."""

import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
K0 = ROOT / 'audits/independent-k0'


def _load(name):
    spec = importlib.util.spec_from_file_location(f'goal_roi_k0_{name}', K0 / 'tools' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


beacon, commitment, guard = _load('beacon'), _load('commitment'), _load('guard')
PROTOCOL = json.loads((K0 / 'protocol.json').read_bytes())


class Protocol(unittest.TestCase):
    def test_criteria_and_matrix_are_the_registered_ones(self):
        self.assertEqual(PROTOCOL['thresholds']['precision'], 0.9)
        self.assertEqual(PROTOCOL['thresholds']['terminal_recall'], 0.8)
        self.assertEqual(PROTOCOL['thresholds']['exact_path_recall'], 0.75)
        m = PROTOCOL['matrix']
        self.assertEqual([f['phase'] for f in m['families']], ['pre'] * 4 + ['live'] * 4)
        self.assertEqual((m['mining_seeds'], m['validation_seeds'], m['evaluations_per_engine']), (3, 3, 72))
        self.assertFalse(PROTOCOL['independence']['declared'])
        self.assertFalse(PROTOCOL['beacon']['parameters_verified'])

    def test_j_lock_is_untouched(self):
        lock = json.loads((ROOT / 'audits/selective-abstention/freeze.json').read_bytes())
        for name, expected in lock['algorithm_sha256'].items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected)


class BankKitBoundary(unittest.TestCase):
    def test_kit_uses_only_the_standard_library_and_no_project_code(self):
        tree = ast.parse((K0 / 'bank_author_kit/template.py').read_text())
        imported = {alias.name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                    for alias in node.names}
        imported |= {node.module.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertLessEqual(imported, {'datetime', 'random'})
        text = ''.join(p.read_text() for p in sorted((K0 / 'bank_author_kit').iterdir()) if p.is_file())
        for word in ('experimental_', 'selector', 'consensus', 'refine', 'miner'):
            self.assertNotIn(word, text)

    def test_template_refuses_cells_outside_the_matrix(self):
        spec = importlib.util.spec_from_file_location('k0_template', K0 / 'bank_author_kit/template.py')
        template = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(template)
        with self.assertRaises(ValueError):
            template.build('F9', 'mining', 1, 1)
        with self.assertRaises(NotImplementedError):
            template.build('F1', 'mining', 1, 1)


class Beacon(unittest.TestCase):
    def test_primary_round_time_and_fixed_fallbacks(self):
        b = PROTOCOL['beacon']
        self.assertEqual(beacon.round_time(b, b['primary_round']).isoformat(), '2027-02-01T12:00:00+00:00')
        rounds = [n for _, n in beacon.candidates(b)]
        self.assertEqual(len(rounds), b['max_fallbacks'] + 1)
        self.assertEqual(len({n - rounds[0] for n in rounds[1:]} - {k * 864000 for k in range(1, 7)}), 0)

    def test_round_choice_depends_only_on_commitment_time_and_availability(self):
        b = PROTOCOL['beacon']
        early = datetime(2026, 12, 1, tzinfo=timezone.utc)
        self.assertEqual(beacon.eligible_round(b, early), (0, b['primary_round']))
        late = datetime(2027, 1, 28, tzinfo=timezone.utc)  # inside the 7-day margin
        self.assertEqual(beacon.eligible_round(b, late)[0], 1)
        self.assertEqual(beacon.eligible_round(b, early, unavailable={b['primary_round']})[0], 1)
        with self.assertRaises(RuntimeError):
            beacon.eligible_round(b, datetime(2030, 1, 1, tzinfo=timezone.utc))

    def test_seeds_are_deterministic_bound_to_protocol_and_cover_the_matrix(self):
        r = hashlib.sha256(b'example').hexdigest()
        a = beacon.derive_seeds(r, 'p' * 64, PROTOCOL['matrix'])
        self.assertEqual(a, beacon.derive_seeds(r, 'p' * 64, PROTOCOL['matrix']))
        self.assertNotEqual(a, beacon.derive_seeds(r, 'q' * 64, PROTOCOL['matrix']))
        self.assertEqual(len(a), 48)
        self.assertEqual(len(set(a.values())), 48)

    def test_randomness_must_be_the_hash_of_the_signature(self):
        sig = 'ab' * 48
        good = dict(round=7, signature=sig, randomness=hashlib.sha256(bytes.fromhex(sig)).hexdigest())
        self.assertTrue(beacon.check_randomness(good))
        b = dict(PROTOCOL['beacon'], relay_quorum=2)
        self.assertIsNone(beacon.agreed(b, {'r1': good}, 7))
        self.assertEqual(beacon.agreed(b, {'r1': good, 'r2': dict(good)}, 7), good)
        bad = dict(good, randomness='00' * 32)
        self.assertIsNone(beacon.agreed(b, {'r1': good, 'r2': bad}, 7))


VERIFICATION = json.loads((K0 / 'beacon-verification.json').read_bytes())


class BeaconVerification(unittest.TestCase):
    def test_relays_agree_and_match_the_locked_protocol(self):
        self.assertTrue(VERIFICATION['relays_agree'])
        self.assertEqual(set(VERIFICATION['matches_protocol'].values()), {True})
        self.assertEqual(VERIFICATION['info']['hash'], PROTOCOL['beacon']['chain_hash'])
        self.assertEqual(VERIFICATION['info']['schemeID'], 'bls-unchained-g1-rfc9380')

    def test_fetch_sends_an_explicit_user_agent(self):
        from unittest.mock import patch
        seen = []

        def fake(request, timeout):
            seen.append(request.get_header('User-agent'))
            raise OSError('offline test')

        with patch.object(beacon.urllib.request, 'urlopen', side_effect=fake):
            out = beacon.fetch(PROTOCOL['beacon'], 1)
        self.assertEqual(set(seen), {beacon.USER_AGENT})
        self.assertTrue(all('error' in e for e in out.values()))


@unittest.skipUnless(importlib.util.find_spec('py_ecc'), 'requirements-verification.txt not installed')
class BLS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bls = _load('bls')
        cls.info = VERIFICATION['info']

    def test_relay_vectors_verify(self):
        for v in VERIFICATION['test_vectors']:
            self.assertTrue(self.bls.verify_entry(self.info, v), v['round'])

    def test_tampering_is_rejected(self):
        v = VERIFICATION['test_vectors'][1]
        key = self.info['public_key']
        self.assertFalse(self.bls.verify(key, v['round'] + 1, v['signature']))
        flipped = format(int(v['signature'], 16) ^ (1 << 7), '096x')
        self.assertFalse(self.bls.verify(key, v['round'], flipped))
        other = VERIFICATION['test_vectors'][0]['signature']
        self.assertFalse(self.bls.verify(key, v['round'], other))
        self.assertFalse(self.bls.verify(key[:-2] + format(int(key[-2:], 16) ^ 1, '02x'), v['round'], v['signature']))
        self.assertFalse(self.bls.verify_entry(dict(self.info, schemeID='pedersen-bls-chained'), v))
        self.assertFalse(self.bls.verify_entry(self.info, dict(v, randomness='00' * 32)))


ISOLATION = K0 / 'isolation'


class IsolationB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('goal_roi_k0_run_b', ISOLATION / 'run_b.py')
        cls.run_b = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.run_b)

    def test_recorded_run_passed_technically_but_is_not_independent(self):
        verdict = json.loads((ISOLATION / 'runs/verdict.json').read_bytes())
        self.assertEqual(verdict['technical_isolation'], 'PASS')
        self.assertEqual(verdict['scientific_independence'], 'NOT DEMONSTRATED')
        sandbox = json.loads((ISOLATION / 'runs/sandbox.json').read_bytes())
        body = {k: v for k, v in sandbox.items() if k != 'record_sha256'}
        self.assertEqual(hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(), sandbox['record_sha256'])
        self.assertEqual(sandbox['probe']['sensitive_environment'], 0)
        self.assertEqual(sandbox['probe']['root_entries'], ['bin', 'kit', 'lib', 'lib64', 'out', 'usr'])

    def test_verdict_fails_on_any_leak(self):
        good = json.loads((ISOLATION / 'runs/sandbox.json').read_bytes())
        control = json.loads((ISOLATION / 'runs/control.json').read_bytes())
        self.assertEqual(self.run_b.verdict(good, control)['technical_isolation'], 'PASS')
        for field, value in (('sensitive_environment', 1), ('git_directories', ['/x/.git']),
                             ('dns_github', dict(ok=True)), ('write_kit', dict(ok=True))):
            leaky = dict(good, probe=dict(good['probe'], **{field: value}))
            self.assertEqual(self.run_b.verdict(leaky, control)['technical_isolation'], 'FAIL', field)
        self.assertEqual(self.run_b.verdict(good, dict(control, probe=None))['technical_isolation'], 'FAIL')

    @unittest.skipUnless(hasattr(os, 'geteuid') and os.geteuid() == 0 and shutil.which('unshare'),
                         'needs root and util-linux unshare')
    def test_live_sandbox_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = self.run_b.run(Path(tmp) / 'sandbox.json', sandbox=True)
        probe = sandbox['probe']
        self.assertEqual(sandbox['exit_code'], 0)
        self.assertFalse(any(probe['repo_paths_visible'].values()))
        self.assertFalse(probe['dns_github']['ok'])
        self.assertFalse(probe['tcp_loopback_proxy']['ok'])
        self.assertTrue(probe['write_out']['ok'])
        self.assertEqual(probe['template'], 'refused_no_mechanism')


class Commitment(unittest.TestCase):
    def test_commitment_binds_content_salt_role_and_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'gen.py').write_text('x = 1\n')
            salt = '11' * 32
            c = commitment.commit(root, salt, 'p' * 64, 'bank_author')['commitment']
            self.assertTrue(commitment.verify(root, salt, 'p' * 64, 'bank_author', c))
            self.assertFalse(commitment.verify(root, '22' * 32, 'p' * 64, 'bank_author', c))
            self.assertFalse(commitment.verify(root, salt, 'q' * 64, 'bank_author', c))
            (root / 'gen.py').write_text('x = 2\n')
            self.assertFalse(commitment.verify(root, salt, 'p' * 64, 'bank_author', c))
            with self.assertRaises(ValueError):
                commitment.commit(root, '11' * 8, 'p' * 64, 'bank_author')


class Scoring(unittest.TestCase):
    T = PROTOCOL['thresholds']

    def test_abstention_zero_detections_and_ineligibility_never_pass(self):
        base = dict(detections=10, exact_terminals=10, exact_paths=10, targets=10)
        self.assertTrue(guard.gate(base, self.T))
        self.assertFalse(guard.gate(dict(base, abstained=True), self.T))
        self.assertFalse(guard.gate(dict(base, ineligible=True), self.T))
        self.assertFalse(guard.gate(dict(base, detections=0, exact_terminals=0, exact_paths=0), self.T))
        self.assertFalse(guard.gate(dict(base, detections=12), self.T))  # 10/12 < 90%

    def test_missing_cells_fail_and_extra_cells_are_refused(self):
        matrix = PROTOCOL['matrix']
        cell = dict(detections=10, exact_terminals=10, exact_paths=10, targets=10)
        full = [dict(cell, family=f['slot'], mining=m, validation=v) for f in matrix['families']
                for m in (1, 2, 3) for v in (1, 2, 3)]
        self.assertEqual(guard.decide(full, matrix, self.T), 'GO')
        self.assertEqual(guard.decide(full[1:], matrix, self.T), 'NO-GO')
        with self.assertRaises(guard.Refused):
            guard.decide(full + [dict(cell, family='F9', mining=1, validation=1)], matrix, self.T)

    def test_only_one_first_run_and_reproduction_must_be_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(guard.record(b'a', 'first', runs=tmp), 'recorded')
            with self.assertRaises(guard.Refused):
                guard.record(b'b', 'first', runs=tmp)
            self.assertEqual(guard.record(b'a', 'reproduction', runs=tmp), 'reproduced')
            self.assertEqual(guard.record(b'b', 'reproduction', runs=tmp), 'invalid_reproduction_mismatch')

    def test_changed_protocol_is_refused(self):
        with self.assertRaises(guard.Refused):
            guard.start('0' * 64)


if __name__ == '__main__':
    unittest.main()
