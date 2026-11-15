"""Regression tests for separation of exploratory and decision evidence."""
import copy
import unittest
from datetime import datetime

from scangoal import brain, report
from scangoal.eligibility import eligible
import test_scangoal as fixtures
from scangoal.parser import parse


class Eligibility(unittest.TestCase):
    def test_fail_closed_and_conflicting_flags(self):
        b = fixtures.Brain().sig(1, 10, True)
        self.assertTrue(eligible(b))
        for changes in ({'in_roi': False}, {'in_roi': None}, {'odds': None},
                        {'odds': 1.09}, {'odds': float('nan')}, {'odds': float('inf')},
                        {'odds': True}, {'market': None}, {'had_ft_at_first_sight': True},
                        {'edited_at_first_sight': True}, {'burst': True}):
            with self.subTest(changes=changes):
                self.assertFalse(eligible(dict(b, **changes)))
        del b['in_roi']
        self.assertFalse(eligible(b))

    def test_first_invalid_signal_cannot_be_rescued_by_reinforcement(self):
        for flag in ('had_ft_at_first_sight', 'edited_at_first_sight', 'burst'):
            m = dict(parse(fixtures.BETFAIR_PRE), date_utc='2026-11-11T10:00:00+00:00',
                     message_id=1, had_ft_at_first_sight=False, edited_at_first_sight=False, burst=False)
            m[flag] = True
            later = dict(m, message_id=2, date_utc='2026-11-11T11:00:00+00:00', **{flag: False})
            with self.subTest(flag=flag):
                self.assertEqual(report.build_bets([m, later], {}, strict=True), [])
                exploratory = report.build_bets([m, later], {}, strict=False)
                self.assertEqual(len(exploratory), 1)
                self.assertFalse(eligible(exploratory[0]))
                self.assertIn(flag, exploratory[0]['evidence_exclusions'])

    def test_excluded_winners_cannot_promote_or_support_registration(self):
        bad = [dict(fixtures.Brain().sig(i, 10, True), in_roi=False) for i in range(40)]
        registry = {'h': {'family': 'Betfair', 'conditions': [['Seleção', 'equipa da casa']],
                          'source': 'gerador', 'born_utc': '2026-11-01T00:00:00+00:00'}}
        original = copy.deepcopy(registry)
        rows, tested, _ = brain.evaluate(bad, registry, 300)
        self.assertEqual((rows[0]['n'], rows[0]['status'], tested), (0, 'em observação', 0))
        explore, _, _ = brain.evaluate(bad, registry, 300, exploratory=True)
        self.assertEqual((explore[0]['n'], explore[0]['status']), (40, 'promissora'))
        self.assertEqual(brain.update_registry(bad, registry), 0)
        self.assertEqual(registry, original)
        output = brain.build(bad, registry, 300)
        self.assertEqual(output['exploratory']['counts'], {'promissora': 1})
        self.assertEqual(output['watch'], [])
        self.assertEqual(output['board']['best'], [])
        self.assertEqual(output['eligibility']['excluded'], 40)

    def test_excluded_results_cannot_train_walk_forward(self):
        fixture = fixtures.EvidencePerMatch()
        early = [dict(fixture.row(str(i), 1, day=1, hour=10), in_roi=False) for i in range(12)]
        late = fixture.row('later', 1, day=2, hour=10)
        registry = {'h': {'family': 'Jogo aberto', 'conditions': [['Minuto', '31 a 60']],
                          'source': 'gerador', 'born_utc': '2026-11-01T00:00:00+00:00'}}
        self.assertEqual(brain.walk_forward(early + [late], registry), brain.walk_forward([late], registry))
        self.assertEqual(brain.walk_forward(early + [late], registry)['followed']['bets'], 0)

    def test_excluded_open_bets_cannot_enter_agenda_or_watch(self):
        valid = [fixtures.Brain().sig(i, 10, True) for i in range(40)]
        bad = dict(fixtures.Brain().sig(41, 10, None), in_roi=False, kickoff_utc='2099-01-01T00:00:00+00:00')
        registry = {}
        brain.update_registry(valid, registry)
        output = brain.build(valid + [bad], registry, 300)
        self.assertTrue(output['counts'].get('promissora'))
        self.assertEqual(output['watch'], [])
        self.assertEqual(output['board']['open'], [])
        self.assertEqual(output['agenda']['items'], [])
        self.assertTrue(all(r['signals'] == 0 for r in brain.entry_points([dict(bad, steps=[[1, 2.0]])])))
