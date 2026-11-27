from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scangoal import opportunity_results, prospective, results_bsd, results_hl


KICKOFF = datetime(2026, 11, 26, 17, 45, tzinfo=timezone.utc)
NOW = KICKOFF + timedelta(hours=17)
DAY = KICKOFF.date().isoformat()
NORMAL = 'ordinary-fixture'
ABSENT = 'registered-after-two-complete-misses'


def fixture(home, away):
    return dict(teams=[home, away], league='UEFA Europa League', phase='pre',
                kickoff_utc=KICKOFF.isoformat(), date_utc=KICKOFF.isoformat(), days=[DAY])


def event(identifier, target, score='2-1', **changes):
    home, away = target['teams']
    return dict(dict(id=identifier, date=target['kickoff_utc'], league={'name': target['league']},
                     homeTeam={'name': home}, awayTeam={'name': away},
                     state={'description': 'Finished', 'score': {'current': score}}), **changes)


class HighlightlyProspectiveRecheck(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        self.archive = directory / 'results_hl.json'
        self.enterContext(patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(directory)}))
        self.enterContext(patch.object(results_hl, 'MIN_INTERVAL_MIN', 90))
        self.enterContext(patch.object(results_hl, 'MAX_REQUESTS', 1))
        self.enterContext(patch.object(results_hl, 'urlopen', side_effect=AssertionError('network forbidden')))
        self.stamp = (NOW - timedelta(hours=2)).isoformat()
        self.archive.write_text(json.dumps({'results': {}, 'misses': {ABSENT: 2},
                                            'not_covered': {ABSENT: self.stamp}, 'last_run_utc': self.stamp}))
        self.normal = fixture('Ordinary Home', 'Ordinary Away')
        self.absent = fixture('Registered Home', 'Registered Away')

    def collect(self, events, *, normal=True, registered=True, total=None, remaining=90, when=NOW):
        calls = []
        targets = {ABSENT: self.absent}
        if normal:
            targets[NORMAL] = self.normal
        def fetch(params, token):
            calls.append(params)
            return {'data': events, 'pagination': {'totalCount': len(events) if total is None else total}}, remaining
        with patch.object(results_hl, 'pending_targets', return_value=targets), \
                patch('scangoal.prospective.registered_matches', return_value={ABSENT} if registered else set()), \
                redirect_stdout(StringIO()):
            state = results_hl.run(fetch=fetch, now=when, token='offline-fixture')
        self.assertEqual(calls, [{'date': DAY, 'limit': results_hl.PAGE_SIZE, 'offset': 0}] if normal else [])
        self.assertEqual(state['stats']['requests'], len(calls))
        return state

    def test_recovers_an_independent_score_from_an_already_requested_complete_day(self):
        state = self.collect([event(101, self.normal), event(102, self.absent)])
        self.assertEqual(set(state['results']), {NORMAL, ABSENT})
        self.assertEqual((state['stats']['rechecked_prospective'], state['stats']['matched_rechecks']), (1, 1))
        self.assertNotIn(ABSENT, state['not_covered'])
        self.assertEqual(state['snapshots'][-1]['not_covered'][ABSENT], self.stamp)
        self.assertEqual(state['misses'][ABSENT], 2)
        bound = opportunity_results.provider_records(state, 'hl', NOW)[ABSENT][0]
        self.assertEqual((bound['ft'], bound['bsd_id'], bound['league']), ([2, 1], 102, 'UEFA Europa League'))

    def test_recheck_never_causes_its_own_request(self):
        state = self.collect([event(102, self.absent)], normal=False)
        self.assertNotIn(ABSENT, state['results'])

    def test_partial_day_does_not_reopen_even_with_a_candidate(self):
        state = self.collect([event(101, self.normal), event(102, self.absent)], total=3, remaining=14)
        self.assertEqual(set(state['results']), {NORMAL})
        self.assertEqual(state['not_covered'][ABSENT], self.stamp)
        self.assertEqual(state['stats']['stopped'], 'max_requests')
        self.assertEqual(state['stats']['rechecked_prospective'], 0)

    def test_unregistered_missing_fixture_is_left_in_archive(self):
        state = self.collect([event(101, self.normal), event(102, self.absent)], registered=False)
        self.assertEqual(set(state['results']), {NORMAL})

    def test_fails_closed_for_doubtful_or_non_regulation_events(self):
        raw = self.archive.read_bytes()
        doubtful = (
            [event(102, self.absent, league={})],
            [event(102, self.absent, league={'name': 'Italian Serie B'})],
            [event(102, self.absent, date=(KICKOFF + timedelta(minutes=16)).isoformat())],
            [event(102, self.absent, awayTeam={'name': 'Another team'})],
            [event(102, self.absent, state={'description': 'Finished after extra time', 'score': {'current': '3-2'}})],
            [event(102, self.absent), event(102, self.absent, '1-1')],
            [event(102, self.absent), event(103, self.absent)],
        )
        for variants in doubtful:
            with self.subTest(variants=variants):
                self.archive.write_bytes(raw)
                state = self.collect([event(101, self.normal), *variants])
                self.assertEqual(set(state['results']), {NORMAL})
                self.assertIn(ABSENT, state['not_covered'])

    def test_keeps_ordinary_result_when_reopened_target_claims_its_provider_id(self):
        self.absent = self.normal
        state = self.collect([event(101, self.normal)])
        self.assertEqual(set(state['results']), {NORMAL})
        self.assertIn(ABSENT, state['not_covered'])

    def test_does_not_reuse_an_id_already_archived_for_a_different_fixture(self):
        archive = json.loads(self.archive.read_bytes())
        historical_id = 'historical-fixture'
        historical = fixture('Historical Home', 'Historical Away')
        binding = results_bsd.event_binding(historical_id, historical, results_hl.to_event(event(102, historical)))
        opportunity_results.remember_provider_results(
            archive, {historical_id: dict(binding, ft=[0, 2])}, datetime.fromisoformat(self.stamp))
        self.archive.write_text(json.dumps(archive))
        state = self.collect([event(101, self.normal), event(102, self.absent)])
        self.assertEqual(set(state['results']), {NORMAL, historical_id})
        self.assertEqual(state['results'][historical_id], archive['results'][historical_id])
        self.assertEqual(state['ft_versions'][historical_id], archive['ft_versions'][historical_id])
        self.assertIn(ABSENT, state['not_covered'])

    def test_preserves_rechecked_conflict_after_a_later_single_score(self):
        seen = KICKOFF - timedelta(minutes=15)
        signal = dict(self.absent, match_id=ABSENT, type='OPEN_GAME', in_roi=True, odds=1.9,
                      market='Asian Total', selection='Over 2.5', signal_seen_utc=seen.isoformat())
        self.assertEqual(prospective.record(seen, signals=[signal]), 1)
        ledger_prefix = prospective.ledger_path().read_bytes()
        pending = event(101, self.normal, state={'description': 'Live'})
        first = self.collect([pending, event(102, self.absent), event(102, self.absent, '1-1')])
        self.assertNotIn(ABSENT, first['results'])
        history = first['ft_versions'][ABSENT]
        self.assertCountEqual([row['ft'] for row in history], [[2, 1], [1, 1]])

        later = NOW + timedelta(hours=2)
        second = self.collect([pending, event(102, self.absent)], when=later)
        self.assertEqual(second['ft_versions'][ABSENT], history)
        self.assertNotIn(ABSENT, second['results'])
        self.assertIn(ABSENT, second['not_covered'])
        self.assertNotIn(ABSENT, opportunity_results.provider_snapshot(second, 'hl', later))

        prospective.record(later, signals=[])
        entries = prospective.load()
        outcomes = [row for row in entries if row.get('match_id') == ABSENT and row['kind'] != 'signal']
        self.assertCountEqual([(row['kind'], row['ft']) for row in outcomes],
                              [('outcome_conflict', [2, 1]), ('outcome_conflict', [1, 1])])
        self.assertEqual({row['provider_observed_utc'] for row in outcomes}, {NOW.isoformat()})
        self.assertTrue(prospective.verify(entries))
        self.assertEqual(prospective.summary(entries)['games_with_independent_result'], 0)
        ledger = prospective.ledger_path().read_bytes()
        self.assertTrue(ledger.startswith(ledger_prefix))
        prospective.record(later, signals=[])
        self.assertEqual(prospective.ledger_path().read_bytes(), ledger)

    def test_replaying_rechecked_conflict_at_same_cut_is_idempotent(self):
        pending = event(101, self.normal, state={'description': 'Live'})
        rows = [pending, event(102, self.absent), event(102, self.absent, '1-1')]
        first = self.collect(rows)
        with patch.object(results_hl, 'MIN_INTERVAL_MIN', 0):
            repeated = self.collect(list(reversed(rows)))
        self.assertEqual(len(first['ft_versions'][ABSENT]), 2)
        self.assertEqual(repeated['ft_versions'], first['ft_versions'])

    def test_archives_a_new_score_without_consolidating_a_rechecked_conflict(self):
        seen = KICKOFF - timedelta(minutes=15)
        signal = dict(self.absent, match_id=ABSENT, type='OPEN_GAME', in_roi=True, odds=1.9,
                      market='Asian Total', selection='Over 2.5', signal_seen_utc=seen.isoformat())
        self.assertEqual(prospective.record(seen, signals=[signal]), 1)
        pending = event(101, self.normal, state={'description': 'Live'})
        first = self.collect([pending, event(102, self.absent), event(102, self.absent, '1-1')])
        prospective.record(NOW, signals=[])
        prefix = prospective.ledger_path().read_bytes()

        later = NOW + timedelta(hours=2)
        second = self.collect([pending, event(102, self.absent, '4-0')], when=later)
        versions = second['ft_versions'][ABSENT]
        self.assertEqual(versions[:-1], first['ft_versions'][ABSENT])
        self.assertEqual(versions[-1]['ft'], [4, 0])
        self.assertEqual(versions[-1]['observed_at'], later.isoformat())
        self.assertEqual(versions[-1]['available_at'], later.isoformat())
        self.assertNotIn(ABSENT, second['results'])
        self.assertIn(ABSENT, second['not_covered'])
        self.assertEqual(len(opportunity_results.provider_records(second, 'hl', NOW)[ABSENT]), 2)
        self.assertEqual(len(opportunity_results.provider_records(second, 'hl', later)[ABSENT]), 3)

        observations = opportunity_results.merge_sources({}, {'highlightly': second})[ABSENT]
        for cutoff in (NOW, later):
            self.assertNotIn(ABSENT, opportunity_results.provider_snapshot(second, 'hl', cutoff))
            settled = opportunity_results.settle_research_bet(dict(signal, _ft_observations=observations), cutoff)
            self.assertTrue(settled['result_quarantined'])
            self.assertIsNone(settled['ft'])
            self.assertIsNone(settled['w'])

        prospective.record(later, signals=[])
        entries = prospective.load()
        self.assertTrue(prospective.verify(entries))
        outcomes = [row for row in entries if row.get('match_id') == ABSENT and row['kind'] != 'signal']
        self.assertCountEqual([(row['kind'], row['ft'], row['provider_observed_utc']) for row in outcomes],
                              [('outcome_conflict', [2, 1], NOW.isoformat()),
                               ('outcome_conflict', [1, 1], NOW.isoformat()),
                               ('outcome_conflict', [4, 0], later.isoformat())])
        self.assertEqual(prospective.summary(entries)['games_with_independent_result'], 0)
        ledger = prospective.ledger_path().read_bytes()
        self.assertTrue(ledger.startswith(prefix))
        for hours, scores in ((4, ['4-0']), (6, ['4-0', '1-1'])):
            time = NOW + timedelta(hours=hours)
            repeated = self.collect([pending, *[event(102, self.absent, score) for score in scores]], when=time)
            self.assertEqual(repeated['ft_versions'][ABSENT], versions)
            prospective.record(time, signals=[])
            self.assertEqual(prospective.ledger_path().read_bytes(), ledger)

    def test_incomplete_day_does_not_archive_rechecked_conflict(self):
        state = self.collect([event(101, self.normal), event(102, self.absent), event(102, self.absent, '1-1')],
                             total=4, remaining=14)
        self.assertEqual(set(state['results']), {NORMAL})
        self.assertNotIn(ABSENT, state['ft_versions'])
        self.assertIn(ABSENT, state['not_covered'])

    def test_recheck_collision_does_not_discard_ordinary_conflict(self):
        self.absent = self.normal
        state = self.collect([event(101, self.normal), event(101, self.normal, '1-1')])
        self.assertEqual(state['results'], {})
        self.assertEqual(set(state['ft_versions']), {NORMAL})
        self.assertCountEqual([row['ft'] for row in state['ft_versions'][NORMAL]], [[2, 1], [1, 1]])

    def test_rejects_id_held_only_by_archived_conflicting_versions(self):
        archive = json.loads(self.archive.read_bytes())
        historical = fixture('Historical Home', 'Historical Away')
        binding = results_bsd.event_binding('historical', historical, results_hl.to_event(event(102, historical)))
        for score in ([2, 1], [1, 1]):
            opportunity_results.remember_provider_results(
                archive, {'historical': dict(binding, ft=score)}, datetime.fromisoformat(self.stamp),
                update_snapshot=False)
        for target in (self.normal, self.absent):
            for scores in (('2-1',), ('2-1', '1-1')):
                with self.subTest(target=target['teams'], scores=scores):
                    self.archive.write_text(json.dumps(archive))
                    state = self.collect([event(102, target, score) for score in scores])
                    self.assertEqual(state['results'], {})
                    self.assertEqual(state['ft_versions'], archive['ft_versions'])
                    self.assertIn(ABSENT, state['not_covered'])

    def test_any_other_archived_owner_blocks_binding(self):
        archive = json.loads(self.archive.read_bytes())
        for mid in ('other-owner', ABSENT):
            binding = results_bsd.event_binding(mid, self.absent, results_hl.to_event(event(102, self.absent)))
            opportunity_results.remember_provider_results(
                archive, {mid: dict(binding, ft=[2, 1])}, datetime.fromisoformat(self.stamp),
                update_snapshot=False)
        self.archive.write_text(json.dumps(archive))
        state = self.collect([event(101, self.normal), event(102, self.absent)])
        self.assertNotIn(ABSENT, state['results'])
        self.assertEqual(state['ft_versions'][ABSENT], archive['ft_versions'][ABSENT])
        self.assertEqual(state['ft_versions']['other-owner'], archive['ft_versions']['other-owner'])


if __name__ == '__main__':
    unittest.main()
