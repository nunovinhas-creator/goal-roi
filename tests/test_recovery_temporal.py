from datetime import datetime, timedelta, timezone
import unittest

from scangoal import brain, chronology, opportunity_results, report


KICKOFF = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


def at(minutes):
    return KICKOFF + timedelta(minutes=minutes)


def signal(match_id, sent, kickoff):
    return dict(match_id=match_id, canonical_match_id=match_id, key=match_id, type='BETFAIR',
                market='1X2', period='regulation', selection='1', teams=['A', 'B'], league='Test League', phase='pre',
                date_utc=at(sent).isoformat(), signal_seen_utc=at(sent + 1).isoformat(),
                kickoff_utc=at(kickoff).isoformat(), odds=2.0, w=None, in_roi=True)


def result(source, minute, score):
    return opportunity_results.collected(source, 1, score, at(minute).isoformat(), kickoff=KICKOFF.isoformat())


class TemporalRecovery(unittest.TestCase):
    def test_replay_restores_the_score_and_quarantine_at_each_cut(self):
        bet = signal('fixture', -45, 0)
        first = result('one', 120, [2, 1])
        conflict = result('two', 130, [0, 0])
        correction = result('two', 150, [2, 1])
        sources = {'fixture': [first, conflict, correction]}
        [latest] = report.research_bets([bet], sources, at(160))
        [during_conflict] = chronology.as_of_view([latest], at(140))
        [before_conflict] = chronology.as_of_view([latest], at(128))
        self.assertIsNone(during_conflict['w'])
        self.assertTrue(during_conflict['result_quarantined'])
        self.assertEqual(before_conflict['w'], 1)
        self.assertFalse(before_conflict['result_quarantined'])
        self.assertEqual(chronology.result_known(latest), at(155))
        self.assertEqual(list(brain.result_changes(latest)),
                         [(at(125), None, 1), (at(135), 1, None), (at(155), None, 1)])

    def test_correction_leaves_the_sequential_commitments_and_payoff_unchanged(self):
        previous = signal('previous', -45, 0)
        following = signal('following', 136, 180)
        result_one = result('one', 120, [2, 1])
        disagreement = result('two', 145, [0, 1])
        before = list(report.research_bets([previous, following], {'previous': [result_one]}, at(155)))
        after = list(report.research_bets([previous, following], {'previous': [result_one, disagreement]}, at(155)))
        self.assertTrue(after[0]['result_quarantined'])
        control = brain.confirmation(before, at(-60), 0.025, at(155))
        revised = brain.confirmation(after, at(-60), 0.025, at(155))
        self.assertEqual(control['commitments'], revised['commitments'])
        self.assertEqual(len(revised['commitments']), 2)
        self.assertEqual(control['e_value'], revised['e_value'])
