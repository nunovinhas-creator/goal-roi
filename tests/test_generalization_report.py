"""End-to-end reproduction of the sealed offline H evaluation."""

import importlib.util
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

AUDIT_PATH = Path(__file__).resolve().parents[1] / 'audits/autonomous-generalization/run.py'
SPEC = importlib.util.spec_from_file_location('autonomous_generalization_audit', AUDIT_PATH)
AUDIT = importlib.util.module_from_spec(SPEC)
with patch.object(sys, 'path', [str(AUDIT_PATH.parent), *sys.path]), patch.dict(sys.modules):
    SPEC.loader.exec_module(AUDIT)


class GeneralizationReport(unittest.TestCase):
    def test_sealed_report_reproduces_without_a_network(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('network unavailable')), \
                patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS unavailable')):
            actual = AUDIT.run(try_model=False)
        reference = AUDIT_PATH.with_name('report.json').read_bytes()
        runtime = f'{sys.implementation.name}-{sys.version_info.major}.{sys.version_info.minor}'
        recorded = json.loads(AUDIT_PATH.with_name('runtime-counters.json').read_bytes()).get(runtime, {})
        if recorded:
            expected = json.loads(reference)
            for world, seeds in recorded.items():
                for fold in expected['worlds'][world]['development']:
                    entry = seeds.get(str(fold['development_seed']))
                    if entry is not None:
                        work = fold['successor_work']
                        work['stages'].update(entry['stages'])
                        work['used'] = entry['used']
            self.assertEqual(actual, expected)
        else:
            encoded = json.dumps(actual, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'
            self.assertEqual(encoded.encode(), reference)
        self.assertTrue(actual['previous_regression']['reproduced'])
        self.assertEqual(actual['decision']['synthetic_transfer'], 'NO-GO')
        self.assertFalse(actual['confirmatory'])
        for world in actual['worlds'].values():
            for fold in world['development']:
                work = fold['successor_work']
                self.assertEqual(sum(work['stages'].values()), work['used'])
                self.assertLessEqual(work['used'], work['limit'])
                self.assertTrue(fold['generation_invariant'])
                self.assertTrue(all(seed['noise_invariant'] for seed in fold['successor']['holdout']))
