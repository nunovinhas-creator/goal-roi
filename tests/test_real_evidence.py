"""Structural checks of the real-data evidence snapshot (audits/real-evidence)."""

import json
from pathlib import Path
import unittest

E = json.loads((Path(__file__).resolve().parents[1] / 'audits/real-evidence/evidence.json').read_bytes())


class RealEvidence(unittest.TestCase):
    def test_coverage_counts_are_nested(self):
        c = E['coverage']
        self.assertGreaterEqual(c['distinct_games'], c['games_with_odds'])
        self.assertGreaterEqual(c['distinct_games'], c['games_with_final_score'])
        self.assertLessEqual(c['evaluable_without_future_information']['signals'], c['eligible_signals'])
        self.assertLessEqual(c['eligible_signals'], c['signal_records'])
        self.assertEqual(c['prospective_ledger_entries'], 0)

    def test_independent_results_are_not_bound_and_cause_is_recorded(self):
        for name in ('hl', 'bsd'):
            r = E['result_verification'][name]
            self.assertEqual(r['bound_by_report'], 0)
            self.assertEqual(r['records_with_league'], 0)
            self.assertEqual(r['agree_with_bot_score'], r['comparable_with_bot_score'])

    def test_discovery_and_check_are_disjoint_and_exploratory(self):
        h = E['hypotheses']
        self.assertTrue(h['status'].startswith('exploratory'))
        for item in h['selected']:
            self.assertIn('discovery', item)
            self.assertIn('check', item)
        self.assertFalse(E['confirmatory'])

    def test_future_information_is_flagged(self):
        r = E['relations']
        self.assertFalse(r['odds_move_after_signal']['prospective'])
        self.assertFalse(r['repeated_signal']['prospective'])
        self.assertTrue(r['score_after_live_signal']['prospective'])


if __name__ == '__main__':
    unittest.main()
