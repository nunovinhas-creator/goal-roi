"""Regressões da classificação e dos prazos RPC, com clientes simulados."""

import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scangoal import session_guard as guard

try:
    from telethon import errors as telethon_errors
except ImportError:
    telethon_errors = None


NOW = datetime(2026, 11, 27, 20, tzinfo=timezone.utc)
FINGERPRINT = guard.fingerprint('sessão sintética', 'chave sintética')


class RPCError(Exception):
    pass


class UnauthorizedError(RPCError):
    code = 401


class SessionPasswordNeededError(UnauthorizedError):
    pass


class AuthKeyUnregisteredError(UnauthorizedError):
    pass


class AuthKeyPermEmptyError(UnauthorizedError):
    pass


class FloodWaitError(RPCError):
    code = 420

    def __init__(self, seconds):
        self.seconds = seconds


class UnrecognizedRPCError(RPCError):
    code = 499


class RPCClassification(unittest.IsolatedAsyncioTestCase):
    async def test_generic_unauthorized_and_unlisted_subclass_do_not_revoke_session(self):
        for error in (UnauthorizedError(), SessionPasswordNeededError(), AuthKeyPermEmptyError()):
            with self.subTest(error=type(error).__name__):
                diagnosis = guard.classify(error)
                self.assertFalse(diagnosis.definitive)
                self.assertFalse(diagnosis.retry)

        diagnosis = guard.classify(AuthKeyUnregisteredError())
        self.assertEqual(diagnosis.kind, 'AUTH_KEY_UNREGISTERED')
        self.assertTrue(diagnosis.definitive)

    async def test_cancellation_keeps_its_type_and_identity(self):
        cancellation = asyncio.CancelledError('cancelamento sintético')
        with self.assertRaises(asyncio.CancelledError) as classified:
            guard.classify(cancellation)
        self.assertIs(classified.exception, cancellation)

        async def request(_):
            raise cancellation

        with self.assertRaises(asyncio.CancelledError) as raised:
            await guard.diagnose(request, object())
        self.assertIs(raised.exception, cancellation)

    async def test_cancelling_diagnose_cancels_the_pending_request(self):
        started, cancelled, pending = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def request(_):
            started.set()
            try:
                await pending.wait()
            finally:
                cancelled.set()

        task = asyncio.create_task(guard.diagnose(request, object()))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())

    @unittest.skipUnless(telethon_errors, 'Telethon indisponível')
    async def test_real_telethon_classes_without_client_or_connection(self):
        password = telethon_errors.SessionPasswordNeededError(request=None)
        self.assertIsInstance(password, telethon_errors.UnauthorizedError)
        self.assertFalse(guard.classify(password).definitive)
        temporary_key = telethon_errors.AuthKeyPermEmptyError(request=None)
        self.assertFalse(guard.classify(temporary_key).definitive)
        revoked = telethon_errors.AuthKeyUnregisteredError(request=None)
        self.assertEqual(guard.classify(revoked).kind, 'AUTH_KEY_UNREGISTERED')
        flood_errors = (telethon_errors.FloodWaitError, telethon_errors.FloodPremiumWaitError,
                        telethon_errors.SlowModeWaitError, telethon_errors.FloodTestPhoneWaitError)
        for error_class in flood_errors:
            with self.subTest(error=error_class.__name__):
                diagnosis = guard.classify(error_class(request=None, capture=900))
                self.assertEqual(diagnosis.kind, 'FLOOD_WAIT')
                self.assertEqual(diagnosis.retry_after, 900)


class RPCBreaker(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'breaker.json'
        self.breaker = guard.Breaker(self.path)

    def test_unknown_rpc_error_stops_attempt_without_opening_breaker(self):
        diagnosis = guard.classify(UnrecognizedRPCError('texto não seguro'))
        self.assertEqual((diagnosis.kind, diagnosis.definitive, diagnosis.retry), ('UNKNOWN', False, False))
        decision = self.breaker.decide(diagnosis, FINGERPRINT, NOW)
        self.assertEqual(decision.action, 'stop')
        self.assertEqual(self.breaker.before_connect(FINGERPRINT, NOW).action, 'proceed')

    def test_flood_wait_blocks_a_new_client_until_full_deadline_after_restart(self):
        diagnosis = guard.classify(FloodWaitError(900))
        self.assertEqual(diagnosis.kind, 'FLOOD_WAIT')
        self.assertEqual(diagnosis.retry_after, 900)
        decision = self.breaker.decide(diagnosis, FINGERPRINT, NOW)
        deadline = NOW + timedelta(seconds=900)
        self.assertEqual((decision.action, decision.not_before), ('defer', deadline))

        restarted = guard.Breaker(self.path)
        before_deadline = restarted.before_connect(FINGERPRINT, deadline - timedelta(microseconds=1))
        self.assertEqual((before_deadline.action, before_deadline.not_before), ('defer', deadline))
        rotated = guard.fingerprint('sessão substituta sintética', 'chave sintética')
        self.assertEqual(restarted.before_connect(rotated, NOW).action, 'defer')
        self.assertEqual(restarted.before_connect(FINGERPRINT, deadline).action, 'proceed')

    def test_late_success_and_shorter_flood_do_not_erase_a_longer_pending_wait(self):
        original = guard.classify(FloodWaitError(900))
        self.breaker.decide(original, FINGERPRINT, NOW)
        self.breaker.decide(None, FINGERPRINT, NOW + timedelta(seconds=30))
        self.breaker.decide(guard.classify(FloodWaitError(5)), FINGERPRINT, NOW + timedelta(seconds=60))

        pending = self.breaker.before_connect(FINGERPRINT, NOW + timedelta(seconds=90))
        self.assertEqual((pending.action, pending.not_before), ('defer', NOW + timedelta(seconds=900)))

    def test_zero_flood_wait_has_a_one_second_floor(self):
        diagnosis = guard.classify(FloodWaitError(0))
        decision = self.breaker.decide(diagnosis, FINGERPRINT, NOW)
        self.assertEqual(diagnosis.retry_after, 1)
        self.assertEqual((decision.action, decision.not_before), ('defer', NOW + timedelta(seconds=1)))

    def test_missing_or_invalid_flood_wait_never_allows_immediate_retry(self):
        missing = FloodWaitError(None)
        del missing.seconds
        for error in (missing, FloodWaitError(False), FloodWaitError(-1), FloodWaitError('900')):
            with self.subTest(error=vars(error)):
                diagnosis = guard.classify(error)
                self.assertFalse(diagnosis.retry)
                self.assertIsNone(diagnosis.retry_after)
                self.assertEqual(self.breaker.decide(diagnosis, FINGERPRINT, NOW).action, 'stop')

    def test_run_does_not_connect_a_second_time_during_flood_wait(self):
        connections = []

        @asynccontextmanager
        async def connect():
            connections.append('connect')

            async def client(_):
                raise FloodWaitError(900)

            yield client

        async def attempts():
            first = await self.breaker.run(FINGERPRINT, connect, object())
            second = await guard.Breaker(self.path).run(FINGERPRINT, connect, object())
            self.assertEqual((first.action, second.action), ('defer', 'defer'))
            self.assertEqual(first.not_before, second.not_before)

        asyncio.run(attempts())
        self.assertEqual(connections, ['connect'])

    def test_run_releases_the_connection_and_lease_on_cancellation(self):
        async def cancel_request(suppress):
            started, pending, disconnected = asyncio.Event(), asyncio.Event(), asyncio.Event()

            @asynccontextmanager
            async def connect():
                async def client(_):
                    started.set()
                    await pending.wait()

                try:
                    yield client
                except asyncio.CancelledError:
                    if not suppress:
                        raise
                finally:
                    disconnected.set()

            task = asyncio.create_task(self.breaker.run(FINGERPRINT, connect, object()))
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(disconnected.is_set())

        for suppress in (False, True):
            with self.subTest(suppress=suppress):
                asyncio.run(cancel_request(suppress))
                self.assertEqual(self.breaker.before_connect(FINGERPRINT).action, 'proceed')
                with guard.Lease(self.path):
                    pass
