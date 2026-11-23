from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scangoal import report, store


KICKOFF = datetime(2026, 11, 20, 15, tzinfo=timezone.utc)


def at(minutes):
    return KICKOFF + timedelta(minutes=minutes)


def message(mid, fixture, teams, kickoff, sent):
    text = (f'💷 Betfair · 20 nov 2026 {kickoff}\n{teams} · First Cup\n'
            '1 · Seleção @2.00 · $1K\nmarket share 60% · whole 1X2 market $8K\n'
            f'https://scangoal.com/match/{fixture}?lang=en')
    return {'message_id': mid, 'date_utc': at(sent).isoformat(), 'collected_at_utc': at(sent + 1).isoformat(),
            'edit_date_utc': None, 'text': text}


def result(row, observed, score):
    stamp = at(observed).isoformat()
    return dict(row, collected_at_utc=stamp, edit_date_utc=stamp, text=row['text'] + f'\n🏁 FT {score}')


class SimulatorSettlementHistory(unittest.TestCase):
    def test_score_revision_with_same_payoff_does_not_move_capital_release(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict('os.environ', {'SCANGOAL_DATA_DIR': directory}))
        first = message(1, 'fixture-a', 'Alpha — Beta', '15:00', -40)
        second = message(2, 'fixture-b', 'Gamma — Delta', '18:00', 125)
        store.append_versions([first, result(first, 110, '1:0'), second, result(second, 280, '1:0')])
        before = report.build(as_of=at(300))
        first_entry, second_entry = before['simulation']['entries']
        self.assertEqual(first_entry['z'], at(115).isoformat())
        self.assertLess(first_entry['z'], second_entry['d'])

        store.append_versions([result(first, 150, '2:0')])
        after = report.build(as_of=at(300))
        self.assertEqual(after['simulation'], before['simulation'])
        self.assertEqual(after['recent_bets'][-1]['ft'], [2, 0])

        store.append_versions([result(first, 160, '0:1'), result(first, 170, '2:0')])
        after_reversal = report.build(as_of=at(300))
        self.assertEqual(after_reversal['simulation']['entries'][0]['z'], at(175).isoformat())

        challenger = dict(first, message_id=3, date_utc=at(180).isoformat())
        store.append_versions([result(challenger, 180, '0:1'), result(challenger, 190, '2:0')])
        during_conflict = report.build(as_of=at(185))
        after_conflict = report.build(as_of=at(300))
        self.assertIsNone(during_conflict['simulation']['entries'][0]['z'])
        self.assertEqual(after_conflict['simulation']['entries'][0]['z'], at(195).isoformat())
