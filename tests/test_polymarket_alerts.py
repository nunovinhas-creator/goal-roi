"""Offline checks for the prematch Polymarket Over 2.5 candidate filter."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scangoal import notify, polymarket_alerts, report
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
        'market': 'Asian Total',
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
            'match_id': ('', None),
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
                'https://scangoal.com/match/a-vs-b-20261119?lang=en',
            )),
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'SCANGOAL_DATA_DIR': directory}):
            Path(directory, 'messages.jsonl').write_text(json.dumps(message) + '\n', encoding='utf-8')
            row, = report.build(now=NOW)[polymarket_alerts.REPORT_KEY]
        self.assertEqual((row['total'], row['share'], row['odds']), (7100, 72, 1.85))


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
        self.assertEqual(texts[0].count('Over 2.5 @ 1,85'), 1)
        self.assertNotIn('2,14', texts[0])
        self.assertIn('quota 79% · valor $9 900', texts[0])
        self.assertIn(notify.item_key(generic), state['notified_items'])

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
