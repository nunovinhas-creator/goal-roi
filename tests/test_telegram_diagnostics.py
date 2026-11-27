"""Diagnóstico da sessão com exceções reais do Telethon e transporte simulado."""

import asyncio
import io
import logging
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telethon import errors, functions

from scangoal import reader, telegram_diagnostics, watch


SIMULATED_DETAIL = 'marcador-sintético\n::warning::texto de diagnóstico'


class Client:
    def __init__(self, failure_at=None, error=None, disconnect_error=None):
        self.failure_at = failure_at
        self.error = error
        self.disconnect_error = disconnect_error
        self.connected = False
        self.calls = []
        self._updates_error = None

    def record(self, action):
        self.calls.append(action)
        if self.failure_at == action:
            raise self.error

    def is_connected(self):
        return self.connected

    async def connect(self):
        self.record('connect')
        self.connected = True

    async def get_entity(self, _name):
        self.record('get_entity')
        return 'bot'

    async def iter_messages(self, _entity, *, limit):
        self.record('iter_messages')
        yield SimpleNamespace(id=1, raw_text='mensagem sintética', date=datetime.now(timezone.utc), buttons=None)

    async def disconnect(self):
        self.record('disconnect')
        self.connected = False
        if self.disconnect_error:
            raise self.disconnect_error


class TelegramDiagnostics(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {
            'SCANGOAL_DATA_DIR': directory, 'TG_SESSION': '', 'TG_API_ID': '1', 'TG_API_HASH': 'abc',
            'TG_SCANGOAL': 'bot',
        }, clear=True))

    def run_cli(self, main):
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as stopped:
            main()
        return stopped.exception.code, stdout.getvalue()

    def watch_rounds(self, read, refresh, **kwargs):
        real = self.real_rounds
        clock = [0]

        async def sleep(seconds):
            clock[0] += seconds

        return real(read, refresh, sleep=sleep, clock=lambda: clock[0], every=120, total=480, **kwargs)

    def test_definitive_authentication_requires_a_specific_error(self):
        for cls in (errors.AuthKeyDuplicatedError, errors.AuthKeyInvalidError, errors.AuthKeyUnregisteredError,
                    errors.SessionRevokedError, errors.SessionExpiredError, errors.UserDeactivatedError,
                    errors.UserDeactivatedBanError):
            with self.subTest(cls=cls):
                self.assertTrue(telegram_diagnostics.is_definitive_auth_error(cls(request=None)))
        for exc in (errors.UnauthorizedError(None, SIMULATED_DETAIL, 401),
                    errors.AuthKeyPermEmptyError(request=None), errors.SessionPasswordNeededError(request=None),
                    errors.AuthKeyNotFound(), errors.FloodWaitError(request=None, capture=120)):
            with self.subTest(cls=type(exc)):
                self.assertFalse(telegram_diagnostics.is_definitive_auth_error(exc))

    def test_reader_preserves_rpc_errors_from_each_network_operation(self):
        for action, attempted in (
            ('connect', ['connect']),
            ('get_entity', ['connect', 'get_entity']),
            ('iter_messages', ['connect', 'get_entity', 'iter_messages']),
        ):
            with self.subTest(action=action):
                error = errors.AuthKeyUnregisteredError(request=None)
                client = Client(action, error, disconnect_error=RuntimeError(SIMULATED_DETAIL))
                with patch.object(reader, 'make_client', return_value=client), redirect_stdout(io.StringIO()) as out:
                    with self.assertRaises(errors.AuthKeyUnregisteredError) as raised:
                        asyncio.run(reader.collect())
                self.assertIs(raised.exception, error)
                self.assertEqual(client.calls, attempted + ['disconnect'])
                self.assertEqual(out.getvalue().strip(), 'SCANGOAL_DISCONNECT_FAILED RuntimeError')

    def test_reader_cli_emits_rpc_class_and_safe_number_only(self):
        error = errors.RPCError(request=None, message=SIMULATED_DETAIL, code=401)
        with patch.object(reader, 'make_client', return_value=Client('get_entity', error)):
            self.assertEqual(self.run_cli(reader.main), (1, 'SCANGOAL_READ_FAILED RPCError code=401\n'))
        for code in ('401\n' + SIMULATED_DETAIL, True, 10000):
            with self.subTest(code=code):
                error = errors.RPCError(request=None, message=SIMULATED_DETAIL, code=code)
                self.assertEqual(telegram_diagnostics.error_fields(error), ('RPCError',))
        invalid_class = type('Error\n::warning::texto', (Exception,), {})
        self.assertEqual(telegram_diagnostics.error_fields(invalid_class()), ('Exception',))

    def test_library_uses_original_rpc_failure_and_keeps_raw_logs_local(self):
        # Construção offline: StringSession vazia, sem connect(), com credenciais sintéticas.
        client = reader.make_client()
        error = errors.ServerError(request=None, message=SIMULATED_DETAIL, code=500)
        sender = SimpleNamespace(send=AsyncMock(side_effect=error))
        with self.assertNoLogs(logging.getLogger(), level='WARNING'), \
                patch('telethon.client.users.asyncio.sleep', new_callable=AsyncMock):
            with self.assertRaises(errors.ServerError) as raised:
                asyncio.run(client._call(sender, functions.help.GetConfigRequest()))
        self.assertIs(raised.exception, error)
        self.assertEqual(sender.send.await_count, 2)
        self.assertTrue(client._no_updates)

    def test_last_fast_read_with_definitive_error_exits_after_cadence_wait(self):
        error = errors.AuthKeyUnregisteredError(request=None)
        client = Client()
        refresh = Mock()
        self.real_rounds = watch.rounds
        original = client.iter_messages

        async def read_page(entity, limit):
            if client.calls.count('iter_messages') == 3:
                client.calls.append('iter_messages')
                raise error
            async for message in original(entity, limit=limit):
                yield message

        sleep = Mock()
        clock = SimpleNamespace(monotonic=lambda: 100, sleep=sleep)
        with patch.dict(os.environ, TG_SESSION='sintética'), patch.object(client, 'iter_messages', read_page), \
                patch.object(reader, 'make_client', return_value=client), \
                patch.object(watch, 'rounds', self.watch_rounds), patch.object(watch, 'refresh', refresh), \
                patch.object(watch, 'time', clock):
            code, stdout = self.run_cli(watch.main)
        self.assertEqual(code, watch.FAILED_EXIT)
        self.assertIn('WATCH_READ_FAILED AuthKeyUnregisteredError code=401', stdout)
        self.assertIn('WATCH_PROBLEMS authorization_lost', stdout)
        self.assertIn('WATCH_FAILED AuthKeyUnregisteredError code=401', stdout)
        self.assertNotIn('WATCH_DONE', stdout)
        self.assertEqual(client.calls, ['connect', 'get_entity', *(['iter_messages'] * 4), 'disconnect'])
        refresh.assert_called_once()
        sleep.assert_called_once_with(watch.WATCH_SECONDS)

    def test_first_definitive_error_is_reraised_without_another_attempt(self):
        for action in ('connect', 'get_entity', 'iter_messages'):
            with self.subTest(action=action):
                error = errors.SessionRevokedError(request=None)
                client = Client(action, error)
                self.real_rounds = watch.rounds
                with patch.object(reader, 'make_client', return_value=client), \
                        patch.object(watch, 'rounds', self.watch_rounds), redirect_stdout(io.StringIO()) as stdout:
                    with self.assertRaises(errors.SessionRevokedError) as raised:
                        asyncio.run(watch.watch())
                self.assertIs(raised.exception, error)
                self.assertEqual(client.calls.count(action), 1)
                self.assertEqual(client.calls.count('connect'), 1)
                self.assertEqual(client.calls[-1], 'disconnect')
                self.assertIn('WATCH_PROBLEMS authorization_lost', stdout.getvalue())

    def test_generic_unauthorized_can_follow_normal_recovery_policy(self):
        error = errors.UnauthorizedError(request=None, message=SIMULATED_DETAIL, code=401)
        results = iter([error, 0, 0, 0])
        self.real_rounds = watch.rounds

        async def read():
            result = next(results)
            if result is error:
                raise error
            return result

        with redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(asyncio.run(self.watch_rounds(read, Mock())), 3)
        self.assertIn('WATCH_READ_FAILED UnauthorizedError code=401', stdout.getvalue())
        self.assertNotIn(SIMULATED_DETAIL, stdout.getvalue())

    def test_revocation_reported_by_background_is_preserved_without_reconnect(self):
        for stage in ('before_read', 'after_reads', 'aborted_request'):
            with self.subTest(stage=stage):
                client = Client()
                error = errors.AuthKeyUnregisteredError(request=None)

                async def rounds(read, _refresh, **_kwargs):
                    if stage == 'after_reads':
                        client._updates_error = error
                        return 3
                    if stage == 'before_read':
                        client._updates_error = error
                    else:
                        async def cancelled_connect():
                            client.calls.append('connect')
                            client._updates_error = error
                            raise asyncio.CancelledError()
                        client.connect = cancelled_connect
                    return await read()

                with patch.object(reader, 'make_client', return_value=client), patch.object(watch, 'rounds', rounds), \
                        redirect_stdout(io.StringIO()):
                    with self.assertRaises(errors.AuthKeyUnregisteredError) as raised:
                        asyncio.run(watch.watch())
                self.assertIs(raised.exception, error)
                self.assertEqual(client.calls, ['connect', 'disconnect'] if stage == 'aborted_request' else ['disconnect'])

    def test_external_cancellation_remains_cancellation_even_with_background_error(self):
        client = Client()

        async def scenario():
            started = asyncio.Event()

            async def connect():
                client._updates_error = errors.SessionRevokedError(request=None)
                started.set()
                await asyncio.Future()

            async def rounds(read, _refresh, **_kwargs):
                return await read()

            with patch.object(client, 'connect', connect), patch.object(reader, 'make_client', return_value=client), \
                    patch.object(watch, 'rounds', rounds):
                task = asyncio.create_task(watch.watch())
                await started.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

        asyncio.run(scenario())
        self.assertEqual(client.calls, ['disconnect'])

    def test_nondefinitive_background_rpc_error_survives_transport_failure(self):
        error = errors.UnauthorizedError(request=None, message=SIMULATED_DETAIL, code=401)
        for entrypoint in (reader.collect, watch.watch):
            with self.subTest(entrypoint=entrypoint):
                client = Client()
                self.real_rounds = watch.rounds
                connect = client.connect

                async def connect_with_error():
                    await connect()
                    if client.calls.count('connect') == 1:
                        client._updates_error = error
                        raise ConnectionError(SIMULATED_DETAIL)

                async def single_round(read, _refresh, **_kwargs):
                    return await read()

                with patch.object(client, 'connect', connect_with_error), \
                        patch.object(reader, 'make_client', return_value=client), \
                        patch.object(watch, 'rounds', single_round):
                    with self.assertRaises(errors.UnauthorizedError) as raised:
                        asyncio.run(entrypoint())
                self.assertIs(raised.exception, error)
                self.assertEqual(client.calls, ['connect', 'disconnect'])
