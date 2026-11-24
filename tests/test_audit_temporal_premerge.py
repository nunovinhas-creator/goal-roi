"""Check that the supplementary audit cannot choose on realized returns."""

import unittest

from scripts.audit_temporal_premerge import Replay


def event(mid, game, sent, seen=None, direction='over'):
    return dict(message_id=mid, fixture_id=game, phase='pre', source='Polymarket', signal_type='POLYMARKET',
                market='Asian Total', market_key='match_goals', period='regulation', line='2.5',
                selection=f'{direction} 2.5', direction=direction, odds=['1.9', '1.9'],
                kickoff_utc='2026-11-24T19:00:00Z',
                emitted_utc=f'2026-11-24T13:{sent:02}:00Z',
                available_utc=f'2026-11-24T13:{seen if seen is not None else sent:02}:00Z')


def quote(event):
    return dict(episode_id=f"quote:{event['message_id']}", decision_utc=event['available_utc'],
                fixture_id=event['fixture_id'], message_ids=[event['message_id']], exclusion_reasons=[])


def outcome(profit):
    return dict(ft=None, theoretical_profit_units=profit, last_payoff=1.0 if profit else None,
                status='confirmed' if profit else 'pending', reason=None)


class PremergeStudy(unittest.TestCase):
    def test_first_decision_is_reserved_even_when_unsettled(self):
        first, later = event(10, 'a', 10), event(11, 'a', 20)
        outcomes = {'quote:10': outcome(None), 'quote:11': outcome(0.9)}
        replay = Replay(dict(events=[first, later], outcomes=outcomes))
        summary = replay.measure([quote(later), quote(first)])
        self.assertEqual(summary['retained'], ['quote:10'])
        self.assertEqual(summary['per_fixture']['roi_priced_n'], 0)
        self.assertEqual(summary['all']['roi_priced_n'], 1)

    def test_matching_uses_past_controls_from_distinct_games_once(self):
        first, second = event(1, 'a', 20), event(2, 'b', 25)
        prior, reused, future = event(3, 'c', 10), event(4, 'c', 15), event(5, 'd', 30)
        outcomes = {f'quote:{mid}': outcome(0.9) for mid in range(1, 6)}
        replay = Replay(dict(events=[first, second, prior, reused, future], outcomes=outcomes))
        result = replay.compare([quote(second), quote(first)], [quote(prior), quote(reused), quote(future), quote(first)])
        self.assertEqual(result['matched_ids'], [['quote:1', 'quote:4']])
        self.assertEqual(result['unmatched'], 1)

    def test_unread_prior_opposite_does_not_reclassify_control(self):
        over = event(1, 'a', 30)
        late_under = event(2, 'a', 10, seen=40, direction='under')
        early_under = event(3, 'a', 15, direction='under')
        replay = Replay(dict(events=[over, late_under], outcomes={}))
        self.assertTrue(replay.no_preceding_opposite(quote(over)))
        replay = Replay(dict(events=[over, late_under, early_under], outcomes={}))
        self.assertFalse(replay.no_preceding_opposite(quote(over)))


if __name__ == '__main__':
    unittest.main()
