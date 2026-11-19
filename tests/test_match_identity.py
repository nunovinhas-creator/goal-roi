"""Fixture identity and economic invariants across research consumers."""

from datetime import timedelta
import unittest

from scangoal import brain, llm_eval, match_identity
from test_chronological_validity import T0, at, hyp, sig
from test_opportunity_ranking import result, run, signal


def fixture(mid, minute, **changes):
    base = sig(mid, 1.0, minute, kickoff_utc=at(90), odds=2.0, phase='pre')
    return dict(base, **changes)


class FixtureAliases(unittest.TestCase):
    def test_exact_aliases_are_one_stake_in_brain_and_llm_controls(self):
        original = fixture('original', 5)
        alias = fixture('alias', 6, teams=[' A ', 'b'], odds=4.0)
        selected = [original, alias]
        metrics = brain.economic_summary(selected, T0)
        self.assertEqual((metrics['admitted'], metrics['resolved_stake_units'], metrics['profit_advertised']), (1, 1, 1.0))
        measured = llm_eval.admitted(selected, T0)
        self.assertEqual(len(measured), 1)
        rows = brain.evaluate(selected, {'h': hyp()}, 300)[0]
        self.assertEqual((rows[0]['games'], rows[0]['roi']), (1, 1.0))
        self.assertEqual(brain.walk_forward(selected, {'h': hyp()})['skipped']['bets'], 1)

    def test_distinct_leagues_kickoffs_and_similar_team_names_are_not_joined(self):
        rows = [fixture('one', 5), fixture('league', 6, league='Other'),
                fixture('rescheduled', 7, kickoff_utc=at(100)),
                fixture('name', 8, teams=['A FC', 'B'])]
        self.assertEqual(len(set(match_identity.aliases(rows).values())), 4)

    def test_contradictory_id_and_partial_collision_do_not_bridge_fixtures(self):
        rows = [fixture('one', 5), fixture('two', 6, league='Other'),
                fixture('bridge', 7), fixture('bridge', 8, league='Other'),
                fixture('ambiguous', 9, league=None)]
        ids = match_identity.aliases(rows)
        self.assertIsNone(ids['bridge'])
        self.assertIsNone(ids['ambiguous'])
        self.assertEqual({ids['one'], ids['two']}, {'one', 'two'})
        self.assertEqual(brain.economic_summary(rows, T0)['resolved_stake_units'], 2)

    def test_fixture_conflict_revealed_after_cutoff_does_not_join_prior_evidence(self):
        original = fixture('one', 5)
        alias = fixture('two', 6)
        late = fixture('two', 40, kickoff_utc=at(100))
        cutoff = T0 + timedelta(minutes=15)
        self.assertEqual(match_identity.aliases([original, alias], cutoff),
                         match_identity.aliases([original, alias, late], cutoff))
        self.assertIsNone(match_identity.aliases([original, alias, late])['two'])

    def test_study_and_benchmark_use_one_stake_when_alias_supplies_result(self):
        a = signal('original', teams=['A', 'B'])
        b = signal('alias', teams=['A ', 'B'], odds=4.0, collected_at_utc='2026-11-19T15:40:00Z')
        report = run([a, b], {'alias': result()})
        self.assertEqual(report['overlap']['shared_fixtures'], 1)
        for side in (report['ranking'][0], report['baseline']):
            self.assertEqual((side['forward_games'], side['resolved_stake_units'], side['profit_advertised']), (1, 1, 0.85))
