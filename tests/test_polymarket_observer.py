"""P2 — testes adversariais e de continuidade do observador Polymarket (offline; nenhum pedido de rede real)."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scangoal import polymarket_observer as po, polymarket_shadow as ps, store
from scangoal.publication import Publisher
from scripts import polymarket_p2_audit as audit
from test_polymarket_shadow import FIXTURE, OVER, RECEIVED, FakeRemote, contract_market

ROOT = Path(__file__).resolve().parents[1]
KICKOFF = datetime(2026, 11, 23, 19, 30, tzinfo=timezone.utc)


def text(teams=('AD Ceuta FC', 'Almeria'), selection='Over 2.5', head='23 nov 2026 22:30', mid='20261123'):
    slug = '-'.join(t.lower().replace(' ', '-') for t in teams) and 'ad-ceuta-fc-vs-almeria-' + mid
    return ('📈📈 🔷 Polymarket ×2 · %s\n%s — %s · 🇪🇸 Spanish Segunda\n%s @1.89 · $11K\n'
            '🧾 4 trades · biggest $3.8K · market share 100%%\nhttps://scangoal.com/match/%s?lang=en'
            % (head, teams[0], teams[1], selection, slug))


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.data, self.spool = self.tmp / 'data', self.tmp / 'spool'
        self.data.mkdir()
        self.spool.mkdir()
        self.enterContext(patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(self.data)}))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.clock = Clock(RECEIVED)
        self.remote = FakeRemote()
        self.remote.received = RECEIVED

    def add(self, mid, *, body=None, seen=None, sent=None, edit=None):
        seen = seen or self.clock.now - timedelta(seconds=5)
        sent = sent or seen - timedelta(seconds=30 + mid % 50)   # horas distintas: nunca um lote ("burst")
        row = {'message_id': mid, 'date_utc': sent.isoformat(timespec='seconds'), 'edit_date_utc': edit,
               'text': body or text(), 'buttons': None, 'collected_at_utc': seen.isoformat(), 'receipt_version': 1}
        return store.append_versions([row])

    def follower(self, ago=300, **kwargs):
        """Como na produção, o seguidor arranca antes das leituras (por defeito 300 s antes do relógio do teste)."""
        kwargs.setdefault('get', self.remote)
        now, self.clock.now = self.clock.now, self.clock.now - timedelta(seconds=ago)
        try:
            return po.Follower(self.data, self.spool, clock=self.clock, **kwargs)
        finally:
            self.clock.now = now

    def spooled(self):
        rows = [json.loads(p.read_text()) for p in sorted(self.spool.glob('*.json'))]
        return [r for r in rows if r.get('schema') == po.SCHEMA]

    def journal(self):
        return po.journal_rows(self.data / po.JOURNAL)[1]


class Observation(Harness):
    def test_new_eligible_signal_observed_within_window(self):
        self.add(1, seen=RECEIVED - timedelta(days=1), body=text(head='22 nov 2026 22:30', mid='20261122'))
        follower = self.follower()
        self.add(2)
        self.assertEqual(follower.tick(), 1)
        [record] = self.spooled()
        self.assertEqual(record['status'], 'contemporaneous')
        self.assertTrue(record['evidence_for_signal'])
        self.assertFalse(record['execution_proven'])
        self.assertEqual((record['orders_sent'], record['credentials_used']), (0, False))
        self.assertEqual(record['condition_id'], FIXTURE['clob_market']['c'])
        self.assertEqual(record['token_over'], OVER)
        self.assertEqual(record['quote']['best_ask'], '0.53')
        self.assertEqual(record['fee']['rate'], '0.05')
        self.assertIn('fd', record['fee']['source'])
        self.assertEqual(record['message_id'], 2)
        self.assertEqual(len(record['version_sha256']), 64)
        self.assertEqual(len(record['signal_sha256']), 64)
        self.assertEqual(len(record['responses']), 5)
        self.assertTrue(all(len(r['sha256']) == 1 or len(r['sha256']) == 64 for r in record['responses']))
        self.assertIsNotNone(record['quote']['source_utc'])
        self.assertIsNotNone(record['quote']['received_utc'])

    def test_history_is_never_observed(self):
        self.add(1)
        self.assertEqual(self.follower().tick(), 0)
        self.assertEqual(self.remote.urls, [])

    def test_ineligible_signals_are_not_queried(self):
        follower = self.follower()
        self.add(3, body=text(selection='Under 2.5'))
        self.add(4, body=text(selection='Over 3.5'))
        self.add(5, body='Promotional message without a signal')
        self.assertEqual(follower.tick(), 0)
        self.assertEqual(self.remote.urls, [])

    def test_first_record_must_prove_the_signal(self):
        from test_polymarket_shadow import signal
        base = signal(had_ft_at_first_sight=False, edited_at_first_sight=False, burst=False)
        self.assertTrue(po.eligible_signal(base))
        for change in ({'had_ft_at_first_sight': True}, {'edited_at_first_sight': True}, {'burst': True},
                       {'had_ft_at_first_sight': None}, {'bet': False}, {'kickoff_utc': None},
                       {'kickoff_utc': '2026-11-23T19:30'}, {'odds': 1.05}, {'match_id': None},
                       {'selection': 'Over 2.50'}, {'period': 'first_half'}, {'phase': 'live'}, {'type': 'STAKE'}):
            with self.subTest(change=change):
                self.assertFalse(po.eligible_signal(dict(base, **change)))

    def test_signal_detected_after_120s_is_recorded_without_network(self):
        follower = self.follower()
        self.add(2, seen=self.clock.now - timedelta(seconds=121))
        follower.tick()
        [record] = self.spooled()
        self.assertEqual((record['status'], record['reason']), ('outside_window', 'detected_late'))
        self.assertEqual(self.remote.urls, [])

    def test_quote_received_after_120s_is_outside_window(self):
        follower = self.follower()
        self.add(2, seen=self.clock.now - timedelta(seconds=100))
        self.remote.received = self.clock.now + timedelta(seconds=30)
        self.remote.data['book_over'] = dict(self.remote.data['book_over'],
                                             timestamp=str(int(self.remote.received.timestamp() * 1000)))
        follower.tick()
        [record] = self.spooled()
        self.assertEqual((record['status'], record['reason']), ('outside_window', 'current_only'))
        self.assertFalse(record['evidence_for_signal'])

    def test_after_kickoff_no_network(self):
        self.clock.now = KICKOFF + timedelta(seconds=1)
        follower = self.follower()
        self.add(2)
        follower.tick()
        [record] = self.spooled()
        self.assertEqual((record['status'], record['reason']), ('outside_window', 'after_kickoff'))
        self.assertEqual(self.remote.urls, [])

    def test_missing_contract_and_suspended_market(self):
        for index, (change, reason) in enumerate((
                (lambda r: r.data['child'].update(markets=[]), 'contract_not_found'),
                (lambda r: contract_market(r).update(acceptingOrders=False), 'market_not_accepting_orders'),
                (lambda r: r.data['clob_market'].update(ao=False), 'market_not_accepting_orders'))):
            with self.subTest(reason=reason, index=index):
                for p in self.spool.glob('*.json'):
                    p.unlink()
                self.remote = FakeRemote()
                change(self.remote)
                follower = self.follower()
                self.add(10 + index)
                follower.tick()
                [record] = self.spooled()
                self.assertEqual((record['status'], record['reason']), ('contract_unavailable', reason))

    def test_stale_book(self):
        follower = self.follower()
        self.add(2)
        self.remote.data['book_over'] = dict(self.remote.data['book_over'],
                                             timestamp=str(int((RECEIVED - timedelta(minutes=6)).timestamp() * 1000)))
        follower.tick()
        [record] = self.spooled()
        self.assertEqual(record['status'], 'stale_book')
        self.assertGreater(record['source_age_seconds'], 300)

    def test_search_tries_home_vs_away_first_then_plain(self):
        original = self.remote.__call__
        queries = []

        def get(url):
            if '/public-search?' in url:
                queries.append(url)
                if len(queries) == 1:
                    return dict(original(url), payload={'events': None})
            return original(url)

        follower = self.follower(get=get)
        self.add(2)
        follower.tick()
        [record] = self.spooled()
        self.assertEqual(record['status'], 'contemporaneous')
        self.assertIn('q=AD+Ceuta+FC+vs+Almeria', queries[0])
        self.assertIn('q=AD+Ceuta+FC+Almeria&', queries[1])
        self.assertEqual(len(record['responses']), 6)

    def test_identity_search_is_bounded(self):
        follower = self.follower()
        self.add(2, body=text(teams=('Ceuta B', 'Almeria')))
        follower.tick()
        searches = [u for u in self.remote.urls if '/public-search?' in u]
        self.assertEqual(len(searches), 2)
        self.assertEqual(len(self.remote.urls), 2)

    def test_identity_unproven(self):
        follower = self.follower()
        self.add(2, body=text(teams=('Ceuta B', 'Almeria')))
        follower.tick()
        [record] = self.spooled()
        self.assertEqual((record['status'], record['reason']), ('identity_unproven', 'identity_not_found'))
        self.assertIsNone(record['condition_id'])


class Failures(Harness):
    def test_total_polymarket_failure_opens_circuit(self):
        self.remote.fail = 'network_error'
        follower = self.follower()
        for mid in (2, 3, 4):
            self.add(mid, body=text())
            follower.tick()
        statuses = [(r['message_id'], r['status'], r['reason']) for r in self.spooled()]
        self.assertEqual(sorted(statuses), [(2, 'technical_failure', 'network_error'),
                                            (3, 'technical_failure', 'network_error'),
                                            (4, 'technical_failure', 'circuit_open')])
        self.assertEqual(len(self.remote.urls), 2)        # depois do disjuntor, zero pedidos

    def test_rate_limit_and_server_errors_open_immediately(self):
        for code in (429, 403, 500, 503):
            with self.subTest(code=code):
                for p in self.spool.glob('*.json'):
                    p.unlink()
                calls = []

                def get(url):
                    calls.append(url)
                    raise ps.Refusal('http_error', code)

                follower = self.follower(get=get)
                self.add(2 * code)
                self.add(2 * code + 1)
                follower.tick()
                self.assertEqual(len(calls), 1)
                self.assertIn('circuit_open', [r['reason'] for r in self.spooled()])

    def test_timeout_is_a_technical_failure(self):
        follower = self.follower(get=lambda url: (_ for _ in ()).throw(ps.Refusal('network_error', 'TimeoutError')))
        self.add(2)
        follower.tick()
        [record] = self.spooled()
        self.assertEqual((record['status'], record['detail']), ('technical_failure', 'TimeoutError'))

    def test_http_get_passes_the_bounded_timeout(self):
        seen = {}

        def opener(request, timeout):
            seen['timeout'] = timeout
            raise TimeoutError()

        with self.assertRaises(ps.Refusal) as ctx:
            ps.http_get('https://clob.polymarket.com/book', opener=opener, timeout=po.REQUEST_TIMEOUT)
        self.assertEqual((ctx.exception.code, seen['timeout']), ('network_error', po.REQUEST_TIMEOUT))

    def test_unexpected_exception_is_recorded_once(self):
        calls = []

        def broken(url):
            calls.append(url)
            raise KeyError('boom')

        follower = self.follower(get=broken)
        self.add(2)
        follower.tick()
        follower.tick()
        [record] = self.spooled()
        self.assertEqual((record['reason'], record['detail']), ('internal_error', 'KeyError'))
        self.assertEqual(len(calls), 1)

    def test_request_and_observation_caps(self):
        follower = self.follower(max_observations=1)
        self.add(2)
        self.add(3)
        follower.tick()
        reasons = sorted((r['message_id'], r['reason']) for r in self.spooled())
        self.assertEqual(reasons, [(2, None), (3, 'observation_cap_reached')])
        budget = po.Budget(self.remote, self.clock, max_requests=2)
        budget('https://clob.polymarket.com/x?token_id=' + OVER)
        budget('https://clob.polymarket.com/x?token_id=' + OVER)
        with self.assertRaises(ps.Refusal):
            budget('https://clob.polymarket.com/x?token_id=' + OVER)

    def test_deadline_stops_requests_mid_observation(self):
        follower = self.follower()
        self.add(2, seen=self.clock.now - timedelta(seconds=119))
        original = self.remote.__call__

        def slow(url):
            response = original(url)
            self.clock.now += timedelta(seconds=2)       # cada pedido leva 2 s
            return response

        follower.get = slow
        follower.budget.get = slow
        follower.tick()
        [record] = self.spooled()
        self.assertEqual((record['status'], record['reason']), ('outside_window', 'deadline_exceeded'))
        self.assertEqual(len(record['responses']), 1)


class Replays(Harness):
    def test_repeated_signal_and_fast_then_full_read(self):
        follower = self.follower()
        self.add(2, seen=self.clock.now - timedelta(seconds=10))
        follower.tick()
        # A recolha completa relê a mesma mensagem, com outra hora de leitura: nenhuma versão nova, nenhum pedido.
        self.assertEqual(self.add(2, seen=self.clock.now), 0)
        follower.tick()
        self.assertEqual(len(self.spooled()), 1)
        self.assertEqual(len(self.remote.urls), 5)

    def test_later_edit_is_not_a_new_signal(self):
        follower = self.follower()
        self.add(2)
        follower.tick()
        self.assertEqual(self.add(2, body=text() + '\n✅ FT 3:1', edit=RECEIVED.isoformat()), 1)
        follower.tick()
        [record] = self.spooled()
        first = po.first_versions(self.data / 'messages.jsonl')[2][0]
        self.assertEqual(record['version_sha256'], first)
        self.assertEqual(len(self.remote.urls), 5)

    def test_runner_restart_never_requeries(self):
        follower = self.follower()
        self.add(2)
        follower.tick()
        restarted = self.follower()                        # mesma execução, spool ainda presente
        self.assertEqual(restarted.tick(), 0)
        po.ingest(self.data, self.spool)
        rerun = self.follower()                            # execução seguinte: spool vazio, diário com a chave
        self.assertIn(self.journal()[0]['key'], rerun.done)
        self.assertEqual(rerun.tick(), 0)
        self.assertEqual(len(self.remote.urls), 5)

    def test_replayed_execution_does_not_duplicate_evidence(self):
        follower = self.follower()
        self.add(2)
        follower.tick()
        saved = {p.name: p.read_bytes() for p in self.spool.glob('*.json')}
        po.ingest(self.data, self.spool)
        for name, body in saved.items():                   # a mesma execução volta a entregar o mesmo registo
            (self.spool / name).write_bytes(body)
        stats = po.ingest(self.data, self.spool)
        self.assertEqual((stats['added'], stats['duplicates']), (0, 1))
        self.assertEqual(len(self.journal()), 1)
        self.assertEqual(list(self.spool.glob('*.json')), [])


class Storage(Harness):
    def observed(self):
        follower = self.follower()
        self.add(2)
        follower.tick()

    def test_ingest_is_append_only(self):
        self.observed()
        po.ingest(self.data, self.spool)
        before = (self.data / po.JOURNAL).read_bytes()
        follower = self.follower()
        self.add(3)
        follower.tick()
        po.ingest(self.data, self.spool)
        after = (self.data / po.JOURNAL).read_bytes()
        self.assertTrue(after.startswith(before))
        self.assertEqual([r['message_id'] for r in self.journal()], [2, 3])

    def test_interrupted_journal_write_leaves_previous_bytes(self):
        self.observed()
        with patch('scangoal.polymarket_observer.os.replace', side_effect=OSError('killed')):
            with self.assertRaises(OSError):
                po.ingest(self.data, self.spool)
        self.assertFalse((self.data / po.JOURNAL).exists())
        self.assertEqual(len(self.spooled()), 1)          # nada perdido: o spool continua lá
        self.assertEqual(list(self.data.glob('.*.tmp')), [])
        po.ingest(self.data, self.spool)
        self.assertEqual(len(self.journal()), 1)

    def test_partial_spool_file_is_ignored(self):
        (self.spool / '.abc.json.123.tmp').write_text('{"schema": "goal-roi/polym')
        (self.spool / 'truncated.json').write_text('{"schema": "goal-roi/polym')
        stats = po.ingest(self.data, self.spool)
        self.assertEqual(stats, {'added': 0, 'duplicates': 0, 'rejected': 1, 'runs_added': 0})
        self.assertTrue((self.spool / 'rejected' / 'truncated.json').exists())
        self.assertFalse((self.data / po.JOURNAL).exists())

    def test_torn_journal_is_never_rewritten(self):
        self.observed()
        (self.data / po.JOURNAL).write_bytes(b'{"key": "1:a"}\n{"key": "2:')
        before = (self.data / po.JOURNAL).read_bytes()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = po.main(['ingest', '--spool', str(self.spool)], environ={po.FLAG: 'on'})
        self.assertEqual(code, 0)
        self.assertIn('POLYMARKET_SHADOW_FAILED ingest JournalCorrupt', out.getvalue())
        self.assertEqual((self.data / po.JOURNAL).read_bytes(), before)
        self.assertEqual(len(self.spooled()), 1)

    def test_tampered_records_are_rejected(self):
        self.observed()
        [path] = self.spool.glob('*.json')
        for change in ({'orders_sent': 1}, {'execution_proven': True}, {'status': 'executed'}, {'key': '9:x'},
                       {'schema': 'other'}):
            with self.subTest(change=change):
                record = json.loads(path.read_text()) if path.exists() else self.last
                self.last = record
                (self.spool / 'tampered.json').write_text(json.dumps(dict(record, **change)))
                path.unlink(missing_ok=True)
                self.assertEqual(po.ingest(self.data, self.spool)['rejected'], 1)
        self.assertFalse((self.data / po.JOURNAL).exists())

    def test_spool_must_be_outside_repository_and_data(self):
        for target in (ROOT / 'spool', ROOT / 'data' / 'spool', self.data / 'spool'):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    po.safe_spool(target)

    def test_messages_journal_is_never_written(self):
        self.add(1)
        before = (self.data / 'messages.jsonl').read_bytes()
        follower = self.follower()
        self.add(2)
        middle = (self.data / 'messages.jsonl').read_bytes()
        follower.tick()
        po.ingest(self.data, self.spool)
        self.assertEqual((self.data / 'messages.jsonl').read_bytes(), middle)
        self.assertTrue(middle.startswith(before))


class Activation(Harness):
    def test_disabled_by_default_does_nothing(self):
        spool = self.tmp / 'never'
        guards = [patch.object(po, name, side_effect=AssertionError(name)) for name in ('follow', 'ingest', 'stop')]
        with contextlib.ExitStack() as stack:
            for guard in guards:
                stack.enter_context(guard)
            for operation in ('follow', 'ingest', 'stop'):
                with self.subTest(operation=operation):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        self.assertEqual(po.main([operation, '--spool', str(spool)], environ={}), 0)
                    self.assertEqual(out.getvalue().strip(), 'POLYMARKET_SHADOW_DISABLED')
        self.assertFalse(spool.exists())
        for value in ('', 'off', 'true', 'ON ', '1', 'yes'):
            self.assertEqual(po.enabled({po.FLAG: value}), value.strip() == 'on')

    def test_follow_respects_lifetime_and_stop(self):
        ticks = []
        clock = iter(range(0, 10_000, 5))
        follower = po.follow(self.data, self.spool, seconds=20, sleep=lambda s: ticks.append(s),
                             monotonic=lambda: next(clock), get=self.remote, clock=self.clock)
        self.assertLessEqual(len(ticks), 4)
        self.assertFalse((self.spool / 'follower.pid').exists())
        (self.spool / 'STOP').write_text('stop')
        ticks.clear()
        po.follow(self.data, self.spool, seconds=20, sleep=ticks.append, get=self.remote, clock=self.clock)
        self.assertEqual(ticks, [])
        self.assertIsInstance(follower, po.Follower)

    def test_failing_tick_never_stops_the_follower(self):
        with patch.object(po.Follower, 'tick', side_effect=RuntimeError('boom')):
            clock = iter(range(0, 100, 5))
            po.follow(self.data, self.spool, seconds=15, sleep=lambda s: None, monotonic=lambda: next(clock),
                      get=self.remote, clock=self.clock)
        self.assertFalse((self.spool / 'follower.pid').exists())


class GitPublication(unittest.TestCase):
    """O diário entra pela transação existente; um escritor concorrente não duplica nem corrompe evidência."""

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.remote, self.repo, self.other = self.tmp / 'origin.git', self.tmp / 'repo', self.tmp / 'other'
        self.git(self.tmp, 'init', '--bare', '--initial-branch=main', str(self.remote))
        self.git(self.tmp, 'clone', str(self.remote), str(self.repo))
        (self.repo / 'data').mkdir()
        (self.repo / 'data' / 'report.json').write_text('{}\n')
        self.git(self.repo, 'add', 'data')
        self.git(self.repo, 'commit', '-m', 'seed')
        self.git(self.repo, 'push', 'origin', 'main')
        self.git(self.tmp, 'clone', str(self.remote), str(self.other))
        self.spool = self.tmp / 'spool'
        self.spool.mkdir()

    def git(self, repo, *args):
        return subprocess.run(['git', '-C', str(repo), '-c', 'user.name=T', '-c', 'user.email=t@example.invalid', *args],
                              text=True, capture_output=True, check=True, timeout=15).stdout.strip()

    def record(self, mid):
        record = {'schema': po.SCHEMA, 'key': '%d:%s' % (mid, 'a' * 64), 'message_id': mid, 'version_sha256': 'a' * 64,
                  'status': 'technical_failure', 'execution_proven': False, 'orders_sent': 0,
                  'first_read_utc': '2026-11-23T10:00:00+00:00'}
        (self.spool / ('%d.json' % mid)).write_text(json.dumps(record))

    def test_concurrent_writer_and_replay(self):
        self.record(1)
        po.ingest(self.repo / 'data', self.spool)
        (self.other / 'data' / 'report.json').write_text('{"other": 1}\n')
        self.git(self.other, 'commit', '-am', 'concurrent')
        self.git(self.other, 'push', 'origin', 'main')
        Publisher(self.repo, spool=self.tmp / 'pub').publish()
        self.assertEqual(self.git(self.remote, 'show', 'main:data/report.json'), '{"other": 1}')
        published = self.git(self.remote, 'show', 'main:data/' + po.JOURNAL)
        self.assertEqual(len(published.splitlines()), 1)
        self.record(1)                                      # mesmo registo entregue outra vez
        self.record(2)
        self.assertEqual(po.ingest(self.repo / 'data', self.spool)['added'], 1)
        Publisher(self.repo, spool=self.tmp / 'pub').publish()
        keys = [json.loads(line)['key'] for line in self.git(self.remote, 'show', 'main:data/' + po.JOURNAL).splitlines()]
        self.assertEqual(keys, ['1:' + 'a' * 64, '2:' + 'a' * 64])

    def test_conflicting_journal_fails_closed_without_losing_evidence(self):
        self.record(1)
        po.ingest(self.repo / 'data', self.spool)
        (self.other / 'data' / po.JOURNAL).write_text('{"key": "9:z"}\n')
        self.git(self.other, 'add', 'data')
        self.git(self.other, 'commit', '-m', 'conflict')
        self.git(self.other, 'push', 'origin', 'main')
        before = self.git(self.remote, 'rev-parse', 'main')
        from scangoal.publication import PublicationError
        with self.assertRaises(PublicationError):
            Publisher(self.repo, spool=self.tmp / 'pub').publish()
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'), before)   # nada reescrito no remoto
        self.assertTrue((self.tmp / 'pub' / 'pending.bundle').exists())          # evidência preservada para o próximo runner

    def test_follower_never_dirties_the_checkout(self):
        data = self.repo / 'data'
        with patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(data)}):
            store.append_versions([{'message_id': 1, 'date_utc': RECEIVED.isoformat(), 'edit_date_utc': None,
                                    'text': text(), 'buttons': None, 'collected_at_utc': RECEIVED.isoformat()}])
            self.git(self.repo, 'add', 'data')
            self.git(self.repo, 'commit', '-m', 'messages')
            follower = po.Follower(data, self.spool, get=FakeRemote(), clock=Clock(RECEIVED + timedelta(seconds=5)))
            store.append_versions([{'message_id': 2, 'date_utc': RECEIVED.isoformat(), 'edit_date_utc': None,
                                    'text': text(), 'buttons': None, 'collected_at_utc': RECEIVED.isoformat()}])
            self.git(self.repo, 'commit', '-qam', 'new message')
            follower.tick()
        self.assertEqual(len(list(self.spool.glob('*.json'))), 1)
        # O seguidor não toca no checkout: nenhuma alteração rastreada nem ficheiro novo em data/.
        self.assertEqual(self.git(self.repo, 'status', '--porcelain', '--untracked-files=no'), '')
        self.assertEqual(self.git(self.repo, 'status', '--porcelain', '--untracked-files=all', '--', 'data'), '')


class ProspectiveAudit(Harness):
    """Relatório P2: denominador completo, prova de preço fresco separada de resposta na janela, sem dados futuros."""

    def snapshot(self, as_of=None):
        from scangoal import report
        as_of = as_of or self.clock.now + timedelta(seconds=1)
        messages, *_ = report.load(result_observations={}, as_of=as_of)
        runs = po.journal_rows(self.data / po.RUNS_JOURNAL)[1]
        return messages, po.first_versions(self.data / 'messages.jsonl'), self.journal(), runs

    def audit(self, as_of=None, **kwargs):
        messages, first, rows, runs = self.snapshot(as_of)
        kwargs.setdefault('runs', runs)
        return audit.audit(messages, first, rows, as_of=as_of or self.clock.now + timedelta(seconds=1), **kwargs)

    def run_observer(self, during=None, ticks=2):
        """Uma execução real de `follow` (relógio e espera simulados); `during` corre antes da 1.ª passagem."""
        steps = iter(range(0, 10_000, 5))
        calls = []

        def sleep(_):
            if during and not calls:
                during()
            calls.append(1)

        follower = po.follow(self.data, self.spool, seconds=5 * ticks + 1, sleep=sleep, monotonic=lambda: next(steps),
                             get=self.remote, clock=self.clock)
        po.ingest(self.data, self.spool)
        return follower

    def stale_book(self):
        self.remote.data['book_over'] = dict(self.remote.data['book_over'],
                                             timestamp=str(int((RECEIVED - timedelta(minutes=6)).timestamp() * 1000)))

    def test_denominator_keeps_unobserved_signals(self):
        follower = self.follower()
        self.add(2, seen=self.clock.now)
        follower.tick()
        po.ingest(self.data, self.spool)
        self.add(3, seen=self.clock.now)
        result = self.audit()
        self.assertEqual((result['eligible_signals'], result['responses_within_120s'],
                          result['fresh_price_within_120s'], result['without_observation']), (2, 1, 1, 1))
        self.assertEqual(result['coverage_fresh_price_pct'], 50.0)
        self.assertEqual(result['depth_filled_fresh'], {'10_usdc': 1, '50_usdc': 1, '100_usdc': 1, '500_usdc': 1})
        self.assertEqual(result['bot_vs_net_odds_10_usdc']['n'], 1)
        self.assertEqual(result['verdict'], 'RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.')
        self.assertIsNone(result['roi_net_proven'])
        self.assertFalse(result['execution_proven'])

    def test_stale_book_is_a_response_not_a_fresh_price(self):
        self.stale_book()
        follower = self.follower()
        self.add(2, seen=self.clock.now)
        follower.tick()
        [record] = self.spooled()
        self.assertEqual(record['status'], 'stale_book')
        self.assertTrue(record['received_within_window'])
        self.assertFalse(record['fresh_book_proven'])
        self.assertFalse(record['evidence_for_signal'])
        po.ingest(self.data, self.spool)
        result = self.audit()
        self.assertEqual(result['statuses']['stale_book'], 1)                     # continua na auditoria
        self.assertEqual((result['responses_within_120s'], result['stale_book_within_120s']), (1, 1))
        self.assertEqual(result['fresh_price_within_120s'], 0)                     # nunca prova de preço fresco
        self.assertEqual(result['coverage_fresh_price_pct'], 0.0)
        self.assertEqual(result['depth_filled_fresh'], {'10_usdc': 0, '50_usdc': 0, '100_usdc': 0, '500_usdc': 0})
        self.assertEqual(result['bot_vs_net_odds_10_usdc']['n'], 0)                 # fora da comparação económica
        self.assertEqual(result['bot_vs_net_odds_10_usdc']['excluded_not_fresh'], 1)

    def test_fresh_boundary_is_inclusive_at_300s(self):
        for age, fresh in ((300, True), (301, False)):
            with self.subTest(age=age):
                record = {'status': 'contemporaneous', 'source_age_seconds': age, 'condition_id': 'c',
                          'first_read_utc': RECEIVED.isoformat(), 'quote': {'received_utc': RECEIVED.isoformat(),
                                                                           'source_utc': (RECEIVED - timedelta(seconds=age)).isoformat()}}
                m = {'collected_at_utc': RECEIVED.isoformat(), 'kickoff_utc': KICKOFF.isoformat()}
                self.assertEqual(audit.fresh_price(record, m), fresh)
                self.assertEqual(po.fresh_book(record['quote'], age), fresh)

    def test_tampered_records_cannot_claim_a_fresh_price(self):
        self.stale_book()
        follower = self.follower()
        self.add(2, seen=self.clock.now)
        follower.tick()
        [record] = self.spooled()
        self.assertFalse(po.valid_record(dict(record, evidence_for_signal=True)))    # o ingest recusa-o
        messages, first, _, runs = self.snapshot()
        good = self.fresh_record()
        cases = {
            'forged age': dict(record, status='contemporaneous', source_age_seconds=0),
            'missing source': dict(good, quote=dict(good['quote'], source_utc=None)),
            'future source': dict(good, source_age_seconds=0, quote=dict(good['quote'],
                source_utc=(RECEIVED + timedelta(microseconds=1)).isoformat())),
            'stale relabelled': dict(record, status='contemporaneous'),
            'stale with a young age': dict(good, status='stale_book'),
            'old book': dict(good, source_age_seconds=400),
            'book from the future': dict(good, source_age_seconds=-10),
            'other first read': dict(good, first_read_utc=(RECEIVED - timedelta(minutes=1)).isoformat()),
            'after kickoff': dict(good, quote=dict(good['quote'], received_utc=(KICKOFF + timedelta(seconds=1)).isoformat())),
            'after 120 s': dict(good, quote=dict(good['quote'], received_utc=(RECEIVED + timedelta(seconds=121)).isoformat())),
            'no contract': dict(good, condition_id=None),
            'flag only': dict(record, evidence_for_signal=True, fresh_book_proven=True),
        }
        for name, row in cases.items():
            with self.subTest(case=name):
                result = audit.audit(messages, first, [row], as_of=KICKOFF + timedelta(hours=1),
                                     since=RECEIVED - timedelta(hours=1))
                self.assertEqual(result['fresh_price_within_120s'], 0)
                self.assertEqual(result['bot_vs_net_odds_10_usdc']['n'], 0)

    def fresh_record(self):
        self.remote = FakeRemote()
        for p in self.spool.glob('*.json'):
            p.unlink()
        follower = self.follower()
        self.add(4, seen=self.clock.now)
        follower.tick()
        [record] = [r for r in self.spooled() if r['message_id'] == 4]
        self.assertTrue(record['evidence_for_signal'])
        return dict(record, message_id=2, key=record['key'].replace('4:', '2:', 1),
                    first_read_utc=next(m for m in self.snapshot()[0] if m['message_id'] == 2)['collected_at_utc'])

    def test_since_required_without_verifiable_activation(self):
        self.add(2, seen=self.clock.now)
        result = self.audit()
        self.assertEqual(result['status'], 'since_required')
        self.assertIsNone(result['eligible_signals'])                                # nunca "zero elegíveis"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(audit.main(['--as-of', (self.clock.now + timedelta(seconds=1)).isoformat()]), 2)
        self.assertEqual(json.loads(out.getvalue())['status'], 'since_required')
        explicit = self.audit(since=self.clock.now - timedelta(hours=1))
        self.assertEqual((explicit['status'], explicit['eligible_signals'], explicit['without_observation']),
                         ('explicit_period_no_observer_runs', 1, 1))
        self.assertEqual(explicit['without_observation_by_observer'], {'observer_running': 0, 'observer_absent': 1})

    def test_successive_runs_without_observations_keep_the_denominator(self):
        start = self.clock.now
        self.clock.now = start - timedelta(minutes=30)
        first = self.run_observer()
        self.clock.now = start - timedelta(minutes=20)
        self.run_observer()
        self.assertEqual(first.observations, 0)
        self.clock.now = start
        self.add(2, seen=start - timedelta(minutes=10))                   # lido entre execuções: observador ausente
        self.assertEqual(self.journal(), [])                              # nenhuma evidência guardada
        result = self.audit()
        self.assertEqual((result['status'], result['since_source']), ('activated', 'observer_runs'))
        self.assertEqual(result['since_utc'], po.iso(start - timedelta(minutes=30)))
        self.assertEqual((result['eligible_signals'], result['without_observation']), (1, 1))
        self.assertEqual(result['without_observation_by_observer'], {'observer_running': 0, 'observer_absent': 1})
        self.assertEqual(result['observer']['runs'], 2)
        self.assertEqual(result['observer']['runs_with_zero_observations'], 2)
        self.assertEqual(result['coverage_fresh_price_pct'], 0.0)

    def test_total_failure_keeps_failed_signals_in_the_denominator(self):
        self.remote.fail = 'network_error'
        follower = self.run_observer(during=lambda: [self.add(mid, seen=self.clock.now) for mid in (2, 3, 4)])
        self.assertEqual(follower.budget.requests, 2)
        result = self.audit()
        self.assertEqual(result['eligible_signals'], 3)
        self.assertEqual(result['statuses']['technical_failure'], 3)
        self.assertEqual((result['fresh_price_within_120s'], result['without_observation']), (0, 0))
        self.assertEqual(result['coverage_fresh_price_pct'], 0.0)
        self.assertEqual(result['observer']['requests'], 2)

    def test_run_killed_without_stop_is_bounded(self):
        self.clock.now = RECEIVED - timedelta(hours=2)
        follower = self.follower(ago=0)
        start = po.run_event(follower.run, 'start', at_utc=follower.run['started_utc'])
        (self.spool / 'run-start.json').write_text(json.dumps(start))
        po.ingest(self.data, self.spool)                                     # sem evento "stop": processo morto
        self.clock.now = RECEIVED
        self.add(2, seen=RECEIVED - timedelta(hours=2) + timedelta(minutes=5))
        self.add(3, seen=RECEIVED - timedelta(hours=1))
        result = self.audit()
        self.assertEqual(result['observer']['runs_without_recorded_stop'], 1)
        self.assertEqual(result['without_observation_by_observer'], {'observer_running': 1, 'observer_absent': 1})

    def test_historical_cut_ignores_future_data(self):
        follower = self.follower()
        self.add(2, seen=RECEIVED - timedelta(seconds=2))
        follower.tick()
        po.ingest(self.data, self.spool)
        earlier = RECEIVED - timedelta(seconds=1)                           # sinal já lido, cotação ainda não recebida
        result = self.audit(as_of=earlier, since=RECEIVED - timedelta(hours=1))
        self.assertEqual((result['responses_within_120s'], result['without_observation']), (0, 1))
        result = self.audit(as_of=RECEIVED - timedelta(minutes=5), since=RECEIVED - timedelta(hours=1))
        self.assertEqual(result['eligible_signals'], 0)
        self.clock.now = RECEIVED + timedelta(hours=1)
        self.run_observer()                                                  # execução posterior ao corte
        result = self.audit(as_of=RECEIVED + timedelta(minutes=30))
        self.assertNotEqual(result.get('since_source'), 'observer_runs')

    def test_not_activated_without_anything(self):
        result = audit.audit([], {}, [], as_of=RECEIVED)
        self.assertEqual((result['status'], result['eligible_signals']), ('since_required', None))


class IncrementalJournal(Harness):
    """Leitura incremental: só o acréscimo, nunca histórico retroativo, equivalente a report.load."""

    def row(self, mid, seen, body=None):
        return {'message_id': mid, 'date_utc': (seen - timedelta(seconds=30)).isoformat(timespec='seconds'),
                'edit_date_utc': None, 'text': body or text(), 'buttons': None, 'collected_at_utc': seen.isoformat(),
                'receipt_version': 1}

    def test_first_signal_matches_report_load_on_the_real_journal(self):
        real = ROOT / 'data' / 'messages.jsonl'
        if not real.exists():
            self.skipTest('real journal not available')
        from scangoal import report
        with patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(real.parent)}):
            loaded = {m['message_id']: m for m in report.load(result_observations={})[0]}
        rows = po.JournalCursor(real).read()
        fields = ('type', 'phase', 'market', 'selection', 'market_scope', 'period', 'bet', 'odds', 'teams', 'league',
                  'kickoff_utc', 'match_id', 'explicit_match_ids', 'share', 'total', 'had_ft_at_first_sight',
                  'edited_at_first_sight', 'collected_at_utc', 'date_utc', 'burst')
        compared = eligible = first_sight_ft = 0
        for row in rows:
            ours, theirs = po.first_signal(row), loaded.get(row['message_id'])
            self.assertIsNotNone(theirs, row['message_id'])
            self.assertEqual(po.eligible_signal(ours), po.eligible_signal(theirs), row['message_id'])
            eligible += po.eligible_signal(ours)
            for flag in ('type', 'had_ft_at_first_sight', 'edited_at_first_sight', 'collected_at_utc', 'date_utc'):
                self.assertEqual(ours.get(flag), theirs.get(flag), (row['message_id'], flag))
            first_sight_ft += bool(theirs['had_ft_at_first_sight'])
            if theirs['type'] == 'POLYMARKET':
                compared += 1
                self.assertEqual({k: ours.get(k) for k in fields}, {k: theirs.get(k) for k in fields}, row['message_id'])
        self.assertEqual(len(rows), len(loaded))
        self.assertGreater(compared, 1000)
        self.assertGreater(first_sight_ft, 0)                                # o caso "lido já com resultado" existe
        self.assertGreater(eligible, 50)

    def test_first_sight_flags_match_report_load(self):
        from scangoal import report
        bodies = {2: text(), 3: text() + '\n🏁 FT 3:1', 4: text()}
        store.append_versions([dict(self.row(mid, RECEIVED, body), edit_date_utc=RECEIVED.isoformat() if mid == 4 else None)
                               for mid, body in bodies.items()])
        loaded = {m['message_id']: m for m in report.load(result_observations={})[0]}
        rows = {r['message_id']: r for r in po.JournalCursor(self.data / 'messages.jsonl').read()}
        for mid in bodies:
            ours = po.first_signal(rows[mid])
            for flag in ('had_ft_at_first_sight', 'edited_at_first_sight', 'bet', 'match_id', 'kickoff_utc', 'odds'):
                self.assertEqual(ours.get(flag), loaded[mid].get(flag), (mid, flag))
            self.assertEqual(po.eligible_signal(ours), po.eligible_signal(loaded[mid]), mid)
        self.assertTrue(loaded[3]['had_ft_at_first_sight'])
        self.assertTrue(loaded[4]['edited_at_first_sight'])
        self.assertEqual([mid for mid in bodies if po.eligible_signal(po.first_signal(rows[mid]))], [2])

    def test_tick_never_calls_report_load(self):
        follower = self.follower()
        self.add(2, seen=self.clock.now)
        with patch('scangoal.report.load', side_effect=AssertionError('full reload')):
            self.assertEqual(follower.tick(), 1)

    def test_cursor_reads_only_the_appended_bytes(self):
        path = self.data / 'messages.jsonl'
        store.append_versions([self.row(1, RECEIVED)])
        cursor = po.JournalCursor(path)
        self.assertEqual([r['message_id'] for r in cursor.read()], [1])
        store.append_versions([self.row(2, RECEIVED)])
        opened = []
        original = Path.open

        def spy(self_path, *args, **kwargs):
            handle = original(self_path, *args, **kwargs)
            opened.append(handle)
            return handle

        with patch.object(Path, 'open', spy):
            self.assertEqual([r['message_id'] for r in cursor.read()], [2])
        self.assertEqual((cursor.full_reads, cursor.incremental_reads), (1, 1))
        self.assertEqual(cursor.read(), [])                                  # sem alteração: nem abre o ficheiro
        self.assertEqual(cursor.offset, path.stat().st_size)

    def test_incomplete_line_waits_for_completion(self):
        path = self.data / 'messages.jsonl'
        store.append_versions([self.row(1, RECEIVED)])
        cursor = po.JournalCursor(path)
        cursor.read()
        line = (json.dumps(dict(self.row(2, RECEIVED), sha256='x' * 64)) + '\n').encode()
        with path.open('ab') as handle:
            handle.write(line[:40])
        self.assertEqual(cursor.read(), [])
        with path.open('ab') as handle:
            handle.write(line[40:])
        self.assertEqual([r['message_id'] for r in cursor.read()], [2])

    def test_rewrite_or_shrink_rescans_without_reemitting(self):
        path = self.data / 'messages.jsonl'
        store.append_versions([self.row(1, RECEIVED), self.row(2, RECEIVED)])
        cursor = po.JournalCursor(path)
        cursor.read()
        prior = path.read_bytes()
        path.write_bytes(prior.replace(b'"message_id": 1', b'"message_id": 1 ', 1)
                         + (json.dumps(self.row(3, RECEIVED)) + '\n').encode())
        self.assertEqual([r['message_id'] for r in cursor.read()], [3])     # prefixo mudou: releitura completa
        path.write_bytes((json.dumps(self.row(2, RECEIVED)) + '\n').encode())
        self.assertEqual(cursor.read(), [])                                  # encolheu: nada de repetido
        self.assertEqual(cursor.full_reads, 3)

    def test_history_injected_later_is_never_observed(self):
        follower = self.follower(ago=0)
        # Um checkout/rebase traz uma mensagem lida antes do arranque: é histórico, nunca uma observação nova.
        store.append_versions([self.row(7, self.clock.now - timedelta(seconds=30))])
        self.assertEqual(follower.tick(), 0)
        self.assertEqual(follower.history_skipped, 1)
        self.assertEqual(self.remote.urls, [])
        store.append_versions([self.row(8, self.clock.now)])                 # a seguinte é nova e é observada
        self.assertEqual(follower.tick(), 1)


class ProductionIsolation(unittest.TestCase):
    """Compara os passos existentes com o workflow anterior à ativação."""

    MARKER = 'GOAL_ROI_POLYMARKET_SHADOW'
    FILES = ('.github/workflows/collect.yml', 'tests/test_silent_failures.py', 'tests/test_git_persistence.py')
    START = 'Start the Polymarket shadow observer (inactive by default)'
    STOP = 'Stop the Polymarket shadow observer and add its observations (inactive by default)'

    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in self.FILES:
            (self.tmp / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, self.tmp / name)
        self.patched = (ROOT / self.FILES[0]).read_text()
        self.original = re.sub(r'^      # P2 Polymarket shadow.*?(?=^      - name: Read bot conversation)',
                               '', self.patched, flags=re.M | re.S)
        self.original = re.sub('^      - name: ' + re.escape(self.STOP) + r'.*?(?=^      - name: Commit data)',
                               '', self.original, flags=re.M | re.S)

    @staticmethod
    def blocks(text):
        names = re.findall(r'^      - name: (.+)$', text, re.M)

        def body(name):                                    # o passo, sem os comentários do passo seguinte
            lines = text.split('      - name: %s\n' % name, 1)[1].split('\n      - ', 1)[0].split('\n')
            while lines and (not lines[-1].strip() or lines[-1].startswith('      #')):
                lines.pop()
            return '\n'.join(lines)

        return {n: body(n) for n in names}, names

    def load(self, name):
        spec = importlib.util.spec_from_file_location('patched_' + name, self.tmp / 'tests' / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.ROOT, module.WORKFLOW = ROOT, self.tmp / self.FILES[0]
        return module

    def test_production_workflow_untouched(self):
        self.assertNotIn(self.MARKER, self.original)
        self.assertNotIn('polymarket', self.original.lower())
        # PR #116 restores the authorization fallback when the worker is off. Previous pin:
        # a91ece0e0090ec858928bb16636a7bdacb9a2bc72535fabd4039d6278d7d957c
        self.assertEqual(hashlib.sha256(self.original.encode()).hexdigest(),
                         'e1a6e5c4e00e6274581fea425e59eb2b14f3b6639e647de589c7bb5fde36287c')

    def test_patch_only_adds_gated_bounded_non_blocking_steps(self):
        blocks, names = self.blocks(self.patched)
        old, old_names = self.blocks(self.original)
        self.assertEqual([n for n in names if n not in (self.START, self.STOP)], old_names)
        for name in old_names:
            self.assertEqual(blocks[name].rstrip('\n'), old[name].rstrip('\n'), name)
        for name in (self.START, self.STOP):
            with self.subTest(step=name):
                block = blocks[name]
                self.assertIn("vars.GOAL_ROI_POLYMARKET_SHADOW == 'on'", block)
                self.assertIn('        continue-on-error: true', block.split('\n'))
                self.assertIn('        timeout-minutes: 1', block.split('\n'))
                self.assertIn('timeout --signal=TERM', block)
                self.assertIn('$RUNNER_TEMP/goal-roi-polymarket', block)
                self.assertNotIn('secrets.', block)
                self.assertNotIn('        id:', block)
        self.assertIn('nohup timeout', blocks[self.START])
        self.assertIn('< /dev/null &', blocks[self.START])
        self.assertIn('--seconds 840', blocks[self.START])
        self.assertEqual(names[names.index(self.START) - 1], 'Resume pending publication')
        self.assertEqual(names[names.index(self.START) + 1], 'Read bot conversation (read-only)')
        self.assertEqual(names[names.index(self.STOP) + 1], 'Commit data from the fast reads')
        notify = "Telegram notices through the user's bot (skipped without keys)"
        self.assertEqual(names[names.index(notify) + 1], 'Commit data')
        for name in old_names:
            self.assertNotIn('polymarket', blocks[name].lower())

    def test_simulated_runs_are_identical_with_the_observer_on_or_failing(self):
        """Simulador do próprio projeto: cada passo a falhar, incluindo os do observador."""
        base = self.load('test_git_persistence')
        _, old_names = self.blocks(self.original)
        for failing in [None, *old_names, self.START, self.STOP]:
            with self.subTest(failing=failing):
                reference = base.simulate_job(self.original, failing if failing in old_names else None)
                active = base.simulate_job(self.patched, failing)
                strip = lambda names: [n for n in names if n not in (self.START, self.STOP)]
                self.assertEqual((active[0], strip(active[1]), strip(active[2])),
                                 (reference[0], reference[1], reference[2]))
                if failing is None:
                    self.assertIn(self.START, active[1])
                    self.assertIn(self.STOP, active[1])
                state, ran, skipped = base.simulate_job(self.patched, failing, shadow=False)
                self.assertTrue({self.START, self.STOP}.issubset(skipped))
                self.assertEqual((state, ran, strip(skipped)), reference)

    def test_project_continuity_tests_pass_on_the_patched_workflow(self):
        silent, persistence = self.load('test_silent_failures'), self.load('test_git_persistence')
        suite = unittest.TestSuite()
        loader = unittest.TestLoader()
        for case in (silent.Steps, silent.Telegram, persistence.ChainContinuityTests):
            suite.addTests(loader.loadTestsFromTestCase(case))
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        self.assertGreaterEqual(result.testsRun, 10)

    def test_collection_modules_do_not_import_the_observer(self):
        for name in ('reader', 'watch', 'store', 'publication', 'notify', 'report', 'stake_alerts',
                     'polymarket_alerts', 'certification', 'brain', 'strategy_board'):
            with self.subTest(module=name):
                self.assertNotIn('polymarket_observer', (ROOT / 'scangoal' / (name + '.py')).read_text(encoding='utf-8'))

    def test_observer_has_no_write_or_order_paths_into_the_repository(self):
        source = (ROOT / 'scangoal/polymarket_observer.py').read_text(encoding='utf-8')
        for forbidden in ("method='POST'", 'Authorization', '/order', 'subprocess', 'git ', 'notify', 'publication'):
            self.assertNotIn(forbidden, source)


class ContinuityUnderSystematicFailure(unittest.TestCase):
    """Processo real, rede real recusada (proxy morto): a recolha continua a funcionar e não é alterada."""

    def test_collection_works_while_observer_fails(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        data, spool = tmp / 'data', tmp / 'spool'
        data.mkdir()
        env = dict(os.environ, SCANGOAL_DATA_DIR=str(data), GOAL_ROI_POLYMARKET_SHADOW='on',
                   HTTPS_PROXY='http://127.0.0.1:9', https_proxy='http://127.0.0.1:9', NO_PROXY='', no_proxy='')
        kickoff = datetime.now(timezone.utc) + timedelta(days=1)
        msk = kickoff + timedelta(hours=3)
        months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
        head = '%d %s %d %02d:%02d' % (msk.day, months[msk.month - 1], msk.year, msk.hour, msk.minute)
        follower = subprocess.Popen([sys.executable, '-B', '-m', 'scangoal.polymarket_observer', 'follow',
                                     '--spool', str(spool), '--seconds', '25'], cwd=ROOT, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            with patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(data)}):
                deadline = time.monotonic() + 15
                while not (spool / 'follower.pid').exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
                started = time.monotonic()
                rows = []
                for mid in range(1, 6):
                    now = datetime.now(timezone.utc)
                    row = {'message_id': mid, 'date_utc': now.isoformat(timespec='seconds'), 'edit_date_utc': None,
                           'text': text(head=head, mid=kickoff.strftime('%Y%m%d')) + '\n#%d' % mid, 'buttons': None,
                           'collected_at_utc': now.isoformat(), 'receipt_version': 1}
                    rows.append(row)
                    self.assertEqual(store.append_versions([row]), 1)      # a escrita normal nunca espera pelo observador
                    time.sleep(1.2)
                elapsed = time.monotonic() - started
                from scangoal import report
                messages, *_ = report.load(result_observations={}, as_of=datetime.now(timezone.utc))
                self.assertEqual(sorted(m['message_id'] for m in messages if m.get('type') == 'POLYMARKET'),
                                 [1, 2, 3, 4, 5])
                journal = (data / 'messages.jsonl').read_bytes()
                deadline = time.monotonic() + 20
                while len(list(spool.glob('[0-9a-f]*.json'))) < 5 and time.monotonic() < deadline:
                    time.sleep(0.2)
            po.main(['stop', '--spool', str(spool)], environ={po.FLAG: 'on'})
            output, _ = follower.communicate(timeout=30)
        finally:
            if follower.poll() is None:
                follower.kill()
        self.assertEqual(follower.returncode, 0, output)
        self.assertLess(elapsed, 10)
        self.assertEqual((data / 'messages.jsonl').read_bytes(), journal)
        self.assertFalse((data / po.JOURNAL).exists())               # o seguidor nunca escreve em data/
        records = [json.loads(p.read_text()) for p in spool.glob('[0-9a-f]*.json')]
        self.assertEqual(len(records), 5, output)
        self.assertTrue(all(r['status'] == 'technical_failure' for r in records))
        self.assertGreaterEqual(sum(r['reason'] == 'circuit_open' for r in records), 3)


if __name__ == '__main__':
    unittest.main()
