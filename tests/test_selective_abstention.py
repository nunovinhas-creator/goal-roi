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


if __name__ == '__main__':
    unittest.main()
