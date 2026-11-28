"""Diagnóstico da sessão com exceções reais do Telethon e transporte simulado."""

import asyncio
import io
import json
import logging
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telethon import errors, functions

from scangoal import reader, store, telegram_diagnostics, watch


SIMULATED_DETAIL = 'marcador-sintético\n::warning::texto de diagnóstico'


class Client:
    def __init__(self, failure_at=None, error=None, disconnect_error=None, background_on_disconnect=None):
        self.failure_at = failure_at
        self.error = error
        self.disconnect_error = disconnect_error
        self.background_on_disconnect = background_on_disconnect
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

    async def iter_messages(self, _entity, *, limit, **bounds):
        self.record('recover_page' if bounds else 'iter_messages')
        yield SimpleNamespace(id=7 if bounds else 42, raw_text='mensagem sintética', date=datetime.now(timezone.utc), buttons=None)

    async def disconnect(self):
        self.record('disconnect')
        self.connected = False
        self._updates_error = self.background_on_disconnect or self._updates_error
        if self.disconnect_error:
            raise self.disconnect_error


class TelegramDiagnostics(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.directory = Path(directory)
        self.output = self.directory / 'step-output'
        self.enterContext(patch.dict(os.environ, {
            'SCANGOAL_DATA_DIR': directory, 'TG_SESSION': '', 'TG_API_ID': '1', 'TG_API_HASH': 'abc',
            'TG_SCANGOAL': 'bot', 'GITHUB_OUTPUT': str(self.output),
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
                self.assertIn('SCANGOAL_DISCONNECT_FAILED RuntimeError', out.getvalue())
                self.assertIn('SCANGOAL_AUTHORIZATION_LOST AuthKeyUnregisteredError code=401', out.getvalue())

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


    def prepare_pending_page(self):
        store.append_versions([{'message_id': 41, 'text': 'histórico sintético'}])
        journal = self.directory / 'messages.jsonl'
        ledger = self.directory / 'prospective_ledger.jsonl'
        ledger.write_bytes(b'{"synthetic":true}\n')
        window = self.directory / 'collection_windows.json'
        gap = {'after_id': 1, 'before_id': 10, 'cursor': 10, 'status': 'pending'}
        window.write_text(json.dumps({'full_high_id': 41, 'intervals': [gap]}))
        return journal, journal.read_bytes(), ledger, ledger.read_bytes(), window, gap

    def test_backfill_revocation_fails_after_saving_recent_messages_and_receipt(self):
        journal, history, ledger, ledger_before, window, gap = self.prepare_pending_page()
        revocation = errors.SessionRevokedError(request=None)
        client = Client('recover_page', revocation)
        with patch.object(reader, 'make_client', return_value=client):
            code, output = self.run_cli(reader.main)
        self.assertEqual(code, reader.AUTHORIZATION_EXIT)
        self.assertIn('SCANGOAL_BACKFILL_PENDING SessionRevokedError code=401', output)
        self.assertIn('SCANGOAL_READ_FAILED SessionRevokedError code=401', output)
        self.assertNotIn('SCANGOAL_READ_OK', output)
        self.assertEqual(self.output.read_text(), 'authorization=lost\n')
        self.assertEqual(json.loads(window.read_text())['intervals'], [gap])
        self.assertTrue(journal.read_bytes().startswith(history))
        self.assertEqual([row['message_id'] for _, row in store.message_rows(journal)], [41, 42])
        self.assertEqual(ledger.read_bytes(), ledger_before)
        receipt = json.loads((self.directory / 'state.json').read_text())
        self.assertEqual(receipt['read'], 1)
        self.assertEqual(receipt['new_versions'], 1)
        self.assertEqual(client.calls, ['connect', 'get_entity', 'iter_messages', 'recover_page', 'disconnect'])

    def test_reader_asynchronous_revocation_during_disconnect_fails_with_receipt(self):
        revocation = errors.SessionRevokedError(request=None)
        client = Client(background_on_disconnect=revocation)
        with patch.object(reader, 'make_client', return_value=client):
            code, output = self.run_cli(reader.main)
        self.assertEqual(code, reader.AUTHORIZATION_EXIT)
        self.assertIn('SCANGOAL_READ_FAILED SessionRevokedError code=401', output)
        self.assertNotIn('SCANGOAL_READ_OK', output)
        self.assertEqual(self.output.read_text(), 'authorization=lost\n')
        self.assertEqual(json.loads((self.directory / 'state.json').read_text())['read'], 1)

    def assert_watch_disconnect_revocation(self, *, asynchronous):
        revocation = errors.SessionRevokedError(request=None)
        client = Client(**{'background_on_disconnect' if asynchronous else 'disconnect_error': revocation})
        async def read_once(read, *_args, **_kwargs):
            await read()
            return 1
        clock = SimpleNamespace(monotonic=lambda: 100, sleep=Mock())
        with patch.dict(os.environ, TG_SESSION='sintética'), patch.object(reader, 'make_client', return_value=client), \
                patch.object(watch, 'rounds', read_once), patch.object(watch, 'time', clock):
            code, output = self.run_cli(watch.main)
        self.assertEqual(code, watch.FAILED_EXIT)
        self.assertIn('WATCH_FAILED SessionRevokedError code=401', output)
        self.assertNotIn('WATCH_DONE', output)
        self.assertEqual(self.output.read_text(), 'authorization=lost\n')
        clock.sleep.assert_called_once_with(watch.WATCH_SECONDS)
        self.assertEqual(client.calls, ['connect', 'get_entity', 'iter_messages', 'disconnect'])

    def test_watch_direct_revocation_during_disconnect_never_reports_success(self):
        self.assert_watch_disconnect_revocation(asynchronous=False)

    def test_watch_asynchronous_revocation_during_disconnect_never_reports_success(self):
        self.assert_watch_disconnect_revocation(asynchronous=True)

    def test_primary_read_exception_identity_survives_cleanup_and_async_revocation(self):
        for primary in (TimeoutError(SIMULATED_DETAIL), ValueError(SIMULATED_DETAIL),
                        errors.AuthKeyUnregisteredError(request=None)):
            with self.subTest(primary=type(primary)):
                self.output.unlink(missing_ok=True)
                cleanup = RuntimeError(SIMULATED_DETAIL)
                client = Client('iter_messages', primary, disconnect_error=cleanup,
                                background_on_disconnect=errors.SessionRevokedError(request=None))
                with patch.object(reader, 'make_client', return_value=client), redirect_stdout(io.StringIO()) as output:
                    with self.assertRaises(type(primary)) as raised:
                        asyncio.run(reader.collect())
                self.assertIs(raised.exception, primary)
                self.assertIn('SCANGOAL_DISCONNECT_FAILED RuntimeError', output.getvalue())
                self.assertNotIn('::warning', output.getvalue())
                self.assertEqual(self.output.read_text(), 'authorization=lost\n')

    def test_cancel_during_disconnect_after_read_failure_remains_cancelled(self):
        async def scenario():
            closing = asyncio.Event()
            revocation = errors.SessionRevokedError(request=None)
            client = Client('iter_messages', ValueError('synthetic primary'))
            async def close():
                client._updates_error = revocation
                closing.set()
                await asyncio.Future()
            with patch.object(reader, 'make_client', return_value=client), patch.object(client, 'disconnect', close):
                task = asyncio.create_task(reader.collect())
                await closing.wait()
                task.cancel('synthetic cancellation')
                with self.assertRaisesRegex(asyncio.CancelledError, 'synthetic cancellation'):
                    await task
        asyncio.run(scenario())
        self.assertEqual(self.output.read_text(), 'authorization=lost\n')

    def test_reader_backfill_transient_and_unknown_remain_pending(self):
        self.prepare_pending_page()
        for failure in (TimeoutError(SIMULATED_DETAIL), RuntimeError(SIMULATED_DETAIL)):
            with self.subTest(failure=type(failure)), patch.object(reader, 'make_client',
                    return_value=Client('recover_page', failure)):
                code, output = self.run_cli(reader.main)
                self.assertEqual(code, 0)
                self.assertIn('SCANGOAL_BACKFILL_PENDING ' + type(failure).__name__, output)
                self.assertFalse(self.output.exists())


    def test_child_backfill_cancellation_preserves_background_rpc_error(self):
        self.prepare_pending_page()
        revocation = errors.SessionRevokedError(request=None)
        client = Client()
        async def interrupted_page(*args):
            client._updates_error = revocation
            raise asyncio.CancelledError(SIMULATED_DETAIL)
        with patch.object(reader, 'make_client', return_value=client), patch.object(reader, 'recover_page', interrupted_page):
            with self.assertRaises(errors.SessionRevokedError) as raised:
                asyncio.run(reader.collect())
        self.assertIs(raised.exception, revocation)
        self.assertEqual(self.output.read_text(), 'authorization=lost\n')

    def test_cli_cancellation_logs_class_without_raw_details(self):
        for entrypoint, function, expected in ((reader.main, 'collect', 'SCANGOAL_READ_FAILED'),
                                               (watch.main, 'watch', 'WATCH_FAILED')):
            with self.subTest(entrypoint=entrypoint), patch.dict(os.environ, TG_SESSION='sintética'), \
                    patch.object(reader if function == 'collect' else watch, function,
                                 AsyncMock(side_effect=asyncio.CancelledError(SIMULATED_DETAIL))), \
                    patch.object(watch, 'time', SimpleNamespace(monotonic=lambda: 0, sleep=Mock())):
                code, output = self.run_cli(entrypoint)
            self.assertNotEqual(code, 0)
            self.assertEqual(output.strip(), expected + ' CancelledError')
