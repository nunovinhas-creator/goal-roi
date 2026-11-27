"""Testes isolados da proteção da sessão Telegram (auditoria P0 da TG_SESSION).

Tudo offline: nenhum cliente real, nenhuma ligação ao Telegram, nenhum segredo. As classes de erro são stubs com os
mesmos nomes das do Telethon (a classificação é por nome). Os dados vão para diretórios temporários."""
import asyncio
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scangoal import reader, session_guard as sg, store

NOW = datetime(2026, 11, 27, 18, 14, tzinfo=timezone.utc)
FAKE_SESSION = '1' + 'A' * 350            # forma de uma StringSession, sem valor real
FAKE_HASH = '0123456789abcdef0123456789abcdef'
FAKE_BOT = '123456789:' + 'x' * 35


def error(name, base=Exception):
    return type(name, (base,), {})


class RPCError(Exception):
    pass


class Classification(unittest.TestCase):
    def test_every_authorization_failure_is_definitive_and_distinct(self):
        expected = {'AuthKeyUnregisteredError': 'AUTH_KEY_UNREGISTERED', 'SessionRevokedError': 'SESSION_REVOKED',
                    'SessionExpiredError': 'SESSION_EXPIRED', 'AuthKeyDuplicatedError': 'AUTH_KEY_DUPLICATED',
                    'UserDeactivatedError': 'USER_DEACTIVATED', 'UserDeactivatedBanError': 'USER_DEACTIVATED_BAN'}
        for name, kind in expected.items():
            d = sg.classify(error(name, RPCError)('secret-in-message'))
            self.assertEqual((d.kind, d.definitive, d.retry), (kind, True, False), name)

    def test_subclass_of_a_definitive_error_is_definitive(self):
        child = error('Custom', error('SessionRevokedError', RPCError))
        self.assertEqual(sg.classify(child()).kind, 'SESSION_REVOKED')

    def test_network_failures_are_transient_and_never_definitive(self):
        for exc in (ConnectionResetError(), TimeoutError(), asyncio.TimeoutError(), OSError('network down'),
                    error('ServerError', RPCError)(), error('FloodWaitError', RPCError)()):
            d = sg.classify(exc)
            self.assertEqual((d.kind, d.definitive, d.retry), ('TRANSIENT', False, True), type(exc).__name__)

    def test_unknown_error_is_neither_retried_nor_treated_as_revocation(self):
        d = sg.classify(ValueError('x'))
        self.assertEqual((d.kind, d.definitive, d.retry), ('UNKNOWN', False, False))

    def test_classification_ignores_the_message(self):
        self.assertEqual(sg.classify(error('SessionRevokedError')(FAKE_SESSION)).kind, 'SESSION_REVOKED')


class FakeClient:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, 0

    async def __call__(self, request):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class Diagnose(unittest.TestCase):
    def run_diagnose(self, outcome):
        client = FakeClient(outcome)
        return asyncio.run(sg.diagnose(client, object())), client

    def test_revocation_is_reported_with_its_real_type_after_a_single_request(self):
        d, client = self.run_diagnose(error('AuthKeyUnregisteredError', RPCError)())
        self.assertEqual(d.kind, 'AUTH_KEY_UNREGISTERED')
        self.assertEqual(client.calls, 1)           # sem repetições: uma tentativa inútil a menos por ciclo

    def test_healthy_session_returns_none(self):
        self.assertIsNone(self.run_diagnose(object())[0])

    def test_transient_failure_does_not_look_like_revocation(self):
        d, _ = self.run_diagnose(ConnectionResetError())
        self.assertFalse(d.definitive)
        self.assertTrue(d.retry)

    def test_hung_request_times_out_as_transient(self):
        class Hung:
            async def __call__(self, request):
                await asyncio.sleep(5)
        d = asyncio.run(sg.diagnose(Hung(), object(), timeout=0.05))
        self.assertEqual(d.kind, 'TRANSIENT')


class BreakerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.breaker = sg.Breaker(Path(self.tmp.name) / 'breaker.json')
        self.revoked = sg.Diagnosis('SESSION_REVOKED', True, False)
        self.transient = sg.Diagnosis('TRANSIENT', False, True)

    def test_seven_consecutive_revocations_raise_exactly_one_alert(self):
        actions = [self.breaker.decide(self.revoked, 'aaaa1111', NOW + timedelta(minutes=14 * i)) for i in range(7)]
        self.assertEqual(actions, ['alert'] + ['skip'] * 6)

    def test_alert_repeats_only_after_the_reminder_interval(self):
        self.breaker.decide(self.revoked, 'aaaa1111', NOW)
        self.assertEqual(self.breaker.decide(self.revoked, 'aaaa1111', NOW + timedelta(hours=23)), 'skip')
        self.assertEqual(self.breaker.decide(self.revoked, 'aaaa1111', NOW + timedelta(hours=25)), 'alert')

    def test_transient_failures_never_open_the_breaker(self):
        for i in range(20):
            self.assertEqual(self.breaker.decide(self.transient, 'aaaa1111', NOW + timedelta(minutes=i)), 'proceed')
        self.assertFalse(self.breaker.load().get('open'))

    def test_replacing_the_secret_closes_the_breaker_and_a_new_revocation_alerts_again(self):
        self.breaker.decide(self.revoked, 'aaaa1111', NOW)
        self.assertEqual(self.breaker.decide(None, 'bbbb2222', NOW + timedelta(hours=2)), 'proceed')
        self.assertEqual(self.breaker.decide(self.revoked, 'bbbb2222', NOW + timedelta(days=8)), 'alert')

    def test_a_working_session_closes_an_open_breaker(self):
        self.breaker.decide(self.revoked, 'aaaa1111', NOW)
        self.assertEqual(self.breaker.decide(None, 'aaaa1111', NOW + timedelta(minutes=5)), 'proceed')
        self.assertFalse(self.breaker.load()['open'])

    def test_corrupt_state_fails_open_toward_trying_the_session(self):
        Path(self.tmp.name, 'breaker.json').write_text('{broken')
        self.assertEqual(self.breaker.decide(None, 'aaaa1111', NOW), 'proceed')

    def test_state_file_holds_no_secret(self):
        self.breaker.decide(self.revoked, sg.fingerprint(FAKE_SESSION, FAKE_HASH), NOW)
        raw = Path(self.tmp.name, 'breaker.json').read_text()
        for secret in (FAKE_SESSION, FAKE_HASH, FAKE_SESSION[:40]):
            self.assertNotIn(secret, raw)


class FingerprintAndRedaction(unittest.TestCase):
    def test_fingerprint_is_stable_short_and_keyed(self):
        a = sg.fingerprint(FAKE_SESSION, FAKE_HASH)
        self.assertEqual(a, sg.fingerprint(' ' + FAKE_SESSION[:100] + '\n' + FAKE_SESSION[100:], FAKE_HASH))
        self.assertEqual(len(a), 8)
        self.assertNotEqual(a, sg.fingerprint(FAKE_SESSION, 'f' * 32))
        self.assertNotEqual(a, sg.fingerprint(FAKE_SESSION[:-1] + 'B', FAKE_HASH))

    def test_redact_removes_session_hash_and_bot_token(self):
        text = f'erro {FAKE_SESSION} hash={FAKE_HASH} bot={FAKE_BOT} fim'
        out = sg.redact(text)
        for secret in (FAKE_SESSION, FAKE_HASH, FAKE_BOT):
            self.assertNotIn(secret, out)
        self.assertIn('fim', out)

    def test_alert_text_names_the_cause_and_contains_no_secret(self):
        text = sg.alert_text(sg.Diagnosis('SESSION_REVOKED', True, False), NOW)
        self.assertIn('SESSION_REVOKED', text)
        self.assertEqual(sg.redact(text), text)

    def test_reader_failure_line_contains_only_the_exception_type(self):
        """O reader atual imprime só o tipo (reader.main). Garante que continua assim, mesmo com segredo na mensagem."""
        async def boom():
            raise error('AuthKeyUnregisteredError', RPCError)(FAKE_SESSION)
        out = io.StringIO()
        with patch.object(reader, 'collect', boom), redirect_stdout(out), self.assertRaises(SystemExit):
            reader.main()
        self.assertEqual(out.getvalue().strip(), 'SCANGOAL_READ_FAILED AuthKeyUnregisteredError')
        self.assertNotIn(FAKE_SESSION, out.getvalue())


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'session.lease'

    def test_second_holder_is_refused_while_the_first_holds_it(self):
        a, b = sg.Lease(self.path, owner='runner-a'), sg.Lease(self.path, owner='runner-b')
        self.assertTrue(a.acquire())
        self.assertFalse(b.acquire())
        a.release()
        self.assertTrue(b.acquire())

    def test_expired_lease_of_a_dead_holder_can_be_taken(self):
        clock = [NOW]
        a = sg.Lease(self.path, ttl=timedelta(minutes=20), owner='a', clock=lambda: clock[0])
        b = sg.Lease(self.path, ttl=timedelta(minutes=20), owner='b', clock=lambda: clock[0])
        self.assertTrue(a.acquire())
        clock[0] += timedelta(minutes=21)
        self.assertTrue(b.acquire())

    def test_release_by_a_non_owner_does_not_free_the_lease(self):
        a, b = sg.Lease(self.path, owner='a'), sg.Lease(self.path, owner='b')
        a.acquire()
        b.release()
        self.assertFalse(b.acquire())

    def test_many_simultaneous_contenders_yield_exactly_one_holder(self):
        winners, barrier = [], threading.Barrier(16)

        def contend(i):
            lease = sg.Lease(self.path, owner=f'c{i}')
            barrier.wait()
            if lease.acquire():
                winners.append(i)
        threads = [threading.Thread(target=contend, args=(i,)) for i in range(16)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(winners), 1)

    def test_context_manager_raises_session_in_use(self):
        with sg.Lease(self.path, owner='a'):
            with self.assertRaisesRegex(RuntimeError, 'SESSION_IN_USE'):
                with sg.Lease(self.path, owner='b'):
                    pass
        self.assertFalse(self.path.exists())


class FakeMessage:
    def __init__(self, mid, text, edit=None):
        self.id, self.raw_text, self.out = mid, text, False
        self.date, self.edit_date, self.buttons = NOW, edit, None


class FakeConversation:
    """Cliente falso: devolve as últimas `limit` mensagens, da mais recente para a mais antiga."""
    def __init__(self, messages):
        self.messages = messages

    def iter_messages(self, entity, limit=None, **bounds):
        async def gen():
            for m in sorted(self.messages, key=lambda m: -m.id)[:limit]:
                yield m
        return gen()


class OutageRecovery(unittest.TestCase):
    """Interrupção da sessão sem perda nem duplicados: a leitura de 400 mensagens cobre a lacuna (como em 19/11 e 27/11,
    em que foram recolhidas 5 e 121 novas versões ao regressar). Escritas só em diretório temporário."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(Path(self.tmp.name) / 'data')})
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self, conversation, limit=400):
        rows, oldest, present = asyncio.run(reader.fetch(conversation, 'entity', limit, '2026-11-27T19:55:00+00:00'))
        reader.stamp_receipt(rows)
        return store.append_versions(rows), rows, oldest, present

    def journal(self):
        return list(store.message_rows(store.data_dir() / 'messages.jsonl'))

    def test_messages_arriving_during_the_outage_are_recovered_exactly_once(self):
        chat = FakeConversation([FakeMessage(i, f'm{i}') for i in range(1, 101)])
        self.assertEqual(self.read(chat)[0], 100)
        before = self.journal()
        chat.messages += [FakeMessage(i, f'm{i}') for i in range(101, 221)]          # chegam com a sessão inválida
        added, _, oldest, _ = self.read(chat)
        self.assertEqual(added, 120)
        self.assertLessEqual(oldest, 101)                                              # janela cobre toda a lacuna
        self.assertEqual(self.journal()[:len(before)], before)                         # histórico intacto
        self.assertEqual(self.read(chat)[0], 0)                                        # repetir não duplica
        ids = [r['message_id'] for _, r in self.journal()]
        self.assertEqual(sorted(ids), list(range(1, 221)))

    def test_an_edit_made_during_the_outage_becomes_a_new_version_not_a_replacement(self):
        chat = FakeConversation([FakeMessage(1, 'aberto')])
        self.read(chat)
        chat.messages = [FakeMessage(1, 'aberto → FT 2-1', edit=NOW)]
        self.assertEqual(self.read(chat)[0], 1)
        texts = [r['text'] for _, r in self.journal()]
        self.assertEqual(texts, ['aberto', 'aberto → FT 2-1'])

    def test_a_gap_longer_than_the_window_is_flagged_not_hidden(self):
        chat = FakeConversation([FakeMessage(i, f'm{i}') for i in range(1, 11)])
        self.read(chat)
        last = max(i for i, _ in store.load_seen(store.data_dir() / 'messages.jsonl')[0])
        chat.messages += [FakeMessage(i, f'm{i}') for i in range(11, 31)]
        _, _, oldest, present = self.read(chat, limit=5)                               # janela curta: 26..30
        state = reader.record_window(store.data_dir(), {'last_run_utc': '2026-11-27T18:00:00+00:00'}, last, oldest,
                                     present, '2026-11-27T19:55:00+00:00')
        self.assertEqual(state['intervals'][0]['status'], 'pending')
        self.assertEqual((state['intervals'][0]['after_id'], state['intervals'][0]['before_id']), (10, 26))

    def test_prospective_ledger_is_never_touched_by_a_collection_read(self):
        ledger = store.data_dir() / 'prospective_ledger.jsonl'
        ledger.parent.mkdir(parents=True)
        ledger.write_bytes(b'{"entry": 1}\n{"entry": 2}\n')
        digest = hashlib.sha256(ledger.read_bytes()).hexdigest()
        self.read(FakeConversation([FakeMessage(1, 'x')]))
        self.assertEqual(hashlib.sha256(ledger.read_bytes()).hexdigest(), digest)


class IsolationGuarantee(unittest.TestCase):
    def test_module_is_not_wired_into_production(self):
        root = Path(__file__).resolve().parents[1]
        users = [p.name for p in list((root / 'scangoal').glob('*.py')) + list((root / 'scripts').glob('*.py'))
                 + list((root / '.github' / 'workflows').glob('*.yml')) if p.name != 'session_guard.py'
                 and 'session_guard' in p.read_text(encoding='utf-8')]
        self.assertEqual(users, [])

    def test_module_never_opens_a_telegram_connection_by_itself(self):
        source = (Path(__file__).resolve().parents[1] / 'scangoal' / 'session_guard.py').read_text(encoding='utf-8')
        self.assertIsNone(re.search(r'^\s*(from|import)\s+telethon', source, re.M))
        self.assertNotIn('.connect(', source)


if __name__ == '__main__':
    unittest.main()
