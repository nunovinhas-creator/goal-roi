"""Development-only tests for the independently versioned I miner."""

from datetime import timedelta
from random import Random
import unittest

from scangoal import event_intelligence as events
from scangoal.discovery.experimental_h.observed import Observed
from scangoal.discovery.experimental_h.rules import Meter
from scangoal.discovery.experimental_i import features, miner, paths
from scangoal.discovery.v32.tape import Tape

from test_autonomous_generalization import M, T, quote, row


def _world():
    output = []
    for game in range(10):
        phase = 'pre' if game % 2 == 0 else 'live'
        for offset, kind in ((-11, 'revision'), (-9, 'quote'), (-7, 'alert'), (-5, 'quote'),
                             (-4, 'score'), (-2, 'revision'), (-1, 'alert'), (0, 'quote')):
            event = row(game, offset, kind, **quote('2.15' if offset == -5 else
                                                  '1.82' if game % 3 else '2.80'))
            if kind != 'quote':
                event.update(odds=None, price_kind=None, price_low=None, price_high=None)
            if phase == 'pre':
                kickoff = T + timedelta(days=game) + 39 * M
                event.update(kickoff_utc=events.iso(kickoff), phase='pre', minute=None, score=None)
            output.append(event)
    return output


class RobustTrajectoryMiner(unittest.TestCase):
    def test_keeps_interior_window_and_both_boundaries_with_fixed_path_cap(self):
        selected = paths._choices(7, 5)[:paths.PATHS_PER_LENGTH]
        self.assertIn((0, 1, 2, 3, 4), selected)
        self.assertIn((1, 2, 3, 4, 5), selected)
        self.assertIn((2, 3, 4, 5, 6), selected)
        for n in range(2, paths.TAIL + 1):
            for k in range(1, min(n, 7) + 1):
                indexes = paths._choices(n, k)
                self.assertEqual(len(indexes), len(set(indexes)))
                self.assertTrue(all(len(item) == k and tuple(sorted(item)) == item and item[-1] < n
                                    for item in indexes))

    def test_budget_is_partitioned_and_keeps_both_phase_portfolios(self):
        data = _world()
        end = T + timedelta(days=11)
        result = miner.discover(Tape(data, end))
        proposed = result['candidates']
        self.assertEqual(set(x['phase'] for x in proposed), {'live', 'pre'})
        self.assertLessEqual(abs(sum(x['phase'] == 'pre' for x in proposed)
                                 - sum(x['phase'] == 'live' for x in proposed)), 1)
        self.assertTrue(any(x['channel'] == 'structural' for x in proposed))
        self.assertTrue(any(edge['to'] - edge['from'] > 1 for x in proposed for edge in x['rule']['relations']))
        report = result['work']
        self.assertEqual(sum(x['limit'] for x in report['families'].values()) + sum(report['observations'].values()),
                         report['limit'])
        self.assertEqual(sum(x['used'] for x in report['families'].values()) + sum(report['observations'].values()),
                         report['used'])
        for limit in (0, 30, 8000):
            partial = miner.discover(Tape(data, end), work=limit)['work']
            self.assertFalse(partial['search_complete'])
            self.assertLessEqual(partial['used'], limit)
            self.assertTrue(partial['incomplete'])

    def test_order_ids_duplicates_and_future_evidence_are_inert(self):
        raw, cutoff = _world(), T + timedelta(days=12)
        expected = miner.discover(Tape(raw, cutoff))
        permuted = [dict(x, message_id=i, journal_line=i * 3, ft=[7, 2])
                    for i, x in enumerate([*raw, *raw[:31]])]
        permuted.append(dict(raw[-1], available_utc=events.iso(cutoff + M), score=[8, 8]))
        Random(42).shuffle(permuted)
        self.assertEqual(expected, miner.discover(Tape(permuted, cutoff)))
        self.assertTrue(all(type(x['rank_score']) is int for x in expected['candidates']))

    def test_identical_support_does_not_merge_different_domains(self):
        # Quote/alert conditions that coincide in this population can diverge
        # after a change of source, price regime or score.
        raw = []
        for i in range(8):
            old = row(i, -4, **quote('2.20' if i < 4 else '1.50'))
            new = row(i, 0, **quote('1.80' if i < 4 else '1.90'))
            raw.extend((old, new))
        source = Observed(Tape(raw, T + timedelta(days=9)))
        sampled, _, _ = paths.sample(source, Meter(100000))
        entries = sampled['live', ('quote', 'quote')]
        candidates = features.columns(entries, 2, Meter(100000), lambda x: x.bit_count() >= 3, {}, {})
        falling = next(x for x in candidates if x.clause == features.Clause(0, 1, 'odds_delta_bp', 'lt', 0))
        self.assertTrue(any(x.clause.field == 'point_odds' and x.passed == falling.passed
                            and x.known == falling.known for x in candidates))
        self.assertNotEqual(falling.clause.slot, next(x.clause.slot for x in candidates
                            if x.clause.field == 'point_odds' and x.passed == falling.passed))

    def test_equal_issue_group_is_never_a_temporal_step(self):
        a = row(0, -2, 'alert', source='Polymarket')
        b = row(0, -2, 'revision', source='Betfair')
        end = row(0, 0)
        source = Observed(Tape([end, b, a], T))
        decision, = source.decisions(Meter(1000))
        self.assertEqual({tuple(row['event_kind'] for row in path) for path in paths.sampled_paths(decision)},
                         {('alert', 'quote'), ('revision', 'quote')})
