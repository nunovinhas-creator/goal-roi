import io
import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

from scangoal import stake_alerts as alerts, store
from scangoal.stake_alert_text import MAX_UNITS, payloads

HEADER = '💸 🟦 Stake money load — $50K'
GROWING = '💸💸 🟦 Stake: load growing ×2 — $11K'
KICKOFF = 'League · 18 nov 2026 20:45'
PICK = '📊 Asian Total → Over 2.5 @ 1.85'
PRE = '\n'.join((HEADER, KICKOFF, 'Alpha — Beta', PICK))
LIVE = '\n'.join((GROWING, KICKOFF, 'Alpha 1', 'Beta 0', PICK))
OTHER = '💷 Betfair LIVE\nAlpha — Beta\n📊 Asian Total → Under 2.5 @ 2.15'
ACTIVATION = datetime(2026, 11, 18, 17, 0, tzinfo=timezone.utc).timestamp()


def row(mid, text=PRE, *, date='17:30', collected='18:13', edited=None):
    return {'message_id': mid, 'text': text, 'date_utc': f'2026-11-18T{date}:00+00:00' if date else None,
            'collected_at_utc': f'2026-11-18T{collected}:00+00:00', 'edit_date_utc': edited}


def blob(*rows):
    return ''.join(json.dumps(r) + '\n' for r in rows).encode()


def status(db):
    return db.execute('SELECT message_id, part, status FROM outbox ORDER BY id').fetchall()


class StakeText(unittest.TestCase):
    def test_prematch(self):
        alert, = payloads(row(22))
        for field in ('Alpha — Beta', 'PRÉ-JOGO', 'Asian Total', 'Over 2.5', 'Odd: 1.85', '17:30', 'mensagem #22'):
            self.assertIn(field, alert)

    def test_live_and_range(self):
        alert, = payloads(row(23, LIVE.replace('1.85', '1.01–1.09'), date='17:55'))
        self.assertIn('LIVE', alert)
        self.assertIn('Alpha — Beta', alert)
        self.assertIn('Odd: 1.01–1.09', alert)

    def test_any_market_or_odds(self):
        text = PRE.replace(PICK, '📊 Corners in 1st half → Under 2 @ 475,25')
        alert, = payloads(row(1, text))
        self.assertIn('Corners in 1st half', alert)
        self.assertIn('475,25', alert)

    def test_other_bookmaker_or_bare_mention(self):
        for text in (OTHER, 'Polymarket\n📊 Stake sizing → Under 2.5 @ 1.15', 'Use a 2% stake.'):
            self.assertEqual(list(payloads(row(1, text))), [])
        alert, = payloads(row(1, PRE + '\n' + OTHER))
        self.assertNotIn('Under 2.5', alert)
        inline, = payloads(row(2, PRE + ' | 📊 Kalshi: Asian Total → Under 2.5 @ 2'))
        self.assertIn('Over 2.5', inline)
        self.assertNotIn('Under 2.5', inline)

    def test_missing_and_cutoff_odds_are_not_invented(self):
        for suffix in ('', ' @ ', ' @ 1.'):
            text = PRE.replace(' @ 1.85', suffix)
            alert, = payloads(row(1, text))
            self.assertIn('Over 2.5', alert)
            self.assertNotIn('Odd:', alert)
        self.assertIn('Linha original', alert)

    def test_ambiguous_edited_layout_preserves_source(self):
        text = HEADER + '\nUnknown layout · with incomplete selection\n📊 odd unclear'
        alert, = payloads(row(1, text))
        self.assertIn(text, alert)
        self.assertIn('NÃO DETERMINADO', alert)
        for field in ('Mercado:', 'Seleção:', 'Odd:', 'Jogo:'):
            self.assertNotIn(field, alert)
        missing_selection = PRE.replace(PICK, '📊 Asian Total → @ 1.85')
        preserved, = payloads(row(4, missing_selection))
        self.assertIn(missing_selection, preserved)
        self.assertNotIn('Seleção:', preserved)
        edited, = payloads(row(2, LIVE, date='17:55', edited='2026-11-18T18:05:00+00:00'))
        self.assertIn('NÃO DETERMINADO', edited)
        conflict, = payloads(row(3, LIVE, date='17:30'))
        self.assertIn('NÃO DETERMINADO', conflict)

    def test_multiple_distinct_markets_and_multiple_blocks(self):
        second = '📊 Both Teams To Score → Yes @ 0.97'
        text = OTHER + '\n' + PRE + '\n' + PICK + ' | ' + second + '\n' + LIVE
        first, middle, last = payloads(row(1, text, date='17:55'))
        self.assertIn('Over 2.5', first)
        self.assertIn('0.97', middle)
        self.assertIn('Both Teams To Score', middle)
        self.assertIn('LIVE', last)
        self.assertTrue(all('Under 2.5' not in part for part in (first, middle, last)))

    def test_multiple_lines_with_ambiguous_selection_are_kept_in_one_fallback(self):
        text = PRE + '\n📊 ambiguous stats'
        alert, = payloads(row(1, text))
        self.assertIn(text, alert)
        self.assertNotIn('Odd:', alert)

    def test_long_original_has_no_truncation(self):
        raw = HEADER + '\n' + '⚽🎯é' * 2500
        parts = list(payloads(row(1, raw)))
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part.encode('utf-16-le')) // 2 <= MAX_UNITS for part in parts))
        original = parts[0].split('Mensagem original\n')[1] + ''.join(p.split('Continuação\n\n', 1)[1] for p in parts[1:])
        self.assertEqual(original, raw)


class DurableOutbox(unittest.TestCase):
    def test_baseline_skips_history_and_edits_even_if_published_late(self):
        historical = blob(row(300))
        with closing(sqlite3.connect(':memory:')) as db:
            alerts.initialize(db, historical, ACTIVATION)
            late_snapshot = historical + blob(
                row(300, LIVE), row(301, date='16:55'), row(302, collected='16:59'), row(299),
            )
            self.assertEqual(alerts.ingest(db, late_snapshot), 1)
            self.assertEqual(status(db), [(299, 1, 'pending')])

    def test_distinct_messages_repeat_the_same_pick_without_replays(self):
        source = blob(row(40), row(41), row(40, LIVE), row(42, OTHER))
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'stake.sqlite3'
            send = Mock(return_value=('sent', 0))
            with alerts.locked_db(state, create=True) as db:
                alerts.initialize(db, b'', ACTIVATION)
                self.assertEqual(alerts.ingest(db, source), 2)
                self.assertEqual(alerts.send_next(db, send, lambda: 100), 'sent')
            with alerts.locked_db(state) as db:
                self.assertEqual(alerts.recover(db), 0)
                self.assertEqual(alerts.ingest(db, source), 0)
                self.assertEqual(alerts.send_next(db, send, lambda: 105), 'sent')
                self.assertIsNone(alerts.send_next(db, send, lambda: 110))
                self.assertEqual(status(db), [(40, 1, 'sent'), (41, 1, 'sent')])
            self.assertEqual(send.call_count, 2)

    def test_interrupted_request_recovers_as_uncertain_without_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'stake.sqlite3'
            with alerts.locked_db(state, create=True) as db:
                alerts.initialize(db, b'', ACTIVATION)
                alerts.ingest(db, blob(row(40)))
                with self.assertRaises(KeyboardInterrupt):
                    alerts.send_next(db, Mock(side_effect=KeyboardInterrupt), lambda: 100)
            with alerts.locked_db(state) as db:
                self.assertEqual(alerts.recover(db), 1)
                send = Mock()
                self.assertIsNone(alerts.send_next(db, send, lambda: 200))
                send.assert_not_called()
                self.assertEqual(status(db), [(40, 1, 'uncertain')])

    def test_source_can_append_while_delivery_fails_and_retry_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = root / 'stake.sqlite3'
            original = root / 'messages.jsonl'
            with patch.dict('os.environ', {'SCANGOAL_DATA_DIR': folder}):
                store.append_versions([row(1)])
                before = original.read_bytes()
                with alerts.locked_db(state, create=True) as db:
                    alerts.initialize(db, b'', ACTIVATION)
                    alerts.ingest(db, before)
                    alerts.send_next(db, Mock(return_value=('pending', 17)), lambda: 100)
                self.assertEqual(original.read_bytes(), before)
                self.assertEqual(store.append_versions([row(2, LIVE)]), 1)
            with alerts.locked_db(state) as db:
                alerts.ingest(db, original.read_bytes())
                send = Mock(return_value=('sent', 0))
                self.assertIsNone(alerts.send_next(db, send, lambda: 116))
                send.assert_not_called()
                self.assertEqual(alerts.send_next(db, send, lambda: 117), 'sent')
                self.assertEqual(status(db), [(1, 1, 'sent'), (2, 1, 'pending')])

    def test_rewritten_or_invalid_snapshot_changes_neither_cursor_nor_outbox(self):
        baseline = blob(row(2))
        with closing(sqlite3.connect(':memory:')) as db:
            alerts.initialize(db, baseline, ACTIVATION)
            for rejected in (b'', blob(row(3)), baseline + b'{invalid'):
                with self.assertRaises(ValueError):
                    alerts.ingest(db, rejected)
            with self.assertRaises(ValueError):
                alerts.ingest(db, baseline + blob(row(3)) + b'{}\n')
            self.assertEqual(status(db), [])
            self.assertEqual(alerts.ingest(db, baseline + blob(row(3))), 1)

    def test_multiple_alerts_per_message_are_enqueued_once(self):
        text = PRE + '\n📊 Both Teams To Score → Yes'
        source = blob(row(12, text))
        with closing(sqlite3.connect(':memory:')) as db:
            alerts.initialize(db, b'', ACTIVATION)
            self.assertEqual(alerts.ingest(db, source), 2)
            self.assertEqual(alerts.ingest(db, source + blob(row(12, text + '\n🏁 FT 2:1'))), 0)
            self.assertEqual(status(db), [(12, 1, 'pending'), (12, 2, 'pending')])

    def test_same_state_is_exclusive_and_missing_state_cannot_enable(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'state.sqlite3'
            with self.assertRaises(sqlite3.OperationalError), alerts.locked_db(state):
                self.fail('opened an uninitialized database')
            with alerts.locked_db(state, create=True):
                with self.assertRaises(BlockingIOError), alerts.locked_db(state):
                    self.fail('two consumers own the same state')


class TelegramTransport(unittest.TestCase):
    def test_positive_confirmation_and_rate_limit(self):
        for http, content, expected in (
            (200, b'{"ok":true}', ('sent', 0)),
            (200, b'bad response', ('uncertain', alerts.RETRY_SECONDS)),
            (429, b'{"parameters":{"retry_after":42}}', ('pending', 42)),
            (429, b'{}', ('pending', alerts.RETRY_SECONDS)),
            (403, b'{}', ('pending', alerts.RETRY_SECONDS)),
            (400, b'{}', ('rejected', 0)),
            (503, b'{}', ('uncertain', alerts.RETRY_SECONDS)),
        ):
            with self.subTest(http=http, content=content):
                conn = Mock()
                conn.getresponse.return_value = response = io.BytesIO(content)
                response.status = http
                connect = Mock(return_value=conn)
                self.assertEqual(alerts.telegram_send('fixture', 'test-chat', 'alert', connect), expected)
                connect.assert_called_once_with('api.telegram.org', timeout=15)
                method, endpoint, body, _ = conn.request.call_args.args
                self.assertEqual((method, endpoint), ('POST', '/botfixture/sendMessage'))
                self.assertEqual(parse_qs(body)['text'], ['alert'])
                conn.request.assert_called_once()

    def test_transport_errors_do_not_retry_ambiguous_delivery(self):
        for exc, status_name in ((ConnectionRefusedError(), 'pending'), (TimeoutError(), 'uncertain')):
            conn = Mock()
            conn.request.side_effect = exc
            self.assertEqual(alerts.telegram_send('fixture', 'test-chat', 'alert', Mock(return_value=conn))[0], status_name)


class PublishedGitSource(unittest.TestCase):
    def test_independent_reader_reads_remote_main_and_leaves_producer_untouched(self):
        def run(repo, *args):
            return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, check=True, text=True).stdout

        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            producer, consumer = base / 'producer', base / 'consumer'
            run(base, 'init', '-b', 'main', str(producer))
            data = producer / 'data'
            data.mkdir()
            source = data / 'messages.jsonl'
            source.write_bytes(blob(row(1)))
            run(producer, 'add', 'data')
            run(producer, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org', 'commit', '-m', 'published')
            run(base, 'clone', '--bare', '--depth', '1', producer.as_uri(), str(consumer))
            published = source.read_bytes()
            source.write_bytes(published + blob(row(2)))
            before = run(producer, 'status', '--porcelain')
            with patch.object(alerts, 'ORIGIN', producer.as_uri()):
                with self.assertRaises(ValueError):
                    alerts.published_snapshot(producer)
                self.assertEqual(alerts.published_snapshot(consumer), published)
            self.assertEqual(run(producer, 'status', '--porcelain'), before)
            self.assertEqual(source.read_bytes(), published + blob(row(2)))


if __name__ == '__main__':
    unittest.main()
