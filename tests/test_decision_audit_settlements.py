from datetime import timedelta
import unittest

from scangoal import chronology, decision_audit_settlements as audit, opportunity_results as results


def utc(value):
    return chronology.parse_utc(value)


SIGNAL = {'date_utc': '2026-11-19T16:00:00Z', 'signal_seen_utc': '2026-11-19T16:01:00Z',
          'kickoff_utc': '2026-11-19T18:00:00Z', 'phase': 'pre', 'market': 'Asian Total',
          'selection': 'Over 2.5', 'odds': 2.0}
DECISION_DEADLINE = utc(SIGNAL['signal_seen_utc']) + timedelta(days=3)
KICKOFF_DEADLINE = utc(SIGNAL['kickoff_utc']) + timedelta(days=3)
FIRST = utc('2026-11-19T22:00:00Z')
LATE = utc('2026-11-23T03:00:00Z')


def version(stamp, score=(2, 1), source='HL'):
    return results.Observation(source, '42', 'regulation', score, stamp, stamp,
                               kickoff=utc(SIGNAL['kickoff_utc']))


class DecisionAuditSettlementTests(unittest.TestCase):
    def test_six_categories_reconcile_to_all_admitted_stakes(self):
        contradictory = version(FIRST, (1, 1), 'BSD')
        rows = [
            audit.outcome(dict(SIGNAL, _ft_observations=(version(FIRST),)), FIRST, deadline=DECISION_DEADLINE),
            audit.outcome(SIGNAL, FIRST, deadline=DECISION_DEADLINE),
            audit.outcome(SIGNAL, LATE, deadline=DECISION_DEADLINE),
            audit.outcome(dict(SIGNAL, _ft_observations=(version(FIRST), contradictory)),
                          LATE, deadline=DECISION_DEADLINE),
            audit.outcome(dict(SIGNAL, market='Total Corners', _ft_observations=(version(FIRST),)),
                          LATE, deadline=DECISION_DEADLINE),
            audit.outcome(dict(SIGNAL, ft=[2, 1], w=1.0), LATE, deadline=DECISION_DEADLINE),
        ]
        self.assertEqual([row['state'] for row in rows], list(audit.STATES))
        self.assertEqual(rows[4]['ft'], [2, 1])
        self.assertTrue(rows[4]['deadline_passed'])
        self.assertEqual(rows[5]['issue'], 'unproven_result_time')
        summary = audit.summarize(rows)
        self.assertEqual(summary['counts'], dict.fromkeys(audit.STATES, 1))
        self.assertEqual(summary['admitted'], 6)
        self.assertEqual(summary['adverse']['stake_units'], 6)
        self.assertEqual(summary['adverse']['profit_units'], -4.04)
        self.assertEqual(summary['adverse']['roi'], -0.6733)

    def test_late_conflict_keeps_frozen_primary_result_and_past_replay(self):
        signal = dict(SIGNAL, _ft_observations=(version(FIRST),))
        probe = dict(SIGNAL, _ft_observations=(version(FIRST), version(LATE, (1, 1), 'BSD')))
        before = audit.outcome(signal, FIRST, deadline=DECISION_DEADLINE)
        self.assertEqual(audit.outcome(probe, FIRST, deadline=DECISION_DEADLINE), before)
        after = audit.outcome(probe, LATE, deadline=DECISION_DEADLINE)
        self.assertEqual(after['state'], 'confirmed')
        self.assertEqual(after['ft'], [2, 1])
        self.assertEqual(after['late_reconciliation'], {'ft': None, 'quarantined': True,
                                                         'result_available_utc': LATE.isoformat()})

    def test_pr55_expiry_is_kickoff_based_and_late_result_stays_separate(self):
        between = DECISION_DEADLINE + timedelta(minutes=30)
        evidence = [version(between)]
        confirmed = audit.outcome(SIGNAL, between, deadline=KICKOFF_DEADLINE, pr55=True, evidence=evidence)
        self.assertEqual(confirmed['state'], 'confirmed')
        self.assertEqual(confirmed['first_result_seen_utc'], between.isoformat())
        self.assertEqual(confirmed['confirmed_payoff'], 1.0)
        self.assertAlmostEqual(confirmed['adverse_profit_units'], 0.96)

        late = audit.outcome(SIGNAL, LATE, deadline=KICKOFF_DEADLINE, pr55=True, evidence=[version(LATE)])
        self.assertEqual(late['state'], 'imputed_loss')
        self.assertIsNone(late['ft'])
        self.assertEqual(late['late_reconciliation']['ft'], [2, 1])
        self.assertEqual(late['adverse_profit_units'], -1.0)

    def test_void_and_half_outcomes_count_one_unit(self):
        outcomes = [
            audit.outcome(dict(SIGNAL, selection=line, _ft_observations=(version(FIRST),)),
                          FIRST, deadline=DECISION_DEADLINE)
            for line in ('Over 3.0', 'Over 2.75', 'Over 3.25')
        ]
        self.assertEqual([row['confirmed_payoff'] for row in outcomes], [0.0, 0.5, -0.5])
        summary = audit.summarize(outcomes)
        self.assertEqual(summary['confirmed_wins'], 1)
        self.assertEqual(summary['confirmed_losses'], 1)
        self.assertEqual(summary['confirmed_voids'], 1)
        self.assertEqual(summary['adverse']['stake_units'], 3)
        self.assertEqual(summary['adverse']['profit_units'], -0.02)

    def test_missing_deadline_and_invalid_payoff_remain_unknown(self):
        self.assertEqual(audit.outcome(SIGNAL, FIRST, deadline=None)['state'], 'unknown')
        malformed = audit.outcome(dict(SIGNAL, _ft_observations=None), FIRST, deadline=DECISION_DEADLINE)
        self.assertEqual(malformed['issue'], 'malformed_result_evidence')
        invalid = dict(SIGNAL, w=True, ft=[2, 1], result_known_utc=FIRST.isoformat(),
                       match_result_first_seen_utc=FIRST.isoformat())
        outcome = audit.outcome(invalid, FIRST + timedelta(minutes=5), deadline=DECISION_DEADLINE)
        self.assertEqual(outcome['state'], 'unknown')
        self.assertEqual(outcome['adverse_profit_units'], -1.0)

    def test_legacy_future_result_does_not_enter_the_replay(self):
        future = dict(SIGNAL, w=1.0, ft=[2, 1], result_known_utc=FIRST.isoformat(),
                      match_result_first_seen_utc=FIRST.isoformat())
        as_of = FIRST - timedelta(minutes=1)
        self.assertEqual(audit.outcome(future, as_of, deadline=DECISION_DEADLINE),
                         audit.outcome(SIGNAL, as_of, deadline=DECISION_DEADLINE))
        known = audit.outcome(future, FIRST + timedelta(minutes=5), deadline=DECISION_DEADLINE)
        self.assertEqual(known['state'], 'confirmed')

    def test_empty_admission_has_no_roi(self):
        summary = audit.summarize([])
        self.assertEqual(summary['admitted'], 0)
        self.assertEqual(summary['adverse']['stake_units'], 0)
        self.assertIsNone(summary['adverse']['roi'])
