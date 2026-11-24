"""B1 (independent result binding) and B4 (append-only prospective ledger)."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scangoal import prospective as P
from scangoal import results_bsd as R

UTC = timezone.utc
T0 = datetime(2031, 3, 1, 12, tzinfo=UTC)


def _event(i, home, away, kickoff, league='La Liga', status='finished', score=(1, 0)):
    return dict(id=i, home_team=home, away_team=away, event_date=kickoff.isoformat(), status=status,
                home_score=score[0], away_score=score[1], league=league)


def _target(league='Spanish La Liga Primera', kickoff=T0):
    return {'teams': ['Levante', 'Elche'], 'date_utc': (kickoff - timedelta(hours=1)).isoformat(),
            'kickoff_utc': kickoff.isoformat(), 'league': league, 'phase': 'pre', 'minute': None, 'days': []}


class B1Binding(unittest.TestCase):
    def test_league_names_written_differently(self):
        n = R.norm_team
        self.assertTrue(R.leagues_compatible(n('Spanish La Liga Primera'), n('La Liga')))
        self.assertTrue(R.leagues_compatible(n('England Premier League'), n('Premier League')))
        self.assertFalse(R.leagues_compatible(n('Danish Super League'), n('Superliga')))
        self.assertFalse(R.leagues_compatible(n('Italian Serie C'), n('Serie A')))
        self.assertFalse(R.leagues_compatible(n('England Premier League'), n('League')))

    def test_unique_exact_fixture_is_bound_and_persists_target_identity(self):
        found, near = R.match_events({'m': _target()}, [_event(7, 'Levante', 'Elche', T0)], {})
        self.assertIn('m', found)
        row = found['m']
        self.assertEqual(row['ft'], [1, 0])
        self.assertEqual(row['league'], 'La Liga')                     # provider identity preserved
        self.assertEqual(row['target_league'], 'Spanish La Liga Primera')
        self.assertEqual(row['target_teams'], ['Levante', 'Elche'])
        self.assertEqual(row['target_kickoff_utc'], T0.isoformat())
        self.assertEqual(row['bsd_id'], 7)
        self.assertEqual(row['identity_method'], 'competition_participants_kickoff')

    def test_ambiguous_or_incompatible_fixtures_are_not_bound(self):
        two = [_event(1, 'Levante', 'Elche', T0), _event(2, 'Levante', 'Elche', T0 + timedelta(minutes=5))]
        self.assertEqual(R.match_events({'m': _target()}, two, {})[0], {})
        other = [_event(3, 'Levante', 'Elche', T0, league='Segunda Division')]
        found, near = R.match_events({'m': _target()}, other, {})
        self.assertEqual(found, {})
        self.assertIn('m', near)
        similar = [_event(4, 'Levante UD', 'Elche CF Ilicitano', T0)]
        self.assertEqual(R.match_events({'m': _target()}, similar, {})[0], {})
        late = [_event(5, 'Levante', 'Elche', T0 + timedelta(minutes=40))]
        self.assertEqual(R.match_events({'m': _target()}, late, {})[0], {})

    def test_provider_without_league_is_not_bound_on_names_and_kickoff_alone(self):
        bare = [_event(6, 'Levante', 'Elche', T0, league=None)]
        self.assertEqual(R.match_events({'m': _target()}, bare, {})[0], {})
        bound = dict(_target(), provider_id=6)
        self.assertEqual(R.match_events({'m': bound}, bare, {})[0]['m']['identity_method'], 'provider_id')

    def test_bound_record_is_accepted_by_the_report_binder(self):
        from scangoal.report import external_matches
        row = R.match_events({'m': _target()}, [_event(7, 'Levante', 'Elche', T0)], {})[0]['m']
        bet = dict(match_id='m', teams=['Levante', 'Elche'], league='Spanish La Liga Primera', kickoff_utc=T0.isoformat())
        self.assertTrue(external_matches(bet, row))
        self.assertFalse(external_matches(dict(bet, league='Spanish Segunda'), row))

    def test_registered_games_are_queried_even_when_the_bot_gave_a_result(self):
        bot_settled = dict(type='OPEN_GAME', ft=[1, 1], teams=['A', 'B'], match_id='g1', date_utc=T0.isoformat(),
                           kickoff_utc=T0.isoformat(), league='L', phase='pre', minute=None)
        with patch('scangoal.report.load', return_value=([], {}, {}, {}, {})), \
                patch('scangoal.report.build_bets', return_value=[bot_settled]):
            with patch('scangoal.prospective.registered_matches', return_value=set()):
                self.assertEqual(R.pending_targets(T0 + timedelta(hours=3)), {})
            with patch('scangoal.prospective.registered_matches', return_value={'g1'}):
                self.assertIn('g1', R.pending_targets(T0 + timedelta(hours=3)))


def _signal(i, seen, **extra):
    base = dict(match_id=f'g{i}', canonical_match_id=f'g{i}', message_id=i, type='OPEN_GAME', group='OPEN_GAME',
                phase='live', market='Asian Total', selection='Over 2.5', odds=1.9, minute=30, score_at_signal=[1, 0],
                teams=[f'H{i}', f'A{i}'], league='Spanish La Liga Primera', kickoff_utc=(seen - timedelta(minutes=31)).isoformat(),
                date_utc=seen.isoformat(), signal_seen_utc=seen.isoformat(), ft=None, match_result_first_seen_utc=None)
    return dict(base, **extra)


def _provider(sig, ft, league='La Liga'):
    return dict(fixture_id=sig['match_id'], bsd_id=100, home=sig['teams'][0], away=sig['teams'][1],
                event_date=sig['kickoff_utc'], ft=ft, league=league, target_league=sig['league'],
                target_teams=sig['teams'], period='regulation', observed_at=T0.isoformat(),
                identity_method='competition_participants_kickoff')


class B4Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'SCANGOAL_DATA_DIR': self.tmp.name})
        self.env.start()
        self.eligible = patch('scangoal.eligibility.eligible', return_value=True)
        self.eligible.start()

    def tearDown(self):
        self.eligible.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_activation_then_only_future_signals_once(self):
        P.record(T0, signals=[_signal(1, T0 - timedelta(minutes=1))], providers={})
        entries = P.load()
        self.assertEqual([e['kind'] for e in entries], ['activation'])
        later = _signal(2, T0 + timedelta(minutes=5))
        self.assertEqual(P.record(T0 + timedelta(minutes=10), signals=[later], providers={}), 1)
        self.assertEqual(P.record(T0 + timedelta(minutes=20), signals=[later], providers={}), 0)
        entries = P.load()
        self.assertTrue(P.verify(entries))
        sig = [e for e in entries if e['kind'] == 'signal']
        self.assertEqual(len(sig), 1)
        self.assertFalse(sig[0]['result_known_at_registration'])
        self.assertTrue(sig[0]['h1'] and sig[0]['h2'])

    def test_known_result_at_registration_is_flagged(self):
        P.record(T0, signals=[], providers={})
        P.record(T0 + timedelta(minutes=10), signals=[_signal(3, T0 + timedelta(minutes=1), ft=[2, 0])], providers={})
        self.assertTrue(next(e for e in P.load() if e['kind'] == 'signal')['result_known_at_registration'])

    def test_first_independent_result_is_kept_and_a_change_is_a_conflict(self):
        s = _signal(4, T0 + timedelta(minutes=1))
        P.record(T0, signals=[], providers={})
        P.record(T0 + timedelta(minutes=10), signals=[s], providers={})
        P.record(T0 + timedelta(hours=3), signals=[s], providers={'Highlightly': {s['match_id']: _provider(s, [2, 1])}})
        P.record(T0 + timedelta(hours=4), signals=[s], providers={'Highlightly': {s['match_id']: _provider(s, [2, 1])}})
        P.record(T0 + timedelta(hours=5), signals=[s], providers={'Highlightly': {s['match_id']: _provider(s, [3, 1])}})
        entries = P.load()
        kinds = [e['kind'] for e in entries]
        self.assertEqual(kinds.count('outcome'), 1)
        self.assertEqual(kinds.count('outcome_conflict'), 1)
        self.assertEqual(next(e for e in entries if e['kind'] == 'outcome')['ft'], [2, 1])
        summary = P.summary(entries)
        self.assertEqual(summary['games_with_conflicting_results'], 1)
        self.assertEqual(summary['H1']['games_with_result'], 0)

    def test_missing_result_after_72_hours_and_h1_counts(self):
        a, b = _signal(5, T0 + timedelta(minutes=1)), _signal(6, T0 + timedelta(minutes=2))
        P.record(T0, signals=[], providers={})
        P.record(T0 + timedelta(minutes=10), signals=[a, b], providers={})
        P.record(T0 + timedelta(hours=80), signals=[a, b], providers={'BSD': {a['match_id']: _provider(a, [2, 0])}})
        entries = P.load()
        self.assertEqual([e['match_id'] for e in entries if e['kind'] == 'outcome_missing'], [b['match_id']])
        s = P.summary(entries)
        self.assertEqual(s['games_with_independent_result'], 1)
        self.assertEqual(s['games_without_result_after_72h'], 1)
        self.assertEqual((s['H1']['games_with_result'], s['H1']['observed']), (1, 1))
        self.assertEqual(s['H1']['status'], 'insufficient_games')
        self.assertEqual(s['H2']['games_settled'], 1)

    def test_divergent_sources_are_a_diagnostic(self):
        P.record(T0, signals=[], providers={})
        over = _signal(7, T0 + timedelta(minutes=1))
        under = dict(_signal(7, T0 + timedelta(minutes=2)), type='STAKE_LOAD', group='STAKE_LOAD/live',
                     selection='Under 3.5', message_id=70)
        P.record(T0 + timedelta(minutes=10), signals=[over, under], providers={})
        self.assertEqual([e['kind'] for e in P.load()].count('diagnostic'), 1)

    def test_tampering_and_protocol_change_stop_appends(self):
        P.record(T0, signals=[], providers={})
        P.record(T0 + timedelta(minutes=10), signals=[_signal(8, T0 + timedelta(minutes=1))], providers={})
        path = Path(self.tmp.name) / P.LEDGER_NAME
        lines = path.read_text().splitlines()
        edited = json.loads(lines[-1])
        edited['odds'] = 9.99
        path.write_text('\n'.join(lines[:-1] + [json.dumps(edited)]) + '\n')
        with self.assertRaises(P.LedgerError):
            P.record(T0 + timedelta(minutes=20), signals=[], providers={})
        path.write_text('\n'.join(lines) + '\n')
        with patch.object(P, 'PROTOCOL', Path(self.tmp.name) / 'other.json'):
            (Path(self.tmp.name) / 'other.json').write_text('{}')
            with self.assertRaises(P.LedgerError):
                P.record(T0 + timedelta(minutes=30), signals=[], providers={})

    def test_duplicate_signal_entries_are_detected(self):
        P.record(T0, signals=[], providers={})
        P.record(T0 + timedelta(minutes=10), signals=[_signal(9, T0 + timedelta(minutes=1))], providers={})
        entries = P.load()
        writer = P.Writer(Path(self.tmp.name) / P.LEDGER_NAME, entries)
        writer.append('signal', **{k: v for k, v in entries[-1].items() if k not in ('kind', 'seq', 'prev_sha256', 'sha256')})
        with self.assertRaises(P.LedgerError):
            P.verify(P.load())

    def test_main_record_writes_summary_and_never_raises(self):
        with patch.object(P, '_signals', return_value=[]), patch.object(P, '_providers', return_value={}):
            self.assertEqual(P.main(['record']), 0)
        self.assertTrue((Path(self.tmp.name) / P.REPORT_NAME).exists())
        self.assertEqual(P.main(['verify']), 0)
        (Path(self.tmp.name) / P.LEDGER_NAME).write_text('{"kind":"signal"}\n')
        self.assertEqual(P.main(['verify']), P.FAILED_EXIT)


if __name__ == '__main__':
    unittest.main()
