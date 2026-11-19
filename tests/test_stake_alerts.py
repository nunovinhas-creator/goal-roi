"""Avisos Stake das linhas 1, 6 e 9: só sinais atuais que cumprem as condições, um aviso por aposta, nunca repetido."""
import tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scangoal import stake_alerts

UTC = timezone.utc
NOW = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)


def sig(minutes_ago=5, odds=1.55, mid='a-vs-b-20261119', sel='Under 3.25', after=20, total=5000, typ='STAKE_LOAD', league='Australian Australia Championship'):
    t = (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec='seconds')
    return {'type': typ, 'phase': 'live', 'league': league, 'teams': ['A', 'B'], 'odds': odds, 'match_id': mid, 'date_utc': t, 'market': 'Asian Total',
            'selection': sel, 'n_signals': 1, 'max_mult': 1, 'after_kickoff_min': after, 'total': total, 'n_bets': 1, 'ft': None, 'w': None,
            'kickoff_utc': None, 'score_at_signal': None, 'minute': None}


class StakeAlerts(unittest.TestCase):
    def test_lines_1_6_9(self):
        m = stake_alerts.matches([sig()], NOW)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]['lines'], [1, 6, 9])

    def test_only_line_1_when_notice_late(self):
        m = stake_alerts.matches([sig(after=60)], NOW)
        self.assertEqual(m[0]['lines'], [1])

    def test_no_match_odds_or_old_or_other_type(self):
        self.assertEqual(stake_alerts.matches([sig(odds=2.45, total=20000, after=60)], NOW), [])
        self.assertEqual(stake_alerts.matches([sig(minutes_ago=45)], NOW), [])
        self.assertEqual(stake_alerts.matches([sig(typ='POLYMARKET')], NOW), [])

    def test_other_type_in_same_game_breaks_one_type_lines(self):
        earlier = dict(sig(minutes_ago=20, typ='OPEN_GAME', sel='Over 2'), market='Asian Total')
        m = stake_alerts.matches([earlier, sig()], NOW)
        self.assertEqual(m, [])

    def test_sent_once(self):
        sent, calls = [], []
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'state.json'
            env = {'TG_BOT_TOKEN': 't', 'TG_CHAT_ID': 'c'}
            send = lambda tok, chat, txt: calls.append(txt)
            self.assertEqual(len(stake_alerts.deliver([sig()], NOW, env, send, path)), 1)
            self.assertEqual(stake_alerts.deliver([sig()], NOW, env, send, path), [])
            self.assertEqual(stake_alerts.deliver([sig()], NOW, {}, send, path), [])
        self.assertEqual(len(calls), 1)
        self.assertIn('linhas 1, 6, 9', calls[0])

    def test_failed_send_is_not_repeated(self):
        def boom(*a):
            raise RuntimeError('x')
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'state.json'
            env = {'TG_BOT_TOKEN': 't', 'TG_CHAT_ID': 'c'}
            stake_alerts.deliver([sig()], NOW, env, boom, path)
            calls = []
            stake_alerts.deliver([sig()], NOW, env, lambda *a: calls.append(1), path)
            self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
