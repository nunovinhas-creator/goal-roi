"""K development: switchable selector, J equivalence, label boundary and ablation report consistency."""

import ast
from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from scangoal.discovery.experimental_h.observed import Observed
from scangoal.discovery.experimental_i import miner as miner_i
from scangoal.discovery.experimental_j import selector as j_selector
from scangoal.discovery.experimental_j.observe import Index
from scangoal.discovery.experimental_k import selector as K
from scangoal.discovery.v32.tape import Tape

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'audits/k-development'
REPORT = json.loads((HERE / 'ablation.json').read_bytes())


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, 'path', [str(path.parent), *sys.path]):
        spec.loader.exec_module(module)
    return module


dev_bank = _module('goal_roi_test_k_dev_bank', HERE / 'dev_bank.py')


def _tape(bank):
    return Tape(bank.observations, bank.cutoff, bank.coverage)


class Boundary(unittest.TestCase):
    def test_k_package_reads_no_labels_banks_or_family_names(self):
        forbidden = {'truth', 'counterexamples', 'bank_i', 'bank_j', 'banks', 'dev_bank', 'audits', 'evaluate'}
        for path in (ROOT / 'scangoal/discovery/experimental_k').glob('*.py'):
            tree = ast.parse(path.read_text())
            names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
            self.assertFalse(forbidden & names, path.name)
        self.assertTrue(REPORT['L5']['passed'])
        self.assertEqual(REPORT['L5']['leaked'], [])

    def test_plan_was_frozen_before_the_report(self):
        import hashlib
        self.assertEqual(REPORT['plan_sha256'], hashlib.sha256((HERE / 'plan.json').read_bytes()).hexdigest())
        self.assertFalse(REPORT['blind_banks_used'])


class Equivalence(unittest.TestCase):
    def test_all_switches_off_reproduces_j(self):
        bank = dev_bank.build(0, dev_bank.MINING_SEEDS[0], fresh=False)
        tape = _tape(bank)
        index, mined = Index(Observed(tape)), miner_i.discover(tape)
        j_choice, j_audit = j_selector.select(mined['candidates'], index, 'pre')
        k = K.investigate(tape, phase='pre', config=K.Config(), index=index, mined=mined)
        self.assertEqual((j_choice or {}).get('id'), (k['choice'] or {}).get('id'))
        self.assertEqual(j_audit['checks'], k['audit']['checks'])
        self.assertEqual(set(K.VARIANTS), {'J', 'J+K1', 'J+K2', 'J+K3', 'K'})

    def test_k2_measures_consensus_only_where_values_are_observable(self):
        bank = dev_bank.build(0, dev_bank.MINING_SEEDS[0], fresh=False)
        index = Index(Observed(_tape(bank)))
        base = j_selector.structural('pre', ('quote', 'alert', 'quote', 'revision', 'alert', 'quote'))
        firing = index.fire(base['id'], base['rule'])
        off, _, n = K.consensus(base['rule'], firing, index, K.Config())
        on, _, _ = K.consensus(base['rule'], firing, index, K.Config(k2=True))
        self.assertGreater(n, 0)
        self.assertTrue(set(on) - set(off), 'K2 admits a literal that J rejected because of unknown values')


class Report(unittest.TestCase):
    def test_summary_matches_cells_and_abstentions_fail(self):
        totals = Counter()
        for row in REPORT['cells']:
            if row['abstained']:
                self.assertFalse(row['gate'])
                self.assertEqual(row['detections'], 0)
            totals[row['source'], row['family'], row['variant'], row['grid']] += row['gate']
        for row in REPORT['summary']:
            self.assertEqual(row['gates'], totals[row['source'], row['family'], row['variant'], row['grid']])

    def test_recommendation_is_no_go_and_negatives_are_published(self):
        self.assertEqual(REPORT['recommendation']['definitive_k_lock'], 'NO-GO')
        self.assertEqual(REPORT['recommendation']['scientific_independence'], 'NOT DECLARED')
        variants = {row['variant'] for row in REPORT['summary']}
        self.assertEqual(variants, set(K.VARIANTS))
        self.assertGreater(len(REPORT['lock_rules']['K']['lost_vs_j']), 0)


if __name__ == '__main__':
    unittest.main()
