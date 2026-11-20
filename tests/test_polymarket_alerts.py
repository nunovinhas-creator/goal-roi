"""Offline checks for the prematch Polymarket Over 2.5 candidate filter."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scangoal import notify, opportunity_forward, polymarket_alerts, report
from scangoal.parser import parse
from test_stale_notices import state as notification_state


NOW = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)


def timestamp(minutes):
    return (NOW + timedelta(minutes=minutes)).isoformat(timespec='seconds')


def signal(**changes):
    return {
        'message_id': 1,
        'type': 'POLYMARKET',
        'bet': True,
        'phase': 'pre',
        'match_id': 'a-vs-b-20261119',
        'teams': ['A', 'B'],
        'league': 'Example League',
        'market': 'Asian Total', 'period': 'regulation', 'market_scope': 'match_goals',
        'selection': 'Over 2.5',
        'odds': 1.85,
        'share': 79,
        'total': 9900,
        'date_utc': timestamp(-3),
        'collected_at_utc': timestamp(-2),
        'kickoff_utc': timestamp(10),
        'had_ft_at_first_sight': False,
        'edited_at_first_sight': False,
        'burst': False,
        **changes,
    }


class PolymarketAlerts(unittest.TestCase):
    def test_qualifying_prematch_full_time_over(self):
        row = signal()
        alerts = polymarket_alerts.candidates([row], set(), NOW)
        self.assertEqual(len(alerts), 1)
        self.assertEqual({k: alerts[0][k] for k in ('match_id', 'odds', 'share', 'total')},
                         {k: row[k] for k in ('match_id', 'odds', 'share', 'total')})

    def test_requires_exact_source_phase_market_and_selection(self):
        cases = {
            'type': ('STAKE_LOAD', 'OPEN_GAME', None),
            'bet': (False,),
            'phase': ('live', None),
            'match_id': ('', ' \t ', None, 123),
            'market': ('1st Half - Asian Total', 'Total Goals', 'Total Corners', '1X2', None),
            'selection': ('Under 2.5', 'Over 2.25', 'Over 2.75', 'Over 3', None),
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    row = signal(**{field: value})
                    self.assertEqual(polymarket_alerts.candidates([row], set(), NOW), [])

    def test_numeric_limits_and_missing_data(self):
        qualifying = signal(share=0, total=0, odds=1.10)
        self.assertEqual(len(polymarket_alerts.candidates([qualifying], set(), NOW)), 1)
        qualifying = signal(share=79.99, total=9999.99)
        self.assertEqual(len(polymarket_alerts.candidates([qualifying], set(), NOW)), 1)

        cases = {
            'share': (-1, 80, None, True, float('nan')),
            'total': (-1, 10_000, None, True, float('inf')),
            'odds': (1.09, None, True, float('inf')),
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    row = signal(**{field: value})
                    self.assertEqual(polymarket_alerts.candidates([row], set(), NOW), [])

    def test_requires_original_message_and_known_read(self):
        cases = {
            'edited_at_first_sight': (True, None),
            'had_ft_at_first_sight': (True, None),
            'burst': (True, None),
            'date_utc': (None, 'bad timestamp'),
            'collected_at_utc': (None, 'bad timestamp', timestamp(-20), timestamp(1)),
        }
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    row = signal(**{field: value})
                    self.assertEqual(polymarket_alerts.candidates([row], set(), NOW), [])

    def test_requires_future_kickoff_and_no_known_result(self):
        for kickoff in (None, 'bad timestamp', timestamp(-1), timestamp(0)):
            with self.subTest(kickoff=kickoff):
                row = signal(kickoff_utc=kickoff)
                self.assertEqual(polymarket_alerts.candidates([row], set(), NOW), [])

        row = signal()
        self.assertEqual(polymarket_alerts.candidates([row], {row['match_id']}, NOW), [])
        self.assertEqual(polymarket_alerts.candidates([signal(ft=[2, 1])], set(), NOW), [])

    def test_later_distinct_message_can_qualify_after_ineligible_first_price(self):
        first = signal(share=80)
        second = signal(message_id=2, odds=1.92, date_utc=timestamp(-1), collected_at_utc=timestamp(0))
        expected = polymarket_alerts.candidates([second], set(), NOW)
        self.assertEqual(len(expected), 1)
        self.assertEqual(expected[0]['odds'], 1.92)
        self.assertEqual(expected[0]['date_utc'], second['date_utc'])
        self.assertEqual(polymarket_alerts.candidates([first, second], set(), NOW), expected)

    def test_read_started_just_before_the_signal(self):
        row = signal(collected_at_utc=timestamp(-4))
        self.assertEqual(len(polymarket_alerts.candidates([row], set(), NOW)), 1)

    def test_report_uses_the_amount_next_to_the_original_quote(self):
        message = {
            'message_id': 1,
            'date_utc': timestamp(-2),
            'collected_at_utc': timestamp(-1),
            'text': '\n'.join((
                '📈 🔷 Polymarket · 19 nov 2026 13:10',
                'A — B · Example League',
                'Over 2.5 @1.85 · $7.1K',
                '🧾 2 trades · biggest $4.4K · market share 72%',
                'Total on Over 2.5 this match: $15K',
                'https://scangoal.com/match/match-123?lang=en',
            )),
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'SCANGOAL_DATA_DIR': directory}):
            Path(directory, 'messages.jsonl').write_text(json.dumps(message) + '\n', encoding='utf-8')
            row, = report.build(now=NOW)[polymarket_alerts.REPORT_KEY]
        self.assertEqual((row['total'], row['share'], row['odds']), (7100, 72, 1.85))
        self.assertEqual(row['match_id'], 'match-123')


    def test_market_scope_and_state_on_original_message(self):
        raw = ("📈 🔷 Polymarket · 19 nov 2026 13:10\nA — B · Example League\n"
               "Over 2.5 @1.85 · $9.9K\n🧾 2 trades · market share 72%\n"
               "https://scangoal.com/match/a-vs-b-20261119?lang=en")
        for text, market, period in (
            (raw.replace("Polymarket ·", "Polymarket · 1st Half ·"), "1st Half - Asian Total", "first_half"),
            (raw.replace("Over 2.5", "Over 2.5 Corners"), "Total Corners", "regulation"),
            (raw.replace("Over 2.5", "Over 2,5 (90 min)"), "Asian Total", "regulation"),
        ):
            with self.subTest(market=market):
                parsed = parse(text)
                self.assertEqual((parsed["market"], parsed["period"], parsed["selection"]),
                                 (market, period, "Over 2.5"))
                alerts = polymarket_alerts.candidates([dict(signal(), **parsed)], set(), NOW)
                self.assertEqual(len(alerts), int(market == "Asian Total"))
        self.assertFalse(parse(raw.replace("Polymarket ·", "Polymarket · LIVE ·"))["bet"])
        live = parse(raw.replace("19 nov 2026 13:10", "LIVE 32' 1:1"))
        self.assertEqual((live["phase"], live["score_at_signal"]), ("live", [1, 1]))

    def test_original_header_scope_is_required_by_both_consumers(self):
        def evaluate(header, quote):
            parsed = parse("\n".join((
                "📈 🔷 Polymarket · " + header + "19 nov 2026 21:00",
                "A — B · Example League", quote + " @1.85 · $9.9K",
                "🧾 2 trades · market share 72%",
                "https://scangoal.com/match/a-vs-b-20261119?lang=en",
            )))
            row = dict(parsed, message_id=1, date_utc=timestamp(-3), collected_at_utc=timestamp(-2),
                       had_ft_at_first_sight=False, edited_at_first_sight=False, burst=False)
            alerts = polymarket_alerts.candidates([row], set(), NOW)
            eligible = opportunity_forward.eligible_signal(row, NOW - timedelta(hours=1), NOW + timedelta(minutes=20))
            return parsed, bool(alerts), eligible is not None

        for market in ('Yellow Cards', 'Team Total', 'Overtime', 'Unknown Scope', 'Player Total', 'Corners'):
            with self.subTest(market=market):
                original, alert, admitted = evaluate(market + " · ", "Over 2.5")
                self.assertFalse(alert or admitted)
                self.assertEqual(original['bet'], market == 'Corners')
        for quote in ('Team Total Over 2.5', 'Over 2.5 team goals', 'Over 2.5 unknown', '1H Over 2.5'):
            with self.subTest(quote=quote):
                self.assertEqual(evaluate("", quote)[1:], (False, False))
        self.assertEqual(evaluate("", "Over 2.5")[1:], (True, True))
        self.assertEqual(evaluate("Total Goals · ", "Over 2.5 (90 min)")[1:], (True, True))

    def test_amount_is_parsed_in_full_without_ambiguous_grouping(self):
        raw = ("📈 🔷 Polymarket · 19 nov 2026 13:10\nA — B · Example League\n"
               "Over 2.5 @1.85–2.10 · $VALUE\n🧾 2 trades · market share 72%")
        for token, amount in (("10,500", None), ("10.500", None), ("10 500", 10500),
                              ("10,500.00", 10500), ("10.500,00", 10500),
                              ("9,9K", 9900), ("9 500,25", 9500.25), ("9500 USD?", None)):
            with self.subTest(token=token):
                row = parse(raw.replace("VALUE", token))
                self.assertEqual(row.get("total"), amount)
                self.assertEqual(row["bet"], amount is not None)
                if amount is not None:
                    self.assertEqual(row["odds"], 1.85)
        self.assertFalse(parse(raw.replace("VALUE", "9000").replace("1.85–2.10", "2.10–1.85"))["bet"])

    def test_exact_line_and_unambiguous_share_reach_alert_and_study(self):
        raw = ("📈 🔷 Polymarket · HEADER19 nov 2026 21:00\nA — B · Example League\n"
               "Over LINE @1.85 · $9.9K\n🧾 2 trades · market share 72% SHARE\n"
               "https://scangoal.com/match/a-vs-b-20261119?lang=en")
        for header, line, share, selection, accepted in (
            ("", "2.50", "", "Over 2.5", True),
            ("", "2,50", "· Market share 72.00%", "Over 2.5", True),
            ("", "2.55", "", "Over 2.55", False),
            ("", "2.50", "· market share 93%", None, False),
            ("Yellow Cards · ", "2.50", "", None, False),
            ("Team Total · ", "2.50", "", None, False),
            ("Overtime · ", "2.50", "", None, False),
            ("Total Goals · Corners · ", "2.50", "", None, False),
            ("1st Half · Full Time · ", "2.50", "", None, False),
        ):
            with self.subTest(header=header, line=line, share=share):
                parsed = parse(raw.replace("HEADER", header).replace("LINE", line).replace("SHARE", share))
                self.assertEqual(parsed.get("selection"), selection)
                self.assertEqual(parsed["bet"], selection is not None)
                row = dict(parsed, message_id=1, date_utc=timestamp(-3), collected_at_utc=timestamp(-2),
                           had_ft_at_first_sight=False, edited_at_first_sight=False, burst=False)
                alerts = notification_report([row], NOW)
                self.assertEqual(bool(notify.messages(alerts, activated_store().load_state(), NOW)), accepted)
                eligible = opportunity_forward.eligible_signal(row, NOW - timedelta(hours=1), NOW + timedelta(minutes=20))
                self.assertEqual(eligible is not None, accepted)


def notification_report(messages, now, watch=()):
    return {
        'brain': {'watch': list(watch)},
        polymarket_alerts.REPORT_KEY: polymarket_alerts.candidates(messages, set(), now),
    }


def activated_store():
    state = notification_state()
    state['migrations'][polymarket_alerts.ACTIVATION] = {'at_utc': timestamp(-5)}
    return notify.MemoryStore(state)


class PolymarketDelivery(unittest.TestCase):
    def test_first_run_records_activation_and_later_reads_do_not_replay_old_signals(self):
        state = {'hello': True}
        self.assertEqual(notify.messages(notification_report([signal()], NOW), state, NOW), [])
        self.assertEqual(state['migrations'][polymarket_alerts.ACTIVATION]['at_utc'], NOW.isoformat())
        later = NOW + timedelta(minutes=2)
        old = signal(collected_at_utc=timestamp(1))
        self.assertEqual(notify.messages(notification_report([old], later), state, later), [])
        new = signal(date_utc=timestamp(1), collected_at_utc=timestamp(2))
        self.assertEqual(len(notify.messages(notification_report([old, new], later), state, later)), 1)

    def test_same_selection_uses_one_durable_notice_and_the_qualifying_quote(self):
        row = signal()
        generic = dict(polymarket_alerts.candidates([row], set(), NOW)[0],
                       date_utc=timestamp(-1), odds=2.14, type='STAKE_LOAD',
                       reasons=[{'id': 'Stake | Exemplo', 'status': 'promissora'}])
        rep = notification_report([row], NOW, [generic])
        store = activated_store()
        texts, published = [], []

        def publish(key):
            self.assertEqual(len(texts), 0)
            self.assertEqual(store.load_state()['deliveries'][key]['state'], 'in_flight')
            self.assertIn((key, 'intent'), [(e['key'], e['event']) for e in store.read_log()[0]])
            published.append(key)
            return True

        for expected in (1, 0):
            state, done = notify.deliver(store, rep, store.load_state(), NOW,
                                         lambda _, __, text: texts.append(text), 'token', 'chat', publish=publish)
            self.assertEqual(done, expected)
        self.assertEqual(len(published), 1)
        self.assertEqual(len(texts), 1)
        self.assertEqual(texts[0].splitlines(), ['🟠 INDÍCIO · Stake / Polymarket', '⚽ A — B',
                                              '🎯 Over 2,5 @ 1,85 · Asian Total · Polymarket',
                                              '🧪 1 hipótese promissora · sem certificação',
                                              'Polymarket pré-jogo · quota 79% · valor $9 900 · início 10:10 de 19/11 (Lisboa)',
                                              '🔗 https://scangoal.com/match/a-vs-b-20261119?lang=en'])
        self.assertIn(notify.item_key(generic), state['notified_items'])

    def test_fixed_notice_keeps_decimal_quote_quota_amount_and_summer_kickoff(self):
        now = datetime(2026, 7, 1, 22, 30, tzinfo=timezone.utc)
        row = signal(match_id='a-vs-b-20260701', date_utc='2026-07-01T22:27:00+00:00',
                     collected_at_utc='2026-07-01T22:29:00+00:00', kickoff_utc='2026-07-01T23:10:00+00:00',
                     odds=1.91, share=78.25, total=7150.50)
        store = activated_store()
        state = store.load_state()
        state['migrations'][polymarket_alerts.ACTIVATION]['at_utc'] = '2026-07-01T22:00:00+00:00'
        n, = notify.game_notices(notification_report([row], now), state, now)
        self.assertEqual(n.text.splitlines(), ['🟠 INDÍCIO · Polymarket', '⚽ A — B', '🎯 Over 2,5 @ 1,91 · Asian Total',
                                               'Polymarket pré-jogo · quota 78,25% · valor $7 150,5 · início 00:10 de 02/07 (Lisboa)',
                                               '🔗 https://scangoal.com/match/a-vs-b-20260701?lang=en'])

    def test_match_id_controls_the_link_without_changing_the_fixed_notice(self):
        cases = {
            'a-vs-b-20261119': True, 'match-123': True, 'Match_123.v2': True,
            'a-vs-b-20261119?bad=1': False, 'a#fragment': False, 'https://example.invalid': False,
            '../a': False, '..': False, '%2e%2e': False, 'a\\b': False, 'a b': False,
            'a\nhttps://example.invalid': False, 'a\u202eb': False, 'valid-123': False,
        }
        for match_id, has_link in cases.items():
            with self.subTest(match_id=match_id):
                row = signal(match_id=match_id)
                rep = notification_report([row], NOW)
                fixed, = rep[polymarket_alerts.REPORT_KEY]
                store = activated_store()
                n, = notify.game_notices(rep, store.load_state(), NOW)
                lines = ['🟠 INDÍCIO · Polymarket', '⚽ A — B', '🎯 Over 2,5 @ 1,85 · Asian Total',
                         'Polymarket pré-jogo · quota 79% · valor $9 900 · início 10:10 de 19/11 (Lisboa)']
                if has_link:
                    lines.append('🔗 https://scangoal.com/match/' + match_id + '?lang=en')
                self.assertEqual(n.text.splitlines(), lines)
                self.assertEqual(n.effects, [['item', notify.item_key(fixed)], ['watch_sent', notify.legacy_key(fixed)]])
                self.assertTrue(n.still_current(NOW))
                self.assertFalse(n.still_current(NOW + timedelta(minutes=10)))

                sent = []
                state, count = notify.deliver(store, rep, store.load_state(), NOW,
                                              lambda *args: sent.append(args[-1]), 'token', 'chat', publish=lambda _: True)
                self.assertEqual((count, sent), (1, [n.text]))
                self.assertEqual(notify.game_notices(rep, state, NOW), [])

    def test_alternate_match_id_does_not_relax_the_fixed_filter(self):
        for changes in ({'share': 80}, {'total': 10_000}, {'odds': 1.09}, {'phase': 'live'},
                        {'market_scope': 'corners'}, {'period': 'first_half'}, {'selection': 'Under 2.5'},
                        {'edited_at_first_sight': True}, {'date_utc': timestamp(-6)}, {'kickoff_utc': timestamp(0)}):
            with self.subTest(changes=changes):
                rep = notification_report([signal(match_id='match-123', **changes)], NOW)
                self.assertEqual(notify.game_notices(rep, activated_store().load_state(), NOW), [])

    def test_a_long_match_id_does_not_consume_the_fixed_notice_budget(self):
        rep = notification_report([signal(match_id='a' * 3900)], NOW)
        n, = notify.game_notices(rep, activated_store().load_state(), NOW)
        self.assertIn('🎯 Over 2,5 @ 1,85 · Asian Total', n.text)
        self.assertIn('quota 79% · valor $9 900 · início 10:10 de 19/11 (Lisboa)', n.text)
        self.assertNotIn('https://', n.text)

    def test_fixed_quote_survives_a_certified_price_from_another_source(self):
        row = signal()
        hid = 'Stake | Exemplo'
        generic = dict(polymarket_alerts.candidates([row], set(), NOW)[0], type='STAKE_LOAD', odds=2.14,
                       reasons=[{'id': hid, 'status': 'forte', 'certified': True}])
        rep = notification_report([row], NOW, [generic])
        rep['brain']['validation'] = {'method': notify.VALIDATION_METHOD, 'state': 'active', 'ledger_valid': True, 'certified': [hid]}
        rep['brain']['presentation'] = {'hypotheses': {hid: {'confirmation': {'certifiable': True, 'rank': 1, 'certified_utc': timestamp(-60)}}}}
        n, = notify.game_notices(rep, activated_store().load_state(), NOW)
        self.assertEqual(n.claims, [hid])
        self.assertEqual(n.text.splitlines()[2:5], ['🎯 Over 2,5 @ 2,14 · Asian Total · Stake', notify.scientific_label(rep, hid)[0],
                                                   'Polymarket pré-jogo · odd anunciada 1,85 · quota 79% · valor $9 900 · início 10:10 de 19/11 (Lisboa)'])
        self.assertTrue(notify.guard_claims(n.text, rep, n.claims, exact_lines=True))

    def test_kickoff_during_publication_blocks_the_send(self):
        row = signal(kickoff_utc=timestamp(1))
        # A second feed's later kickoff estimate cannot keep the Polymarket quote current.
        generic = dict(polymarket_alerts.candidates([row], set(), NOW)[0],
                       kickoff_utc=timestamp(5), reasons=[{'id': 'Stake | Exemplo', 'status': 'promissora'}])
        rep = notification_report([row], NOW, [generic])
        store = activated_store()
        after_publish = NOW

        def publish(_):
            nonlocal after_publish
            after_publish += timedelta(minutes=1)
            return True

        def sender(*_):
            self.fail('An expired signal must not be sent')

        state, done = notify.deliver(store, rep, store.load_state(), NOW, sender, 'token', 'chat',
                                     publish=publish, clock=lambda: after_publish)
        self.assertEqual(done, 0)
        record, = state['deliveries'].values()
        self.assertEqual(record['reason'], 'signal_expired_before_send')


if __name__ == '__main__':
    unittest.main()
