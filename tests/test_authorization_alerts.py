"""Offline authorization alert regressions. Git pushes only to a temporary bare repo."""
import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from telethon.errors import SessionRevokedError

from scangoal import authorization_alerts as alerts
from scangoal import authorization_events as events
from scangoal import notify, reader, watch
from test_telegram_diagnostics import Client

NOW = datetime(2026, 11, 27, 18, 14, tzinfo=timezone.utc)
REVOCATION = 'SessionRevokedError'
ROOT = Path(__file__).resolve().parents[1]


class GitAlerts(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.origin = self.root / 'origin'
        self.git('init', '--bare', '-q', str(self.origin))
        self.checkout = self.clone('first')
        self.storage = alerts.GitState(self.checkout)
        self.time = NOW
        self.sender = Mock()
        self.alerts = self.worker(self.storage, self.sender)

    def git(self, *args):
        return subprocess.run(['git', *args], check=True, capture_output=True, text=True).stdout.strip()

    def clone(self, name):
        path = self.root / name
        self.git('clone', '-q', str(self.origin), str(path))
        return path

    def worker(self, storage, sender):
        return alerts.Alerts(storage, 'synthetic', 'synthetic', sender, lambda: self.time.isoformat())

    def detect(self, service=None):
        service = service or self.alerts
        state = service.detect(self.time.isoformat(), REVOCATION)
        return state['active'], state

    def test_same_incident_after_48_hours_and_fresh_checkout(self):
        first, state = self.detect()
        self.alerts.deliver(first, state)
        self.time += timedelta(hours=48)
        other = self.worker(alerts.GitState(self.clone('restart')), self.sender)
        again, state = self.detect(other)
        self.assertEqual(again, first)
        other.deliver(again, state)
        self.sender.assert_called_once()
        self.assertEqual(self.storage.read()[1]['incidents'][first]['status'], 'confirmed')

    def test_recovery_requires_matching_token_and_new_revocation_gets_new_id(self):
        first, state = self.detect()
        self.alerts.deliver(first, state)
        self.time += timedelta(minutes=1)
        self.alerts.recover('a' * 32, self.time.isoformat())
        self.assertEqual(self.storage.read()[1]['active'], first)
        self.alerts.recover(first, self.time.isoformat())
        self.assertIsNone(self.storage.read()[1]['active'])
        self.assertIsNone(self.alerts.detect(NOW.isoformat(), REVOCATION)['active'])
        self.time += timedelta(minutes=1)
        second, state = self.detect()
        self.alerts.recover(first, self.time.isoformat())
        self.assertNotEqual(first, second)
        self.alerts.deliver(second, state)
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(len(self.storage.read()[1]['incidents']), 2)

    def test_outcome_after_concurrent_authenticated_recovery_is_preserved(self):
        first, state = self.detect()
        def recovered_during_send(*_args):
            self.time += timedelta(seconds=1)
            self.alerts.recover(first, self.time.isoformat())
            self.time += timedelta(seconds=1)
            self.detect()
        self.sender.side_effect = recovered_during_send
        result = self.alerts.deliver(first, state)
        self.assertNotEqual(first, result['active'])
        self.assertEqual(result['incidents'][first]['status'], 'confirmed')
        self.assertIn('recovered_at', result['incidents'][first])

    def test_ref_unavailable_and_failed_reservation_prevent_send(self):
        with patch.object(self.storage, 'read', side_effect=alerts.StateUnavailable):
            with self.assertRaises(alerts.StateUnavailable):
                self.detect()
        incident, state = self.detect()
        with patch.object(self.storage, 'write', return_value=False):
            with self.assertRaises(alerts.StateUnavailable):
                self.alerts.deliver(incident, state)
        self.sender.assert_not_called()
        self.assertEqual(self.storage.read()[1]['incidents'][incident]['status'], 'detected')
        self.alerts.deliver(incident, state)
        self.sender.assert_called_once()

    def test_confirmation_publication_failure_can_be_retried_without_resend(self):
        incident, state = self.detect()
        real_write = self.storage.write
        def fail_confirmation(tip, current):
            if current['incidents'][incident]['status'] == 'confirmed':
                return False
            return real_write(tip, current)
        with patch.object(self.storage, 'write', side_effect=fail_confirmation):
            with self.assertRaises(alerts.StateUnavailable):
                self.alerts.deliver(incident, state)
        value = self.storage.read()[1]
        self.assertEqual(value['incidents'][incident]['status'], 'attempting')
        other = self.worker(alerts.GitState(self.clone('fresh')), self.sender)
        other.deliver(incident, value)
        self.alerts.flush_outcome()
        self.sender.assert_called_once()
        self.assertEqual(self.storage.read()[1]['incidents'][incident]['status'], 'confirmed')

    def test_lost_reply_and_known_rejection_remain_distinct_and_are_not_retried(self):
        for failure, expected in ((notify.DeliveryUncertain('synthetic'), 'uncertain'),
                                  (notify.DeliveryFailed('synthetic'), 'rejected')):
            with self.subTest(status=expected):
                self.time += timedelta(minutes=1)
                incident, state = self.detect()
                self.sender.side_effect = failure
                result = self.alerts.deliver(incident, state)
                self.assertEqual(result['incidents'][incident]['status'], expected)
                before = self.sender.call_count
                self.alerts.deliver(incident, result)
                self.assertEqual(self.sender.call_count, before)
                self.alerts.recover(incident, self.time.isoformat())

    def test_bot_unconfigured_leaves_incident_eligible_for_later_attempt(self):
        incident, state = self.detect()
        unconfigured = alerts.Alerts(self.storage, sender=self.sender, clock=lambda: self.time.isoformat())
        unconfigured.deliver(incident, state)
        self.sender.assert_not_called()
        self.alerts.deliver(incident, self.storage.read()[1])
        self.sender.assert_called_once()

    def test_cancellation_after_attempt_claim_is_uncertain(self):
        incident, state = self.detect()
        self.sender.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            self.alerts.deliver(incident, state)
        state = self.storage.read()[1]
        self.assertEqual(state['incidents'][incident]['status'], 'uncertain')
        self.alerts.deliver(incident, state)
        self.sender.assert_called_once()

    def test_independent_dispatch_failure_still_alerts(self):
        first, state = self.detect()
        self.alerts.deliver(first, state)
        with patch.dict(os.environ, SCANGOAL_DATA_DIR=str(self.root / 'data')):
            env = {'TG_BOT_TOKEN': 'synthetic', 'TG_CHAT_ID': 'synthetic', 'NEXT_COLLECTION_FAILED': '1'}
            self.assertTrue(notify.failure(self.sender, self.time, env))
            self.assertFalse(notify.failure(self.sender, self.time, env))
        self.assertEqual(self.sender.call_count, 2)
        self.assertIn('perdeu autorização Telegram', self.sender.call_args_list[0].args[-1])
        self.assertIn('falhou às', self.sender.call_args_list[1].args[-1])

    def test_competing_processes_only_one_sends(self):
        targets = (self.checkout, self.clone('concurrent'))
        sent = self.root / 'sent'
        script = """
from pathlib import Path
import sys
from scangoal.authorization_alerts import Alerts, GitState
checkout, sent, at = sys.argv[1:]
def sender(*args):
    with open(sent, 'a') as out:
        out.write('attempt\\n')
worker = Alerts(GitState(Path(checkout)), 'synthetic', 'synthetic', sender)
state = worker.detect(at, 'SessionRevokedError')
worker.deliver(state['active'], state)
"""
        env = {**os.environ, 'PYTHONPATH': str(ROOT)}
        with contextlib.ExitStack() as stack:
            processes = [stack.enter_context(subprocess.Popen(
                [sys.executable, '-c', script, str(target), str(sent), NOW.isoformat()],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)) for target in targets]
            for process in processes:
                stdout, stderr = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertEqual(sent.read_text().splitlines(), ['attempt'])
        self.assertEqual(len(self.storage.read()[1]['incidents']), 1)


class DetectionHandoff(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.dict(os.environ, {'SCANGOAL_AUTH_ALERT_DIR': str(self.dir),
                                                 'SCANGOAL_DATA_DIR': str(self.dir / 'data'), 'TG_SCANGOAL': 'synthetic'},
                                     clear=True))

    def queued(self):
        return [json.loads(path.read_text()) for path in sorted(self.dir.glob('*.event'))]

    def test_reader_connect_and_disconnect_emit_without_bot_credentials(self):
        for phase in ('connect', 'disconnect_error', 'background_on_disconnect'):
            failure = SessionRevokedError(request=None)
            client = Client('connect', failure) if phase == 'connect' else Client(**{phase: failure})
            with self.subTest(phase=phase), patch.object(reader, 'make_client', return_value=client):
                with self.assertRaises(SessionRevokedError) as raised:
                    asyncio.run(reader.collect())
                self.assertIs(raised.exception, failure)
                self.assertEqual(self.queued()[-1]['kind'], 'loss')
        self.assertEqual(len(self.queued()), 3)

    def test_reader_backfill_handoff_precedes_disconnect_and_preserves_receipt(self):
        store = self.dir / 'data'
        store.mkdir()
        (store / 'collection_windows.json').write_text(json.dumps({'full_high_id': 41, 'intervals': [
            {'after_id': 1, 'before_id': 10, 'cursor': 10, 'status': 'pending'}]}))
        client = Client('recover_page', SessionRevokedError(request=None))
        original_disconnect = client.disconnect
        async def close():
            self.assertEqual(self.queued()[-1]['kind'], 'loss')
            await original_disconnect()
        with patch.object(reader, 'make_client', return_value=client), patch.object(client, 'disconnect', close):
            with self.assertRaises(SessionRevokedError):
                asyncio.run(reader.collect())
        self.assertEqual(json.loads((store / 'state.json').read_text())['read'], 1)
        self.assertEqual(json.loads((store / 'collection_windows.json').read_text())['intervals'][0]['cursor'], 10)

    def test_watch_all_four_rounds_handoff_is_at_detection_before_final_refresh(self):
        for ordinal in range(1, 5):
            with self.subTest(ordinal=ordinal):
                for path in self.dir.glob('*.event'):
                    path.unlink()
                clock = [0]
                detections = []
                readings = 0
                async def sleep(seconds):
                    clock[0] += seconds
                async def read():
                    nonlocal readings
                    readings += 1
                    if readings == ordinal:
                        raise SessionRevokedError(request=None)
                    return 0
                def stamp():
                    detections.append(clock[0])
                    return NOW.isoformat()
                def refresh():
                    self.assertTrue(self.queued())
                with patch.object(events, 'utc_now', stamp), self.assertRaises(SessionRevokedError):
                    asyncio.run(watch.rounds(read, refresh, sleep=sleep, clock=lambda: clock[0]))
                self.assertEqual(detections, [120 * ordinal])
                self.assertEqual(clock[0], 120 * ordinal)

    def test_recovery_event_requires_token_before_authenticated_read(self):
        incident = 'f' * 32
        with patch.object(reader, 'make_client', return_value=Client()):
            asyncio.run(reader.collect())
        self.assertEqual(self.queued(), [])
        events.write_atomic(self.dir / 'incident', incident)
        with patch.object(reader, 'make_client', return_value=Client()):
            asyncio.run(reader.collect())
        self.assertEqual(self.queued()[0]['incident'], incident)
        self.assertEqual(self.queued()[0]['kind'], 'recovery')
        with patch.object(reader, 'make_client', return_value=Client()), patch.object(reader, 'RECENT', 0):
            asyncio.run(reader.collect())
        self.assertEqual(len(self.queued()), 1)

    def test_worker_drains_events_without_publication_or_dispatch_dependencies(self):
        events.loss(SessionRevokedError(request=None))
        (self.dir / 'stop').touch()
        storage = MemoryState()
        clock = VirtualClock()
        sender = Mock(side_effect=lambda *_: clock.advance(3))
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            alerts.run(self.dir, alerts.Alerts(storage, 'synthetic', 'synthetic', sender, clock.stamp), pause=clock.advance)
        sender.assert_called_once()
        self.assertTrue((self.dir / 'stopped').exists())
        self.assertEqual(self.queued(), [])
        self.assertIn('AUTH_ALERT_CONFIRMED', captured.getvalue())


class VirtualClock:
    def __init__(self):
        self.time = NOW

    def advance(self, seconds):
        self.time += timedelta(seconds=seconds)

    def stamp(self):
        return self.time.isoformat()


class MemoryState:
    def __init__(self):
        self.state = alerts.initial_state()

    def read(self):
        return '', self.state

    def update(self, change):
        change(self.state)
        return self.state
