"""Exercise both provider archives, their collectors and the prospective ledger offline."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from itertools import permutations
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scangoal import opportunity_results as evidence
from scangoal import prospective, results_bsd, results_hl


KICKOFF = datetime(2031, 3, 1, 12, tzinfo=timezone.utc)
SIGNAL = KICKOFF + timedelta(minutes=30)
OBSERVED = KICKOFF + timedelta(hours=2)
MID = 'fixture'
SOURCES = (('bsd', 'BSD'), ('hl', 'Highlightly'))


def signal():
    return dict(match_id=MID, canonical_match_id=MID, type='OPEN_GAME', message_id=5,
                teams=['Alpha', 'Beta'], league='Italian Serie B', phase='live', minute=30,
                odds=1.9, market='Asian Total', selection='Over 2.5', score_at_signal=[1, 0], in_roi=True,
                kickoff_utc=KICKOFF.isoformat(), date_utc=SIGNAL.isoformat(), signal_seen_utc=SIGNAL.isoformat(),
                days=[KICKOFF.date().isoformat()])


def bsd_event(score, **changes):
    return dict(dict(id=17, home_team='Alpha', away_team='Beta', league_id=100, status='finished',
                     event_date=KICKOFF.isoformat(), home_score=score[0], away_score=score[1]), **changes)


def hl_event(score, **changes):
    return dict(dict(id=17, homeTeam={'name': 'Alpha'}, awayTeam={'name': 'Beta'},
                     league={'name': 'Serie B'}, date=KICKOFF.isoformat(),
                     state={'description': 'Finished', 'score': {'current': f'{score[0]}-{score[1]}'}}), **changes)


def binding(source, score):
    event = bsd_event(score) if source == 'bsd' else results_hl.to_event(hl_event(score))
    return results_bsd.match_events({MID: signal()}, [event], {})[0][MID]


class ProviderConflicts(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(self.root)}))

    def reset(self, *, register=True):
        prospective.ledger_path().unlink(missing_ok=True)
        for source, _ in SOURCES:
            (self.root / f'results_{source}.json').unlink(missing_ok=True)
        prospective.record(KICKOFF, signals=[])
        if register:
            prospective.record(SIGNAL, signals=[signal()])

    def save(self, source, state):
        (self.root / f'results_{source}.json').write_text(json.dumps(state))

    def archive(self, source, versions):
        state = {'results': {}}
        for stamp, score in versions:
            evidence.remember_provider_results(state, {MID: binding(source, score)}, stamp, update_snapshot=False)
        self.save(source, state)
        return state

    def outcomes(self):
        rows = prospective.load()
        self.assertTrue(prospective.verify(rows))
        return [(row['kind'], row['provider'], row['ft']) for row in rows if row['kind'] in ('outcome', 'outcome_conflict')]

    def assert_excluded(self):
        summary = prospective.summary(prospective.load())
        self.assertEqual(summary['games_with_conflicting_results'], 1)
        self.assertEqual(summary['games_with_independent_result'], 0)
        self.assertEqual(summary['H1']['games_with_result'], 0)
        self.assertEqual(summary['H2']['games_settled'], 0)

    def collect(self, source, raw, when=OBSERVED):
        calls = []
        def fetch(*args):
            calls.append(args)
            if source == 'bsd':
                return {'count': len(raw), 'next': None, 'results': raw}
            return {'data': raw, 'pagination': {'totalCount': len(raw)}}, 99
        collector = results_bsd if source == 'bsd' else results_hl
        with patch.object(collector, 'pending_targets', return_value={MID: signal()}):
            state = collector.run(fetch=fetch, now=when, token='offline-fixture')
        self.assertEqual(len(calls), 1)
        return state

    def test_first_conflict_through_each_collector_is_known_at_registration(self):
        for source, label in SOURCES:
            make_event = bsd_event if source == 'bsd' else hl_event
            outputs = []
            for pair in permutations(((2, 1), (3, 1))):
                with self.subTest(source=source, pair=pair):
                    self.reset(register=False)
                    invalid = (bsd_event((9, 0), status='awarded') if source == 'bsd'
                               else hl_event((9, 0), state={'description': 'After extra time'}))
                    state = self.collect(source, [make_event(pair[0]), invalid, make_event(pair[1])])
                    self.assertNotIn(MID, evidence.provider_snapshot(state, source, OBSERVED))
                    self.assertNotIn(MID, state['results'])
                    self.assertEqual({row.score for row in evidence.provider_observations(state, source)[MID]},
                                     {(2, 1), (3, 1)})
                    prospective.record(OBSERVED, signals=[signal()])
                    entry = next(row for row in prospective.load() if row['kind'] == 'signal')
                    self.assertTrue(entry['result_known_at_registration'])
                    outcomes = self.outcomes()
                    self.assertCountEqual(outcomes, [('outcome_conflict', label, [2, 1]),
                                                     ('outcome_conflict', label, [3, 1])])
                    self.assert_excluded()
                    outputs.append(prospective.ledger_path().read_bytes())
                    prospective.record(OBSERVED, signals=[signal()])
                    self.assertEqual(prospective.ledger_path().read_bytes(), outputs[-1])
            self.assertEqual(*outputs)

    def test_intermediate_contradiction_hidden_by_latest_score_is_never_lost(self):
        for source, label in SOURCES:
            with self.subTest(source=source):
                self.reset()
                later = OBSERVED + timedelta(hours=1)
                final = later + timedelta(hours=1)
                state = self.archive(source, [(OBSERVED, (2, 1)), (later, (3, 1)), (final, (2, 1))])
                self.assertEqual(evidence.provider_snapshot(state, source, final)[MID]['ft'], [2, 1])
                prospective.record(final, signals=[])
                self.assertCountEqual(self.outcomes(), [('outcome_conflict', label, [2, 1]),
                                                        ('outcome_conflict', label, [3, 1])])
                self.assert_excluded()
                self.save(source, {'results': {}})
                prefix = prospective.ledger_path().read_bytes()
                prospective.record(final + timedelta(hours=75), signals=[])
                self.assertEqual(prospective.ledger_path().read_bytes(), prefix)

    def test_existing_confirmation_survives_simultaneous_versions_and_conflict_is_sticky(self):
        for source, label in SOURCES:
            make_event = bsd_event if source == 'bsd' else hl_event
            outputs = []
            for pair in permutations(((2, 1), (3, 1))):
                with self.subTest(source=source, pair=pair):
                    self.reset()
                    state = self.collect(source, [make_event((2, 1))])
                    prospective.record(OBSERVED, signals=[])
                    prefix = prospective.ledger_path().read_bytes()
                    stamp = OBSERVED + timedelta(hours=1)
                    raw = [make_event(score) for score in pair]
                    if source == 'hl':
                        raw = list(map(results_hl.to_event, raw))
                    results_bsd.capture_result_versions(state, {}, raw, stamp)
                    self.assertEqual(state['results'][MID]['ft'], [2, 1])
                    self.assertNotIn(MID, evidence.provider_snapshot(state, source, stamp))
                    third = stamp + timedelta(hours=1)
                    evidence.remember_provider_results(state, {MID: binding(source, (2, 1))}, third)
                    self.save(source, state)
                    prospective.record(stamp, signals=[])
                    self.assertTrue(prospective.ledger_path().read_bytes().startswith(prefix))
                    self.assertEqual(self.outcomes(), [('outcome', label, [2, 1]),
                                                       ('outcome_conflict', label, [3, 1])])
                    self.assert_excluded()
                    outputs.append(prospective.ledger_path().read_bytes())
                    prospective.record(third, signals=[])
                    self.assertEqual(prospective.ledger_path().read_bytes(), outputs[-1])
            self.assertEqual(*outputs)

    def test_cutoff_and_invalid_versions_apply_to_both_providers(self):
        for source, label in SOURCES:
            with self.subTest(source=source):
                self.reset()
                later = OBSERVED + timedelta(hours=1)
                state = self.archive(source, [(OBSERVED, (2, 1)), (OBSERVED, (3, 1)), (OBSERVED, (4, 1)),
                                              (OBSERVED, (5, 1)), (OBSERVED, (6, 1))])
                versions = state['ft_versions'][MID]
                versions[1]['available_at'] = later.isoformat()
                versions[2]['snapshot']['fixture_id'] = 'other'
                versions[3]['snapshot']['league'] = 'Serie A'
                versions[4]['snapshot']['period'] = 'extra time'
                self.save(source, state)
                prospective.record(OBSERVED, signals=[])
                self.assertEqual(self.outcomes(), [('outcome', label, [2, 1])])
                prospective.record(later, signals=[])
                self.assertEqual(self.outcomes(), [('outcome', label, [2, 1]), ('outcome_conflict', label, [3, 1])])
                self.assert_excluded()

    def test_preexisting_first_snapshot_remains_proof_across_history_upgrade(self):
        for source, label in SOURCES:
            with self.subTest(source=source):
                self.reset()
                row = dict(binding(source, (2, 1)), matched_at_utc=OBSERVED.isoformat())
                state = {'results': {MID: row}}
                later = OBSERVED + timedelta(hours=1)
                evidence.remember_provider_results(state, {MID: binding(source, (3, 1))}, later)
                self.save(source, state)
                prospective.record(OBSERVED, signals=[])
                self.assertEqual(self.outcomes(), [('outcome', label, [2, 1])])
                prospective.record(later, signals=[])
                self.assertEqual(self.outcomes(), [('outcome', label, [2, 1]), ('outcome_conflict', label, [3, 1])])

    def test_different_provider_scores_are_explicit_conflict_without_first_confirmation(self):
        for score, expected in (((3, 1), 'outcome_conflict'), ((2, 1), 'outcome')):
            with self.subTest(highlightly=score):
                self.reset()
                self.archive('bsd', [(OBSERVED, (2, 1))])
                self.archive('hl', [(OBSERVED, score)])
                prospective.record(OBSERVED, signals=[])
                self.assertEqual(self.outcomes(), [(expected, 'BSD', [2, 1]), (expected, 'Highlightly', list(score))])
                if expected == 'outcome_conflict':
                    self.assert_excluded()
                else:
                    self.assertEqual(prospective.summary(prospective.load())['games_with_independent_result'], 1)

    def test_damaged_or_unproven_versions_do_not_mark_a_signal_as_known(self):
        for source, _ in SOURCES:
            with self.subTest(source=source):
                self.reset(register=False)
                state = self.archive(source, [(OBSERVED, (2, 1)), (OBSERVED, (3, 1))])
                for row in state['ft_versions'][MID]:
                    row['snapshot']['league'] = None
                    row['snapshot']['identity_method'] = 'provider_id'
                self.save(source, state)
                prospective.record(OBSERVED, signals=[signal()])
                self.assertFalse(prospective.load()[-1]['result_known_at_registration'])
                self.assertEqual(self.outcomes(), [])

    def test_two_legacy_outcomes_gain_explicit_conflict_without_rewriting_either(self):
        self.reset()
        writer = prospective.Writer(prospective.ledger_path(), prospective.load())
        for provider, ft in (('BSD', [2, 1]), ('Highlightly', [3, 1])):
            writer.append('outcome', at_utc=OBSERVED.isoformat(), match_id=MID, provider=provider, ft=ft)
        prefix = prospective.ledger_path().read_bytes()
        prospective.record(OBSERVED, signals=[])
        after = prospective.ledger_path().read_bytes()
        self.assertTrue(after.startswith(prefix))
        legacy = [row for row in prospective.load() if row['kind'] == 'outcome']
        conflicts = [row for row in prospective.load() if row['kind'] == 'outcome_conflict']
        self.assertEqual([row['prior_outcome_sha256'] for row in conflicts], [row['sha256'] for row in legacy])
        self.assert_excluded()
        prospective.record(OBSERVED, signals=[])
        self.assertEqual(prospective.ledger_path().read_bytes(), after)

    def test_stale_ledger_cutoff_refuses_append_without_changing_prefix(self):
        self.reset()
        prefix = prospective.ledger_path().read_bytes()
        with self.assertRaisesRegex(prospective.LedgerError, 'invalid_cutoff_for_ledger_head'):
            prospective.record(KICKOFF, signals=[])
        self.assertEqual(prospective.ledger_path().read_bytes(), prefix)

    def test_reference_changes_create_a_version_without_rewriting_activation(self):
        self.reset()
        before = prospective.ledger_path().read_bytes()
        activation = deepcopy(prospective.load()[0])
        self.assertEqual(activation['code_manifest_schema'], 2)
        self.assertIn('report.py', activation['code_sha256'])
        self.assertIsNone(activation['reference_sha256']['team_aliases.json'])
        (self.root / 'team_aliases.json').write_text('{}')
        prospective.record(OBSERVED, signals=[])
        self.assertEqual(prospective.load()[0], activation)
        row = prospective.load()[-1]
        self.assertEqual(row['kind'], 'code_version')
        self.assertIsNotNone(row['reference_sha256']['team_aliases.json'])
        self.assertTrue(prospective.ledger_path().read_bytes().startswith(before))
        after = prospective.ledger_path().read_bytes()
        prospective.record(OBSERVED, signals=[])
        self.assertEqual(prospective.ledger_path().read_bytes(), after)
