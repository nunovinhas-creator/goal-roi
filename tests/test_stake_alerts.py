"""Safety regression tests for live Stake alerts and remote-before-send deduplication."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scangoal import stake_alerts

UTC = timezone.utc
NOW = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)
ENV = {'TG_BOT_TOKEN': 't', 'TG_CHAT_ID': 'c'}


def sig(minutes_ago=5, odds=1.55, mid='a-vs-b-20261119', sel='Under 3.25',
        after=20, total=5000, typ='STAKE_LOAD', league='Australian Australia Championship',
        phase='live'):
    t = (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec='seconds')
    return {'type': typ, 'phase': phase, 'league': league, 'teams': ['A', 'B'],
            'odds': odds, 'match_id': mid, 'date_utc': t, 'market': 'Asian Total',
            'selection': sel, 'n_signals': 1, 'max_mult': 1,
            'after_kickoff_min': after, 'total': total, 'n_bets': 1,
            'ft': None, 'w': None, 'kickoff_utc': None,
            'score_at_signal': None, 'minute': None}


def initialize(path):
    path.write_text(json.dumps({
        'format': stake_alerts.STATE_FORMAT, 'sent': {}, 'sent_total': 0
    }), encoding='utf-8')
    path.with_name(stake_alerts.LOG_NAME).write_text(
        json.dumps({'format': stake_alerts.LOG_FORMAT}) + '\n', encoding='utf-8')


class StakeAlerts(unittest.TestCase):
    def test_a_signal_that_expires_during_the_git_push_never_reaches_telegram(self):
        now = NOW
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, stake_alerts.STATE_NAME)
            initialize(path)

            def publish(_):
                nonlocal now
                now += timedelta(minutes=2)
                return True

            result = stake_alerts.deliver([sig(minutes_ago=29)], NOW, ENV,
                                          lambda *_: self.fail('expired alert sent'), path, publish, lambda: now)
            self.assertEqual(result, [])
            self.assertEqual(stake_alerts.load_state(path)['sent'], {})
            self.assertIn('"event": "expired"', path.with_name(stake_alerts.LOG_NAME).read_text())

    def test_lines_1_6_9(self):
        m = stake_alerts.matches([sig()], NOW)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]['lines'], [1, 6, 9])

    def test_dedicated_stake_notice_keeps_its_rules_and_context(self):
        match, = stake_alerts.matches([sig(odds=1.5)], NOW)
        self.assertEqual(stake_alerts.text(match, NOW).splitlines(), [
            'Stake · linhas 1, 6, 9', 'A — B · Australian Australia Championship', 'Asian Total · Under 3.25 @1,50',
            'Sinal das 09:55 (Lisboa), há 5 min', 'https://scangoal.com/match/a-vs-b-20261119?lang=en',
        ])

    def test_partial_lines_never_alert(self):
        # Regressão de produção: odd 1,45 gerou um aviso linha 6;
        # aposta posterior cumpriu só a linha 1. Nenhuma deve chegar ao Telegram.
        incomplete = [
            sig(odds=1.45),                       # 6 (ou outra combinação parcial)
            sig(after=60),                       # 1, sem 6 nem 9
            sig(total=20000),                    # 6 e 9, sem 1
            sig(league='International Friendly'), # 1 e 9, sem 6
            sig(odds=1.45, total=20000),          # sem a faixa de odd e sem a linha 1
        ]
        for s in incomplete:
            with self.subTest(odds=s['odds'], total=s['total'],
                              after=s['after_kickoff_min'], league=s['league']):
                self.assertEqual(stake_alerts.matches([s], NOW), [])

    def test_exact_conjunction_all_line_subsets(self):
        # Cobrir combinações parciais mesmo se um futuro refactor alterar os átomos.
        source = sig()
        all_conditions = {n: set(conds) for n, (_, conds) in stake_alerts.LINES.items()}
        from itertools import combinations
        union = set().union(*all_conditions.values())
        for size in range(4):
            for included in combinations((1, 6, 9), size):
                active = set().union(*(all_conditions[n] for n in included))
                # Átomos partilhados por linhas podem tornar outras linhas verdadeiras;
                # a verdade é determinada pela conjunção efetiva, não pelo nome do subconjunto.
                should_alert = all(conds <= active for conds in all_conditions.values())
                with self.subTest(included=included), patch.object(stake_alerts.brain, 'atoms', return_value=list(active)):
                    found = stake_alerts.matches([source.copy()], NOW)
                    self.assertEqual(bool(found), should_alert)
                    if found:
                        self.assertEqual(found[0]['lines'], [1, 6, 9])

    def test_do_not_emit_partial_alerts_or_intents(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / stake_alerts.STATE_NAME
            initialize(path)
            sends, pushes = [], []
            incomplete = [sig(odds=1.45, mid='only-six'), sig(after=60, mid='only-one'),
                          sig(total=20000, mid='six-nine')]
            result = stake_alerts.deliver(incomplete, NOW, ENV,
                                          lambda *args: sends.append(args), path,
                                          lambda key: pushes.append(key) or True)
            self.assertEqual(result, [])
            self.assertEqual(sends, [])
            self.assertEqual(pushes, [])
            self.assertEqual(stake_alerts.load_state(path)['sent_total'], 0)
            self.assertEqual(stake_alerts.load_log(path.with_name(stake_alerts.LOG_NAME))[0], {})

    def test_old_wrong_odd_other_type_or_finished(self):
        self.assertEqual(stake_alerts.matches([sig(odds=2.45, total=20000, after=60)], NOW), [])
        self.assertEqual(stake_alerts.matches([sig(minutes_ago=45)], NOW), [])
        self.assertEqual(stake_alerts.matches([sig(typ='POLYMARKET')], NOW), [])
        self.assertEqual(stake_alerts.matches([dict(sig(), ft=[2, 1])], NOW), [])

    def test_live_only_pre_and_unknown_phase_rejected(self):
        # Prematch freshness can be true; it must NOT activate this module.
        ko = (NOW + timedelta(hours=1)).isoformat(timespec='seconds')
        self.assertEqual(stake_alerts.matches([dict(sig(phase='pre'), kickoff_utc=ko)], NOW), [])
        self.assertEqual(stake_alerts.matches([dict(sig(), phase=None)], NOW), [])
        self.assertEqual(stake_alerts.matches([dict(sig(), phase='unknown')], NOW), [])
        for field in ('match_id', 'market', 'selection'):
            self.assertEqual(stake_alerts.matches([dict(sig(), **{field: None})], NOW), [])

    def test_other_type_in_same_game_breaks_one_type_lines(self):
        earlier = dict(sig(minutes_ago=20, typ='OPEN_GAME', sel='Over 2'))
        self.assertEqual(stake_alerts.matches([earlier, sig()], NOW), [])

    def test_published_intent_precedes_send_and_is_never_repeated(self):
        calls = []
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / stake_alerts.STATE_NAME
            initialize(path)
            def publish(key):
                calls.append('push')
                state = stake_alerts.load_state(path)
                intents, _ = stake_alerts.load_log(path.with_name(stake_alerts.LOG_NAME))
                stake_alerts.verify_state(state, intents)
                self.assertIn(key, intents)
                return True
            def send(token, chat, message):
                self.assertEqual(calls, ['push'])
                self.assertIn('linhas 1, 6, 9', message)
                calls.append('send')
            self.assertEqual(len(stake_alerts.deliver([sig()], NOW, ENV, send, path, publish)), 1)
            self.assertEqual(stake_alerts.deliver([sig()], NOW, ENV, send, path, publish), [])
            self.assertEqual(stake_alerts.deliver([sig()], NOW, {}, send, path, publish), [])
            self.assertEqual(calls, ['push', 'send'])

    def test_failed_send_never_retried(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / stake_alerts.STATE_NAME
            initialize(path)
            def lost(*_):
                raise RuntimeError('Telegram reply lost')
            with self.assertRaises(stake_alerts.StakeStateError):
                stake_alerts.deliver([sig()], NOW, ENV, lost, path, lambda key: True)
            calls = []
            stake_alerts.deliver([sig()], NOW, ENV, lambda *a: calls.append(1), path, lambda key: True)
            self.assertEqual(calls, [])

    def test_remote_publish_failure_or_ambiguous_reply_prevents_send(self):
        for outcome in (False, None):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as d:
                path = Path(d) / stake_alerts.STATE_NAME
                initialize(path)
                sent = []
                with self.assertRaisesRegex(stake_alerts.StakeStateError, 'not confirmed'):
                    stake_alerts.deliver([sig()], NOW, ENV, lambda *a: sent.append(1),
                                         path, lambda key: outcome)
                self.assertEqual(sent, [])
                # A subsequent invocation does not reattempt this key.
                self.assertEqual(stake_alerts.deliver([sig()], NOW, ENV, lambda *a: sent.append(1),
                                                      path, lambda key: True), [])
                self.assertEqual(sent, [])

    def test_missing_state_or_log_fails_closed(self):
        for target in ('state', 'log'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as d:
                path = Path(d) / stake_alerts.STATE_NAME
                initialize(path)
                (path if target == 'state' else path.with_name(stake_alerts.LOG_NAME)).unlink()
                sent = []
                with self.assertRaises(stake_alerts.StakeStateError):
                    stake_alerts.deliver([sig()], NOW, ENV, lambda *a: sent.append(1),
                                         path, lambda key: True)
                self.assertEqual(sent, [])

    def test_corrupt_files_fail_closed(self):
        for target in ('state', 'log'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as d:
                path = Path(d) / stake_alerts.STATE_NAME
                initialize(path)
                (path if target == 'state' else path.with_name(stake_alerts.LOG_NAME)).write_text(
                    '{not-json', encoding='utf-8')
                with self.assertRaises(stake_alerts.StakeStateError):
                    stake_alerts.deliver([sig()], NOW, ENV, lambda *a: self.fail('sent'),
                                         path, lambda key: True)

    def test_truncation_or_regression_of_either_file_fails_closed(self):
        for target in ('state', 'log'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as d:
                path = Path(d) / stake_alerts.STATE_NAME
                initialize(path)
                stake_alerts.deliver([sig()], NOW, ENV, lambda *a: None,
                                     path, lambda key: True)
                if target == 'state':
                    initialize_state = {'format': stake_alerts.STATE_FORMAT, 'sent': {}, 'sent_total': 0}
                    path.write_text(json.dumps(initialize_state), encoding='utf-8')
                else:
                    path.with_name(stake_alerts.LOG_NAME).write_text(
                        json.dumps({'format': stake_alerts.LOG_FORMAT}) + '\n', encoding='utf-8')
                with self.assertRaises(stake_alerts.StakeStateError):
                    stake_alerts.deliver([sig()], NOW, ENV, lambda *a: self.fail('resent'),
                                         path, lambda key: True)

    def test_counter_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / stake_alerts.STATE_NAME
            initialize(path)
            state = json.loads(path.read_text(encoding='utf-8'))
            state['sent_total'] = 100
            path.write_text(json.dumps(state), encoding='utf-8')
            with self.assertRaises(stake_alerts.StakeStateError):
                stake_alerts.deliver([sig()], NOW, ENV, lambda *a: self.fail('sent'),
                                     path, lambda key: True)

    def test_durable_configuration_required_without_test_publisher(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / stake_alerts.STATE_NAME
            initialize(path)
            for bad_env in (ENV, {**ENV, 'NOTIFY_DURABLE': 'git'},
                            {**ENV, 'NOTIFY_DURABLE': 'git', 'GITHUB_REF_NAME': 'dev'}):
                with self.subTest(bad_env=bad_env), self.assertRaises(stake_alerts.StakeStateError):
                    stake_alerts.deliver([sig()], NOW, bad_env, lambda *a: self.fail('sent'), path)

    def test_no_credentials_does_not_read_or_send(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / stake_alerts.STATE_NAME
            self.assertEqual(stake_alerts.deliver([sig()], NOW, {}, lambda *a: self.fail('sent'), path), [])

    def test_deduplication_keeps_every_key_without_500_key_truncation(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / stake_alerts.STATE_NAME
            initialize(path)
            bets = [sig(mid=f'game-{i}') for i in range(510)]
            sent = stake_alerts.deliver(bets, NOW, ENV, lambda *a: None, path, lambda key: True)
            self.assertEqual(len(sent), 510)
            self.assertEqual(stake_alerts.load_state(path)['sent_total'], 510)
            self.assertEqual(stake_alerts.deliver(bets, NOW, ENV, lambda *a: self.fail('resent'),
                                                  path, lambda key: True), [])


if __name__ == '__main__':
    unittest.main()
