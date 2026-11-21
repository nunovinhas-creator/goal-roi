"""Reviewed aliases for three Italian Serie C bets (BSD near-misses of 14-15 Nov 2026).

The targets are the real bot signals (teams, signal time, live, no kickoff). Every BSD
event below is SYNTHETIC test data, never presented as a real BSD reply: it only checks
that the unchanged matching rules settle with the aliases exactly once and refuse
anything ambiguous, misnamed, mistimed or unfinished.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scangoal import results_bsd
from scangoal.results_bsd import match_events, norm_team

ROOT = Path(__file__).resolve().parents[1]
ALIASES_FILE = ROOT/'data'/'team_aliases.json'

# Real bot targets (messages 28108, 28550, 28607; Italian Serie C; live signals, no kickoff).
TARGETS = {
    'lumezzane-vs-trento-calcio-1921-20261114':
        {'teams': ['Lumezzane', 'Trento Calcio 1921'], 'date_utc': '2026-11-14T18:09:06+00:00', 'kickoff_utc': None, 'phase': 'live', 'league': 'Italian Serie C'},
    'fc-guidonia-montecelio-1937-vs-as-ostia-mare-20261115':
        {'teams': ['FC Guidonia Montecelio 1937', 'AS Ostia Mare'], 'date_utc': '2026-11-15T15:29:31+00:00', 'kickoff_utc': None, 'phase': 'live', 'league': 'Italian Serie C'},
    'cittadella-vs-us-folgore-caratese-asd-20261115':
        {'teams': ['Cittadella', 'US Folgore Caratese ASD'], 'date_utc': '2026-11-15T16:59:32+00:00', 'kickoff_utc': None, 'phase': 'live', 'league': 'Italian Serie C'},
}
# BSD names as recorded in data/results_bsd.json near_misses (names only).
BSD_NAMES = {
    'lumezzane-vs-trento-calcio-1921-20261114': ('Lumezzane', 'Trento'),
    'fc-guidonia-montecelio-1937-vs-as-ostia-mare-20261115': ('Guidonia Montecelio 1937', 'Ostiamare'),
    'cittadella-vs-us-folgore-caratese-asd-20261115': ('Cittadella', 'Folgore Caratese'),
}


def aliases():
    """The committed data/team_aliases.json, loaded exactly like results_bsd.load_aliases()."""
    with patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(ALIASES_FILE.parent)}):
        return results_bsd.load_aliases()


def synthetic(i, mid, minutes_before_signal=60, status='finished', score=(1, 1), names=None):
    home, away = names or BSD_NAMES[mid]
    signal = datetime.fromisoformat(TARGETS[mid]['date_utc'])
    ko = (signal - timedelta(minutes=minutes_before_signal)).astimezone(timezone.utc)
    return {'id': i, 'status': status, 'home_team': {'name': home}, 'away_team': away,
            'event_date': ko.isoformat().replace('+00:00', 'Z'), 'league': TARGETS[mid]['league'], 'home_score': score[0], 'away_score': score[1]}


class SerieCAliases(unittest.TestCase):
    def test_exactly_three_reviewed_aliases_and_guidonia_already_equivalent(self):
        a = aliases()
        self.assertEqual(a[norm_team('Trento Calcio 1921')], ['Trento'])
        self.assertEqual(a[norm_team('AS Ostia Mare')], ['Ostiamare'])
        self.assertEqual(a[norm_team('US Folgore Caratese ASD')], ['Folgore Caratese'])
        self.assertEqual(norm_team('FC Guidonia Montecelio 1937'), norm_team('Guidonia Montecelio 1937'))
        self.assertNotIn(norm_team('FC Guidonia Montecelio 1937'), a)        # no unnecessary alias
        raw = json.loads(ALIASES_FILE.read_text(encoding='utf-8'))
        for kept in ('Macedonia U19', 'Wolfsburg', '_comment'):              # existing entries preserved
            self.assertIn(kept, raw)

    def test_normalization_never_strips_identity_words(self):
        self.assertEqual(norm_team('Trento Calcio 1921'), 'trento calcio 1921')
        self.assertEqual(norm_team('US Folgore Caratese ASD'), 'us folgore caratese asd')
        self.assertEqual(norm_team('AS Ostia Mare'), 'as ostia mare')
        self.assertNotEqual(norm_team('Trento Calcio 1921'), norm_team('Trento'))  # only the reviewed alias bridges them

    def test_single_finished_candidate_settles_each_bet_only_with_aliases(self):
        events = [synthetic(i, mid, score=(2, 2)) for i, mid in enumerate(TARGETS, 1)]
        found, near = match_events(TARGETS, events, aliases())
        self.assertEqual(set(found), set(TARGETS))
        self.assertEqual({m: f['ft'] for m, f in found.items()}, {m: [2, 2] for m in TARGETS})
        for mid, f in found.items():
            self.assertEqual((f['home'], f['away']), BSD_NAMES[mid])
        found_without, near_without = match_events(TARGETS, events, {})
        # Without the aliases all three stay near-misses only (as in the real data/results_bsd.json):
        # Guidonia needs no alias, but its opponent "AS Ostia Mare" does.
        self.assertEqual(found_without, {})
        self.assertEqual(set(near_without), set(TARGETS))

    def test_two_compatible_events_never_settle(self):
        for mid in TARGETS:
            with self.subTest(mid=mid):
                events = [synthetic(1, mid, 50, score=(1, 2)), synthetic(2, mid, 70, score=(2, 2))]
                found, _ = match_events({mid: TARGETS[mid]}, events, aliases())
                self.assertEqual(found, {})

    def test_wrong_teams_rejected(self):
        wrong = {'lumezzane-vs-trento-calcio-1921-20261114': ('Lumezzane', 'Trento Women'),
                 'fc-guidonia-montecelio-1937-vs-as-ostia-mare-20261115': ('Guidonia Montecelio 1937', 'Ostia'),
                 'cittadella-vs-us-folgore-caratese-asd-20261115': ('Cittadella Women', 'Folgore Caratese')}
        for mid, names in wrong.items():
            with self.subTest(mid=mid):
                found, _ = match_events({mid: TARGETS[mid]}, [synthetic(1, mid, names=names)], aliases())
                self.assertEqual(found, {})

    def test_incompatible_times_rejected(self):
        for mid in TARGETS:
            for minutes in (-5, 151, 24*60):           # kickoff after the live signal, or too early / another day
                with self.subTest(mid=mid, minutes=minutes):
                    found, _ = match_events({mid: TARGETS[mid]}, [synthetic(1, mid, minutes)], aliases())
                    self.assertEqual(found, {})

    def test_unfinished_or_scoreless_never_settles(self):
        for mid in TARGETS:
            for event in (synthetic(1, mid, status='inprogress'), dict(synthetic(1, mid), home_score=None)):
                with self.subTest(mid=mid):
                    self.assertEqual(match_events({mid: TARGETS[mid]}, [event], aliases())[0], {})

    def test_near_misses_alone_never_settle_and_report_only_counts_them(self):
        self.assertEqual(match_events(TARGETS, [], aliases()), ({}, {}))
        source = (ROOT/'scangoal'/'report.py').read_text(encoding='utf-8')
        self.assertIn("audit[name + '_near_misses'] = len(", source)  # used for audit counts only

    def test_collector_requests_unchanged_and_existing_results_kept(self):
        """Same pages requested with or without the aliases; an existing result is never rewritten."""
        now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
        targets = {m: dict(t, days=[t['date_utc'][:10]]) for m, t in TARGETS.items()}
        events = {'2026-11-14': [synthetic(1, 'lumezzane-vs-trento-calcio-1921-20261114', score=(1, 2))],
                  '2026-11-15': [synthetic(2, 'fc-guidonia-montecelio-1937-vs-as-ostia-mare-20261115'),
                                 synthetic(3, 'cittadella-vs-us-folgore-caratese-asd-20261115')]}
        runs = []
        for alias_map in ({}, aliases()):
            with tempfile.TemporaryDirectory() as d:
                Path(d, 'results_bsd.json').write_text(json.dumps({'results': {'kept-match': {'ft': [3, 0]}},
                    'near_misses': {}, 'last_run_utc': None, 'stats': {}}))
                calls = []
                def fetch(url, token):
                    calls.append(url)
                    day = url.split('date_from=')[1][:10]
                    return {'results': events[day], 'count': len(events[day]), 'next': None}
                with patch.dict(os.environ, {'SCANGOAL_DATA_DIR': d}), \
                        patch.object(results_bsd, 'pending_targets', return_value=targets), \
                        patch.object(results_bsd, 'load_aliases', return_value=alias_map), redirect_stdout(io.StringIO()):
                    state = results_bsd.run(fetch=fetch, now=now, token='fixture-key')
                runs.append((calls, state))
        self.assertEqual(runs[0][0], runs[1][0])                 # identical requests: the aliases add none
        self.assertEqual(len(runs[1][0]), 2)                     # one page per day
        self.assertEqual(runs[1][1]['results']['kept-match'], {'ft': [3, 0]})
        self.assertEqual(set(runs[1][1]['results']) - {'kept-match'}, set(TARGETS))
        self.assertEqual(runs[1][1]['results']['lumezzane-vs-trento-calcio-1921-20261114']['ft'], [1, 2])


if __name__ == '__main__':
    unittest.main()
