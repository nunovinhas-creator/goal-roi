"""Replay the sealed independent experiment and verify its assessment boundaries."""

from collections import Counter
import importlib.util
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

AUDIT_PATH = Path(__file__).resolve().parents[1] / 'audits/robust-generalization/evaluate.py'
SPEC = importlib.util.spec_from_file_location('goal_roi_i_transfer_evaluator', AUDIT_PATH)
AUDIT = importlib.util.module_from_spec(SPEC)
with patch.object(sys, 'path', [str(AUDIT_PATH.parent), *sys.path]), patch.dict(sys.modules):
    SPEC.loader.exec_module(AUDIT)


class RobustGeneralizationReport(unittest.TestCase):
    def test_report_and_all_frozen_work_counters_reproduce_offline(self):
        runtime_calls = []
        prepare, build = AUDIT._prepare, AUDIT.bank_i.build

        def prepared(*args):
            result = prepare(*args)
            runtime_calls.append('proposals_chosen')
            return result

        def isolated(index, seed, *, fresh):
            if fresh:
                self.assertIn('proposals_chosen', runtime_calls)
            return build(index, seed, fresh=fresh)

        with patch.object(socket, 'socket', side_effect=AssertionError('network unavailable')), \
                patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS unavailable')), \
                patch.object(AUDIT, '_prepare', side_effect=prepared), \
                patch.object(AUDIT.bank_i, 'build', side_effect=isolated):
            report = AUDIT.run()
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'
        self.assertEqual(encoded.encode(), AUDIT_PATH.with_name('report.json').read_bytes())
        self.assertFalse(report['confirmatory'])
        self.assertTrue(report['known_guardrail_passed'])
        self.assertEqual(report['decision']['independent_transfer'], 'NO-GO')
        for scenario in report['scenarios'].values():
            self.assertFalse(scenario['eligibility'])
            for fold in scenario['development']:
                self.assertTrue(fold['stable_rank'])
                self.assertTrue(all(trial['chosen_witness_invariant'] for trial in fold['i']['transfer']))

    def test_unobservable_labelled_terminals_are_a_bank_defect(self):
        lock, _ = AUDIT.verified_lock()
        index = next(i for i, item in enumerate(AUDIT.bank_i.SCENARIOS) if item[0] == 'live_score_confirmation')
        for seed in lock['design']['validation_seeds']:
            bank = AUDIT.bank_i.build(index, seed, fresh=True)
            available = AUDIT.Evaluator(bank, lock).availability()
            self.assertEqual(available['eligible_targets'], 0)
            self.assertEqual(available['targets'], 10)
            self.assertEqual(available['unavailable_target_reasons'], {'line_already_decided_by_score': 10})

    def test_rejection_is_separate_from_indetermination(self):
        reference = json.loads(AUDIT_PATH.with_name('report.json').read_bytes())
        all_states = Counter()
        world = reference['scenarios']['pre_range_to_point']
        for development in world['development']:
            for trial in development['i']['transfer']:
                for mechanism, states in trial['selected']['counterexamples'].items():
                    all_states.update(states)
                    if mechanism == 'late_range':
                        self.assertGreaterEqual(states.get('indeterminate', 0), 1)
        self.assertEqual(sum(all_states.values()), 108)
        self.assertGreater(all_states['absent'], 0)
        self.assertGreater(all_states['indeterminate'], 0)
        self.assertGreater(all_states['present'], 0)
