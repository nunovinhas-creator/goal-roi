"""Ressalva da auditoria da #41: um sinal válido no PLANO pode expirar antes do ENVIO (envios anteriores, publicação git com
até 3 × pull/push). `notify.deliver` revalida cada aviso de aposta com o relógio real (1) antes de gravar a intenção e (2)
depois de a intenção estar publicada, imediatamente antes de contactar o Telegram.

Tudo offline, com relógio controlado; o remetente é injetado (nenhum Telegram real). Os testes com git usam repositórios
locais (um repositório bare faz de GitHub).
"""
import copy, io, json, os, shutil, unittest
from contextlib import redirect_stdout
from datetime import timedelta
from unittest.mock import patch

from scangoal import notify
from test_stale_notices import NOW, live, pre, report, state

TOKEN, CHAT = 'test-token', 'test-chat'


class Crash(Exception):
    pass


class Clock:
    """Relógio da execução controlado pelo teste: `advance` simula o tempo gasto em envios ou na publicação git."""

    def __init__(self, t=NOW):
        self.t, self.reads = t, []

    def __call__(self):
        self.reads.append(self.t)
        return self.t

    def advance(self, minutes):
        self.t += timedelta(minutes=minutes)


def events(store, key=None):
    log, _ = store.read_log()
    return [(e['key'], e['event']) for e in log if key is None or e['key'] == key]


def game_keys(store):
    return sorted({e['key'] for e in store.read_log()[0] if e['key'].startswith('game|')})


class Delivery(unittest.TestCase):

    def run_once(self, watch, store=None, st=None, now=NOW, clock=None, sender=None, publish=None, checkpoint=None, rep=None):
        store = store or notify.MemoryStore(state())
        st = store.load_state() if st is None else st
        sent = []
        sender = sender or (lambda token, chat, text: sent.append(text))
        with redirect_stdout(io.StringIO()) as out:
            st, done = notify.deliver(store, rep or report(watch), st, now, sender, TOKEN, CHAT, checkpoint, publish, clock or Clock(now))
        return store, st, done, sent, out.getvalue()

    # --- 1. válido no plano e no envio --------------------------------------------------------------------------------
    def test_valid_at_plan_and_at_send_is_delivered_once(self):
        store, st, done, sent, _ = self.run_once([live('a', 29)])
        self.assertEqual((done, len(sent), st['sent_total']), (1, 1, 11))
        key = game_keys(store)[0]
        self.assertEqual(events(store, key), [(key, 'pending'), (key, 'intent'), (key, 'delivered')])
        self.assertEqual(st['deliveries'][key]['state'], 'delivered')

    # --- 2. válido no plano, expirado antes do envio ------------------------------------------------------------------
    def test_valid_at_plan_but_expired_before_send_is_not_sent(self):
        clock = Clock()
        sender_calls = []

        def slow_hello(token, chat, text):                          # o aviso anterior (boas-vindas) demora 2 minutos
            sender_calls.append(text)
            clock.advance(2)
        st0 = state()
        st0['hello'] = False
        store, st, done, _, out = self.run_once([live('a', 29)], store=notify.MemoryStore(st0), clock=clock, sender=slow_hello)
        self.assertEqual(len(sender_calls), 1)                      # só as boas-vindas
        self.assertIn('Avisos do goal-roi ligados', sender_calls[0])
        key = game_keys(store)[0]
        self.assertEqual(events(store, key), [(key, 'pending'), (key, 'expired')])   # antes da intenção: sem efeitos
        rec = st['deliveries'][key]
        self.assertEqual((rec['state'], rec['reason']), ('expired', 'signal_expired_before_intent'))
        self.assertEqual(st.get('notified_items') or {}, {})        # nada marcado como avisado
        self.assertEqual((st['sent_total'], st['delivery_counts']), (11, {'delivered': 1, 'expired': 1}))
        self.assertIn('NOTIFY_EXPIRED_BEFORE_INTENT', out)

    def test_expired_between_intent_and_send_without_publication(self):
        clock = Clock()

        def tick(name):
            if name == 'after_intent':                              # tempo gasto a gravar a intenção
                clock.advance(2)
        store, st, done, sent, out = self.run_once([live('a', 29)], clock=clock, checkpoint=tick)
        key = game_keys(store)[0]
        self.assertEqual((sent, done), ([], 0))
        self.assertEqual(events(store, key), [(key, 'pending'), (key, 'intent'), (key, 'expired')])
        self.assertEqual(st['deliveries'][key]['reason'], 'signal_expired_before_send')
        self.assertIn('NOTIFY_EXPIRED_BEFORE_SEND', out)

    # --- 3. expiração durante a publicação git ------------------------------------------------------------------------
    def test_expired_during_git_publication_is_not_sent_and_terminal_is_recorded(self):
        for w in (live('a', 29), pre('b', 60, 1)):                  # ao vivo: 30 min; pré-jogo: o início chega durante o push
            with self.subTest(phase=w['phase']):
                clock, published = Clock(), []

                def slow_publish(key):
                    published.append(key)
                    clock.advance(2)                                 # 3 × pull/push lentos
                    return True
                store, st, done, sent, _ = self.run_once([w], clock=clock, publish=slow_publish)
                key = game_keys(store)[0]
                self.assertEqual((sent, done, published), ([], 0, [key]))
                self.assertEqual(events(store, key), [(key, 'pending'), (key, 'intent'), (key, 'expired')])
                rec = st['deliveries'][key]
                self.assertEqual((rec['state'], rec['reason'], rec['events']),
                                 ('expired', 'signal_expired_before_send', ['pending', 'intent', 'expired']))
                self.assertEqual(st['sent_total'], 10)              # não é entrega: o total não muda
                self.assertNotIn('delivered', st['delivery_counts'])
                self.assertIn('item|id:%s|1X2|1' % w['match_id'], st['notified_items'])   # efeitos da intenção publicada mantidos

    def test_published_and_still_valid_is_sent(self):
        clock = Clock()
        store, st, done, sent, _ = self.run_once([live('a', 25)], clock=clock, publish=lambda key: clock.advance(2) or True)
        self.assertEqual((done, len(sent)), (1, 1))

    # --- 4. falha de publicação -----------------------------------------------------------------------------------
    def test_publication_failure_sends_nothing_and_an_expired_retry_never_sends(self):
        store, st, done, sent, out = self.run_once([live('a', 25)], publish=lambda key: False)
        key = game_keys(store)[0]
        self.assertEqual((sent, done), ([], 0))
        self.assertIn('NOTIFY_PUBLISH_FAILED', out)
        self.assertEqual(st['deliveries'][key]['state'], 'pending')                 # retirada: efeitos desfeitos
        self.assertEqual(st.get('notified_items') or {}, {})
        self.assertEqual(events(store, key), [(key, 'pending'), (key, 'intent'), (key, 'withdrawn')])
        # Execução seguinte 10 minutos depois: o sinal já tem 35 minutos. Não é planeado: a pendente expira, sem envio.
        later = NOW + timedelta(minutes=10)
        store, st, done, sent, _ = self.run_once([live('a', 25)], store=store, now=later, clock=Clock(later))
        self.assertEqual((sent, done), ([], 0))
        self.assertEqual(events(store, key)[-1], (key, 'expired'))
        self.assertEqual(st['deliveries'][key]['state'], 'expired')

    def test_publication_failure_then_valid_retry_sends_exactly_once(self):
        store, st, *_ = self.run_once([live('a', 10)], publish=lambda key: False)
        later = NOW + timedelta(minutes=2)
        store, st, done, sent, _ = self.run_once([live('a', 10)], store=store, now=later, clock=Clock(later), publish=lambda key: True)
        self.assertEqual((done, len(sent)), (1, 1))
        key = game_keys(store)[0]
        self.assertEqual([e for _, e in events(store, key)], ['pending', 'intent', 'withdrawn', 'intent', 'delivered'])

    # --- 5. reinício depois de uma intenção publicada --------------------------------------------------------------
    def test_restart_after_published_intent_never_sends(self):
        for point in ('after_publish', 'before_send'):
            with self.subTest(crash=point):
                def crash(name, point=point):
                    if name == point:
                        raise Crash()
                store = notify.MemoryStore(state())
                with self.assertRaises(Crash):
                    self.run_once([live('a', 10)], store=store, publish=lambda key: True, checkpoint=crash)
                later = NOW + timedelta(minutes=1)                  # ainda válido: mesmo assim nunca é reenviado
                store, st, done, sent, _ = self.run_once([live('a', 10)], store=store, now=later, clock=Clock(later), publish=lambda key: True)
                key = game_keys(store)[0]
                self.assertEqual((sent, done), ([], 0))
                self.assertEqual(st['deliveries'][key]['state'], 'uncertain')

    def test_restart_after_expiry_was_decided(self):
        """Queda depois de gravar `expired` no estado mas antes do registo, e queda depois de ambos: consistente, sem envio."""
        clock = Clock()

        class LogDies(notify.MemoryStore):
            die = True

            def append_log(self, entries):
                if self.die and any(e['event'] == 'expired' for e in entries):
                    self.die = False
                    raise Crash()
                super().append_log(entries)
        store = LogDies(state())
        with self.assertRaises(Crash):
            self.run_once([live('a', 29)], store=store, clock=clock, publish=lambda key: clock.advance(2) or True)
        key = game_keys(store)[0]
        self.assertEqual(events(store, key), [(key, 'pending'), (key, 'intent')])  # o registo ainda não tem o desfecho
        later = NOW + timedelta(minutes=3)
        store, st, done, sent, _ = self.run_once([live('a', 29)], store=store, now=later, clock=Clock(later))
        self.assertEqual((sent, done), ([], 0))
        self.assertEqual(st['deliveries'][key]['state'], 'expired')                # o estado já tinha o desfecho
        self.assertEqual([e for _, e in events(store, key)], ['pending', 'intent', 'expired'])   # registo completado (reconstruído)

    def test_state_lost_after_published_intent_and_expiry_is_rebuilt_from_the_log(self):
        """Estado reposto de antes da intenção (só o registo sabe): reconcile reconstrói `expired` e os efeitos; sem envio."""
        clock = Clock()
        store = notify.MemoryStore(state())
        before = store.load_state()
        self.run_once([live('a', 29)], store=store, clock=clock, publish=lambda key: clock.advance(2) or True)
        store.state = before                                         # estado antigo, registo completo
        later = NOW + timedelta(minutes=1)
        store, st, done, sent, _ = self.run_once([live('a', 29)], store=store, now=later, clock=Clock(later))
        key = game_keys(store)[0]
        self.assertEqual((sent, done), ([], 0))
        self.assertEqual((st['deliveries'][key]['state'], st['deliveries'][key].get('reconstructed')), ('expired', True))
        self.assertEqual(st['sent_total'], 10)
        self.assertIn('item|id:a|1X2|1', st['notified_items'])     # mesmos efeitos que a intenção publicada

    # --- 6. idempotência entre execuções --------------------------------------------------------------------------
    def test_idempotent_across_runs(self):
        clock = Clock()
        store, st, *_ = self.run_once([live('a', 29), live('b', 5)], clock=clock, publish=lambda key: clock.advance(1.5) or True)
        snap = copy.deepcopy(store.log)
        delivered = sorted(k for k, r in st['deliveries'].items() if r['state'] == 'delivered')
        expired = sorted(k for k, r in st['deliveries'].items() if r['state'] == 'expired')
        self.assertEqual((len(delivered), len(expired)), (1, 1))
        for k in range(1, 6):
            t = NOW + timedelta(minutes=k)
            store, st, done, sent, _ = self.run_once([live('a', 29), live('b', 5)], store=store, now=t, clock=Clock(t), publish=lambda key: True)
            self.assertEqual((sent, done), ([], 0))
        self.assertEqual(store.log, snap)                            # nada novo no registo: nem intenções, nem desfechos
        self.assertEqual(st['sent_total'], 11)

    # --- o resto não muda ------------------------------------------------------------------------------------------
    def test_multi_bet_notice_with_one_expired_bet_is_not_sent(self):
        clock = Clock()
        watch = [live('a', 29, selection='1'), live('a', 5, selection='X')]          # mesmo jogo, duas apostas
        store, st, done, sent, _ = self.run_once(watch, clock=clock, publish=lambda key: clock.advance(2) or True)
        self.assertEqual((sent, done), ([], 0))
        rec = next(r for k, r in st['deliveries'].items() if k.startswith('game|'))
        self.assertEqual(rec['state'], 'expired')

    def test_reinforcement_keeps_a_bet_current(self):
        clock = Clock()
        watch = [live('a', 29), live('a', 3, odds=3.6)]                             # mesma aposta: reforço recente
        store, st, done, sent, _ = self.run_once(watch, clock=clock, publish=lambda key: clock.advance(2) or True)
        self.assertEqual((done, len(sent)), (1, 1))

    def test_summary_status_and_regime_notices_are_never_revalidated(self):
        from test_telegram_presentation import EVENING, HA, HB, base_state, report as full_report
        rep = full_report({HA: 'abandonada', HB: 'promissora'},
                          regime=[{'type': 'OPEN_GAME', 'state': 'piorou', 'recent': {'hit_rate': 0.3}, 'before': {'hit_rate': 0.8}}])
        st0 = base_state()
        st0['hello'] = False
        clock = Clock(EVENING)
        sent = []

        def slow(token, chat, text):
            sent.append(text)
            clock.advance(24 * 60)                                  # cada envio "demora" um dia inteiro
        store = notify.MemoryStore(st0)
        with redirect_stdout(io.StringIO()):
            st, done = notify.deliver(store, rep, store.load_state(), EVENING, slow, TOKEN, CHAT, None, lambda key: True, clock)
        kinds = [r['kind'] for r in st['deliveries'].values() if r['state'] == 'delivered']
        self.assertEqual(sorted(kinds), ['hello', 'regime', 'summary'])
        self.assertEqual(done, 3)
        self.assertNotIn('expired', st.get('delivery_counts') or {})

    def test_expired_is_not_a_delivery_problem_for_the_exit_code(self):
        self.assertNotIn('expired', notify.PROBLEMS)

    def test_default_clock_is_the_run_start_plus_monotonic_elapsed_time(self):
        with patch.object(notify.time, 'monotonic', side_effect=[100.0, 100.0, 160.0, 1900.0]):
            clock = notify.run_clock(NOW)
            self.assertEqual(clock(), NOW)
            self.assertEqual(clock(), NOW + timedelta(seconds=60))
            self.assertEqual(clock(), NOW + timedelta(seconds=1800))

    def test_default_clock_expires_a_signal_when_real_time_passes(self):
        """Sem relógio injetado (como em produção): o tempo real decorrido conta."""
        elapsed = [1000.0]

        def slow_publish(key):                                       # a publicação git demora 2 minutos reais
            elapsed[0] += 120.0
            return True
        with patch.object(notify.time, 'monotonic', side_effect=lambda: elapsed[0]):
            store = notify.MemoryStore(state())
            sent = []
            with redirect_stdout(io.StringIO()):
                st, done = notify.deliver(store, report([live('a', 29)]), store.load_state(), NOW, lambda *a: sent.append(a), TOKEN, CHAT,
                                          None, slow_publish)
        self.assertEqual((sent, done), ([], 0))
        self.assertEqual({r['state'] for r in st['deliveries'].values()}, {'expired'})


@unittest.skipUnless(shutil.which('git'), 'git required')
class RealGit(unittest.TestCase):
    """Barreira de publicação real (`GitPublisher`, NOTIFY_DURABLE=git) com um push lento: expira durante a publicação."""

    def setUp(self):
        from test_notify_durability import GitWorld
        self.world = GitWorld('run')
        self.world.setUp()

    def tearDown(self):
        self.world.doCleanups()

    def seed(self, rep):
        from test_notify_durability import git
        view = self.world.runner()
        (view / 'data' / 'report.json').write_text(json.dumps(rep, ensure_ascii=False))
        (view / 'data' / 'notify_state.json').write_text(json.dumps(state()))
        git(view, 'add', '-A')
        git(view, 'commit', '-qm', 'seed incident-like data')
        git(view, 'push', '-q', 'origin', 'HEAD:main')

    def test_slow_real_push_expires_the_signal_and_the_remote_records_it(self):
        from test_notify_durability import DURABLE
        self.seed(report([live('a', 29)]))
        clock = Clock()
        real_call = notify.GitPublisher.__call__

        def slow_push(publisher, key):
            ok = real_call(publisher, key)                          # push real para o repositório bare
            clock.advance(2)                                        # ... que demorou 2 minutos
            return ok
        a, sent = self.world.runner(), []
        with patch.object(notify.GitPublisher, '__call__', slow_push), patch.object(notify, 'run_clock', lambda now: clock):
            out = self.world.notify(a, lambda t, c, text: sent.append(text), env=DURABLE, now=NOW)
        self.assertEqual(sent, [])
        self.assertIn('NOTIFY_EXPIRED_BEFORE_SEND', out)
        remote = self.world.remote_state()                          # o remoto tem a intenção publicada antes de qualquer envio
        rec = next(r for k, r in remote['deliveries'].items() if k.startswith('game|'))
        self.assertEqual(rec['events'][:2], ['pending', 'intent'])
        self.world.commit_step(a)                                   # passo "Commit data" do workflow
        remote = self.world.remote_state()
        rec = next(r for k, r in remote['deliveries'].items() if k.startswith('game|'))
        self.assertEqual((rec['state'], rec['events']), ('expired', ['pending', 'intent', 'expired']))
        b, sent_b = self.world.runner(), []                         # runner seguinte, do remoto: nada a enviar
        later = NOW + timedelta(minutes=1)
        self.world.notify(b, lambda t, c, text: sent_b.append(text), env=DURABLE, now=later)
        self.assertEqual(sent_b, [])

    def test_fast_real_push_still_delivers(self):
        from test_notify_durability import DURABLE
        self.seed(report([live('a', 20)]))
        a, sent = self.world.runner(), []
        self.world.notify(a, lambda t, c, text: sent.append(text), env=DURABLE, now=NOW)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sent[0].startswith('🟠 INDÍCIO · '))
        self.assertEqual(sent[0].splitlines()[1], '⚽ Casa a — Fora a')

    def test_1625_reinforcement_survives_a_slow_push_and_is_sent_once(self):
        from test_stale_notices import HID, at, reason
        from test_telegram_presentation import active, certificate

        planned = NOW.replace(hour=16, minute=29)
        hint = 'Stake | Exemplo'
        old = live('a', 0, date_utc=at(-29, planned), reasons=[reason('forte', True)])
        newer = live('a', 0, date_utc=at(-4, planned), odds=2.15, type='STAKE_LOAD', reasons=[reason(hid=hint)])
        rep = report([old, newer], {HID: 'forte', hint: 'promissora'})
        rep['brain']['validation'] = active(HID)
        rep['brain']['presentation']['hypotheses'] = {HID: {'confirmation': certificate(), 'prospective_games': 60},
                                                     hint: {'prospective_games': 30}}
        self.seed(rep)
        clock = Clock(planned)
        real_publish = notify.GitPublisher.__call__
        runner, sent = self.world.runner(), []

        def slow_push(publisher, key):
            published = real_publish(publisher, key)
            clock.advance(2)
            return published

        def sender(token, chat, text):
            remote = self.world.runner()
            log, invalid = notify.parse_log((remote / 'data' / notify.LOG_NAME).read_bytes())
            self.assertEqual(invalid, 0)
            intent, = [event for event in log if event['event'] == 'intent']
            state_at_send = json.loads((remote / 'data' / notify.STATE_NAME).read_text())
            self.assertEqual(state_at_send['deliveries'][intent['key']]['state'], 'in_flight')
            self.assertEqual(intent['sha256'], notify.sha(text))
            self.assertEqual(intent['effects'], [
                ['notice_games', HID, 60], ['notice_games', hint, 30], ['item', 'item|id:a|1X2|1'],
                ['watch_sent', '2026-11-17T16:00:00+00:00|Casa a — Fora a|1'],
                ['watch_sent', '2026-11-17T16:25:00+00:00|Casa a — Fora a|1'],
            ])
            self.assertEqual(clock.t, planned + timedelta(minutes=2))
            sent.append(text)

        with patch.object(notify.GitPublisher, '__call__', slow_push), patch.object(notify, 'run_clock', return_value=clock):
            self.world.notify(runner, sender, now=planned)
        text, = sent
        self.assertEqual(text.splitlines()[2:5], [
            '🎯 1 @ 2,15 · 1X2 · Stake · INDÍCIO · sinal de 17/11 16:25 UTC',
            '↳ Referência histórica · 17/11 16:00 UTC · Betfair durante o jogo · odd 3,40',
            notify.scientific_label(rep, HID)[0],
        ])
        self.assertTrue(notify.guard_claims(text, rep, [HID], exact_lines=True))
        self.world.commit_step(runner)
        restart = self.world.runner()
        self.world.notify(restart, lambda *args: sent.append(args[-1]), now=clock.t)
        self.assertEqual(sent, [text])


if __name__ == '__main__':
    unittest.main()
