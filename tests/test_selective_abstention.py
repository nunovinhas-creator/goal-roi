"""PR J: label-free selection, consensus refinement and abstention."""

import ast
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from scangoal.discovery.experimental_h.observed import Observed
from scangoal.discovery.experimental_i import miner as miner_i
from scangoal.discovery.experimental_j import observe, refine, selector
from scangoal.discovery.v32.tape import Tape

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'scangoal/discovery/experimental_j'
BANKS = ROOT / 'audits/robust-generalization/bank_i.py'
SPEC = importlib.util.spec_from_file_location('goal_roi_j_test_bank_i', BANKS)
bank_i = importlib.util.module_from_spec(SPEC)
with patch.object(sys, 'path', [str(BANKS.parent), *sys.path]):
    SPEC.loader.exec_module(bank_i)


def _scenario(name):
    return next(i for i, row in enumerate(bank_i.SCENARIOS) if row[0] == name)


def _tape(bank):
    return Tape(bank.observations, bank.cutoff, bank.coverage)


class LabelBoundary(unittest.TestCase):
    def test_package_has_no_access_to_labels_or_evaluators(self):
        forbidden = {'truth', 'counterexamples', 'bank_i', 'banks', 'audits', 'evaluate', 'gate'}
        for path in PACKAGE.glob('*.py'):
            tree = ast.parse(path.read_text())
            names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            names |= {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                      for alias in node.names}
            modules = {node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            self.assertFalse(forbidden & names, path.name)
            self.assertFalse(any('audit' in m or 'bank' in m for m in modules), path.name)


class Ranking(unittest.TestCase):
    def test_i_shortlist_puts_longest_lane_first_whatever_its_score(self):
        rows = [dict(id='long', phase='pre', channel='structural', shape=tuple('abcdefgh'), rank_score=1),
                dict(id='short', phase='pre', channel='structural', shape=('a', 'b'), rank_score=9999)]
        self.assertEqual([row['id'] for row in miner_i._shortlist(rows)], ['long', 'short'])


class Consensus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        index = _scenario('pre_ticket_decline')
        cls.bank = bank_i.build(index, 46027, fresh=False)
        cls.index = observe.Index(Observed(_tape(cls.bank)))

    def test_literals_are_scale_free_and_each_has_an_observed_counterexample(self):
        shape = ('alert', 'quote', 'revision', 'alert', 'quote', 'quote')
        base = selector.structural('pre', shape)
        firing = self.index.fire(base['id'], base['rule'])
        literals, equivalents, n = refine.consensus(base['rule'], firing, self.index)
        self.assertGreater(len(literals), 0)
        for literal in literals:
            i, j, name, op, value = literal
            self.assertTrue(j >= 0 or isinstance(value, str), literal)
            if j >= 0 and not isinstance(value, bool):
                self.assertEqual(value, refine.CENTERS[name])
            self.assertIn(literal, equivalents)
        # Each literal alone keeps a majority and excludes at least one observed fixture.
        for literal in literals:
            rule, added = refine.extend(base['rule'], [literal])
            self.assertEqual(added, [literal])
            kept = self.index.fixtures(self.index.fire(repr(literal), rule).present)
            self.assertLess(len(kept), n)
            self.assertGreaterEqual(len(kept), refine.CONSENSUS * n)

    def test_refinement_is_an_added_proposal_not_a_filter(self):
        result = selector.investigate(_tape(self.bank), phase='pre', index=self.index)
        mined = miner_i.discover(_tape(self.bank))
        self.assertEqual(result['proposals'][:len(mined['candidates'])], mined['candidates'])
        self.assertFalse(result['abstained'])
        self.assertEqual(result['choice']['channel'], 'consensus')
        self.assertTrue(result['audit']['checks']['replicated'])


class Abstention(unittest.TestCase):
    def test_abstains_when_no_trajectory_is_regular(self):
        bank = bank_i.build(_scenario('live_score_confirmation'), 46027, fresh=False)
        result = selector.investigate(_tape(bank), phase='live')
        self.assertTrue(result['abstained'])
        self.assertIsNone(result['choice'])
        self.assertEqual(result['audit']['abstain_reason'], 'no_regular_trajectory')

    def test_abstains_on_an_empty_phase(self):
        bank = bank_i.build(_scenario('pre_ticket_decline'), 46027, fresh=False)
        result = selector.investigate(_tape(bank), phase='live')
        self.assertTrue(result['abstained'])


class Transport(unittest.TestCase):
    def test_choice_survives_irrelevant_transport_changes(self):
        index = _scenario('live_scope_switch')
        bank = bank_i.build(index, 90787, fresh=False)
        moved = bank_i.transformed(bank, 90787 + 41081)
        a = selector.investigate(_tape(bank), phase='live')
        b = selector.investigate(_tape(moved), phase='live')
        self.assertEqual(a['choice']['id'], b['choice']['id'])
        self.assertEqual(a['audit']['checks'], b['audit']['checks'])


EVALUATOR = ROOT / 'audits/selective-abstention/evaluate.py'
ESPEC = importlib.util.spec_from_file_location('goal_roi_j_evaluator', EVALUATOR)
J_EVAL = importlib.util.module_from_spec(ESPEC)
with patch.object(sys, 'path', [str(EVALUATOR.parent), *sys.path]), patch.dict(sys.modules):
    ESPEC.loader.exec_module(J_EVAL)


class IndependentEvaluation(unittest.TestCase):
    def test_lock_eligibility_and_report_reproduce_offline_in_the_sealed_order(self):
        import json
        import socket
        calls = []
        choices, build, eligible = J_EVAL._choices, J_EVAL.bank_j.build, J_EVAL.eligibility

        def counted(*args):
            result = eligible(*args)
            calls.append('eligibility_done')
            return result

        def sealed(*args):
            result = choices(*args)
            calls.append('choices_sealed')
            return result

        def guarded(index, seed, *, fresh):
            # Validation banks exist only for the label-count pre-check and after choices are sealed.
            if fresh and 'eligibility_done' in calls:
                self.assertIn('choices_sealed', calls)
            return build(index, seed, fresh=fresh)

        with patch.object(socket, 'socket', side_effect=AssertionError('network unavailable')), \
                patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS unavailable')), \
                patch.object(J_EVAL, '_choices', side_effect=sealed), \
                patch.object(J_EVAL, 'eligibility', side_effect=counted), \
                patch.object(J_EVAL.bank_j, 'build', side_effect=guarded):
            report = J_EVAL.run()
        self.assertEqual(calls, ['eligibility_done', 'choices_sealed'])
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'
        self.assertEqual(encoded.encode(), EVALUATOR.with_name('report.json').read_bytes())
        self.assertEqual(report['decision']['independent_selection'], 'NO-GO')
        self.assertFalse(report['confirmatory'])
        for family in report['families'].values():
            self.assertTrue(family['assessable'])
            for fold in family['folds']:
                self.assertTrue(fold['search_complete'])
                self.assertTrue(fold['j_transport_same_choice'])
                self.assertTrue(all(trial['j_invariant'] for trial in fold['trials']))

    def test_an_abstention_counts_in_every_denominator(self):
        import json
        report = json.loads(EVALUATOR.with_name('report.json').read_bytes())
        totals = report['totals']
        self.assertEqual({e: totals[e]['evaluations'] for e in 'hij'}, dict(h=24, i=24, j=24))
        self.assertEqual({e: totals[e]['targets'] for e in 'hij'}, dict(h=240, i=240, j=240))


if __name__ == '__main__':
    unittest.main()
