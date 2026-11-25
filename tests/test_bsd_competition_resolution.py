"""Exercise BSD v2 competition evidence without contacting the provider."""

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from scangoal import opportunity_results, prospective, report, results_bsd as bsd


KICKOFF = datetime(2026, 11, 24, 19, tzinfo=timezone.utc)
OBSERVED = KICKOFF + timedelta(hours=3)
FIXTURE = 'test-bsd-fixture'


def target(**overrides):
    return dict({
        'match_id': FIXTURE,
        'teams': ['Alpha', 'Beta'],
        'league': 'Italy Serie B',
        'kickoff_utc': KICKOFF.isoformat(),
        'date_utc': (KICKOFF - timedelta(hours=1)).isoformat(),
        'phase': 'pre',
        'days': [KICKOFF.date().isoformat()],
    }, **overrides)


def event(**overrides):
    return dict({
        'id': 610001,
        'home_team_id': 1001,
        'home_team': 'Alpha FC',
        'away_team_id': 1002,
        'away_team': 'Beta',
        'event_date': KICKOFF.isoformat(),
        'status': 'finished',
        'home_score': 2,
        'away_score': 1,
        'league_id': 100,
    }, **overrides)


class BSDCompetitionResolution(unittest.TestCase):
    def match(self, events, *, fixture=None, aliases=None):
        return bsd.match_events({FIXTURE: target() if fixture is None else fixture}, events, aliases or {})[0]

    def test_numeric_league_has_its_own_provenance(self):
        row = self.match([event()])[FIXTURE]
        self.assertEqual(row['bsd_id'], event()['id'])
        self.assertEqual(row['bsd_league_id'], 100)
        self.assertEqual(row['league'], 'Serie B')
        self.assertEqual(row['target_league'], 'Italy Serie B')
        self.assertEqual(row['identity_method'], 'bsd_league_id_participants_kickoff')
        self.assertTrue(row['competition_provenance'])
        self.assertEqual(row['ft'], [2, 1])

    def test_alias_requires_a_reviewed_exact_name(self):
        labeled = event(home_team={'id': 1001, 'name': 'Football Club Alpha'})
        self.assertEqual(self.match([labeled]), {})
        self.assertIn(FIXTURE, self.match([labeled], aliases={'alpha': ['Football Club Alpha']}))

    def test_team_identity_rejects_rotation_homonyms_and_squad_suffixes(self):
        wrong_home_names = ['Alpha City', 'Alpha U19', 'Alpha U21', 'Alpha Women', 'Alpha Reserves', 'Alpha II']
        for home in wrong_home_names:
            with self.subTest(home=home):
                self.assertEqual(self.match([event(home_team={'id': 1001, 'name': home})]), {})
        self.assertEqual(self.match([event(home_team='Beta', away_team='Alpha')]), {})

    def test_competition_must_match_even_with_previously_bound_event_id(self):
        for league in ('Italy Serie C', 'Portugal Serie B', 'Italy Serie B Women', 'Italy Serie B U19', 'Serie'):
            with self.subTest(league=league):
                self.assertEqual(self.match([event()], fixture=target(league=league)), {})
        bound = target(provider_id=event()['id'])
        self.assertEqual(self.match([event(league_id=7)], fixture=bound), {})
        self.assertEqual(self.match([event(league={'id': 101, 'name': 'Serie B'})], fixture=bound), {})
        self.assertEqual(self.match([event(league_id=86)], fixture=target(league='Scotland League One')), {})

    def test_missing_or_invalid_competition_cannot_turn_candidate_id_into_a_binding(self):
        for league_id in (None, 987654321, '100', True):
            with self.subTest(league_id=league_id):
                self.assertEqual(self.match([event(league_id=league_id)]), {})
        bare = event(league_id=None)
        self.assertIn(FIXTURE, self.match([bare], fixture=target(provider_id=bare['id'])))
        self.assertEqual(self.match([bare], fixture=target(provider_id=bare['id'] + 1)), {})

    def test_explicit_kickoff_with_inclusive_fifteen_minute_boundary(self):
        for offset, eligible in ((-16, False), (-15, True), (15, True), (16, False)):
            with self.subTest(offset=offset):
                shifted = event(event_date=(KICKOFF + timedelta(minutes=offset)).isoformat())
                self.assertEqual(bool(self.match([shifted])), eligible)
        live = target(kickoff_utc=None, phase='live', minute=30, date_utc=(KICKOFF + timedelta(minutes=30)).isoformat())
        self.assertEqual(self.match([event()], fixture=live), {})
        self.assertEqual(self.match([event(event_date=KICKOFF.replace(tzinfo=None).isoformat())]), {})

    def test_two_plausible_event_ids_never_settle_regardless_of_score(self):
        for alternate_score in (2, 4):
            with self.subTest(alternate_score=alternate_score):
                other = event(id=event()['id'] + 1, home_score=alternate_score)
                self.assertEqual(self.match([event(), other]), {})

    def test_unknown_alternative_blocks_binding_but_other_verified_league_does_not(self):
        other = event(id=event()['id'] + 1, league_id=None)
        self.assertEqual(self.match([event(), other]), {})
        self.assertIn(FIXTURE, self.match([event(), dict(other, league_id=7)]))

    def test_one_bsd_id_cannot_resolve_two_local_targets(self):
        found, _ = bsd.match_events({FIXTURE: target(), 'other': target(match_id='other')}, [event()], {})
        self.assertEqual(found, {})

    def test_extra_time_penalties_and_unreliable_finish_are_not_settled(self):
        for change in ({'status': 'aet'}, {'status': 'penalties'}, {'status': 'awarded'},
                       {'extra_time_score': {'home': 0, 'away': 1}},
                       {'penalty_shootout': {'home': 4, 'away': 3}}, {'abandoned': True},
                       {'cancelled': True}, {'postponed': True}, {'home_score': True}):
            with self.subTest(change=change):
                self.assertEqual(self.match([event(**change)]), {})

    def test_report_rechecks_bsd_proof_independently_from_target_label(self):
        row = self.match([event()])[FIXTURE]
        self.assertTrue(report.external_matches(target(), row))
        for update in ({'league': None}, {'league': 'Serie A'}, {'league': row['target_league']},
                       {'bsd_league_id': 987654321},
                       {'competition_provenance': None}, {'identity_method': None}, {'fixture_id': 'another-fixture'},
                       {'event_date': (KICKOFF + timedelta(minutes=16)).isoformat()}):
            with self.subTest(update=update):
                self.assertFalse(report.external_matches(target(), dict(row, **update)))

    def collect(self, events, *, truncated=False):
        calls = []

        def fetch(url, _token):
            calls.append(url)
            return {'count': len(events) + int(truncated), 'results': events,
                    'next': bsd.ROOT + '?offset=1' if truncated else None}

        with TemporaryDirectory() as folder, patch.object(bsd, 'data_dir', return_value=Path(folder)), \
                patch.object(bsd, 'pending_targets', return_value={FIXTURE: target()}), redirect_stdout(StringIO()):
            path = Path(folder, 'results_bsd.json')
            archive = {'results': {'legacy': {'bsd_id': 50, 'ft': [1, 0]}}, 'near_misses': {}, 'last_run_utc': None}
            path.write_text(json.dumps(archive), encoding='utf-8')
            bsd.run(fetch=fetch, now=OBSERVED, token='test-token')
            saved = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(urlsplit(calls[0]).path, '/api/v2/events/')
        self.assertEqual(parse_qs(urlsplit(calls[0]).query)['status'], ['finished'])
        self.assertEqual(saved['results']['legacy'], archive['results']['legacy'])
        return saved

    def test_normal_collection_archives_proof_without_additional_requests(self):
        saved = self.collect([event(), event()])
        result = opportunity_results.provider_snapshot(saved, 'bsd', OBSERVED)[FIXTURE]
        self.assertEqual(result['ft'], [2, 1])
        self.assertTrue(report.external_matches(target(), result))

    def test_truncated_page_cannot_prove_a_unique_new_binding(self):
        with patch.object(bsd, 'MAX_REQUESTS', 1):
            saved = self.collect([event()], truncated=True)
        self.assertTrue(saved['stats']['page_cap_reached'])
        self.assertNotIn(FIXTURE, saved['results'])

    def test_same_event_with_contradictory_scores_does_not_publish_an_arbitrary_result(self):
        for variants in ([event(), event(home_score=3)], [event(home_score=3), event()]):
            with self.subTest(first=variants[0]['home_score']):
                saved = self.collect(variants)
                self.assertNotIn(FIXTURE, opportunity_results.provider_snapshot(saved, 'bsd', OBSERVED))

    def test_later_revision_requires_compatible_competition_or_previously_bound_id(self):
        raw = event(league_id=13)
        found = self.match([raw], fixture=target(league='Scottish Premiership'))
        state = {'results': {}}
        bsd.capture_result_versions(state, found, [raw], OBSERVED)
        later = OBSERVED + timedelta(hours=1)
        for update in ({'league_id': None, 'league': 'Scottish Premiership Women'},
                       {'league_id': 7}, {'league_id': 13, 'competition': {'id': 7, 'name': 'Champions League'}}):
            with self.subTest(update=update):
                bsd.capture_result_versions(state, {}, [dict(raw, home_score=3, **update)], later)
                self.assertEqual([row['ft'] for row in state['ft_versions'][FIXTURE]], [[2, 1]])
        bsd.capture_result_versions(state, {}, [dict(raw, home_score=3, league_id=None)], later)
        self.assertEqual([row['ft'] for row in state['ft_versions'][FIXTURE]], [[2, 1], [3, 1]])

    def test_later_bsd_score_change_appends_conflict_without_rewriting_first(self):
        signal_time = KICKOFF + timedelta(minutes=30)
        signal = target(phase='live', date_utc=signal_time.isoformat(), signal_seen_utc=signal_time.isoformat(),
                        type='OPEN_GAME', market='Asian Total', selection='Over 2.5', odds=1.9,
                        minute=30, score_at_signal=[1, 0], message_id=1)
        first, changed = [self.match([event(home_score=score)], fixture=signal)[FIXTURE] for score in (2, 3)]
        with TemporaryDirectory() as folder, patch.dict(os.environ, {'SCANGOAL_DATA_DIR': folder}), \
                patch('scangoal.eligibility.eligible', return_value=True):
            prospective.record(KICKOFF, signals=[], providers={})
            prospective.record(signal_time, signals=[signal], providers={})
            prospective.record(OBSERVED, signals=[], providers={'BSD': {FIXTURE: first}})
            path = Path(folder, prospective.LEDGER_NAME)
            first_bytes = path.read_bytes()
            prospective.record(OBSERVED + timedelta(hours=1), signals=[], providers={'BSD': {FIXTURE: changed}})
            self.assertTrue(path.read_bytes().startswith(first_bytes))
            entries = prospective.load()
        self.assertTrue(prospective.verify(entries))
        facts = [(entry['kind'], entry['ft']) for entry in entries if entry['kind'].startswith('outcome')]
        self.assertEqual(facts, [('outcome', [2, 1]), ('outcome_conflict', [3, 1])])
        self.assertEqual(prospective.summary(entries)['H1']['games_with_result'], 0)
