"""P1 (17/11/2026): o Telegram nunca apresenta como oportunidade atual um sinal de aposta já expirado.

Incidente: entre as 12:58 e as 13:07 UTC de 17/11/2026, a tática "Betfair | Faixa de odd = odd acima de 3,00 & Momento =
durante o jogo" passou a promissora (12:56:54) e o notificador enviou, como "A seguir", doze sinais ao vivo de 14 a 17/11
(47 minutos a três dias de idade): o filtro temporal só valia para os alertas certificados, e `brain.watch` incluía qualquer
aposta sem resultado. Tudo offline, com relógio controlado; nada é enviado (remetente injetado) e nada em data/ é tocado.
"""
import copy, io, json, os, tempfile, unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scangoal import brain, freshness, notify

UTC = timezone.utc
NOW = datetime(2026, 11, 17, 15, 0, 0, tzinfo=UTC)
HID = 'Betfair | Faixa de odd = odd acima de 3,00 & Momento = durante o jogo'
FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'incident_2026_11_17'
ENV = {'TG_BOT_TOKEN': 'test-token', 'TG_CHAT_ID': 'test-chat'}


def at(minutes, base=NOW):
    return (base + timedelta(minutes=minutes)).isoformat(timespec='seconds')


def reason(status='promissora', certified=False, hid=HID):
    return {'id': hid, 'status': status, 'n': 40, 'games': 40, 'hit_rate': 0.4, 'prob_above': 0.97, 'break_even': 0.3,
            'edge': 0.1, 'edge_ci': [0.02, 0.2], 'prob_above_cut': 0.95, 'prudent_rate': 0.35, 'certified': certified}


def live(mid, minutes, **over):
    """Sinal ao vivo (BETFAIR LIVE: sem hora de início), publicado `minutes` minutos antes de NOW."""
    w = {'date_utc': at(-minutes), 'kickoff_utc': None, 'phase': 'live', 'match': 'Casa %s — Fora %s' % (mid, mid), 'match_id': mid,
         'type': 'BETFAIR', 'market': '1X2', 'selection': '1', 'odds': 3.4, 'finished': False, 'reasons': [reason()]}
    w.update(over)
    return w


def pre(mid, minutes, kickoff_in, **over):
    """Sinal pré-jogo publicado `minutes` minutos antes de NOW, com início `kickoff_in` minutos depois de NOW."""
    return live(mid, minutes, **dict({'phase': 'pre', 'kickoff_utc': at(kickoff_in)}, **over))


def report(watch, status_list=None):
    return {'generated_at_utc': NOW.isoformat(), 'settled_bets': 400, 'audit': {'hl_results': 50, 'bsd_results': 20},
            'daily': [{'day': '2026-11-17', 'bets': 4, 'settled': 3, 'wins': 2, 'units': 0.85}], 'timeline': [{'u': 1.5}],
            'operational': {'scope': 'eligible_bets_only', 'regime': [{'type': 'BETFAIR', 'state': 'estável'}]},
            'brain': {'evidence_scope': 'eligible_bets_only', 'status_rules': brain.STATUS_RULES, 'hypotheses': 6000,
                      'eligibility': {'eligible': 600, 'total': 700}, 'counts': {'promissora': 1},
                      'validation': {'method': notify.VALIDATION_METHOD, 'state': 'not_activated', 'ledger_valid': False, 'certified': []},
                      'status_list': {HID: 'promissora'} if status_list is None else status_list, 'watch': list(watch),
                      'presentation': {'version': brain.PRESENTATION, 'hypotheses': {}}}}


def state():
    return {'hello': True, 'sent_total': 10, 'watch_sent': [], 'statuses': {HID: 'promissora'},
            'migrations': {notify.STATUS_MIGRATION: {}, notify.FORMAT_MIGRATION: {'at_utc': at(-600)}}}


def games(texts):
    return [t.splitlines()[1] for t in texts if t.startswith(('🟠 INDÍCIO', '🟢 ALERTA'))]


class Rule(unittest.TestCase):
    """A regra única (freshness.check), caso a caso."""

    def ok(self, w, now=NOW):
        return freshness.check(w, now)

    def test_01_live_5_minutes_is_eligible(self):
        self.assertEqual(self.ok(live('a', 5)), (True, 'ok'))

    def test_02_live_29_minutes_is_eligible(self):
        self.assertEqual(self.ok(live('a', 29)), (True, 'ok'))
        self.assertEqual(self.ok(live('a', 30)), (True, 'ok'))                  # limite inclusivo: "no máximo 30 minutos"

    def test_03_live_31_minutes_is_rejected(self):
        self.assertEqual(self.ok(live('a', 31)), (False, 'live_too_old'))
        self.assertEqual(self.ok(live('a', 30, date_utc=at(-30.02))), (False, 'live_too_old'))

    def test_04_live_two_days_old_is_rejected(self):
        self.assertEqual(self.ok(live('a', 2 * 24 * 60)), (False, 'live_too_old'))

    def test_05_known_final_result_is_rejected(self):
        self.assertEqual(self.ok(live('a', 5, finished=True)), (False, 'finished'))
        self.assertEqual(self.ok(live('a', 5, ft=[1, 0])), (False, 'finished'))
        self.assertEqual(self.ok(pre('a', 60, 120, finished=True)), (False, 'finished'))
        # A ausência de resultado não prova que o jogo está a decorrer: só o tempo decide.
        self.assertEqual(self.ok(live('a', 31, finished=False)), (False, 'live_too_old'))
        # Ao vivo com início conhecido há mais do que a duração de um jogo: já terminou, mesmo sem resultado.
        self.assertEqual(self.ok(live('a', 5, kickoff_utc=at(-freshness.GAME_MAX_MIN - 1))), (False, 'live_game_over'))
        self.assertEqual(self.ok(live('a', 5, kickoff_utc=at(-80))), (True, 'ok'))

    def test_06_prematch_with_future_kickoff_is_eligible(self):
        self.assertEqual(self.ok(pre('a', 5, 60)), (True, 'ok'))
        self.assertEqual(self.ok(pre('a', 6 * 60, 1)), (True, 'ok'))            # 6 h antes do sinal, mas ainda antes do início

    def test_07_prematch_after_kickoff_is_rejected(self):
        self.assertEqual(self.ok(pre('a', 10, -5)), (False, 'prematch_started'))  # sinal recente, mas o jogo já começou
        self.assertEqual(self.ok(pre('a', 10, 0)), (False, 'prematch_started'))   # no instante do início
        self.assertEqual(self.ok(pre('a', 3 * 24 * 60, -3 * 24 * 60 + 90)), (False, 'prematch_started'))

    def test_11_missing_invalid_or_inconsistent_times_are_conservative(self):
        self.assertEqual(self.ok(live('a', 5, date_utc=None)), (False, 'signal_time_missing'))
        self.assertEqual(self.ok(live('a', 5, date_utc='')), (False, 'signal_time_missing'))
        self.assertEqual(self.ok(live('a', 5, date_utc='ontem à noite')), (False, 'signal_time_invalid'))
        self.assertEqual(self.ok(live('a', 5, date_utc=12345)), (False, 'signal_time_invalid'))
        self.assertEqual(self.ok(live('a', 5, date_utc='2026-13-45T25:00:00')), (False, 'signal_time_invalid'))
        self.assertEqual(self.ok(live('a', -60)), (False, 'signal_time_future'))  # sinal "daqui a uma hora": incoerente
        self.assertEqual(self.ok(live('a', -2)), (True, 'ok'))                    # dentro da tolerância de relógio
        self.assertEqual(self.ok(pre('a', 5, 60, kickoff_utc='amanhã')), (False, 'kickoff_invalid'))
        self.assertEqual(self.ok(pre('a', 5, 60, kickoff_utc=None)), (False, 'prematch_no_kickoff'))   # não se prova que não começou
        self.assertEqual(self.ok(pre('a', 5, 30 * 24 * 60)), (False, 'prematch_kickoff_incoherent'))   # início a 30 dias
        self.assertEqual(self.ok(live('a', 5, kickoff_utc=at(120))), (False, 'live_before_kickoff'))   # "ao vivo" antes do início
        self.assertEqual(self.ok(live('a', 5), now=NOW.replace(tzinfo=None)), (False, 'clock_invalid'))
        self.assertEqual(self.ok(live('a', 5), now=None), (False, 'clock_invalid'))
        # Sem fase: anterior ao início conhecido conta como pré-jogo; sem início, regra ao vivo (a mais curta).
        self.assertEqual(self.ok(dict(pre('a', 5, 60), phase=None)), (True, 'ok'))
        self.assertEqual(self.ok(dict(live('a', 45), phase=None)), (False, 'live_too_old'))
        self.assertEqual(self.ok(dict(pre('a', 10, -5), phase=None)), (False, 'prematch_started'))
        self.assertEqual(self.ok(dict(live('a', 10, kickoff_utc=at(-15)), phase=None)), (True, 'ok'))   # depois do início: ao vivo
        # Hora sem fuso conta como UTC (como no resto do projeto).
        self.assertEqual(self.ok(live('a', 5, date_utc=at(-5)[:-6])), (True, 'ok'))

    def test_signal_time_is_the_original_one_not_the_collection_or_report_time(self):
        w = live('a', 3 * 24 * 60, collected_at_utc=at(-1), signal_seen_utc=at(-1), generated_at_utc=at(0))
        self.assertEqual(self.ok(w), (False, 'live_too_old'))


class Notices(unittest.TestCase):
    """A barreira no ponto de envio (`notify.game_notices`), para todos os níveis."""

    def sent(self, watch, st=None, now=NOW, status_list=None):
        st = state() if st is None else st
        return notify.messages(report(watch, status_list), st, now), st

    def test_01_02_recent_live_hints_are_sent(self):
        out, _ = self.sent([live('a', 5), live('b', 29)])
        self.assertEqual(games(out), ['⚽ Casa a — Fora a', '⚽ Casa b — Fora b'])

    def test_03_04_old_live_hints_are_not_sent(self):
        out, st = self.sent([live('a', 31), live('b', 2 * 24 * 60)])
        self.assertEqual(games(out), [])
        self.assertEqual(st.get('notified_items') or {}, {})                   # nada marcado: nada fica em fila

    def test_every_level_is_filtered(self):
        for status in ('promissora', 'forte', 'confirmada'):
            for certified in (False, True):
                with self.subTest(status=status, certified=certified):
                    out, _ = self.sent([live('a', 45, reasons=[reason(status, certified)])], status_list={HID: status})
                    self.assertEqual(games(out), [])
        out, _ = self.sent([live('a', 5, reasons=[reason('forte', True)])], status_list={HID: 'forte'})
        self.assertEqual(len(games(out)), 1)                                     # um alerta não certificado sai como indício

    def test_05_finished_game_is_not_sent(self):
        out, _ = self.sent([live('a', 5, finished=True), pre('b', 30, 60, ft=[2, 1])])
        self.assertEqual(games(out), [])

    def test_06_07_prematch(self):
        out, _ = self.sent([pre('a', 300, 20), pre('b', 10, -5)])
        self.assertEqual(games(out), ['⚽ Casa a — Fora a'])

    def test_08_promotion_with_ten_historical_signals_sends_nothing_old(self):
        hist = [live('h%d' % i, 60 * (i + 1) * 7) for i in range(10)]        # 7 h a 70 h antes: nenhum apostável
        st = state()
        st['statuses'] = {}
        out, st = self.sent(hist, st, status_list={})                          # em observação: watch vazio na prática
        self.assertEqual(games(out), [])
        out, st = self.sent(hist, st)                                           # 12:56:54 — passa a promissora
        self.assertEqual(games(out), [])
        for k in range(1, 6):                                                   # e nas execuções seguintes também nada
            out, st = self.sent(hist, st, now=NOW + timedelta(minutes=2 * k))
            self.assertEqual(games(out), [])
        self.assertEqual(st.get('notified_items') or {}, {})

    def test_09_mix_of_recent_and_old_sends_only_the_recent(self):
        watch = [live('old%d' % i, 60 * 24 + i) for i in range(8)] + [live('new', 4), pre('soon', 120, 15)]
        out, st = self.sent(watch)
        self.assertEqual(sorted(games(out)), ['⚽ Casa new — Fora new', '⚽ Casa soon — Fora soon'])
        self.assertEqual(sorted(st['notified_items']), ['item|id:new|1X2|1', 'item|id:soon|1X2|1'])

    def test_10_already_notified_is_never_duplicated(self):
        watch = [live('a', 5)]
        out, st = self.sent(watch)
        self.assertEqual(len(games(out)), 1)
        for minutes in (1, 3, 10):
            again, st = self.sent([live('a', 5 - minutes, odds=3.6)] + watch, st, now=NOW + timedelta(minutes=minutes))
            self.assertEqual(games(again), [])                                  # reforço da mesma aposta: nunca outro aviso

    def test_12_successive_runs_never_drain_a_queue_of_old_notices(self):
        stale = [live('s%d' % i, 60 * (i + 2)) for i in range(12)]             # o incidente: 12 sinais antigos
        fresh = [live('f%d' % i, 2 + i) for i in range(7)]                    # 7 atuais: mais do que MAX_WATCH_PER_RUN
        st, seen = state(), []
        for k in range(4):
            now = NOW + timedelta(minutes=2 * k)
            out, st = self.sent(stale + fresh, st, now=now)
            seen.append(games(out))
        self.assertEqual([len(x) for x in seen], [notify.MAX_WATCH_PER_RUN, 2, 0, 0])
        self.assertFalse(any('Casa s' in g for run in seen for g in run))
        self.assertEqual(sorted(st['notified_items']), sorted('item|id:f%d|1X2|1' % i for i in range(7)))

    def test_pending_stale_notice_expires_instead_of_being_sent(self):
        """Uma intenção pendente de uma execução interrompida não é enviada depois de o sinal expirar."""
        store, st, sent = notify.MemoryStore(state()), state(), []
        rep = report([live('a', 25)])
        notices = notify.plan(rep, copy.deepcopy(st), NOW)
        st['deliveries'] = {n.key: {'kind': n.kind, 'state': 'pending', 'events': ['pending'], 'created_utc': NOW.isoformat()} for n in notices
                            if n.key.startswith('game|')}
        self.assertEqual(len(st['deliveries']), 1)
        st, done = notify.deliver(store, rep, st, NOW + timedelta(minutes=10), lambda *a: sent.append(a), 'tok', 'chat')
        self.assertEqual((sent, done), ([], 0))
        self.assertEqual({r['state'] for r in st['deliveries'].values()}, {'expired'})


class Incident(unittest.TestCase):
    """Reprodução do incidente com o relatório e o estado reais (tests/fixtures/incident_2026_11_17, extraídos do git)."""

    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((FIXTURE / 'report.json').read_text(encoding='utf-8'))
        cls.state = json.loads((FIXTURE / 'state.json').read_text(encoding='utf-8'))
        cls.source = json.loads((FIXTURE / 'SOURCE.json').read_text(encoding='utf-8'))
        cls.runs = [datetime.fromisoformat(t) for t in cls.source['runs_utc']]

    def test_the_twelve_notices_are_the_twelve_watch_entries(self):
        sent = sorted(x for n in self.source['notices'].values() for x in n['watch_sent'])
        self.assertEqual(sent, sorted(notify.legacy_key(w) for w in self.report['brain']['watch']))
        events = sorted(n['event'] for n in self.source['notices'].values())
        self.assertEqual(events, ['delivered'] * 11 + ['uncertain'])
        listed = {'FC Spaeri — FC Iberia 1999': '2026-11-15T15:40:44+00:00', 'CD Universidad San Martin — Union Comercio': '2026-11-15T17:42:10+00:00',
                  'Greece U21 — Iceland U21': '2026-11-16T18:53:17+00:00', 'Kashima — Yokohama FM': '2026-11-15T07:15:24+00:00',
                  'Deportivo Recoleta — Libertad Asuncion': '2026-11-14T22:54:19+00:00', 'Villa Dalmine — Deportivo Camioneros': '2026-11-14T20:26:16+00:00'}
        by = {w['match']: w['date_utc'] for w in self.report['brain']['watch']}
        self.assertEqual({m: by[m] for m in listed}, listed)                     # os exemplos do relatório do incidente

    def test_every_incident_signal_is_rejected_at_its_real_send_time(self):
        when = {x: datetime.fromisoformat(n['at_utc']) for n in self.source['notices'].values() for x in n['watch_sent']}
        for w in self.report['brain']['watch']:
            with self.subTest(match=w['match']):
                self.assertEqual(freshness.check(w, when[notify.legacy_key(w)]), (False, 'live_too_old'))
                # A regra não bloqueia por sistema: o mesmo sinal, 5 minutos depois de publicado, seria um aviso válido.
                self.assertEqual(freshness.check(w, datetime.fromisoformat(w['date_utc']) + timedelta(minutes=5)), (True, 'ok'))

    def test_replay_of_the_three_runs_sends_no_game_notice(self):
        st = copy.deepcopy(self.state)
        for now in self.runs:
            out = notify.plan(copy.deepcopy(self.report), st, now)
            self.assertEqual([n.key for n in out if n.key.startswith('game|')], [], now)
            for n in out:
                notify.apply_effects(st, n.effects, now)
        self.assertEqual(st.get('notified_items'), self.state.get('notified_items'))
        self.assertEqual(st.get('watch_sent'), self.state.get('watch_sent'))

    def test_replay_through_run_with_files_sends_nothing_and_keeps_history(self):
        """Ponto de entrada real (`notify.run`, o mesmo das leituras rápidas): nenhum envio, estado e registo só a crescer."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'report.json').write_text(json.dumps(self.report), encoding='utf-8')
            (Path(d) / notify.STATE_NAME).write_text(json.dumps(self.state), encoding='utf-8')
            (Path(d) / notify.LOG_NAME).write_bytes(b'')
            calls = []
            with patch.dict(os.environ, {'SCANGOAL_DATA_DIR': d}), redirect_stdout(io.StringIO()):
                for now in self.runs:
                    notify.run(lambda *a: calls.append(a), now, ENV)
            st = json.loads((Path(d) / notify.STATE_NAME).read_text(encoding='utf-8'))
            log = (Path(d) / notify.LOG_NAME).read_text(encoding='utf-8')
        self.assertEqual(calls, [])
        new = [json.loads(l) for l in log.splitlines() if not json.loads(l).get('reconstructed')]   # o resto é o histórico reposto
        self.assertEqual([e for e in new if e['key'].startswith('game|')], [])
        self.assertEqual(st['sent_total'], self.state['sent_total'])
        self.assertEqual(st.get('notified_items'), self.state.get('notified_items'))
        self.assertEqual(st.get('watch_sent'), self.state.get('watch_sent'))

    def test_a_fresh_signal_in_the_same_report_would_still_be_sent(self):
        rep = copy.deepcopy(self.report)
        now = self.runs[0]
        fresh = dict(rep['brain']['watch'][0], date_utc=(now - timedelta(minutes=4)).isoformat(timespec='seconds'),
                     match='Equipa Nova — Outra Equipa', match_id='equipa-nova-vs-outra-equipa-20261117')
        rep['brain']['watch'].insert(0, fresh)
        out = notify.plan(rep, copy.deepcopy(self.state), now)
        self.assertEqual(games([n.text for n in out]), ['⚽ Equipa Nova — Outra Equipa'])


class BrainWatch(unittest.TestCase):
    """Primeira barreira: `brain.build(..., as_of=...)` só põe em `watch` oportunidades atuais; nada mais muda no cérebro."""

    def sig(self, i, date, win, **over):
        seen = (datetime.fromisoformat(date) + timedelta(minutes=1)).isoformat(timespec='seconds')
        b = {'type': 'BETFAIR', 'phase': 'live', 'match_id': 'm%d' % i, 'key': ('m%d' % i,), 'teams': ['A%d' % i, 'B%d' % i], 'league': 'Liga',
             'odds': 2.0, 'odds_last': 2.0, 'date_utc': date, 'signal_seen_utc': seen, 'kickoff_utc': None, 'max_mult': 1, 'n_signals': 1,
             'share': 90, 'share_last': 90, 'mkt_volume': 5000, 'lead_min': None, 'minute': None, 'market': '1X2', 'selection': '1',
             'score_at_signal': None, 'x4_min': None, 'in_roi': True, 'w': None if win is None else (1.0 if win else -1.0), 'no_source': False}
        b.update(over)
        return b

    def build(self, opened, as_of, barrier=True):
        settled = [self.sig(i, '2026-11-%02dT20:00:00+00:00' % (10 + i % 5), True,
                            result_available_utc='2026-11-%02dT21:30:00+00:00' % (10 + i % 5))
                   for i in range(40)]
        reg = {}
        brain.update_registry(settled + opened, reg, now=as_of)
        if barrier:
            return brain.build(settled + opened, reg, 300, as_of=as_of)
        with patch.object(freshness, 'current', lambda w, now: True):          # a seleção anterior à correção, à mesma hora
            return brain.build(settled + opened, reg, 300, as_of=as_of)

    def test_08_promotion_with_ten_historical_open_signals_leaves_watch_empty(self):
        old = [self.sig(100 + i, '2026-11-1%dT21:00:00+00:00' % (i % 5), None) for i in range(10)]
        promoted_at = datetime(2026, 11, 14, 21, 40, tzinfo=UTC)               # todos os sinais já lidos, o mais novo com 40 min
        before = self.build(old, promoted_at, barrier=False)
        self.assertTrue(before['status_list'])                                  # a tática está promissora (ou melhor)
        self.assertEqual(len(before['watch']), 10)                              # antes da correção: os 10 sinais antigos
        after = self.build(old, promoted_at)
        self.assertEqual(after['watch'], [])
        for k in ('counts', 'status_list', 'top', 'validation', 'track', 'tested', 'presentation', 'board', 'walk_forward'):
            self.assertEqual(after[k], before[k], k)                           # nada mais muda no cérebro

    def test_09_mixed_keeps_only_the_current_and_marks_phase_and_finished(self):
        as_of = datetime(2026, 11, 17, 20, 10, tzinfo=UTC)
        opened = [self.sig(100, '2026-11-17T20:00:00+00:00', None), self.sig(101, '2026-11-15T20:00:00+00:00', None),
                  self.sig(102, '2026-11-17T20:05:00+00:00', None, ft=[1, 1],
                           result_available_utc='2026-11-17T20:08:00+00:00'),
                  self.sig(103, '2026-11-17T20:06:00+00:00', None, match_result_first_seen_utc='2026-11-17T20:08:00+00:00'),
                  self.sig(104, '2026-11-17T19:00:00+00:00', None, phase='pre', kickoff_utc='2026-11-17T21:00:00+00:00')]
        out = self.build(opened, as_of)
        self.assertEqual(sorted(w['match_id'] for w in out['watch']), ['m100', 'm104'])
        self.assertEqual({w['match_id']: (w['phase'], w['finished']) for w in out['watch']}, {'m100': ('live', False), 'm104': ('pre', False)})
        unfiltered = self.build(opened, as_of, barrier=False)
        self.assertEqual({w['match_id']: w['finished'] for w in unfiltered['watch']},
                         {'m100': False, 'm101': False, 'm102': True, 'm103': True, 'm104': False})
        self.assertEqual(self.build(opened, None)['watch'], self.build(opened, None, barrier=False)['watch'])   # sem relógio: como antes


if __name__ == '__main__':
    unittest.main()
