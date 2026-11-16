"""PR 5: Telegram delivery and presentation. Offline: injected sender, network blocked, temporary data directories.

U1-U20 are the adversarial tests of audits/pr5-telegram-plan.md §3; C* are process crashes at every critical point of a
delivery (owner's additional audit), X* are extra attacks. Nothing here can reach the network or a real Telegram chat.
"""
import copy
import io
import json
import os
import shutil
import socket
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from scangoal import brain, notify

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'tests' / 'fixtures' / 'pr5'
UTC = timezone.utc
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
TOKEN, CHAT = 'SECRET-TOKEN-7f3a9', '987654321'
ENV = {'TG_BOT_TOKEN': TOKEN, 'TG_CHAT_ID': CHAT}
HA, HB, HC = 'Jogo aberto | Minuto = 31 a 60', 'Jogo aberto | Minuto = 31 a 60 & Parte do jogo = 2.ª parte', 'Jogo aberto | Liga = Liga X'
_real_connect = socket.socket.connect


def setUpModule():
    def refuse(*args, **kwargs):
        raise AssertionError('network access attempted in an offline test')
    socket.socket.connect = refuse


def tearDownModule():
    socket.socket.connect = _real_connect


class Crash(BaseException):
    """A process death: never caught by the notifier (only Exception is)."""


def at(minutes=0, base=NOW):
    return (base + timedelta(minutes=minutes)).isoformat()


def games(prefix, n, start=0):
    return ['%s%03d' % (prefix, i) for i in range(start, start + n)]


def row(status='promissora', *, games_=40, ids=None, family='Jogo aberto', conditions=(), pending=None, last_to=None, prospective=30,
        confirmation=None, last_games=31, **extra):
    r = {'status': status, 'pending_status': pending, 'changes': 1,
         'last_change': None if last_to is False else {'games': last_games, 'from': 'em observação', 'to': last_to or status,
                                                       'prob_above': 0.97, 'known_utc': '2026-11-15T18:23:15+00:00'},
         'family': family, 'conditions': [list(c) for c in conditions], 'expect': None, 'games': games_, 'n': games_ + 2,
         'roi': 0.21, 'game_roi': 0.2589, 'game_roi_cut': 0.2311, 'prob_above': 0.9797, 'prob_above_cut': 0.97, 'edge': 0.12,
         'edge_ci': [0.05, 0.19], 'prospective_games': prospective, 'born_utc': '2026-11-12T15:25:14+00:00',
         'evidence_level': 'prospetiva', 'confirmation': confirmation, 'game_ids': ids}
    r.update(extra)
    return r


def certificate(rank=1):
    return {'rank': rank, 'certified_utc': '2026-11-15T10:00:00+00:00', 'certifiable': True, 'games': 60}


OFF = {'method': notify.VALIDATION_METHOD, 'state': 'not_activated', 'ledger_valid': False, 'certified': []}


def active(*ids):
    return {'method': notify.VALIDATION_METHOD, 'state': 'active', 'ledger_valid': True, 'certified': list(ids),
            'ledger_entries': len(ids), 'ledger_head_sha256': 'ab' * 32}


def reason(hid, status='promissora', certified=False, **extra):
    r = {'id': hid, 'status': status, 'n': 42, 'games': 40, 'hit_rate': 0.7, 'prob_above': 0.97, 'break_even': 0.55, 'edge': 0.14,
         'edge_ci': [0.07, 0.2], 'prob_above_cut': 0.95, 'prudent_rate': 0.69, 'certified': certified}
    r.update(extra)
    return r


def entry(match_id, selection='Over 2.5', minutes=-5, *, match=None, kickoff=60, market='Asian Total', odds=1.9, reasons=None):
    return {'date_utc': at(minutes), 'match': match or 'Home %s — Away %s' % (match_id, match_id), 'match_id': match_id, 'type': 'OPEN_GAME',
            'market': market, 'selection': selection, 'odds': odds, 'kickoff_utc': at(kickoff) if kickoff is not None else None,
            'reasons': reasons or [reason(HA)]}


def report(statuses=None, rows=None, watch=(), validation=None, *, eligible=600, hypotheses=6000, settled=400, hl=56, bsd=19,
           rules=None, generated=None, regime=None):
    statuses = dict({HA: 'promissora'} if statuses is None else statuses)
    rows = dict(rows if rows is not None else {h: row(s) for h, s in statuses.items()})
    return {'generated_at_utc': generated or NOW.isoformat(), 'settled_bets': settled, 'audit': {'hl_results': hl, 'bsd_results': bsd},
            'daily': [{'day': NOW.date().isoformat(), 'bets': 4, 'settled': 3, 'wins': 2, 'units': 0.85}], 'timeline': [{'u': 1.5}],
            'operational': {'scope': 'eligible_bets_only', 'regime': regime or [{'type': 'OPEN_GAME', 'state': 'estável'}]},
            'brain': {'evidence_scope': 'eligible_bets_only', 'status_rules': rules or brain.STATUS_RULES,
                      'eligibility': {'eligible': eligible, 'total': eligible + 100}, 'hypotheses': hypotheses,
                      'counts': {'promissora': sum(1 for s in statuses.values() if s == 'promissora')},
                      'validation': copy.deepcopy(validation or OFF), 'status_list': statuses, 'watch': list(watch),
                      'presentation': {'version': brain.PRESENTATION, 'hypotheses': rows},
                      'llm_today': {'calls_today': 3, 'neurons_today': 700.4}}}


def migrated(st, rep=None):
    """The state after the silent PR 5 format migration (as the first PR 5 run leaves it)."""
    notify.migrate_format(rep or report(), st, NOW - timedelta(hours=1))
    return st


def base_state(statuses=None):
    """A migrated production-like state (PR 1-3 markers, PR 5 format marker, delivery history)."""
    st = {'hello': True, 'sent_total': 312, 'watch_sent': ['2026-11-15T21:07:18+00:00|Belgium — Italy|Over 5'],
          'summary_day': '2026-11-15', 'regime': {'OPEN_GAME': 'estável'}, 'operational_regime': {'OPEN_GAME': 'estável'},
          'statuses': dict({HA: 'promissora'} if statuses is None else statuses),
          'migrations': {k: {'at_utc': '2026-11-15T09:27:59+00:00'} for k in ('eligible_statuses_v1', 'status_rules:independent_games_hysteresis_v1',
                                                                             'status_rules:' + brain.STATUS_RULES)}}
    return st


class Data:
    """A temporary SCANGOAL_DATA_DIR with report.json, notify_state.json and an injected sender."""

    def __init__(self, test, state, rep):
        self.dir = tempfile.TemporaryDirectory()
        test.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name)
        self.env = patch.dict(os.environ, {'SCANGOAL_DATA_DIR': self.dir.name})
        self.env.start()
        test.addCleanup(self.env.stop)
        self.sent = []
        self.write_state(state)
        self.write_report(rep)

    def write_state(self, state):
        if state is not None:
            (self.path / 'notify_state.json').write_text(json.dumps(state), encoding='utf-8')

    def write_report(self, rep):
        (self.path / 'report.json').write_text(json.dumps(rep, ensure_ascii=False), encoding='utf-8')

    def state(self):
        return json.loads((self.path / 'notify_state.json').read_text(encoding='utf-8'))

    def log_bytes(self):
        p = self.path / notify.LOG_NAME
        return p.read_bytes() if p.exists() else b''

    def log(self):
        return notify.parse_log(self.log_bytes())[0]

    def sender(self, fail_at=None, error=None):
        """Records each transmitted text; the fail_at-th call (1-based, over this sender) raises `error` instead."""
        calls = [0]

        def send(token, chat, text):
            assert (token, chat) == (TOKEN, CHAT)
            calls[0] += 1
            if fail_at is not None and calls[0] == fail_at:
                raise error
            self.sent.append(text)
        return send

    def run(self, now=NOW, sender=None, checkpoint=None):
        with redirect_stdout(io.StringIO()):
            return notify.run(sender or self.sender(), now, ENV, checkpoint)


def labelled(text):
    return text.split('\n')[0].startswith('Estado científico:')


def body(text):
    lines = text.split('\n')
    while len(lines) > 1 and lines[0].startswith('Estado científico:'):
        lines.pop(0)
    return '\n'.join(lines)


def consistent(test, data):
    """State and log agree: every state event is in the log, the counters match the records, nothing is in flight."""
    st, log = data.state(), data.log()
    seen = {(e['key'], e['event']) for e in log}
    recs = st.get('deliveries') or {}
    for key, r in recs.items():
        test.assertNotEqual(r['state'], 'in_flight', key)
        for ev in r['events']:
            test.assertIn((key, ev), seen, (key, ev))
        test.assertEqual(r['events'][-1], 'withdrawn' if r['state'] == 'pending' and r['events'][-1] == 'withdrawn' else r['state'], key)
    counts = st.get('delivery_counts') or {}
    for k in notify.COUNTERS:
        test.assertEqual(counts.get(k, 0), sum(1 for r in recs.values() if r['state'] == k), k)
    return st


# ---------------------------------------------------------------------------------------------------------------------

class U01_FailureInTheMiddle(unittest.TestCase):
    def five_games(self):
        return report(watch=[entry('m%d' % i, minutes=-i) for i in range(1, 6)])

    def test_confirmed_failure_on_third_of_five(self):
        d = Data(self, base_state(), self.five_games())
        d.run(sender=d.sender(fail_at=3, error=notify.DeliveryFailed('HTTP 400')))
        st = consistent(self, d)
        self.assertEqual((len(d.sent), st['sent_total']), (2, 314))
        states = sorted(r['state'] for r in st['deliveries'].values())
        self.assertEqual(states, ['delivered', 'delivered', 'failed', 'pending', 'pending'])
        first = list(d.sent)
        d.run(now=NOW + timedelta(minutes=2))
        st = consistent(self, d)
        self.assertEqual(d.sent[:2], first)
        self.assertEqual(len(d.sent), 4)                                 # 4th and 5th once; the failed 3rd never again
        self.assertEqual(len(set(d.sent)), 4)
        self.assertEqual(st['sent_total'], 316)
        self.assertEqual(st['delivery_counts'], {'delivered': 4, 'failed': 1})
        d.run(now=NOW + timedelta(minutes=4))
        self.assertEqual(len(d.sent), 4)

    def test_uncertain_on_third_of_five(self):
        d = Data(self, base_state(), self.five_games())
        d.run(sender=d.sender(fail_at=3, error=notify.DeliveryUncertain('timeout')))
        d.run(now=NOW + timedelta(minutes=2))
        st = consistent(self, d)
        self.assertEqual((len(d.sent), st['sent_total'], st['delivery_counts'].get('uncertain')), (4, 316, 1))


class U02_CrashBetweenSendAndRecord(unittest.TestCase):
    def test_in_flight_becomes_uncertain_and_is_never_resent(self):
        d = Data(self, base_state(), report(watch=[entry('m1')]))

        def crash(name):
            if name == 'after_response':
                raise Crash()
        with self.assertRaises(Crash):
            d.run(checkpoint=crash)
        self.assertEqual(len(d.sent), 1)
        self.assertEqual([r['state'] for r in d.state()['deliveries'].values()], ['in_flight'])
        d.run(now=NOW + timedelta(minutes=2))
        st = consistent(self, d)
        self.assertEqual(len(d.sent), 1)                                 # never resent
        self.assertEqual((st['sent_total'], st['delivery_counts']), (312, {'uncertain': 1}))   # never counted as delivered
        self.assertEqual([e['event'] for e in d.log()], ['pending', 'intent', 'uncertain'])


class CrashPoints(unittest.TestCase):
    """C1-C9: the process dies at every critical point of the first delivery; the next run must never resend, never lose a
    counter and leave state and log consistent. Three games, so the crash hits the first and the run continues later."""

    POINTS = ('planned', 'before_intent', 'after_intent_state', 'after_intent', 'during_send', 'after_response',
              'after_record_state', 'after_record', 'during_record_save', 'during_pending_log', 'during_intent_log')

    def scenario(self, point):
        d = Data(self, base_state(), report(watch=[entry('m%d' % i, minutes=-i) for i in range(1, 4)]))
        transmitted = []

        def sender(token, chat, text):
            if point == 'during_send':
                raise Crash()
            transmitted.append(text)
            d.sent.append(text)

        def checkpoint(name):
            if name == point:
                raise Crash()
        saves = [0]
        real_replace = os.replace

        def replace(src, dst):
            saves[0] += 1
            if point == 'during_record_save' and saves[0] == 3:        # 1: pending; 2: intent; 3: outcome of the 1st notice
                raise Crash()
            return real_replace(src, dst)
        appends = [0]
        real_append = notify.FileStore.append_log

        def append(store, entries):
            appends[0] += bool(entries)
            if (point == 'during_pending_log' and appends[0] == 1) or (point == 'during_intent_log' and appends[0] == 2):
                raise Crash()
            return real_append(store, entries)
        log_before = d.log_bytes()
        with patch.object(notify.os, 'replace', replace), patch.object(notify.FileStore, 'append_log', append):
            try:
                with redirect_stdout(io.StringIO()):
                    notify.run(sender, NOW, ENV, checkpoint)
                crashed = False
            except Crash:
                crashed = True
        self.assertTrue(crashed, point)
        log_mid = d.log_bytes()
        self.assertTrue(log_mid.startswith(log_before))
        d.run(now=NOW + timedelta(minutes=2))
        d.run(now=NOW + timedelta(minutes=4))
        self.assertTrue(d.log_bytes().startswith(log_mid))             # the log only grows
        return d, transmitted

    def test_every_crash_point(self):
        expected_uncertain = {'after_intent_state', 'after_intent', 'during_send', 'after_response', 'during_record_save',
                              'during_intent_log'}
        for point in self.POINTS:
            with self.subTest(point):
                d, _ = self.scenario(point)
                st = consistent(self, d)
                self.assertEqual(len(d.sent), len(set(d.sent)), point)                            # nothing transmitted twice
                delivered = [r for r in st['deliveries'].values() if r['state'] == 'delivered']
                self.assertEqual(st['sent_total'], 312 + len(delivered), point)                    # counter = positive answers
                unc = st['delivery_counts'].get('uncertain', 0)
                self.assertEqual(unc, 1 if point in expected_uncertain else 0, point)
                self.assertEqual(len(st['deliveries']), 3, point)
                self.assertEqual(len(d.sent), 3 - (1 if point in ('after_intent_state', 'after_intent', 'during_send',
                                                                     'during_intent_log') else 0), point)
                self.assertEqual(sorted(st['notified_items']), sorted('item|id:m%d|Asian Total|Over 2.5' % i for i in range(1, 4)))

    def test_crash_between_state_and_log_never_loses_a_delivery(self):
        d, _ = self.scenario('after_record_state')
        recs = [e for e in d.log() if e['event'] == 'delivered']
        self.assertEqual(len(recs), 3)
        self.assertEqual(sum(1 for e in recs if e.get('reconstructed')), 1)                       # repaired from the state
        self.assertEqual(d.state()['sent_total'], 315)


class LogAndStateRecovery(unittest.TestCase):
    def test_restored_older_state_is_repaired_from_the_log(self):
        d = Data(self, migrated(base_state()), report(watch=[entry('m1'), entry('m2', minutes=-2)]))
        old = (d.path / 'notify_state.json').read_bytes()                # an older PR 5 state (already migrated)
        d.run()
        self.assertEqual(len(d.sent), 2)
        (d.path / 'notify_state.json').write_bytes(old)                  # forbidden by the runbook, survived anyway
        d.run(now=NOW + timedelta(minutes=2))
        st = consistent(self, d)
        self.assertEqual(len(d.sent), 2)                                 # no resend
        self.assertEqual(st['sent_total'], 314)                          # counters rebuilt from the log
        self.assertTrue(all(r.get('reconstructed') for r in st['deliveries'].values()))
        self.assertIn('item|id:m1|Asian Total|Over 2.5', st['notified_items'])

    def test_torn_log_line_is_isolated_and_never_rewritten(self):
        d = Data(self, base_state(), report(watch=[entry('m1')]))
        (d.path / notify.LOG_NAME).write_bytes(b'{"v": 1, "key": "torn')       # a crash in the middle of an append
        d.run()
        raw = d.log_bytes()
        self.assertTrue(raw.startswith(b'{"v": 1, "key": "torn\n'))
        entries, bad = notify.parse_log(raw)
        self.assertEqual((bad, [e['event'] for e in entries]), (1, ['pending', 'intent', 'delivered']))

    def test_corrupt_state_is_never_replaced(self):
        d = Data(self, None, report(watch=[entry('m1')]))
        (d.path / 'notify_state.json').write_text('{broken')
        with self.assertRaises(ValueError):
            d.run()
        self.assertEqual((d.path / 'notify_state.json').read_text(), '{broken')
        self.assertEqual((d.sent, d.log_bytes()), ([], b''))


class U03_Reinforcements(unittest.TestCase):
    def test_x1_x2_x4_x8_of_the_same_bet_is_one_notice(self):
        d = Data(self, base_state(), report())
        watch = []
        for k, minutes in enumerate((-40, -30, -20, -10)):              # one new reinforcement per collection
            watch = [entry('m1', minutes=minutes)] + watch
            d.write_report(report(watch=watch))
            d.run(now=NOW + timedelta(minutes=10 * k))
        self.assertEqual(len(d.sent), 1)
        self.assertEqual(consistent(self, d)['sent_total'], 313)


class U04_SelectionsOfOneGame(unittest.TestCase):
    def test_two_selections_one_message_and_a_later_selection_is_not_lost(self):
        d = Data(self, base_state(), report(watch=[entry('m1', 'Over 2.5'), entry('m1', 'Over 3.5', minutes=-6, odds=2.4)]))
        d.run()
        self.assertEqual(len(d.sent), 1)
        self.assertIn('Aposta: Over 2.5 @ 1,9; Over 3.5 @ 2,4', d.sent[0])
        later = [entry('m1', 'Over 2.5'), entry('m1', 'Over 3.5', minutes=-6, odds=2.4), entry('m1', 'BTTS Yes', minutes=-1, market='BTTS', odds=1.8)]
        d.write_report(report(watch=later))
        d.run(now=NOW + timedelta(minutes=2))
        self.assertEqual(len(d.sent), 2)
        self.assertIn('Aposta: BTTS Yes @ 1,8', d.sent[1])
        self.assertNotIn('Over 2.5', d.sent[1])
        self.assertNotIn('Over 3.5', d.sent[1])
        # Contract (review #5920565094): a genuinely new bet in a game already notified is an UPDATE of that game, labelled as such.
        self.assertIn('A seguir (nova seleção num jogo já avisado): Home m1 — Away m1', d.sent[1])
        self.assertIn('Apostas já avisadas neste jogo: 2 (não repetidas).', d.sent[1])
        self.assertNotIn('nova seleção', d.sent[0])
        d.write_report(report(watch=later + [entry('m1', 'BTTS Yes', minutes=0, market='BTTS', odds=1.9)]))   # its reinforcement
        d.run(now=NOW + timedelta(minutes=4))
        self.assertEqual(len(d.sent), 2)


class P1_PromotionDoesNotRepeatTheBet(unittest.TestCase):
    """Review #5920565094, P1: hint -> reinforcement at another time -> the hypothesis becomes certified -> same game, market
    and selection. The bet is never notified twice; the certification is a separate event (status notice)."""

    def scenario(self, reinforce_market='Asian Total', reinforce_selection='Over 2.5'):
        d = Data(self, base_state({HA: 'promissora'}), report({HA: 'promissora'}, watch=[entry('m1', minutes=-20)]))
        d.run()
        self.assertEqual(len(d.sent), 1)
        self.assertIn('A seguir: Home m1 — Away m1', d.sent[0])
        rows = {HA: row('forte', confirmation=certificate())}
        hint = entry('m1', minutes=-20, reasons=[reason(HA, 'forte', certified=True)])
        again = entry('m1', reinforce_selection, minutes=-2, market=reinforce_market, odds=1.95, reasons=[reason(HA, 'forte', certified=True)])
        d.write_report(report({HA: 'forte'}, rows=rows, watch=[again, hint], validation=active(HA)))
        d.run(now=NOW + timedelta(minutes=10))
        return d

    def test_same_bet_is_not_repeated_and_the_certification_is_its_own_event(self):
        d = self.scenario()
        self.assertEqual(len(d.sent), 2)
        self.assertTrue(body(d.sent[1]).startswith('✅ Tática validada, passou a forte: %s' % HA))
        self.assertNotIn('Over 2.5', d.sent[1])
        self.assertFalse(any('ALERTA' in m for m in d.sent))
        self.assertEqual(list(consistent(self, d)['notified_items']), ['item|id:m1|Asian Total|Over 2.5'])

    def test_a_new_selection_after_promotion_is_an_update_alert(self):
        d = self.scenario('BTTS', 'BTTS Yes')
        alerts = [m for m in d.sent if 'ALERTA' in m]
        self.assertEqual(len(alerts), 1)
        self.assertIn('🟢 ALERTA (nova seleção num jogo já avisado): Home m1 — Away m1', alerts[0])
        self.assertIn('Aposta: BTTS Yes @ 1,95', alerts[0])
        self.assertNotIn('Over 2.5', alerts[0])

    def test_legacy_hint_then_certified_alert_for_the_same_bet(self):
        # The hint was sent by the pre-PR 5 code (only its legacy key exists); the alert arrives later with another time.
        rows = {HA: row('forte', confirmation=certificate())}
        old = entry('m1', minutes=-30)
        st = base_state({HA: 'forte'})
        st['watch_sent'] = sorted(st['watch_sent'] + [notify.legacy_key(old)])
        alert = entry('m1', minutes=-1, reasons=[reason(HA, 'forte', certified=True)])
        d = Data(self, st, report({HA: 'forte'}, rows=rows, watch=[alert], validation=active(HA)))
        d.run()
        self.assertEqual(d.sent, [])


def equivalent_rows(status='promissora', n=3, overlap=40, extra=3):
    """n compatible hypotheses over 40 shared games each with `extra` own games (Jaccard 40/46 = 0.87)."""
    hids = [HA, HB, 'Jogo aberto | Minuto = 31 a 60 & Ritmo indicado pelo bot = menos de 90'][:n]
    conds = [[('Minuto', '31 a 60')], [('Minuto', '31 a 60'), ('Parte do jogo', '2.ª parte')],
             [('Minuto', '31 a 60'), ('Ritmo indicado pelo bot', 'menos de 90')]]
    return {h: row(status, ids=games('g', overlap) + games(h[-3:], extra), conditions=conds[i], games_=overlap + extra - i)
            for i, h in enumerate(hids)}


class U05_U06_Grouping(unittest.TestCase):
    def run_change(self, rows):
        statuses = {h: r['status'] for h, r in rows.items()}
        d = Data(self, base_state({}), report({}, rows={}))
        d.run()
        d.write_report(report(statuses, rows=rows))
        d.run(now=NOW + timedelta(minutes=2))
        return d

    def test_u05_three_equivalent_hypotheses_one_message(self):
        d = self.run_change(equivalent_rows())
        self.assertEqual(len(d.sent), 1)
        self.assertIn('Hipótese passou a promissora (antes: em observação): %s' % HA, d.sent[0])
        self.assertIn('e mais 2 equivalentes (partilham ≥ 85 % dos jogos)', d.sent[0])
        self.assertEqual(set(d.state()['announced_statuses']), set(equivalent_rows()))

    def test_u06_three_unrelated_hypotheses_three_messages(self):
        rows = {h: row(ids=games(h[-4:], 40), conditions=[('Liga', h)]) for h in ('Jogo aberto | Liga = A', 'Jogo aberto | Liga = B', 'Polymarket | Liga = C')}
        self.assertEqual(len(self.run_change(rows).sent), 3)

    def test_no_transitive_chain_and_no_incompatible_pair(self):
        rows = {'Jogo aberto | Minuto = 31 a 60': row(ids=games('g', 43), conditions=[('Minuto', '31 a 60')], games_=43),
                'Jogo aberto | Minuto = 31 a 60 & Liga = L': row(ids=games('g', 43, 3), conditions=[('Minuto', '31 a 60'), ('Liga', 'L')], games_=42),
                'Jogo aberto | Minuto = 31 a 60 & Dia = D': row(ids=games('g', 43, 6), conditions=[('Minuto', '31 a 60'), ('Dia', 'D')], games_=41)}
        jac = notify.equivalence.jaccard
        sets = [frozenset(r['game_ids']) for r in rows.values()]
        self.assertGreaterEqual(min(jac(sets[0], sets[1]), jac(sets[1], sets[2])), 0.85)   # A~B, B~C
        self.assertLess(jac(sets[0], sets[2]), 0.85)                                          # but not A~C
        self.assertEqual(len(self.run_change(rows).sent), 2)                                  # complete linkage: never A+B+C
        incompatible = {'Jogo aberto | Liga = X': row(ids=games('g', 40), conditions=[('Liga', 'X')]),
                        'Jogo aberto | Liga = Y': row(ids=games('g', 40), conditions=[('Liga', 'Y')])}
        self.assertEqual(len(self.run_change(incompatible).sent), 2)                          # identical games, conflicting value

    def test_watch_reasons_are_grouped_too(self):
        rows = equivalent_rows()
        w = entry('m1', reasons=[reason(h) for h in rows])
        d = Data(self, base_state({h: 'promissora' for h in rows}), report({h: 'promissora' for h in rows}, rows=rows, watch=[w]))
        d.run()
        self.assertEqual(len(d.sent), 1)
        self.assertEqual(d.sent[0].count('Encaixa em:'), 1)
        self.assertIn('e mais 2 equivalentes', d.sent[0])


class U07_DegradedReports(unittest.TestCase):
    def setUp(self):
        self.statuses = {HA: 'promissora', HB: 'promissora', HC: 'abandonada'}
        self.d = Data(self, base_state(self.statuses), report(self.statuses))
        self.d.run()
        self.ref = copy.deepcopy(self.d.state()['announced_statuses'])

    def assert_silent(self, rep, minutes=2):
        self.d.write_report(rep)
        self.d.run(now=NOW + timedelta(minutes=minutes))
        self.assertEqual(self.d.sent, [])
        self.assertEqual(self.d.state()['announced_statuses'], self.ref)

    def test_absent_hypothesis(self):
        self.assert_silent(report({HA: 'promissora', HC: 'abandonada'}))

    def test_empty_status_list(self):
        self.assert_silent(report({}, rows={}))

    def test_results_source_failure_holds_a_real_change_until_healthy(self):
        change = dict(self.statuses, **{HA: 'abandonada'})
        self.assert_silent(report(change, hl=0))                                # HL results file unreadable: degraded
        self.assertEqual(self.d.state()['health']['problems'], ['fonte de resultados em falha (HL)'])
        self.assert_silent(report(change, settled=300), minutes=4)            # results vanished: degraded
        self.assertEqual(self.d.state()['health']['problems'], ['resultados em falta'])
        self.d.write_report(report(change))                                    # healthy again: the change is delayed, not lost
        self.d.run(now=NOW + timedelta(minutes=6))
        self.assertEqual(len(self.d.sent), 1)
        self.assertIn('passou a abandonada', self.d.sent[0])

    def test_eligible_universe_drop_and_rule_or_verification_change(self):
        change = dict(self.statuses, **{HA: 'abandonada'})
        self.assert_silent(report(change, eligible=300))
        self.assert_silent(report(change, validation=dict(OFF, state='history_unverified')), minutes=6)
        # New status rules are a silent re-baseline (PR 1-3 mechanism), never a stream of transitions.
        self.d.write_report(report(change, rules='other_rules_v9'))
        self.d.run(now=NOW + timedelta(minutes=8))
        st = self.d.state()
        self.assertEqual((self.d.sent, st['announced_statuses']), ([], change))
        self.assertEqual(st['migrations']['status_rules:other_rules_v9']['previous_announced_statuses'], self.ref)

    def test_degradation_stable_for_24h_becomes_the_reference(self):
        change = dict(self.statuses, **{HA: 'abandonada'})
        self.assert_silent(report(change, eligible=300))
        self.d.run(now=NOW + timedelta(hours=23))
        self.assertEqual(self.d.sent, [])
        self.d.run(now=NOW + timedelta(hours=24, minutes=3))
        self.assertEqual(len(self.d.sent), 1)                                 # delayed by at most 24 h, never discarded
        self.assertTrue(self.d.state()['health']['accepted'])


class U08_Oscillation(unittest.TestCase):
    def test_a_b_a_within_24h_is_one_notice_and_goes_to_the_summary(self):
        d = Data(self, base_state(), report())
        d.run()
        d.write_report(report({HA: 'abandonada'}))
        d.run(now=NOW + timedelta(minutes=10))
        d.write_report(report({HA: 'promissora'}))
        d.run(now=NOW + timedelta(minutes=20))
        d.write_report(report({HA: 'abandonada'}))
        d.run(now=NOW + timedelta(minutes=30))
        self.assertEqual(len(d.sent), 1)
        d.write_report(report({HA: 'promissora'}))
        d.run(now=NOW + timedelta(minutes=40))
        d.run(now=NOW.replace(hour=21, minute=5))
        self.assertEqual(len(d.sent), 2)
        self.assertIn('Resumo de', d.sent[1])
        self.assertIn('Oscilações em menos de 24 h (não anunciadas): %s: promissora → abandonada → promissora' % HA, d.sent[1])
        self.assertEqual(d.state()['oscillations'], [])

    def test_a_reversal_that_persists_is_announced_after_24h(self):
        d = Data(self, base_state(), report())
        d.run()
        d.write_report(report({HA: 'abandonada'}))
        d.run(now=NOW + timedelta(minutes=10))
        d.write_report(report({HA: 'promissora'}))
        d.run(now=NOW + timedelta(minutes=20))
        d.run(now=NOW + timedelta(hours=24, minutes=11))
        self.assertEqual(len(d.sent), 2)
        self.assertIn('passou a promissora (antes: abandonada)', d.sent[1])

    def test_critical_changes_are_never_held(self):
        rows = {HA: row('forte', confirmation=certificate())}
        d = Data(self, base_state({HA: 'promissora'}), report({HA: 'forte'}, rows=rows, validation=active(HA)))
        d.run()
        d.write_report(report({HA: 'promissora'}, validation=OFF))           # certification lost
        d.run(now=NOW + timedelta(minutes=10))
        d.write_report(report({HA: 'forte'}, rows=rows, validation=active(HA)))
        d.run(now=NOW + timedelta(minutes=20))
        self.assertEqual([body(m).split(',')[0].split(':')[0] for m in d.sent],
                         ['✅ Tática validada', '⛔ Tática deixou de estar validada', '✅ Tática validada'])

    def test_certification_loss_is_announced_even_with_a_degraded_report(self):
        rows = {HA: row('forte', confirmation=certificate())}
        d = Data(self, base_state({HA: 'forte'}), report({HA: 'forte'}, rows=rows, validation=active(HA)))
        d.run()
        d.write_report(report({}, rows={}, validation=dict(OFF, state='ledger_invalid'), hl=0))
        d.run(now=NOW + timedelta(minutes=10))
        self.assertEqual(len(d.sent), 1)
        self.assertIn('⛔ Tática deixou de estar validada, voltou a "em observação": %s' % HA, d.sent[0])
        self.assertTrue(d.sent[0].startswith('Estado científico: NÃO CERTIFICADA — certificação inválida.'))


class U09_Pending(unittest.TestCase):
    def test_pending_status_is_never_announced(self):
        d = Data(self, base_state(), report())
        d.run()
        d.write_report(report({HA: 'abandonada'}, rows={HA: row('abandonada', pending='promissora')}))
        d.run(now=NOW + timedelta(minutes=2))
        d.write_report(report({HA: 'abandonada'}, rows={HA: row('abandonada', last_to='promissora')}))   # change not fixed
        d.run(now=NOW + timedelta(minutes=4))
        self.assertEqual(d.sent, [])


FORGED = {
    'ledger_not_valid': (dict(active(HA), ledger_valid=False), certificate()),
    'other_method': (dict(active(HA), method='bonferroni'), certificate()),
    'listed_without_confirmation': (active(HA), None),
    'history_unverified': (dict(active(HA), state='history_unverified'), certificate()),
    'confirmation_but_not_listed': (active(), certificate()),
    'rank_is_boolean': (active(HA), dict(certificate(), rank=True)),
    'no_certified_instant': (active(HA), dict(certificate(), certified_utc=None)),
    'not_certifiable': (active(HA), dict(certificate(), certifiable=False)),
    'certified_is_a_string': (dict(active(), certified=HA), certificate()),
    'certificate_of_another_hypothesis': (active(HB), certificate()),
}


class U10_U11_ForgedCertificates(unittest.TestCase):
    def test_never_validated_and_treated_as_promising(self):
        for name, (validation, conf) in FORGED.items():
            with self.subTest(name):
                rows = {HA: row('forte', confirmation=conf), HB: row('promissora', confirmation=certificate(2) if name.endswith('hypothesis') else None)}
                w = entry('m1', reasons=[reason(HA, 'forte', certified=True)])
                d = Data(self, base_state({HA: 'promissora', HB: 'promissora'}), report({HA: 'forte', HB: 'promissora'}, rows=rows, watch=[w], validation=validation))
                d.run()
                d.run(now=NOW.replace(hour=21, minute=1))
                self.assertEqual(notify.certificate(json.loads((d.path / 'report.json').read_text()), HA), None)
                for m in d.sent:
                    folded = notify.fold(m)
                    self.assertNotIn('validad', folded, name)
                    self.assertNotIn('forte', folded, name)
                    self.assertNotIn('ALERTA', m, name)
                    self.assertTrue(labelled(m), name)
                self.assertEqual(d.state()['statuses'][HA], 'promissora', name)
                self.assertTrue(any('A seguir:' in m and '(promissora)' in m for m in d.sent), name)

    def test_u11_strong_without_certificate(self):
        d = Data(self, base_state({}), report({}, rows={}))
        d.run()
        d.write_report(report({HA: 'forte'}, rows={HA: row('forte', last_to='forte')}))
        d.run(now=NOW + timedelta(minutes=2))
        self.assertEqual(len(d.sent), 1)
        self.assertIn('Hipótese passou a promissora (antes: em observação)', d.sent[0])
        self.assertNotIn('forte', notify.fold(d.sent[0]))
        self.assertNotIn('validad', notify.fold(d.sent[0]))

    def test_a_real_certificate_is_announced_and_named(self):
        rows = {HA: row('forte', confirmation=certificate(3))}
        d = Data(self, base_state(), report({HA: 'forte'}, rows=rows, watch=[entry('m1', reasons=[reason(HA, 'forte', certified=True)])], validation=active(HA)))
        d.run()
        self.assertEqual(len(d.sent), 2)
        label = 'Estado científico: CERTIFICADA — método eprocess_online_alpha_v1, registo verificado, seleção n.º 3, certificada em 2026-11-15 10:00 UTC: %s' % HA
        self.assertTrue(all(m.startswith(label) for m in d.sent))
        self.assertTrue(body(d.sent[0]).startswith('🟢 ALERTA'))
        self.assertTrue(body(d.sent[1]).startswith('✅ Tática validada, passou a forte: %s' % HA))


LLM_TEXT = 'Tática VALIDADA e certificada: confirmada pelo modelo, forte!'


class U12_U13_Claims(unittest.TestCase):
    def test_u12_language_model_text_never_reaches_a_message(self):
        rows = {HA: row(reason=LLM_TEXT, note=LLM_TEXT)}
        w = entry('m1', reasons=[reason(HA, reason=LLM_TEXT, note=LLM_TEXT)])
        rep = report(rows=rows, watch=[w])
        rep['brain']['notes'] = [{'id': HA, 'note': LLM_TEXT}]
        rep['brain']['llm'] = [{'id': HA, 'reason': LLM_TEXT}]
        d = Data(self, base_state(), rep)
        d.run()
        d.run(now=NOW.replace(hour=21, minute=1))
        self.assertEqual(len(d.sent), 2)
        for m in d.sent:
            self.assertNotIn('modelo, forte', m)
            self.assertNotIn('VALIDADA', m)
        self.assertFalse(notify.guard_claims(LLM_TEXT, rep))

    def test_u13_every_inflection_is_blocked_without_certificate(self):
        rep = report()
        for word in ('VALIDADA', 'validádas', 'certificado', 'confirmadíssima', 'Validação', 'CONFIRMOU', 'fortes', 'Forte',
                     'Não é uma recomendação, mas a tática está validada', 'sem dúvida nenhuma: tática muito forte',
                     'Tática validada (forte): %s' % HA, 'certificada em 2026: %s' % HA):
            with self.subTest(word):
                self.assertFalse(notify.guard_claims('Estado científico: NÃO CERTIFICADA — certificação desligada.\n' + word, rep))
        for ok in ('não certificada', 'NÃO CERTIFICADA', 'sem certificado para esta hipótese', 'nenhuma tática certificada',
                   'certificação desligada', 'Tática deixou de estar validada, passou a promissora', 'nunca foi validada',
                   'histórico da certificação por verificar', 'Fortaleza — Confiança', 'certificação inválida'):
            with self.subTest(ok):
                self.assertTrue(notify.guard_claims(ok, rep))
        self.assertFalse(notify.guard_claims('certificação ativa', rep))           # system claim only when it is active
        self.assertTrue(notify.guard_claims('certificação ativa', report(validation=active())))

    def test_claims_must_name_their_own_certified_hypothesis(self):
        rows = {HA: row('forte', confirmation=certificate()), HB: row('promissora')}
        rep = report({HA: 'forte', HB: 'promissora'}, rows=rows, validation=active(HA))
        label, positive = notify.scientific_label(rep, HA)
        self.assertTrue(positive)
        self.assertTrue(notify.guard_claims(label + '\n✅ Tática validada, passou a forte: ' + HA, rep, [HA]))
        self.assertFalse(notify.guard_claims(label + '\n✅ Tática validada, passou a forte: ' + HB, rep, [HA]))      # wrong hypothesis
        self.assertFalse(notify.guard_claims(label + '\n✅ Tática validada, passou a forte: ' + HA + ' & X = y', rep, [HA]))
        self.assertFalse(notify.guard_claims('✅ Tática validada, passou a forte: ' + HA, rep, [HA]))                # label missing
        self.assertFalse(notify.guard_claims(label + '\nok', rep, [HB]))                                            # HB uncertified

    def test_a_team_name_with_a_claim_word_blocks_the_notice(self):
        d = Data(self, base_state(), report(watch=[entry('m1', match='Validada FC — Certificados SC')]))
        d.run()
        st = consistent(self, d)
        self.assertEqual((d.sent, st['delivery_counts']), ([], {'blocked': 1}))
        d.run(now=NOW + timedelta(minutes=2))
        self.assertEqual(d.sent, [])                                    # at most once: never retried


class U14_RealStates(unittest.TestCase):
    """States copied from git history (tests/fixtures/pr5/SOURCE.json) and the PR 5 report of the frozen main commit."""

    def load(self, name):
        return json.loads((FIX / name).read_text(encoding='utf-8'))

    def test_production_state_and_report_migrate_silently(self):
        state, rep = self.load('state_production.json'), self.load('report_production.json')
        before = copy.deepcopy(state)
        now = datetime.fromisoformat(rep['generated_at_utc'])
        d = Data(self, state, rep)
        d.run(now=now)
        st = d.state()
        self.assertEqual(d.sent, [])
        for k, v in before.items():
            if k not in ('last_run_utc', 'migrations'):
                self.assertEqual(st[k], v, k)
        self.assertEqual({k: st['migrations'][k] for k in before['migrations']}, before['migrations'])   # three markers intact
        self.assertEqual(st['announced_statuses'], before['statuses'])
        self.assertIn(notify.FORMAT_MIGRATION, st['migrations'])
        self.assertEqual(st['migrations'][notify.FORMAT_MIGRATION]['sent_total'], before['sent_total'])
        d.run(now=now + timedelta(minutes=10))
        self.assertEqual(d.sent, [])
        self.assertEqual(d.log_bytes(), b'')                            # nothing attempted, nothing logged

    def test_pr1_pr2_pr3_states_with_the_current_report(self):
        rep = self.load('report_production.json')
        now = datetime.fromisoformat(rep['generated_at_utc'])
        for name in ('state_pr1.json', 'state_pr2.json', 'state_pr3.json'):
            with self.subTest(name):
                state = self.load(name)
                before = copy.deepcopy(state)
                d = Data(self, state, rep)
                d.run(now=now)
                st = d.state()
                # Nothing the state already knew is announced again. An older state (PR 3 marker, 20:59 UTC) paired with a later
                # report only hears about changes the brain fixed AFTER that state was written (genuinely new to it).
                written = datetime.fromisoformat(before['last_run_utc'])
                for m in d.sent:
                    title = body(m).split('\n')[0]
                    hid = title.rsplit('): ', 1)[-1]
                    self.assertNotEqual(before['statuses'].get(hid, 'em observação'), st['statuses'].get(hid, 'em observação'), (name, title))
                    known = rep['brain']['presentation']['hypotheses'][hid]['last_change']['known_utc']
                    self.assertGreater(datetime.fromisoformat(known), written, (name, title))
                if name != 'state_pr3.json':
                    self.assertEqual(d.sent, [], name)           # PR 1 / PR 2 states: silent re-baseline to the PR 3 rules
                self.assertEqual(st['sent_total'], before['sent_total'] + len(d.sent))
                self.assertTrue(set(before['watch_sent']) <= set(st['watch_sent']))
                for k, v in before['migrations'].items():
                    self.assertEqual(st['migrations'][k], v)
                for k in ('hello', 'regime', 'summary_day'):
                    self.assertEqual(st.get(k), before.get(k), (name, k))

    def test_offline_format_migration_cli(self):
        state, rep = self.load('state_production.json'), self.load('report_production.json')
        d = Data(self, state, rep)
        with redirect_stdout(io.StringIO()) as out:
            self.assertTrue(notify.migrate_format_file(NOW))
            self.assertFalse(notify.migrate_format_file(NOW))
        self.assertEqual(out.getvalue().split(), ['NOTIFY_FORMAT_MIGRATED', 'NOTIFY_FORMAT_UNCHANGED'])
        st = d.state()
        self.assertEqual({k: st[k] for k in state if k != 'migrations'}, {k: v for k, v in state.items() if k != 'migrations'})
        self.assertEqual({k: st['migrations'][k] for k in state['migrations']}, state['migrations'])
        self.assertEqual(d.log_bytes(), b'')
        pr1 = self.load('state_pr1.json')
        d.write_state(pr1)
        with self.assertRaises(notify.NotifyError):                     # PR 3 reference not migrated yet: refused
            notify.migrate_format_file(NOW)
        self.assertEqual(d.state(), pr1)


@unittest.skipUnless(shutil.which('git'), 'git required')
class U15_PostCutoverCheck(unittest.TestCase):
    """post_cutover_check over a real history: GitHub (bare remote) <- runner clones with the workflow's commit step."""

    def history(self, durable):
        import importlib.util
        spec = importlib.util.spec_from_file_location('pcc', ROOT / 'scripts' / 'post_cutover_check.py')
        pcc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pcc)
        state, rep = json.loads((FIX / 'state_production.json').read_text()), json.loads((FIX / 'report_production.json').read_text())
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)

        def git(cwd, *a):
            return subprocess.run(['git', '-C', str(cwd), '-c', 'user.name=t', '-c', 'user.email=t@invalid', '-c', 'commit.gpgsign=false', *a],
                                  check=True, capture_output=True, text=True).stdout.strip()
        remote, clone = tmp / 'github.git', tmp / 'runner'
        git(tmp, 'init', '-q', '--bare', '-b', 'main', str(remote))
        git(tmp, 'clone', '-q', str(remote), str(clone))
        data = clone / 'data'
        data.mkdir()
        (data / 'notify_state.json').write_text(json.dumps(state))
        (data / 'report.json').write_text(json.dumps(rep))
        git(clone, 'add', '-A'); git(clone, 'commit', '-qm', 'before'); git(clone, 'push', '-q', 'origin', 'HEAD:main')
        revs = [git(clone, 'rev-parse', 'HEAD')]
        env = dict(ENV, NOTIFY_DURABLE='git', GITHUB_REF_NAME='main') if durable else ENV

        def cycle(now, rep_):
            (data / 'report.json').write_text(json.dumps(rep_))
            with patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(data)}), redirect_stdout(io.StringIO()):
                notify.run(lambda *a: None, now, env)
            git(clone, 'add', 'data')                                    # the workflow's "Commit data" step
            git(clone, 'commit', '-qm', 'data: collect [skip ci]')
            git(clone, 'pull', '-q', '--rebase', 'origin', 'main')
            git(clone, 'push', '-q', 'origin', 'HEAD:main')
            revs.append(git(clone, 'rev-parse', 'HEAD'))
        cycle(datetime.fromisoformat(rep['generated_at_utc']), rep)
        rep2 = copy.deepcopy(rep)
        rep2['brain']['watch'] = [entry('m1'), entry('m2', minutes=-3)]
        cycle(NOW + timedelta(hours=1), rep2)
        return pcc, clone, revs, git

    def test_check_passes_before_and_after_the_first_pr5_cycles(self):
        pcc, clone, revs, git = self.history(durable=True)
        for a, b in ((revs[0], revs[1]), (revs[1], revs[2])):
            result = pcc.check(clone, a, b)
            self.assertTrue(result['ok'], result['checks'])
        self.assertEqual(pcc.check(clone, revs[1], revs[2])['sent_in_run'], 2)
        self.assertTrue(pcc.check(clone, revs[1], revs[2])['checks']['pr5_intent_published_before_outcome'])
        # A rewritten log is caught.
        (clone / 'data' / notify.LOG_NAME).write_text('')
        git(clone, 'add', '-A'); git(clone, 'commit', '-qm', 'log truncated')
        bad = git(clone, 'rev-parse', 'HEAD')
        self.assertFalse(pcc.check(clone, revs[2], bad)['checks']['pr5_log_append_only'])

    def test_without_the_barrier_the_check_reports_it(self):
        pcc, clone, revs, _ = self.history(durable=False)
        result = pcc.check(clone, revs[1], revs[2])
        self.assertFalse(result['checks']['pr5_intent_published_before_outcome'])
        self.assertFalse(result['ok'])


class U16_LongMessages(unittest.TestCase):
    def test_forty_hypotheses_fit_and_keep_the_scientific_state(self):
        ids = games('g', 40)
        rows = {'Jogo aberto | Minuto = 31 a 60 & Liga = L%02d com um nome comprido para encher a mensagem' % i:
                row(ids=ids, conditions=[('Minuto', '31 a 60'), ('Liga', 'L%02d com um nome comprido para encher a mensagem' % i)][:1 + (i > 0)], games_=40)
                for i in range(40)}
        d = Data(self, base_state({}), report({}, rows={}))
        d.run()
        d.write_report(report({h: 'promissora' for h in rows}, rows=rows))
        d.run(now=NOW + timedelta(minutes=2))
        self.assertTrue(d.sent)
        for m in d.sent:
            self.assertLessEqual(len(m), notify.MAX_TEXT)
            self.assertTrue(m.startswith('Estado científico: NÃO CERTIFICADA — certificação desligada.'))
        reasons = [reason(h) for h in rows]
        w = entry('m1', reasons=reasons)
        text = notify.messages(report({h: 'promissora' for h in rows}, rows={h: dict(r, game_ids=None) for h, r in rows.items()}, watch=[w]),
                               base_state({h: 'promissora' for h in rows}), NOW)[0]
        self.assertLessEqual(len(text), notify.MAX_TEXT)
        self.assertTrue(labelled(text))
        self.assertIn('mensagem encurtada', text)


class U17_Preview(unittest.TestCase):
    def test_preview_is_offline_and_writes_nothing(self):
        d = Data(self, migrated(base_state()), report(watch=[entry('m1')]))
        (d.path / notify.LOG_NAME).write_bytes(b'{"v": 1, "key": "k", "event": "pending"}\n')
        files = {p.name: p.read_bytes() for p in d.path.iterdir()}
        out = d.path.parent / ('preview-%s.txt' % d.path.name)
        self.addCleanup(lambda: out.exists() and out.unlink())
        attempts = []
        with patch.dict(os.environ, ENV), patch.object(notify, 'send', side_effect=AssertionError('sender used')) as real_sender, \
                patch.object(socket.socket, 'connect', lambda *a: attempts.append(a)), \
                patch.object(notify.sys, 'argv', ['notify', '--preview', '--now', NOW.isoformat(), '--out', str(out)]):
            notify.main()
        self.assertEqual(attempts, [])
        real_sender.assert_not_called()
        _, simulated = notify.preview(d.path / 'notify_state.json', d.path / 'report.json', NOW)
        self.assertEqual([r['state'] for r in simulated['deliveries'].values()], ['delivered'])
        self.assertEqual({p.name: p.read_bytes() for p in d.path.iterdir()}, files)     # state and log byte for byte
        text = out.read_text(encoding='utf-8')
        self.assertIn('A seguir: Home m1 — Away m1', text)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(CHAT, text)

    def test_preview_refuses_any_connection(self):
        with notify.no_network():
            with self.assertRaises(notify.NotifyError):
                socket.create_connection(('api.telegram.org', 443))
            with self.assertRaises(notify.NotifyError):
                socket.getaddrinfo('api.telegram.org', 443)

    def test_preview_never_reads_credentials(self):
        d = Data(self, base_state(), report(watch=[entry('m1')]))

        class Spy(dict):
            def get(self, k, default=None):
                raise AssertionError('environment read: %s' % k)
        with patch.object(notify.os, 'environ', Spy()):
            texts, _ = notify.preview(d.path / 'notify_state.json', d.path / 'report.json', NOW)
        self.assertEqual(len(texts), 1)


class U18_DailySummary(unittest.TestCase):
    def test_one_summary_per_day_after_21_utc(self):
        d = Data(self, base_state(), report())
        for hh, mm in ((20, 59), (21, 0), (21, 30), (23, 59)):
            d.run(now=NOW.replace(hour=hh, minute=mm))
        self.assertEqual(len(d.sent), 1)
        self.assertIn('Resumo de 16/11', d.sent[0])
        self.assertIn('saldo do dia +0,85 un.', d.sent[0])
        self.assertNotIn('fortes', d.sent[0])
        self.assertNotIn('confirmadas', d.sent[0])
        d.run(now=NOW.replace(hour=21) + timedelta(days=1))
        self.assertEqual(len(d.sent), 2)
        self.assertIn('Avisos desde o último resumo: 1 entregues', d.sent[1])


class U19_LateAlert(unittest.TestCase):
    def test_alert_out_of_time_neither_sent_nor_pending(self):
        rows = {HA: row('forte', confirmation=certificate())}
        late = entry('m1', minutes=-31, kickoff=-60, reasons=[reason(HA, 'forte', certified=True)])
        d = Data(self, base_state({HA: 'forte'}), report({HA: 'forte'}, rows=rows, watch=[late], validation=active(HA)))
        d.run()
        self.assertEqual(d.sent, [])
        self.assertEqual(d.state().get('deliveries'), {})
        self.assertEqual(d.log_bytes(), b'')

    def test_pending_alert_expires_when_it_is_no_longer_bettable(self):
        rows = {HA: row('forte', confirmation=certificate())}
        a = entry('m1', minutes=-1, kickoff=None, reasons=[reason(HA, 'forte', certified=True)])
        b = entry('m2', minutes=-2, kickoff=None, reasons=[reason(HA, 'forte', certified=True)])
        d = Data(self, base_state({HA: 'forte'}), report({HA: 'forte'}, rows=rows, watch=[a, b], validation=active(HA)))
        d.run(sender=d.sender(fail_at=1, error=notify.DeliveryFailed('HTTP 429')))       # first fails, second stays pending
        d.run(now=NOW + timedelta(minutes=45))                                            # too late for the second
        st = consistent(self, d)
        self.assertEqual(d.sent, [])
        self.assertEqual(sorted(r['state'] for r in st['deliveries'].values()), ['expired', 'failed'])


class U20_Log(unittest.TestCase):
    def test_log_only_grows_and_holds_no_secret(self):
        d = Data(self, base_state(), report(watch=[entry('m1')]))
        snapshots = []
        for k in range(4):
            d.write_report(report(watch=[entry('m%d' % i, minutes=-i) for i in range(1, k + 2)]))
            d.run(now=NOW + timedelta(minutes=2 * k))
            snapshots.append(d.log_bytes())
        d.run(now=NOW.replace(hour=21, minute=1))
        snapshots.append(d.log_bytes())
        for a, b in zip(snapshots, snapshots[1:]):
            self.assertTrue(b.startswith(a))
        raw = d.log_bytes().decode('utf-8') + (d.path / 'notify_state.json').read_text(encoding='utf-8')
        self.assertNotIn(TOKEN, raw)
        self.assertNotIn(CHAT, raw)
        for e in d.log():
            self.assertLessEqual(set(e), {'v', 'at_utc', 'key', 'event', 'kind', 'sha256', 'effects', 'reason', 'reconstructed'})


class X_Sender(unittest.TestCase):
    """Classification of the real sender's answers (urlopen mocked; no network)."""

    def outcome(self, effect=None, body=b'{"ok": true}'):
        class Answer:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n):
                return body
        with patch.object(notify, 'urlopen', side_effect=effect, return_value=Answer()):
            try:
                notify.send(TOKEN, CHAT, 'x')
                return 'delivered'
            except notify.DeliveryFailed as exc:
                self.assertNotIn(TOKEN, str(exc))
                return 'failed'
            except notify.DeliveryUncertain as exc:
                self.assertNotIn(TOKEN, str(exc))
                return 'uncertain'

    def test_classification(self):
        url = 'https://api.telegram.org/bot%s/sendMessage' % TOKEN
        cases = [(None, b'{"ok": true}', 'delivered'), (None, b'{"ok": false}', 'failed'), (None, b'not json', 'uncertain'),
                 (HTTPError(url, 400, 'bad', {}, None), b'', 'failed'), (HTTPError(url, 429, 'slow', {}, None), b'', 'failed'),
                 (HTTPError(url, 502, 'gateway', {}, None), b'', 'uncertain'), (URLError(socket.gaierror('dns')), b'', 'failed'),
                 (URLError(ConnectionRefusedError()), b'', 'failed'), (URLError(TimeoutError()), b'', 'uncertain'),
                 (TimeoutError(), b'', 'uncertain'), (ConnectionResetError(), b'', 'uncertain')]
        for effect, body, expected in cases:
            with self.subTest(expected=expected, effect=repr(effect)):
                self.assertEqual(self.outcome(effect, body), expected)


class X_Presentation(unittest.TestCase):
    def test_order_of_the_content(self):
        rep = report(watch=[entry('m1')])
        text = notify.messages(rep, base_state(), NOW)[0]
        order = ['Estado científico', 'A seguir:', 'Encaixa em:', 'Jogos independentes', 'ROI por jogo', 'Probabilidade de estar acima',
                 'Evidência nova', 'Aposta:', 'Jogo às']
        pos = [text.index(k) for k in order]
        self.assertEqual(pos, sorted(pos), text)
        self.assertIn('ROI por jogo: +25,9 % à odd anunciada; +23,1 % com 2 % de perda de odd', text)
        self.assertIn('Evidência nova: 30 jogos prospetivos desde o registo; primeiro aviso desta hipótese.', text)

    def test_new_evidence_since_the_last_notice(self):
        d = Data(self, base_state(), report(watch=[entry('m1')]))
        d.run()
        rows = {HA: row(prospective=34)}
        d.write_report(report(rows=rows, watch=[entry('m2')]))
        d.run(now=NOW + timedelta(minutes=2))
        self.assertIn('Evidência nova: 34 jogos prospetivos desde o registo; +4 desde o último aviso.', d.sent[1])

    def test_every_kind_of_message_starts_with_the_scientific_state(self):
        st = base_state()
        st['hello'] = False
        rep = report({HA: 'abandonada', HB: 'promissora'}, watch=[entry('m1')],
                     regime=[{'type': 'OPEN_GAME', 'state': 'piorou', 'recent': {'hit_rate': 0.3}, 'before': {'hit_rate': 0.8}}])
        out = notify.messages(rep, st, NOW.replace(hour=21, minute=5))
        self.assertEqual(len(out), 6)            # hello, game, two status changes, regime, summary
        self.assertTrue(all(labelled(m) for m in out))

    def test_report_without_presentation_announces_no_status_change(self):
        rep = report({HA: 'abandonada'})
        del rep['brain']['presentation']
        self.assertEqual(notify.messages(rep, base_state(), NOW), [])

    def test_compat_messages_api_matches_the_engine(self):
        rep = report({HA: 'abandonada'}, watch=[entry('m1'), entry('m2', minutes=-3)])
        expected = notify.messages(rep, base_state(), NOW)
        d = Data(self, base_state(), rep)
        d.run()
        self.assertEqual(d.sent, expected)


class X_LegacyAndHistory(unittest.TestCase):
    def test_signals_announced_in_the_old_format_are_not_resent(self):
        old = entry('m1', minutes=-20)
        st = base_state()
        del st['migrations']['status_rules:' + brain.STATUS_RULES]          # an un-migrated, pre-PR 5 production state
        st['watch_sent'] = sorted(st['watch_sent'] + [notify.legacy_key(old)])
        d = Data(self, st, report(watch=[old]))
        d.run()
        self.assertEqual(d.sent, [])
        self.assertEqual(d.state()['migrations'][notify.FORMAT_MIGRATION]['seeded_items'], ['item|id:m1|Asian Total|Over 2.5'])
        later = entry('m1', minutes=-1)                                     # a reinforcement with another signal time
        d.write_report(report(watch=[later, old]))
        d.run(now=NOW + timedelta(minutes=2))
        self.assertEqual(d.sent, [])
        st = base_state()
        st['watch_sent'] = sorted(st['watch_sent'] + [notify.legacy_key(old)])
        d = Data(self, st, report(watch=[later]))                            # the old signal already left the watch list
        d.run()
        self.assertEqual(d.sent, [])

    def test_exact_legacy_key_protects_even_with_an_unreadable_signal_time(self):
        odd = dict(entry('m1'), date_utc='15/11 21:07')                      # not ISO: the 3-day pair rule cannot apply
        st = migrated(base_state())
        st['watch_sent'] = sorted(st['watch_sent'] + [notify.legacy_key(odd)])
        d = Data(self, st, report(watch=[odd]))
        d.run()
        self.assertEqual(d.sent, [])

    def test_watch_sent_is_never_truncated(self):
        st = base_state()
        st['watch_sent'] = sorted('2026-11-%02dT10:%02d:00+00:00|X%d — Y|Over 2.5' % (1 + i // 60, i % 60, i) for i in range(450))
        d = Data(self, st, report(watch=[entry('m1')]))
        d.run()
        ws = d.state()['watch_sent']
        self.assertEqual(len(ws), 451)
        self.assertTrue(set(st['watch_sent']) <= set(ws))

    def test_an_attempted_key_is_never_sent_again_even_if_its_effects_were_lost(self):
        for outcome in ('uncertain', 'failed', 'blocked', 'delivered', 'expired'):
            with self.subTest(outcome):
                st = migrated(base_state())
                st['hello'] = False
                st['deliveries'] = {'hello': {'kind': 'hello', 'state': outcome, 'events': ['pending', 'intent', outcome]}}
                st['delivery_counts'] = {outcome: 1}
                d = Data(self, st, report())
                (d.path / notify.LOG_NAME).write_text(''.join(json.dumps({'v': 1, 'key': 'hello', 'event': e, 'kind': 'hello'}) + '\n'
                                                             for e in ('pending', 'intent', outcome)))
                d.run()
                self.assertEqual(d.sent, [])

    def test_rollback_and_roll_forward_never_resends_what_the_old_code_sent(self):
        # PR 5 migrated, then a rollback to the old code sent an ALERT and wrote only the legacy key, then PR 5 again.
        rows = {HA: row('forte', confirmation=certificate())}
        alert = entry('m9', minutes=-2, reasons=[reason(HA, 'forte', certified=True)])
        st = base_state({HA: 'forte'})
        d = Data(self, st, report({HA: 'forte'}, rows=rows, validation=active(HA)))
        d.run()
        st = d.state()
        st['watch_sent'] = sorted(st['watch_sent'] + [notify.legacy_key(alert)])
        d.write_state(st)
        d.write_report(report({HA: 'forte'}, rows=rows, watch=[alert], validation=active(HA)))
        d.run(now=NOW + timedelta(minutes=1))
        self.assertEqual(d.sent, [])

    def test_revert_then_roll_forward_does_not_repeat_what_the_old_code_announced(self):
        d = Data(self, base_state(), report())
        d.run()
        st = d.state()                                                     # the old code runs after a revert:
        st['statuses'] = {HA: 'abandonada'}                                # it announced the change with its own reference
        st['sent_total'] += 1
        st['last_run_utc'] = (NOW + timedelta(minutes=10)).isoformat()
        d.write_state(st)
        d.write_report(report({HA: 'abandonada'}))
        d.run(now=NOW + timedelta(minutes=20))                             # PR 5 again
        st = d.state()
        self.assertEqual(d.sent, [])
        self.assertEqual(st['announced_statuses'], {HA: 'abandonada'})
        self.assertEqual(len(st['format_resyncs']), 1)
        d.run(now=NOW + timedelta(minutes=30))
        self.assertEqual(len(d.state()['format_resyncs']), 1)              # its own runs never trigger it

    def test_absent_but_still_certified_is_unknown_not_a_loss(self):
        rows = {HA: row('forte', confirmation=certificate())}
        d = Data(self, base_state({HA: 'forte'}), report({HA: 'forte'}, rows=rows, validation=active(HA)))
        d.run()
        d.write_report(report({}, rows=rows, validation=active(HA)))      # inconsistent report: no status, certificate intact
        d.run(now=NOW + timedelta(minutes=2))
        self.assertEqual(d.sent, [])
        self.assertEqual(d.state()['announced_statuses'], {HA: 'forte'})


class P2_StateLoss(unittest.TestCase):
    """Review #5920565094, P2: an installation with history never restarts silently from an empty state."""

    def delivered_once(self):
        d = Data(self, base_state(), report(watch=[entry('m1')]))
        d.run()
        self.assertEqual(len(d.sent), 1)
        return d

    def refused(self, d, error, now=NOW + timedelta(minutes=5)):
        files = {p.name: p.read_bytes() for p in d.path.iterdir()}
        with self.assertRaises(error):
            d.run(now=now)
        self.assertEqual({p.name: p.read_bytes() for p in d.path.iterdir()}, files)   # nothing created, nothing written
        return files

    def test_missing_state_after_deliveries_is_refused(self):
        d = self.delivered_once()
        (d.path / 'notify_state.json').unlink()
        self.refused(d, notify.StateMissing)
        self.assertEqual(len(d.sent), 1)                                 # neither the greeting nor m1 again
        self.assertFalse((d.path / 'notify_state.json').exists())

    def test_missing_state_is_refused_even_without_a_delivery_log(self):
        d = Data(self, None, report(watch=[entry('m1')]))
        with redirect_stdout(io.StringIO()) as out, self.assertRaises(notify.StateMissing):
            notify.run(d.sender(), NOW, ENV)
        self.assertIn('NOTIFY_STATE_MISSING', out.getvalue())
        self.assertEqual((d.sent, sorted(p.name for p in d.path.iterdir())), ([], ['report.json']))

    def test_cli_never_breaks_the_collection_and_sends_nothing(self):
        d = self.delivered_once()
        (d.path / 'notify_state.json').unlink()
        with patch.dict(os.environ, ENV), patch.object(notify, 'send', side_effect=AssertionError('sent')) as real, \
                patch.object(notify.sys, 'argv', ['notify']), redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as code:
                notify.main()
        self.assertEqual(code.exception.code, 0)
        real.assert_not_called()
        self.assertIn('NOTIFY_STATE_MISSING', out.getvalue())
        self.assertIn('NOTIFY_FAILED StateMissing', out.getvalue())

    def test_empty_or_pre_pr5_state_next_to_a_delivery_log_is_refused(self):
        for name, state in (('empty', {}), ('pre-PR 5 copy', base_state())):
            with self.subTest(name):
                d = self.delivered_once()
                d.write_state(state)
                self.refused(d, notify.StateRegressed)
                self.assertEqual(len(d.sent), 1)

    def test_counter_below_the_migration_value_is_refused(self):
        d = self.delivered_once()
        st = d.state()
        st['sent_total'] = st['migrations'][notify.FORMAT_MIGRATION]['sent_total'] - 1
        d.write_state(st)
        self.refused(d, notify.StateRegressed)

    def test_init_state_only_for_a_new_installation(self):
        d = Data(self, None, report(watch=[entry('m1')]))
        with redirect_stdout(io.StringIO()):
            self.assertTrue(notify.init_state_file())
        self.assertEqual(d.state(), {'installation': {'method': 'init-state', 'at_utc': d.state()['installation']['at_utc']}})
        d.run()
        self.assertEqual(len(d.sent), 2)                                 # greeting + m1 on a genuinely new installation
        for setup in ('state exists', 'log only'):
            with self.subTest(setup):
                if setup == 'log only':
                    (d.path / 'notify_state.json').unlink()
                with redirect_stdout(io.StringIO()), self.assertRaises(notify.NotifyError):
                    notify.init_state_file()
        self.assertFalse((d.path / 'notify_state.json').exists())
        with patch.object(notify.sys, 'argv', ['notify', '--init-state']), redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as code:
                notify.main()
        self.assertEqual((code.exception.code, out.getvalue().split()[0]), (1, 'NOTIFY_INIT_REFUSED'))

    def test_preview_follows_the_same_rule(self):
        d = Data(self, None, report(watch=[entry('m1')]))
        with self.assertRaises(notify.StateMissing):
            notify.preview(d.path / 'notify_state.json', d.path / 'report.json', NOW)


class P2R_ResetStateWithoutLog(unittest.TestCase):
    """Review #5921136533, P2-R: an accidental `{}` (or any state without history) is never a new installation, even when no
    delivery log exists yet; only `--init-state` (explicit `installation` marker) authorises one. Historical states stay valid."""

    def real_report(self):
        # The production report plus open signals the pre-PR 5 notifier had already announced (keys in production watch_sent).
        rep = json.loads((FIX / 'report_production.json').read_text())
        prod = json.loads((FIX / 'state_production.json').read_text())
        old = []
        for k in prod['watch_sent'][-3:]:
            date, match, sel = k.split('|')[0], '|'.join(k.split('|')[1:-1]), k.split('|')[-1]
            old.append(dict(entry('old%d' % len(old), sel, match=match), date_utc=date))
        rep['brain']['watch'] = old
        return rep

    def test_empty_state_with_no_or_empty_log_sends_nothing(self):
        for log in (None, b''):
            for state in ({}, {'hello': True}, {'sent_total': 312, 'watch_sent': [], 'statuses': {}},
                          {'installation': {'method': 'manual'}}, {'migrations': {notify.FORMAT_MIGRATION: {}}}):
                with self.subTest(log=log, state=state):
                    d = Data(self, state, self.real_report())
                    if log is not None:
                        (d.path / notify.LOG_NAME).write_bytes(log)
                    files = {p.name: p.read_bytes() for p in d.path.iterdir()}
                    with self.assertRaises(notify.StateRegressed):
                        d.run(now=datetime.fromisoformat(self.real_report()['generated_at_utc']))
                    self.assertEqual(d.sent, [])
                    self.assertEqual({p.name: p.read_bytes() for p in d.path.iterdir()}, files)
        # The same report on the real production state: nothing to send either (the old notices are known).
        d = Data(self, json.loads((FIX / 'state_production.json').read_text()), self.real_report())
        d.run(now=datetime.fromisoformat(self.real_report()['generated_at_utc']))
        self.assertEqual(d.sent, [])

    def test_pre_pr1_copy_without_markers_is_refused(self):
        st = base_state()
        del st['migrations']                                             # essential fields but no PR 1 marker
        d = Data(self, st, report(watch=[entry('m1')]))
        with self.assertRaises(notify.StateRegressed):
            d.run()
        self.assertEqual(d.sent, [])

    def test_historical_states_and_explicit_installation_are_accepted(self):
        for name in ('state_pr1.json', 'state_pr2.json', 'state_pr3.json', 'state_production.json'):
            notify.check_integrity(json.loads((FIX / name).read_text()), [])
        notify.check_integrity({'installation': {'method': 'init-state', 'at_utc': NOW.isoformat()}}, [])

    def test_offline_format_migration_refuses_a_reset_state(self):
        d = Data(self, {}, report())
        with redirect_stdout(io.StringIO()), self.assertRaises(notify.StateRegressed):
            notify.migrate_format_file(NOW)
        self.assertEqual(d.state(), {})

    def test_cli_reports_and_keeps_the_collection_running(self):
        d = Data(self, {}, self.real_report())
        with patch.dict(os.environ, ENV), patch.object(notify, 'send', side_effect=AssertionError('sent')) as real, \
                patch.object(notify.sys, 'argv', ['notify']), redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as code:
                notify.main()
        self.assertEqual(code.exception.code, 0)
        real.assert_not_called()
        self.assertIn('NOTIFY_FAILED StateRegressed', out.getvalue())

    def test_new_installation_with_init_state_keeps_working(self):
        d = Data(self, None, report(watch=[entry('m1')]))
        with redirect_stdout(io.StringIO()):
            notify.init_state_file(NOW)
        d.run()
        d.write_report(report(watch=[entry('m1'), entry('m2', minutes=-1)]))
        d.run(now=NOW + timedelta(minutes=5))
        st = consistent(self, d)
        self.assertEqual(len(d.sent), 3)                                  # greeting, m1, m2: each once
        self.assertEqual(st['installation']['method'], 'init-state')
        self.assertIn(notify.FORMAT_MIGRATION, st['migrations'])


if __name__ == '__main__':
    unittest.main()
