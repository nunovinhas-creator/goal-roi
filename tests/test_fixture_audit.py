from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import unittest

from scangoal import report, results_bsd, store
from test_temporal_audit import T, stamp


SIGNAL = ('💷 Betfair · 20 nov 2026 15:00\nAlpha — Beta · First Cup\n'
          '1 · Seleção @2.00 · $1K\nmarket share 60% · whole 1X2 market $8K\n'
          'https://scangoal.com/match/fixture-a?lang=en')


def message(mid, value, sent, received, **fields):
    return dict({'message_id': mid, 'date_utc': stamp(sent), 'collected_at_utc': stamp(received),
                 'edit_date_utc': None, 'text': value}, **fields)


class FixtureAudit(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {'SCANGOAL_DATA_DIR': directory}))
        store.append_versions([message(1, SIGNAL, -45, -42)])

    def bets(self, after=150):
        from datetime import timedelta

        return report.build(now=T + timedelta(minutes=after))['recent_bets']

    def test_N02_a_prematch_FT_cannot_settle_even_if_collected_after_kickoff(self):
        ft = SIGNAL + '\n🏁 FT 2:0 · Seleção ✅'
        store.append_versions([message(2, ft, -10, 120)])
        self.assertEqual(len(self.bets()), 1)
        self.assertIsNone(self.bets()[0]['w'])
        store.append_versions([message(1, ft, -45, 125, edit_date_utc=stamp(124), receipt_version=1)])
        self.assertEqual(self.bets()[0]['w'], 1.0)

    def test_N03_competition_or_an_independently_bound_provider_id(self):
        target = {'teams': ['Alpha', 'Beta'], 'league': 'First Cup', 'kickoff_utc': stamp(0), 'date_utc': stamp(-45)}
        event = {'id': 109, 'home_team': 'Alpha', 'away_team': 'Beta', 'event_date': stamp(0),
                 'status': 'finished', 'home_score': 2, 'away_score': 0}
        found, _ = results_bsd.match_events({'fixture-a': target}, [event], {})
        self.assertEqual(found, {})
        found, _ = results_bsd.match_events({'fixture-a': dict(target, provider_id=109)}, [event], {})
        self.assertEqual(found['fixture-a']['bsd_id'], 109)
        other = dict(event, league='Second Cup')
        self.assertEqual(results_bsd.match_events({'fixture-a': dict(target, provider_id=109)}, [other], {})[0], {})

    def test_N07_conflicting_explicit_note_ids_never_fall_back_to_team_names(self):
        note = '🏁 Full time: Alpha 1:0 Beta'
        store.append_versions([
            message(2, note + '\nhttps://scangoal.com/match/fixture-b', 110, 111),
            message(3, note, 112, 113, buttons=[['Match', 'https://scangoal.com/match/fixture-c?lang=en']]),
            message(4, note + '\nhttps://scangoal.com/match/fixture-a?lang=en', 114, 115,
                    buttons=[['Match', 'https://scangoal.com/match/fixture-b?lang=en']]),
        ])
        self.assertIsNone(self.bets()[0]['w'])
        store.append_versions([message(5, note + '\nhttps://scangoal.com/match/fixture-a', 120, 121)])
        self.assertEqual((self.bets()[0]['w'], self.bets()[0]['ft']), (1.0, [1, 0]))
