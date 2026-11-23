"""Fixture identity and economic invariants across research consumers."""

from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scangoal import brain, chronology, llm_eval, match_identity, opportunity_results, report
from test_chronological_validity import T0, at, hyp, sig
from test_opportunity_ranking import result, run, signal


def fixture(mid, minute, **changes):
    base = sig(mid, 1.0, minute, kickoff_utc=at(90), odds=2.0, phase='pre', teams=['A', 'B'])
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
        self.assertEqual(len({row['canonical_match_id'] for row in match_identity.annotate(rows)}), 4)

    def test_contradictory_id_and_partial_collision_do_not_bridge_fixtures(self):
        rows = [fixture('one', 5), fixture('two', 6, league='Other'),
                fixture('bridge', 7), fixture('bridge', 8, league='Other'),
                fixture('ambiguous', 9, league=None)]
        identified = match_identity.annotate(rows)
        self.assertEqual([row['canonical_match_id'] for row in identified], ['one', 'two', 'one', None, None])
        self.assertEqual(identified[3]['match_identity_exclusion'], 'reused_match_id')
        self.assertEqual(identified[4]['match_identity_exclusion'], 'unproven_independence')
        self.assertEqual(brain.economic_summary(rows, T0)['resolved_stake_units'], 2)

    def test_fixture_conflict_revealed_after_cutoff_does_not_join_prior_evidence(self):
        original = fixture('one', 5)
        alias = fixture('two', 6)
        late = fixture('two', 40, kickoff_utc=at(100))
        cutoff = T0 + timedelta(minutes=15)
        self.assertEqual(match_identity.annotate([original, alias], cutoff),
                         match_identity.annotate([original, alias, late], cutoff)[:2])
        self.assertIsNone(match_identity.annotate([original, alias, late])[-1]['canonical_match_id'])

    def test_study_and_benchmark_use_one_stake_when_alias_supplies_result(self):
        a = signal('original', teams=['A', 'B'])
        b = signal('alias', teams=['A ', 'B'], odds=4.0, collected_at_utc='2026-11-19T15:40:00Z')
        report = run([a, b], {'alias': result()})
        self.assertEqual(report['overlap']['shared_fixtures'], 1)
        for side in (report['ranking'][0], report['baseline']):
            self.assertEqual((side['forward_games'], side['resolved_stake_units'], side['profit_advertised']), (1, 1, 0.85))

    def test_partial_live_evidence_does_not_duplicate_exposure_or_controls(self):
        for kickoff in (at(60), None):
            with self.subTest(kickoff=kickoff):
                rows = [sig(mid, 1.0, 5 + i, kickoff_utc=kickoff, teams=['A', 'B'])
                        for i, mid in enumerate(['new', 'old'])]
                self.assertEqual(brain.economic_summary(rows, T0)['resolved_stake_units'], 1)
                self.assertEqual(len(llm_eval.admitted(rows, T0)), 1)

    def test_november_26_reuse_preserves_the_closed_win_on_november_27(self):
        first = signal('g')
        reused = signal('g', teams=['X', 'Y'], date_utc='2026-11-26T15:35:00Z',
                        collected_at_utc='2026-11-26T15:36:00Z', kickoff_utc='2026-11-26T18:00:00Z')
        evidence = {'g': [result(), result([0, 0], '2026-11-26T21:00:00Z')]}
        for now in ('2026-11-24T12:00:00Z', '2026-11-27T12:00:00Z'):
            ranked = run([reused, first], evidence, now=now)
            for side in (ranked['ranking'][0], ranked['baseline']):
                self.assertEqual((side['forward_games'], side['wins'], side['resolved_stake_units']), (1, 1, 1))
            self.assertEqual(ranked['identity_exclusions'], {'reused_match_id': 1} if '27T' in now else {})

        def provider(score, seen, kickoff):
            stamp = chronology.parse_utc(seen)
            return opportunity_results.Observation('HL', 'g', 'regulation', score, stamp, stamp,
                                                   kickoff=chronology.parse_utc(kickoff))

        observations = {'g': [provider((2, 1), '2026-11-19T20:05:00Z', first['kickoff_utc']),
                              provider((0, 0), '2026-11-26T21:00:00Z', reused['kickoff_utc'])]}
        rows = [dict(s, key=('g', 'Asian Total', 'Over 2.5'), in_roi=True, w=None,
                     result_known_source=None, signal_seen_utc=s['collected_at_utc']) for s in (reused, first)]
        now = chronology.parse_utc('2026-11-27T12:00:00Z')
        born = chronology.parse_utc('2026-11-19T15:20:00Z')
        research = list(report.research_bets(rows, observations, now))
        self.assertEqual(brain.economic_summary(research, born, now)['wins'], 1)
        self.assertEqual(list(llm_eval.admitted(research, born)), ['g'])

    def test_report_keeps_the_november_24_win_after_persisted_id_reuse(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(directory)}))

        def message(mid, day, teams):
            return {'message_id': mid, 'date_utc': f'2026-11-{day}T07:35:00Z',
                    'collected_at_utc': f'2026-11-{day}T07:36:00Z',
                    'text': '\n'.join((
                        f'📈 🔷 Polymarket · {day} nov 2026 13:00', f'{teams} · Example League',
                        'Over 2.5 @1.85 · $9.9K', '🧾 2 trades · market share 79%',
                        'https://scangoal.com/match/g?lang=en',
                    ))}

        def ft(day, score):
            stamp = f'2026-11-{day}T12:05:00Z'
            kickoff = f'2026-11-{day}T10:00:00Z'
            return {'ft': score, 'period': 'regulation', 'observed_at': stamp,
                    'available_at': stamp, 'kickoff_utc': kickoff,
                    'snapshot': {'league': 'Example League', 'home': 'Alpha' if day == 24 else 'Gamma',
                                 'away': 'Beta' if day == 24 else 'Delta', 'event_date': kickoff, 'ft': score}}

        original = message(1, 24, 'Alpha — Beta')
        collision = message(2, 26, 'Gamma — Delta')
        first, later = ft(24, [2, 1]), ft(26, [0, 0])
        born = chronology.parse_utc('2026-11-23T15:20:00Z')
        for messages, versions, cutoff in (
            ([original], [first], '2026-11-24T13:00:00Z'),
            ([original, collision], [first, later], '2026-11-27T12:00:00Z'),
        ):
            with self.subTest(cutoff=cutoff):
                (directory / 'messages.jsonl').write_text(
                    ''.join(json.dumps(row) + '\n' for row in messages), encoding='utf-8',
                )
                (directory / 'results_hl.json').write_text(
                    json.dumps({'ft_versions': {'g': versions}}), encoding='utf-8',
                )
                now = chronology.parse_utc(cutoff)
                page = report.build(now=now)
                bet, = report.LAST['research_signals']
                self.assertEqual((bet['ft'], bet['n_signals'], bet['w']), ([2, 1], 1, 1.0))
                self.assertEqual(chronology.result_known(bet), chronology.parse_utc(first['available_at']))
                economy = brain.economic_summary([bet], born, now)
                self.assertEqual((economy['admitted'], economy['resolved_stake_units'], economy['wins']), (1, 1, 1))
                self.assertEqual(len(llm_eval.admitted([bet], born)), 1)
                self.assertEqual(page['brain']['eligibility']['reasons'].get('reused_match_id', 0), len(messages) - 1)
