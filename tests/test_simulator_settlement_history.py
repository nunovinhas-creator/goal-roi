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


def truncated(entry, cut):
    """What a report published at `cut` would hold for this position, derived from a later report."""
    events = entry.get('s') or ([[entry['z'], entry['w']]] if entry['z'] else [])
    kept = [event for event in events if datetime.fromisoformat(event[0]) <= cut]
    at, w = kept[-1] if kept else (None, None)
    out = {key: entry[key] for key in ('d', 'o', 't')}
    out.update(z=at if w is not None else None, w=w)
    if len(kept) > 1 or (kept and w is None):
        out['s'] = kept
    return out


class SettlementCorrections(unittest.TestCase):
    """A later FT correction moves the payoff on the day it became known, never before."""

    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict('os.environ', {'SCANGOAL_DATA_DIR': directory}))
        self.bet = message(1, 'fixture-a', 'Alpha — Beta', '15:00', -40)

    def entry(self, minutes):
        entries = report.build(as_of=at(minutes))['simulation']['entries']
        self.assertEqual(len(entries), 1)
        return entries[0]

    def assert_invariant(self, cuts, final):
        later = self.entry(final)
        for cut in cuts:
            self.assertEqual(truncated(later, at(cut)), self.entry(cut), 'cut at minute %d' % cut)

    def test_won_then_corrected_to_lost(self):
        store.append_versions([self.bet, result(self.bet, 110, '1:0'), result(self.bet, 150, '0:1')])
        entry = self.entry(300)
        self.assertEqual(entry['s'], [[at(115).isoformat(), 1.0], [at(155).isoformat(), -1.0]])
        self.assertEqual((entry['z'], entry['w']), (at(155).isoformat(), -1.0))
        self.assertEqual(self.entry(130), {'d': entry['d'], 'z': at(115).isoformat(), 'w': 1.0, 'o': 2.0,
                                          't': 'BETFAIR/pre'})
        self.assert_invariant((100, 115, 130, 155, 200), 300)

    def test_lost_then_corrected_to_won(self):
        store.append_versions([self.bet, result(self.bet, 110, '0:2'), result(self.bet, 150, '3:2')])
        entry = self.entry(300)
        self.assertEqual(entry['s'], [[at(115).isoformat(), -1.0], [at(155).isoformat(), 1.0]])
        self.assertEqual(self.entry(130)['w'], -1.0)
        self.assert_invariant((100, 130, 160), 300)

    def test_settled_result_in_quarantine_reserves_the_stake_again(self):
        store.append_versions([self.bet, result(self.bet, 110, '1:0')])
        challenger = dict(self.bet, message_id=3, date_utc=at(140).isoformat())
        store.append_versions([result(challenger, 150, '0:1')])
        entry = self.entry(300)
        self.assertEqual(entry['s'], [[at(115).isoformat(), 1.0], [at(155).isoformat(), None]])
        self.assertEqual((entry['z'], entry['w']), (None, None))
        self.assert_invariant((120, 155, 200), 300)
        built = report.build(as_of=at(300))
        self.assertIsNone(built['recent_bets'][-1]['w'])
        self.assertEqual(built['simulation']['timed_settlements'], 0)

    def test_score_revision_without_payoff_change_adds_no_event(self):
        store.append_versions([self.bet, result(self.bet, 110, '1:0'), result(self.bet, 150, '2:0')])
        entry = self.entry(300)
        self.assertNotIn('s', entry)
        self.assertEqual(entry['z'], at(115).isoformat())

    def test_rebuilding_is_deterministic(self):
        store.append_versions([self.bet, result(self.bet, 110, '1:0'), result(self.bet, 150, '0:1')])
        self.assertEqual(report.build(as_of=at(300))['simulation'], report.build(as_of=at(300))['simulation'])
        self.assertEqual(report.build(as_of=at(300))['simulation']['payoff_revisions'], 1)

    def test_unsettled_bet_has_no_history(self):
        store.append_versions([self.bet])
        entry = self.entry(300)
        self.assertEqual((entry['z'], entry['w']), (None, None))
        self.assertNotIn('s', entry)
