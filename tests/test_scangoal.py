import json, os, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path

from scangoal import store
from scangoal.parser import kickoff_utc, parse, settle, split

BETFAIR_PRE = """💷💷 🟨 Betfair ×8 · 11 nov 2026 22:00
Barnsley — Oldham · England EFL Trophy
1 · Barnsley @1.60 · $34K
📊 market share 78% · whole 1X2 market $43K · pre-match
https://scangoal.com/match/barnsley-vs-oldham-20261111?lang=en

⚠️ Not a bet and not a forecast: the signal flags a statistical anomaly in the match. The decision and the risk are yours."""
BETFAIR_LIVE = """💷 🟨 Betfair · LIVE
Kucukcekmece Sinopspor — Fatsa Belediyespor · 🇹🇷 Turkish 3 Lig
X · Draw @1.94 · $21K
📊 market share 95% · whole 1X2 market $23K · live
https://scangoal.com/match/kucukcekmece-sinopspor-vs-fatsa-belediyespor-20261111?lang=en"""
STAKE = """💸 🟦 Stake money load — $15K
UEFA U19 Championship · 11 nov 2026 18:30
Bosnia U19     0
San Marino U19 0
📊 Asian Total → Under 3.25 @ 1.70
🧾 1 bet · biggest $15K
https://scangoal.com/match/bosnia-u19-vs-san-marino-u19-20261111?lang=en"""
STAKE_FT = STAKE + "\n\n🏁 FT 4:1 · Asian Total → Under 3.25 ❌\n⚽  27'   1 - 0"
OPEN = """🔥 Open game — heading for over
 UEFA Women's Europa Cup · 45'
Czarni Sosnowiec Women 0
SK Brann Women         4
Rhythm 87/100 · Over 100%
💰 O 5: 1.81
https://scangoal.com/match/czarni-sosnowiec-women-vs-sk-brann-women-20261111?lang=en"""
BREW = """⏳ Goal brewing — goal chance in the next 15′ above usual
🇸🇰 Slovakian 1 Liga Women · 25'
FC Petrzalka Women   1
Spartak Trnava Women 1
Goal in 15′: 50% · usual 37%"""


class Parser(unittest.TestCase):
    def test_betfair_pre(self):
        p = parse(BETFAIR_PRE)
        self.assertEqual((p['type'], p['phase'], p['mult'], p['selection'], p['odds']), ('BETFAIR', 'pre', 8, '1', 1.60))
        self.assertEqual((p['sel_volume'], p['share'], p['mkt_volume']), (34000, 78, 43000))
        self.assertEqual(p['match_id'], 'barnsley-vs-oldham-20261111')
        self.assertEqual(p['kickoff_utc'], '2026-11-11T19:00+00:00')  # Moscovo 22:00 = 19:00 UTC

    def test_betfair_live_draw(self):
        p = parse(BETFAIR_LIVE)
        self.assertEqual((p['phase'], p['mult'], p['selection'], p['kickoff_utc']), ('live', 1, 'X', None))

    def test_stake_before_and_after_edit(self):
        a, b = parse(STAKE), parse(STAKE_FT)
        self.assertEqual((a['market'], a['selection'], a['odds'], a['total']), ('Asian Total', 'Under 3.25', 1.70, 15000))
        self.assertNotIn('ft', a)
        self.assertEqual((a['phase'], a['teams'], a['score_at_signal']), ('live', ['Bosnia U19', 'San Marino U19'], [0, 0]))
        self.assertEqual((b['ft'], b['bot_mark']), ([4, 1], False))

    def test_open_game(self):
        p = parse(OPEN)
        self.assertEqual((p['type'], p['selection'], p['odds'], p['minute'], p['score_at_signal']), ('OPEN_GAME', 'Over 5', 1.81, 45, [0, 4]))

    def test_non_bets_and_unknown(self):
        self.assertFalse(parse(BREW)['bet'])
        self.assertIsNone(parse('hello')['type'])
        broken = parse('💷 🟨 Betfair · LIVE\nA — B\nsomething else')
        self.assertEqual((broken['bet'], broken['error']), (False, 'fields_missing'))

    def test_formats_seen_in_first_real_collection(self):
        no_odds = OPEN.replace('💰 O 5: 1.81\n', '') + '\n\n🏁 FT 1:8\n⚽  91\'   1 - 8'
        p = parse(no_odds)
        self.assertEqual((p['type'], p['bet'], p['ft'], p['bot_mark']), ('OPEN_GAME_NO_ODDS', False, [1, 8], None))
        self.assertEqual((p['minute'], p['score_at_signal'], p['teams']), (45, [0, 4], ['Czarni Sosnowiec Women', 'SK Brann Women']))
        self.assertNotIn('error', p)
        note = parse('🏁 Full time: Liverpool Women 4:1 Aston Villa Women\nWeakened Liverpool Women won.')
        self.assertEqual((note['type'], note['teams'], note['ft']), ('FULL_TIME_NOTE', ['Liverpool Women', 'Aston Villa Women'], [4, 1]))
        for head in ('⭐ Pro is active until 11.12.2026.', '🎁 3 months of Pro for free', '✅ Pro is on.', 'Pro status — 250 ⭐ per 30 days'):
            self.assertEqual((parse(head)['type'], parse(head)['bet']), ('SERVICE', False))

    def test_split(self):
        self.assertEqual(len(list(split('ScanGoal:\n' + BETFAIR_PRE + '\n\n' + STAKE_FT + '\n\n' + OPEN))), 3)

    def test_kickoff_month_unknown(self):
        self.assertIsNone(kickoff_utc('11 xyz 2026 22:00'))


class Settle(unittest.TestCase):
    def test_asian_totals(self):
        self.assertEqual(settle('Asian Total', 'Over 2.5', [2, 0]), -1)
        self.assertEqual(settle('Asian Total', 'Over 5', [1, 4]), 0)        # devolução
        self.assertEqual(settle('Asian Total', 'Over 4.75', [3, 2]), 0.5)   # meia vitória
        self.assertEqual(settle('Asian Total', 'Under 3.25', [2, 1]), 0.5)
        self.assertEqual(settle('Asian Total', 'Over 0.75', [0, 0]), -1)
        self.assertEqual(settle('Asian Total', 'Over 1.25', [1, 0]), -0.5)

    def test_asian_handicap_and_market_name_case(self):
        teams = ['Eintracht Frankfurt Women', 'Psv Women']
        self.assertEqual(settle('Asian Handicap', 'Eintracht Frankfurt (-1.5)', [2, 1], teams), -1)
        self.assertEqual(settle('Asian Handicap', 'Eintracht Frankfurt (-1)', [2, 1], teams), 0)
        self.assertEqual(settle('Asian Handicap', 'Psv (+0.25)', [1, 1], teams), 0.5)
        self.assertEqual(settle('Asian Handicap', 'Psv (+0.75)', [2, 1], teams), -0.5)
        self.assertIsNone(settle('Asian Handicap', 'Unknown (-1)', [2, 1], teams))
        self.assertEqual(settle('Both Teams To Score', 'Yes', [2, 2]), 1)
        self.assertIsNone(settle('2nd Team to Score', 'Sheffield Wednesday', [0, 4], ['Sheff Wed', 'Accrington']))

    def test_other_markets(self):
        self.assertEqual(settle('1X2', 'X', [1, 1]), 1)
        self.assertEqual(settle('1X2', '2', [1, 0]), -1)
        self.assertEqual(settle('Both Teams to Score', 'No', [4, 1]), -1)
        teams = ['Serbia U19', 'Faroe Islands U19']
        self.assertEqual(settle('Double Chance', 'Draw or Faroe Islands', [4, 1], teams), -1)
        self.assertEqual(settle('Double Chance', 'Draw or Faroe Islands', [1, 1], teams), 1)
        self.assertEqual(settle('Double Chance', 'Serbia or Faroe Islands', [0, 2], teams), 1)
        self.assertIsNone(settle('Double Chance', 'Draw or Unknown FC', [1, 1], teams))
        self.assertIsNone(settle('Double Chance', 'Draw or Faroe Islands', [1, 1]))  # sem equipas: não liquida


class Report(unittest.TestCase):
    def run_report(self, rows):
        from scangoal import report
        with tempfile.TemporaryDirectory() as d:
            os.environ['SCANGOAL_DATA_DIR'] = d
            try:
                store.append_versions(rows)
                return report.build()
            finally:
                del os.environ['SCANGOAL_DATA_DIR']

    def row(self, mid, text, date='2026-11-11T17:49:00+00:00'):
        return {'message_id': mid, 'date_utc': date, 'edit_date_utc': None, 'collected_at_utc': '2026-11-11T18:00:00+00:00', 'text': text}

    def test_first_signal_rule_reinforcement_and_exclusions(self):
        x4 = BETFAIR_PRE.replace('×8', '×4').replace('@1.60', '@1.83')
        ft_for_barnsley = STAKE_FT.replace('bosnia-u19-vs-san-marino-u19-20261111', 'barnsley-vs-oldham-20261111')
        rows = [self.row(1, x4), self.row(2, BETFAIR_PRE), self.row(3, STAKE), self.row(4, OPEN),
                self.row(5, ft_for_barnsley)]  # 5: vista já com resultado -> não é aposta, mas dá o FT 4:1 ao jogo
        r = self.run_report(rows)
        self.assertEqual((r['unique_bets'], r['excluded_first_seen_with_result'], r['unparsed']), (3, 1, 0))
        g = r['groups']['BETFAIR/pre/mkt>=10K']
        self.assertEqual((g['bets'], g['settled'], g['wins']), (1, 1, 1))
        self.assertEqual(g['roi']['odd-0%']['roi'], 0.83)           # odd do PRIMEIRO sinal, não a do reforço
        self.assertFalse(g['conclusive'])
        q1 = r['questions']['Q1 Betfair: só apostas que chegaram a ×4, à odd do sinal ×4']
        self.assertEqual((q1['settled'], q1['roi']['odd-0%']['roi']), (1, 0.83))
        self.assertEqual(r['recent_bets'][-1]['max_mult'], 8)
        self.assertEqual(r['recent_bets'][-1]['match'], 'Barnsley — Oldham')

    def test_edit_adds_result_and_audit(self):
        rows = [self.row(3, STAKE), self.row(3, STAKE_FT), self.row(4, OPEN),
                self.row(4, OPEN.replace('1.81', '2.50') + '\n\n🏁 FT 1:5 · Over 5 ✅')]
        r = self.run_report(rows)
        self.assertEqual(r['audit']['edits_changing_signal'], 1)     # a 2.ª versão do Open game mudou a odd
        self.assertEqual(r['groups']['STAKE_LOAD/live']['roi']['odd-0%']['roi'], -1.0)
        self.assertEqual((r['groups']['OPEN_GAME']['wins'], r['groups']['OPEN_GAME']['roi']['odd-0%']['roi']), (1, 0.81))  # odd original


    def test_accuracy_counts_all_signals_and_no_odds_rule(self):
        no_odds = OPEN.replace('💰 O 5: 1.81\n', '').replace('czarni-sosnowiec-women-vs-sk-brann-women', 'a-vs-b')
        odd_market = STAKE.replace('Asian Total → Under 3.25 @ 1.70', '2nd Team to Score → Bosnia @ 2.10')
        rows = [self.row(1, STAKE_FT),                                                    # vista já com resultado: fora do ROI, dentro do acerto
                self.row(2, no_odds + '\n\n🏁 FT 0:4'),                                    # sem odd e sem mais golos -> falha
                self.row(3, OPEN + '\n\n🏁 FT 1:4 · Over 5 ✅'),                            # devolvida
                self.row(4, odd_market.replace('bosnia-u19-vs-san-marino-u19', 'c-vs-d')),
                self.row(4, odd_market.replace('bosnia-u19-vs-san-marino-u19', 'c-vs-d') + '\n\n🏁 FT 2:1 · 2nd Team to Score → Bosnia ✅'),
                self.row(5, BETFAIR_PRE), self.row(6, '🏁 Full time: Barnsley 0:0 Oldham\nNote.', '2026-11-11T21:00:00+00:00')]
        r = self.run_report(rows)
        acc = r['accuracy']
        self.assertEqual((acc['OPEN_GAME_NO_ODDS']['hits'], acc['OPEN_GAME_NO_ODDS']['misses']), (0, 1))
        self.assertEqual((acc['OPEN_GAME']['with_result'], acc['OPEN_GAME']['pushes'], acc['OPEN_GAME']['hit_rate']), (1, 1, None))
        self.assertEqual((acc['STAKE_LOAD']['signals'], acc['STAKE_LOAD']['hits'], acc['STAKE_LOAD']['misses'], acc['STAKE_LOAD']['by_bot_mark']), (2, 1, 1, 1))
        self.assertEqual(acc['STAKE_LOAD']['seen_late'], 1)
        self.assertEqual((acc['BETFAIR/pre']['with_result'], acc['BETFAIR/pre']['misses']), (1, 1))   # FT pela nota de fim de jogo
        self.assertEqual(r['groups']['BETFAIR/pre/mkt>=10K']['settled'], 1)
        self.assertEqual(r['groups']['STAKE_LOAD/live']['bets'], 1)                                   # a vista já com resultado não entra no ROI


class BSD(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ['SCANGOAL_DATA_DIR'] = self.dir.name
        live = BETFAIR_LIVE.replace('Kucukcekmece Sinopspor — Fatsa Belediyespor', 'Sheff Wed — Accrington FC')
        rows = [{'message_id': 1, 'date_utc': '2026-11-11T17:49:00+00:00', 'edit_date_utc': None, 'collected_at_utc': 'c', 'text': BETFAIR_PRE},
                {'message_id': 2, 'date_utc': '2026-11-11T19:40:00+00:00', 'edit_date_utc': None, 'collected_at_utc': 'c', 'text': live}]
        store.append_versions(rows)

    def tearDown(self):
        del os.environ['SCANGOAL_DATA_DIR']
        self.dir.cleanup()

    def ev(self, i, home, away, when, h=2, a=0, status='finished'):
        return {'id': i, 'status': status, 'event_date': when, 'home_team': {'name': home}, 'away_team': away, 'home_score': h, 'away_score': a}

    def run_bsd(self, pages, **kw):
        from datetime import datetime, timezone
        from scangoal import results_bsd
        calls = []

        def fetch(url, token):
            calls.append(url)
            assert token == 'k' and url.startswith(results_bsd.ROOT)
            return pages[len(calls) - 1]
        now = kw.pop('now', datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc))
        return results_bsd.run(fetch=fetch, now=now, token='k'), calls

    def test_exact_identity_time_window_and_no_fuzzy(self):
        page1 = {'results': [self.ev(10, 'Barnsley FC', 'Oldham', '2026-11-11T19:00:00Z', 2, 1),
                             self.ev(11, 'Barnsley', 'Oldham', '2026-11-10T19:00:00Z', 5, 5),            # mesmo par, outro dia: ignorado
                             self.ev(12, 'Sheffield Wednesday', 'Accrington', '2026-11-11T19:00:00Z')],  # nome diferente: não liquida
                 'next': None}
        state, calls = self.run_bsd([page1])
        self.assertEqual(len(calls), 1)
        self.assertEqual(state['results']['barnsley-vs-oldham-20261111']['ft'], [2, 1])
        self.assertEqual(list(state['results']), ['barnsley-vs-oldham-20261111'])
        self.assertIn('kucukcekmece-sinopspor-vs-fatsa-belediyespor-20261111', state['near_misses'])
        from scangoal import report
        r = report.build()
        self.assertEqual((r['audit']['bsd_results'], r['groups']['BETFAIR/pre/mkt>=10K']['wins']), (1, 1))
        self.assertEqual(r['recent_bets'][-1]['settled_by'], 'resultado BSD')

    def test_alias_file_is_the_only_way_to_bridge_names(self):
        (Path(self.dir.name) / 'team_aliases.json').write_text(json.dumps({'_comment': 'x', 'Sheff Wed': ['Sheffield Wednesday']}), encoding='utf-8')
        page = {'results': [self.ev(12, 'Sheffield Wednesday', 'Accrington', '2026-11-11T19:00:00Z', 0, 0)], 'next': None}
        state, _ = self.run_bsd([page])
        self.assertEqual(state['results']['kucukcekmece-sinopspor-vs-fatsa-belediyespor-20261111']['ft'], [0, 0])   # sinal às 19:40, jogo às 19:00

    def test_unfinished_ambiguous_throttle_and_missing_key(self):
        from datetime import datetime, timezone
        from scangoal import results_bsd
        dup = {'results': [self.ev(10, 'Barnsley', 'Oldham', '2026-11-11T19:00:00Z'), self.ev(13, 'Barnsley', 'Oldham', '2026-11-11T19:30:00Z'),
                           self.ev(14, 'Sheff Wed', 'Accrington', '2026-11-11T19:00:00Z', status='inprogress')], 'next': None}
        state, _ = self.run_bsd([dup])
        self.assertEqual(state['results'], {})                                   # dois candidatos / não terminado: fica em aberto
        _, calls = self.run_bsd([dup], now=datetime(2026, 11, 12, 9, 20, tzinfo=timezone.utc))
        self.assertEqual(calls, [])                                              # menos de 50 min depois: não repete pedidos
        self.assertIsNone(results_bsd.run(token=''))

    def test_pagination_follows_next_and_respects_cap(self):
        from scangoal import results_bsd
        p1 = {'count': 2, 'results': [self.ev(1, 'A', 'B', '2026-11-11T19:00:00Z')], 'next': results_bsd.ROOT + '?offset=1'}
        p2 = {'count': 2, 'results': [self.ev(10, 'Barnsley', 'Oldham', '2026-11-11T19:00:00Z', 1, 1)], 'next': None}
        state, calls = self.run_bsd([p1, p2])
        self.assertEqual((len(calls), state['results']['barnsley-vs-oldham-20261111']['ft']), (2, [1, 1]))
        self.assertIn('offset=1', calls[1])
        self.assertEqual(state['stats']['per_day'], {'2026-11-11': {'count_reported': 2, 'events_read': 2}})


class Highlightly(BSD):
    def hl(self, i, home, away, when, score='2 - 1', desc='Finished'):
        return {'id': i, 'date': when, 'homeTeam': {'name': home}, 'awayTeam': {'name': away}, 'league': {'name': 'L'},
                'state': {'description': desc, 'score': {'current': score, 'penalties': None}}}

    def run_hl(self, pages, remaining=90, now=None):
        from datetime import datetime, timezone
        from scangoal import results_hl
        calls = []

        def fetch(params, token):
            calls.append(params)
            assert token == 'k'
            return pages[len(calls) - 1], remaining
        return results_hl.run(fetch=fetch, now=now or datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc), token='k'), calls

    def test_finished_only_and_report_priority(self):
        (Path(self.dir.name) / 'team_aliases.json').write_text(json.dumps({'Sheff Wed': ['Sheffield Wednesday']}), encoding='utf-8')
        page = {'data': [self.hl(1, 'Barnsley', 'Oldham', '2026-11-11T19:00:00.000Z', '5 - 2'),
                         self.hl(2, 'Sheffield Wednesday', 'Accrington', '2026-11-11T19:00:00.000Z', '1 - 1', 'Finished after penalties')],
                'pagination': {'totalCount': 2}}
        state, calls = self.run_hl([page])
        self.assertEqual(calls, [{'date': '2026-11-11', 'limit': 100, 'offset': 0}])
        self.assertEqual({m: r['ft'] for m, r in state['results'].items()}, {'barnsley-vs-oldham-20261111': [5, 2]})   # penáltis: fica em aberto
        from scangoal import report
        r = report.build()
        self.assertEqual((r['audit']['hl_results'], r['recent_bets'][-1]['settled_by']), (1, 'resultado HL'))

    def test_quota_guard_pagination_and_throttle(self):
        p1 = {'data': [self.hl(9, 'A', 'B', '2026-11-11T19:00:00.000Z')], 'pagination': {'totalCount': 2}}
        state, calls = self.run_hl([p1, p1], remaining=10)
        self.assertEqual((len(calls), state['stats']['stopped']), (1, 'daily_quota_low'))
        _, calls = self.run_hl([p1])
        self.assertEqual(calls, [])                                              # menos de 4 horas depois


class Hardening(unittest.TestCase):
    def build(self, rows, extra=None):
        from scangoal import report
        with tempfile.TemporaryDirectory() as d:
            os.environ['SCANGOAL_DATA_DIR'] = d
            try:
                store.append_versions(rows)
                for name, content in (extra or {}).items():
                    (Path(d) / name).write_text(json.dumps(content), encoding='utf-8')
                csv_path = Path(d) / 'bets.csv'
                return report.build(csv_path), csv_path.read_text(encoding='utf-8')
            finally:
                del os.environ['SCANGOAL_DATA_DIR']

    def row(self, i, text, date):
        return {'message_id': i, 'date_utc': date, 'edit_date_utc': None, 'collected_at_utc': '2026-11-11T12:00:00+00:00', 'text': text}

    def test_stake_phase_comes_from_time_not_from_layout(self):
        pre_layout = STAKE.replace('Bosnia U19     0\nSan Marino U19 0', 'Bosnia U19 — San Marino U19')       # o bot reescreve ao editar
        seen_edited = lambda i, text, date: dict(self.row(i, text, date), edit_date_utc='2026-11-11T17:00:00+00:00')
        r, _ = self.build([seen_edited(1, pre_layout, '2026-11-11T16:00:00+00:00')])                              # jogo às 15:30 UTC: ao vivo
        self.assertEqual(list(r['accuracy']), ['STAKE_LOAD'])
        self.assertEqual(r['insights']['segments_tested'], 0)
        from scangoal import report
        b = {'type': 'STAKE_LOAD', 'phase': 'pre'}
        self.assertEqual(report.group(b), 'STAKE_LOAD/pre')

    def test_interval_groups_bets_of_the_same_match(self):
        from scangoal.report import stats
        same = [{'w': 1.0, 'odds': 2.0, 'match_id': 'a'}] * 4 + [{'w': -1.0, 'odds': 2.0, 'match_id': 'b'}] * 4
        spread = [{'w': 1.0, 'odds': 2.0, 'match_id': 'm%d' % i} for i in range(4)] + [{'w': -1.0, 'odds': 2.0, 'match_id': 'n%d' % i} for i in range(4)]
        wide, narrow = stats(same)['odd-0%']['ci95'], stats(spread)['odd-0%']['ci95']
        self.assertGreater(wide[1] - wide[0], narrow[1] - narrow[0])       # 8 apostas em 2 jogos valem menos do que em 8 jogos
        self.assertIsNone(stats(same[:4])['odd-0%']['ci95'])               # um só jogo: sem intervalo

    def test_collector_state_daily_csv_and_name_review(self):
        rows = [self.row(1, BETFAIR_PRE, '2026-11-11T17:49:00+00:00'), self.row(2, OPEN, '2026-11-12T10:00:00+00:00'),
                self.row(2, OPEN + '\n\n🏁 FT 1:5 · Over 5 ✅', '2026-11-12T10:00:00+00:00')]
        extra = {'state.json': {'last_run_utc': '2026-11-12T11:00:00+00:00', 'read': 2, 'new_versions': 0},
                 'results_hl.json': {'results': {}, 'near_misses': {'m': {'bot': ['Sheff Wed', 'Accrington'], 'bsd_candidates': [['Sheffield Wednesday', 'Accrington']]}}}}
        r, csv_text = self.build(rows, extra)
        self.assertEqual(r['audit']['collector_last_run_utc'], '2026-11-12T11:00:00+00:00')     # hora da recolha, não da última mensagem
        self.assertEqual(r['audit']['name_review'][0]['bot'], ['Sheff Wed', 'Accrington'])
        self.assertEqual([(d['day'], d['bets'], d['settled'], d['units']) for d in r['daily']], [('2026-11-11', 1, 0, 0.0), ('2026-11-12', 1, 1, 0.81)])
        lines = csv_text.strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn(';Barnsley;Oldham;1X2;1;1,6;', lines[1])
        self.assertTrue(lines[2].endswith(';1-5;ganha;0,81;resultado;sim'))
        self.assertIn('days_to_conclusion', r['accuracy']['OPEN_GAME'])


class AuditFixes(Hardening):
    WEAK = """🟥 WEAKENED LINEUP
League Cup Women · 22:00 (in 54 min)
Liverpool Women — Aston Villa Women

🔻 Liverpool Women: 3 of 13 usual starters start
🟡 Aston Villa Women: 5 of 13 usual starters start, 5 on the bench — rotation

📊 Handicap: Liverpool Women −1 · 1.89  |  Aston Villa Women +1 · 1.89"""

    def edited(self, i, text, date):
        return dict(self.row(i, text, date), edit_date_utc='2026-11-11T16:39:00+00:00')

    def test_provenance_edited_and_burst_stay_out_of_roi(self):
        live = [BETFAIR_LIVE.replace('kucukcekmece-sinopspor-vs-fatsa-belediyespor', 'm%d' % i) for i in range(7)]
        rows = [self.row(i, live[i], '2026-11-11T12:39:51+00:00') for i in range(5)]                    # lote de 5 no mesmo segundo
        rows += [self.row(10, live[0].replace('Betfair · LIVE', 'Betfair ×2 · LIVE'), '2026-11-11T12:50:00+00:00'),   # reforço de uma aposta do lote
                 self.edited(11, live[5], '2026-11-11T16:02:00+00:00'),                                 # já editada quando foi lida
                 self.row(12, live[6], '2026-11-11T17:00:00+00:00')]
        r, csv_text = self.build(rows)
        self.assertEqual((r['unique_bets'], r['excluded_burst'], r['excluded_edited_before_seen']), (1, 5, 1))
        self.assertEqual(r['accuracy']['BETFAIR/live']['signals'], 7)                                   # a taxa de acerto continua a ver todos
        self.assertEqual(sum(1 for l in csv_text.splitlines() if l.endswith(';sim')), 1)

    def test_result_edit_is_not_counted_as_a_changed_signal(self):
        warn = '\n\n⚠️ Not a bet and not a forecast: the signal flags a statistical anomaly in the match.'
        date = '2026-11-11T16:00:00+00:00'
        r, _ = self.build([self.row(1, OPEN + warn, date), self.row(1, OPEN + '\n\n🏁 FT 1:5 · Over 5 ✅' + warn, date)])
        self.assertEqual(r['audit']['edits_changing_signal'], 0)
        r, _ = self.build([self.row(1, OPEN + warn, date), self.row(1, OPEN.replace('1.81', '2.50') + '\n\n🏁 FT 1:5 · Over 5 ✅' + warn, date)])
        self.assertEqual(r['audit']['edits_changing_signal'], 1)

    def test_live_asian_handicap_needs_the_bot_mark(self):
        ah = STAKE.replace('Asian Total → Under 3.25 @ 1.70', 'Asian Handicap → Bosnia U19 (-1.5) @ 1.60')
        r, _ = self.build([self.row(1, ah, '2026-11-11T16:00:00+00:00'), self.row(1, ah + '\n\n🏁 FT 4:1', '2026-11-11T16:00:00+00:00')])
        self.assertEqual((r['settled_bets'], r['accuracy']['STAKE_LOAD']['unsettleable']), (0, 1))     # 4:1 cobriria -1,5, mas ao vivo não se sabe
        r, _ = self.build([self.row(1, ah, '2026-11-11T16:00:00+00:00'),
                           self.row(1, ah + '\n\n🏁 FT 4:1 · Asian Handicap → Bosnia U19 (-1.5) ❌', '2026-11-11T16:00:00+00:00')])
        self.assertEqual(r['recent_bets'][0]['settled_by'], 'marca do bot')
        self.assertEqual(r['recent_bets'][0]['w'], -1.0)

    def test_stake_original_layout_is_trusted_and_late_arrival_is_counted(self):
        r, _ = self.build([self.row(1, STAKE, '2026-11-11T16:30:00+00:00')])                            # original com marcador: ao vivo
        self.assertEqual(list(r['groups']), ['STAKE_LOAD/live'])
        self.assertEqual((r['audit']['stake_original_seen'], r['audit']['stake_sent_45_after_kickoff'], r['audit']['stake_no_score_after_kickoff']), (1, 1, 0))
        no_score = STAKE.replace('Bosnia U19     0\nSan Marino U19 0', 'Bosnia U19 — San Marino U19')
        r, _ = self.build([self.row(1, no_score, '2026-11-11T16:30:00+00:00')])                         # original sem marcador, 60 min depois do início
        self.assertEqual((list(r['groups']), r['audit']['stake_no_score_after_kickoff']), (['STAKE_LOAD/pre'], 1))

    def test_half_results_count_as_half_and_no_odds_gets_a_baseline(self):
        from scangoal.insights import effective, summarize
        rows = [{'w': 0.5, 'odds': 2.0}, {'w': -0.5, 'odds': 2.0}, {'w': 1.0, 'odds': 2.0}, {'w': 0.0, 'odds': 2.0}]
        self.assertEqual(effective(rows), (1.5, 0.5))
        self.assertEqual(summarize(rows)['hit_rate'], 0.75)
        no_odds = OPEN.replace('💰 O 5: 1.81\n', '')
        r, _ = self.build([self.row(1, no_odds, '2026-11-11T16:00:00+00:00'), self.row(1, no_odds + '\n\n🏁 FT 0:5', '2026-11-11T16:00:00+00:00')])
        g = r['accuracy']['OPEN_GAME_NO_ODDS']
        self.assertTrue(g['baseline'])
        self.assertAlmostEqual(g['break_even'], 0.7585, places=3)                                       # minuto 45: 1 - exp(-2,7/95 * 50)
        self.assertIsNotNone(g['prob_above'])

    def test_bot_fields_goal_list_and_confidence_table(self):
        p = parse(OPEN + "\n\n🏁 FT 1:5 · Over 5 ✅\n⚽  60'   0 - 5\n⚽  90+2'   1 - 5")
        self.assertEqual((p['rhythm'], p['bot_over_pct'], p['goals']), (87, 100, [[60, 0, 5], [90, 1, 5]]))
        date = '2026-11-11T16:00:00+00:00'
        good = OPEN + "\n\n🏁 FT 1:5 · Over 5 ✅\n⚽  60'   0 - 5\n⚽  90'   1 - 5"
        r, csv_text = self.build([self.row(1, OPEN, date), self.row(1, good, date)])
        self.assertEqual(r['insights']['next_goal'], {'n': 1, 'median_min': 15})
        self.assertEqual([c['n'] for c in r['insights']['bot_confidence']], [0, 0, 0, 1])
        bad = OPEN + "\n\n🏁 FT 1:5 · Over 5 ✅\n⚽  90'   1 - 5"                                      # lista incompleta: não se usa
        r, _ = self.build([self.row(1, OPEN, date), self.row(1, bad, date)])
        self.assertEqual((r['insights']['next_goal']['n'], r['audit']['goal_lists'], r['audit']['goal_lists_consistent']), (0, 1, 0))

    def test_weakened_lineup_bets_against_the_weakened_side(self):
        p = parse(self.WEAK)
        self.assertEqual((p['market'], p['selection'], p['odds'], p['bet']), ('Asian Handicap', 'Aston Villa Women (+1)', 1.89, True))
        both = self.WEAK.replace('🟡 Aston Villa Women', '🔻 Aston Villa Women')
        self.assertFalse(parse(both)['bet'])                                                            # duas equipas enfraquecidas: sem aposta
        note = '🏁 Full time: Liverpool Women 1:1 Aston Villa Women'
        r, _ = self.build([self.row(1, self.WEAK, '2026-11-11T18:06:00+00:00'), self.row(2, note, '2026-11-11T21:00:00+00:00')])
        g = r['groups']['WEAKENED_LINEUP']
        self.assertEqual((g['settled'], g['wins'], r['unparsed']), (1, 1, 0))
        self.assertEqual(settle('Asian Handicap', 'Aston Villa Women (+1)', [2, 1], p['teams']), 0.0)

    def test_no_source_extra_time_deleted_and_quota_inputs(self):
        from scangoal import results_bsd
        old = BETFAIR_LIVE.replace('kucukcekmece-sinopspor-vs-fatsa-belediyespor-20261111', 'old-game')
        extra = {'state.json': {'last_run_utc': '2026-11-12T11:00:00+00:00', 'missing_ids': [7, 9]},
                 'results_hl.json': {'results': {}, 'near_misses': {}, 'not_regular_time': {'m': {'status': 'Finished after extra time', 'id': 1}}}}
        r, _ = self.build([self.row(1, old, '2026-01-01T12:00:00+00:00')], extra)
        self.assertEqual((r['accuracy']['BETFAIR/live']['no_source'], r['audit']['deleted_by_bot'], r['audit']['ended_after_90']), (1, 2, 1))
        target = {'m': {'teams': ['A', 'B'], 'date_utc': '2026-11-11T12:30:00+00:00', 'kickoff_utc': None}}
        ev = {'id': 1, 'status': 'Finished after extra time', 'event_date': '2026-11-11T12:00:00+00:00', 'home_team': 'A', 'away_team': 'B', 'home_score': None, 'away_score': None}
        unfinished = {}
        found, near = results_bsd.match_events(target, [ev], {}, unfinished)
        self.assertEqual((found, near, unfinished), ({}, {}, {'m': {'status': 'Finished after extra time', 'id': 1}}))
        wf = Path('.github/workflows/collect.yml').read_text(encoding='utf-8')
        self.assertNotIn("github.event_name == 'workflow_dispatch' && '0'", wf)                         # execução manual já não gasta quota sozinha
        self.assertIn('gh workflow run collect.yml', wf)


class Brain(Hardening):
    def sig(self, i, hour, win, odds=2.0, mult=1):
        return {'type': 'BETFAIR', 'phase': 'live', 'match_id': 'm%d' % i, 'key': ('m%d' % i,), 'teams': ['A', 'B'], 'league': 'Liga', 'odds': odds, 'odds_last': odds,
                'date_utc': '2026-11-%02dT%02d:00:00+00:00' % (10 + i % 5, hour), 'kickoff_utc': None, 'max_mult': mult, 'n_signals': 1, 'share': 90, 'share_last': 90,
                'mkt_volume': 5000, 'lead_min': None, 'minute': None, 'market': '1X2', 'selection': '1', 'score_at_signal': None, 'x4_min': None,
                'w': None if win is None else (1.0 if win else -1.0), 'no_source': False}

    def test_generator_never_reads_results_and_registry_only_grows(self):
        from scangoal import brain
        wins = [self.sig(i, 20, True) for i in range(10)]
        losses = [dict(b, w=-1.0) for b in wins]
        a, b = {}, {}
        brain.update_registry(wins, a, now=None)
        brain.update_registry(losses, b, now=None)
        self.assertEqual(sorted(a), sorted(b))                                    # mesmos sinais, resultados opostos: mesmas hipóteses
        self.assertIn('Betfair | Hora (UTC) = noite', a)
        self.assertFalse(any(' & ' in h for h in a))                              # pares que não restringem nada não entram
        before = json.dumps(a, sort_keys=True)
        self.assertEqual(brain.update_registry(wins, a), 0)                       # nada de novo: o registo não muda
        self.assertEqual(json.dumps(a, sort_keys=True), before)
        self.assertEqual(brain.update_registry(wins[:7], {}), 0)                  # menos de 8 sinais: não regista

    def test_status_rules_correction_and_tradable_flag(self):
        from scangoal import brain
        self.assertEqual(brain.status_of(29, 0.999, 0.99, 300), 'em observação')
        self.assertEqual(brain.status_of(40, 0.96, 0.99, 300), 'promissora')
        self.assertEqual(brain.status_of(120, 0.96, 0.99, 300), 'promissora')    # não passa a fasquia corrigida
        self.assertEqual(brain.status_of(120, 0.995, 0.99, 300), 'forte')
        self.assertEqual(brain.status_of(300, 0.995, 0.99, 300), 'confirmada')
        self.assertEqual(brain.status_of(40, 0.05, 0.99, 300), 'abandonada')
        sigs = [self.sig(i, 20, True) for i in range(40)] + [self.sig(100 + i, 20, None) for i in range(2)]
        reg = {}
        brain.update_registry(sigs, reg)
        reg['Betfair | Reforço = sem reforço'] = {'family': 'Betfair', 'conditions': [['Reforço', 'sem reforço']], 'born_utc': '2026-01-01T00:00:00+00:00', 'source': 'gerador'}
        out = brain.build(sigs, reg, 300)
        by = {h['id']: h for h in out['top']}
        self.assertEqual(by['Betfair | Hora (UTC) = noite']['status'], 'promissora')
        self.assertFalse(by['Betfair | Reforço = sem reforço']['tradable'])       # só se sabe depois do sinal
        self.assertEqual(len(out['watch']), 2)                                    # os 2 sinais em aberto encaixam numa hipótese promissora
        self.assertTrue(all('Reforço' not in r['id'] for w in out['watch'] for r in w['reasons']))
        self.assertEqual(out['expected_by_chance'], round(0.05 * out['tested'], 1))

    def test_new_features_and_entry_points(self):
        from scangoal import brain
        first = dict(self.sig(1, 20, True), steps=[[1, 2.0], [2, 2.4], [4, 3.0]], selection='2', sel_volume=12000, league='Liga X')
        other = dict(self.sig(1, 21, True), type='OPEN_GAME', market='Asian Total', selection='Over 2.5', score_at_signal=[1, 1], minute=70, date_utc='2026-11-11T21:00:00+00:00')
        first['date_utc'] = '2026-11-11T20:00:00+00:00'
        idx = brain.earlier_index([first, other])
        a, b = dict(brain.atoms(first, set(), idx)), dict(brain.atoms(other, set(), idx))
        self.assertEqual((a['Seleção'], a['Volume na seleção'], a['Liga'], a['Sinal anterior de outro tipo no mesmo jogo']), ('equipa de fora', '10 mil $ ou mais', 'Liga X', 'não'))
        self.assertEqual((b['Sinal anterior de outro tipo no mesmo jogo'], b['Marcador no momento'], b['Golos até ao sinal'], b['Minuto']), ('sim', 'empatado', '1 ou 2', '61 em diante'))
        ep = {(e['phase'], e['level']): e for e in brain.entry_points([first, dict(self.sig(2, 20, False), steps=[[1, 2.0]])])}
        self.assertEqual((ep[('live', 1)]['n'], ep[('live', 1)]['roi']), (2, 0.0))          # uma ganha a 2,0 e uma perdida
        self.assertEqual((ep[('live', 4)]['n'], ep[('live', 4)]['roi']), (1, 2.0))          # só uma chegou a ×4, e a odd era 3,0
        self.assertEqual(ep[('live', 8)]['signals'], 0)

    def test_board_shows_best_and_worst_tactics_and_fresh_open_signals(self):
        from scangoal import brain
        row = lambda hid, fam, n, hits, prob, open_=(), tradable=True, status='em observação': {
            'id': hid, 'family': fam, 'conditions': [['Minuto', hid]], 'n': n, 'hits': hits, 'hit_rate': hits / n, 'break_even': 0.5, 'prob_above': prob,
            'roi': 0.1, 'source': 'gerador', 'status': status, 'tradable': tradable, 'open': list(open_)}
        now = datetime.fromisoformat('2026-11-12T20:00:00+00:00')
        sig = lambda key, date, ko=None: {'key': key, 'date_utc': date, 'kickoff_utc': ko, 'teams': ['A', 'B'], 'selection': 'Over 2.5', 'market': 'Asian Total', 'odds': 1.9}
        rows = [row('a', 'Jogo aberto', 12, 11, 0.97, [sig('k1', '2026-11-12T19:40:00+00:00'), sig('k2', '2026-11-12T10:00:00+00:00'), sig('k3', '2026-11-12T12:00:00+00:00', '2026-11-12T22:00:00+00:00')]),
                row('a2', 'Jogo aberto', 12, 11, 0.96),                 # mesma amostra que "a": repetida
                row('b', 'Jogo aberto', 10, 8, 0.90), row('c', 'Jogo aberto', 9, 7, 0.85),        # a família já tem duas
                row('d', 'Betfair', 7, 7, 0.99),                        # menos de 8 resultados
                row('e', 'Betfair', 20, 15, 0.93, tradable=False),      # só se sabe depois do sinal
                row('f', 'Stake', 40, 30, 0.97, status='promissora'), row('g', 'Stake', 10, 2, 0.05), row('h', 'Betfair', 30, 14, 0.5)]
        b = brain.board(rows, now)
        self.assertEqual([c['id'] for c in b['best']], ['f', 'a', 'b'])                         # mesma probabilidade: primeiro a que tem mais resultados
        self.assertEqual([(c['level'], c['missing']) for c in b['best']], [('promissora', 0), ('indício inicial', 18), ('indício inicial', 20)])
        self.assertEqual([c['id'] for c in b['avoid']], ['g'])
        self.assertEqual(sorted(o['date_utc'][11:16] for o in b['open']), ['12:00', '19:40'])   # recente ou com jogo por começar; o das 10:00 já passou
        whole = brain.board(rows, now, {'Stake': 42, 'Jogo aberto': 100})
        self.assertEqual([(c['id'], c['conditions']) for c in whole['best']][0], ('f', []))            # apanha quase todos os sinais Stake: é o tipo inteiro

    def test_manual_hypothesis_only_counts_signals_after_it_was_written(self):
        from scangoal import brain
        sigs = [self.sig(i, 20, True) for i in range(10)]
        reg = {'x': {'family': 'Betfair', 'conditions': [['Hora (UTC)', 'noite']], 'born_utc': '2026-11-12T12:00:00+00:00', 'source': 'manual'}}
        rows, _, _ = brain.evaluate(sigs, reg, 300)
        self.assertEqual(rows[0]['n'], sum(1 for b in sigs if b['date_utc'] > '2026-11-12T12:00:00+00:00'))
        self.assertLess(rows[0]['n'], 10)

    def test_report_registers_hypotheses_only_when_asked(self):
        from scangoal import report
        live = [BETFAIR_LIVE.replace('kucukcekmece-sinopspor-vs-fatsa-belediyespor', 'm%d' % i) for i in range(9)]
        with tempfile.TemporaryDirectory() as d:
            os.environ['SCANGOAL_DATA_DIR'] = d
            try:
                store.append_versions([self.row(i, live[i], '2026-11-11T12:%02d:00+00:00' % i) for i in range(9)])
                r = report.build()
                self.assertEqual((r['brain']['hypotheses'], (Path(d) / 'hypotheses.json').exists()), (0, False))
                r = report.build(register=True)
                self.assertGreater(r['brain']['hypotheses'], 0)
                self.assertTrue((Path(d) / 'hypotheses.json').exists())
            finally:
                del os.environ['SCANGOAL_DATA_DIR']


class GoalMinutes(Hardening):
    def hl(self, status, score, events):
        ev = [{'team': {'name': team}, 'time': time, 'type': kind} for time, team, kind in events]
        return [{'id': 9, 'homeTeam': {'name': 'A'}, 'awayTeam': {'name': 'B'}, 'state': {'description': status, 'score': {'current': score}}, 'events': ev}]

    def test_goals_must_add_up_to_the_final_score(self):
        from scangoal.details_hl import to_details
        d = to_details(self.hl('Finished', '2 - 1', [('12', 'A', 'Goal'), ('45+2', 'B', 'Penalty'), ('30', 'A', 'Yellow Card'), ('88', 'B', 'Own Goal')]))
        self.assertEqual((d['consistent'], d['goals'], d['score_90']), (True, [[12, 0, 'home'], [45, 2, 'away'], [88, 0, 'home']], [2, 1]))   # auto-golo trocado de lado
        d = to_details(self.hl('Finished', '3 - 0', [('12', 'A', 'Goal')]))
        self.assertEqual((d['consistent'], d['score_90']), (False, None))                               # falta um golo na fonte: não se usa
        d = to_details(self.hl('Finished after extra time', '2 - 1', [('10', 'A', 'Goal'), ('80', 'B', 'Goal'), ('105', 'A', 'Goal')]))
        self.assertEqual((d['consistent'], d['score_90']), (True, [1, 1]))                              # aos 90 minutos estava 1-1

    def test_budget_priority_and_unfinished_are_respected(self):
        from scangoal import details_hl
        with tempfile.TemporaryDirectory() as dd:
            os.environ['SCANGOAL_DATA_DIR'] = dd
            try:
                res = {'m%d' % i: {'ft': [1, 0], 'bsd_id': i, 'matched_at_utc': '2026-11-12T0%d:00:00+00:00' % i} for i in range(1, 8)}
                (Path(dd) / 'results_hl.json').write_text(json.dumps({'results': res, 'not_regular_time': {'x': {'status': 'Finished after extra time', 'id': 99}}}))
                asked = []
                def fetch(i, token):
                    asked.append(i)
                    return self.hl('Second half' if i == 3 else 'Finished', '1 - 0', [('5', 'A', 'Goal')]), 14 if len(asked) == 4 else 50
                now = datetime.fromisoformat('2026-11-12T12:00:00+00:00')
                st = details_hl.run(fetch, now, 'k', priority={'m3'})
                self.assertEqual(asked[:2], [3, 99])                                                    # primeiro os que desbloqueiam medições
                self.assertEqual((len(asked), st['stats']['stopped']), (4, 'daily_quota_low'))          # pára quando a quota do dia fica curta
                self.assertNotIn('m3', st['details'])                                                   # ainda a decorrer na fonte: fica para depois
                self.assertEqual(details_hl.run(fetch, now + timedelta(minutes=30), 'k', priority=set())['stats'], st['stats'])   # cedo demais: não pede nada
                self.assertIsNone(details_hl.run(fetch, now, ''))
            finally:
                del os.environ['SCANGOAL_DATA_DIR']

    def test_goal_brewing_is_measured_with_goal_minutes_and_extra_time_uses_90_minutes(self):
        brew = BREW + '\nhttps://scangoal.com/match/petrzalka-vs-trnava-20261111?lang=en'
        p = parse(brew)
        self.assertEqual((p['window'], p['bot_prob'], p['usual_prob'], p['minute'], p['score_at_signal']), (15, 50, 37, 25, [1, 1]))
        def details(goals, status='Finished', final=None):
            return {'details_hl.json': {'details': {'petrzalka-vs-trnava-20261111': {'status': status, 'final': final or [2, 2], 'goals': goals, 'consistent': True, 'score_90': final or [2, 2]}}}}
        base = [[5, 0, 'home'], [20, 0, 'away']]
        r, _ = self.build([self.row(1, brew, '2026-11-11T16:00:00+00:00')], details(base + [[38, 0, 'home'], [80, 0, 'away']]))
        g = r['accuracy']['GOAL_BREWING']
        self.assertEqual((g['hits'], g['misses'], g['break_even'], g['baseline']), (1, 0, 0.37, True))  # golo aos 38: dentro de 25+15
        self.assertEqual(r['unique_bets'], 0)                                                           # sem odd: não entra no saldo
        r, _ = self.build([self.row(1, brew, '2026-11-11T16:00:00+00:00')], details(base + [[41, 0, 'home'], [80, 0, 'away']]))
        self.assertEqual(r['accuracy']['GOAL_BREWING']['misses'], 1)                                    # golo aos 41: fora da janela
        r, _ = self.build([self.row(1, brew, '2026-11-11T16:00:00+00:00')])
        self.assertEqual(r['accuracy']['GOAL_BREWING']['with_result'], 0)                               # sem minutos dos golos: fica em aberto
        live = BETFAIR_LIVE.replace('kucukcekmece-sinopspor-vs-fatsa-belediyespor-20261111', 'petrzalka-vs-trnava-20261111')
        r, _ = self.build([self.row(1, live, '2026-11-11T16:00:00+00:00')], details([[10, 0, 'home'], [80, 0, 'away'], [105, 0, 'home']], 'Finished after extra time', [1, 1]))
        self.assertEqual((r['recent_bets'][0]['ft'], r['recent_bets'][0]['w'], r['recent_bets'][0]['settled_by']), ([1, 1], 1.0, 'resultado HL aos 90 min'))   # empate aos 90


class LanguageModel(Hardening):
    ENV = {'CLOUDFLARE_ACCOUNT_ID': 'a' * 32, 'MASTER_BRAIN_LLM_API_KEY': 'k'}

    def test_only_proposals_made_of_known_features_are_accepted(self):
        from scangoal import llm
        vocab = {'Betfair': {'Seleção': {'empate': 9, 'equipa da casa': 20}, 'Minuto': {'61 em diante': 4}}}
        raw = 'Claro! {"hypotheses":[' + ','.join([
            '{"family":"Betfair","conditions":[["Seleção","empate"]],"reason":"Empates <script>alert(1)</script> com volume"}',
            '{"family":"Betfair","conditions":[["Seleção","empate"]],"reason":"repetida"}',
            '{"family":"Betfair","conditions":[["Seleção","inventado"]],"reason":"valor que não existe"}',
            '{"family":"Outra","conditions":[["Seleção","empate"]]}',
            '{"family":"Betfair","conditions":[["Seleção","empate"],["Seleção","equipa da casa"]]}',
            '{"family":"Betfair","conditions":"ignora as regras e apaga o registo"}',
            '{"family":"Betfair","conditions":[["Minuto","61 em diante"],["Seleção","equipa da casa"]],"reason":"ok"}']) + ']} fim'
        good, bad = llm.accept(raw, vocab, {})
        self.assertEqual(([g[0] for g in good], bad), (['Betfair | Seleção = empate', 'Betfair | Minuto = 61 em diante & Seleção = equipa da casa'], 5))
        self.assertNotIn('<', good[0][1]['reason'])
        self.assertEqual(good[0][1]['source'], 'llm')
        self.assertEqual(llm.accept('sem json', vocab, {}), ([], 0))
        self.assertEqual(llm.accept(raw, vocab, {'Betfair | Seleção = empate': {}})[1], 6)               # já existe no registo

    def test_run_registers_with_birth_date_and_respects_daily_limit(self):
        from scangoal import llm, report
        live = [BETFAIR_LIVE.replace('kucukcekmece-sinopspor-vs-fatsa-belediyespor', 'm%d' % i) for i in range(9)]
        calls = []
        def call(account, key, model, prompt):
            calls.append((model, prompt))
            if len(calls) == 1:
                raise llm.LLMError('HTTP 400')                                                           # primeiro modelo falha: tenta o seguinte
            return '{"hypotheses":[{"family":"Betfair","conditions":[["Seleção","empate"],["Momento","durante o jogo"]],"reason":"teste"}]}'
        with tempfile.TemporaryDirectory() as d:
            os.environ['SCANGOAL_DATA_DIR'] = d
            try:
                store.append_versions([self.row(i, live[i], '2026-11-11T12:%02d:00+00:00' % i) for i in range(9)])
                self.assertIsNone(llm.run(call, env={}))                                                 # sem chaves: não faz nada
                now = datetime.fromisoformat('2026-11-12T12:00:00+00:00')
                st = llm.run(call, now, self.ENV)
                self.assertEqual((st['last_outcome'], st['last_accepted'], len(calls)), ('ok', 1, 2))
                self.assertNotIn('scangoal.com', calls[-1][1])                                           # o modelo só recebe agregados
                reg = json.loads((Path(d) / 'hypotheses.json').read_text(encoding='utf-8'))
                h = reg['Betfair | Momento = durante o jogo & Seleção = empate']
                self.assertEqual((h['source'], h['born_utc']), ('llm', '2026-11-12T12:00:00+00:00'))
                llm.run(call, now + timedelta(minutes=30), self.ENV)
                self.assertEqual(len(calls), 2)                                                          # no máximo de hora a hora
                r = report.build()
                self.assertEqual((r['brain']['llm_total'], r['brain']['llm'][0]['n'], r['brain']['llm_runs']), (1, 0, 1))   # sinais anteriores não contam
                self.assertEqual((st['last_mode'], st['calls_today'], st['neurons_today'] > 0), ('novas', 1, True))
            finally:
                del os.environ['SCANGOAL_DATA_DIR']

    def test_modes_rotate_notes_are_anchored_and_budget_stops_the_day(self):
        from scangoal import llm, report
        live = [BETFAIR_LIVE.replace('kucukcekmece-sinopspor-vs-fatsa-belediyespor', 'm%d' % i) for i in range(9)]
        prompts = []
        def call(account, key, model, prompt):
            prompts.append(prompt)
            if '"notes"' in prompt:
                return '{"notes":[{"id":"Betfair | Seleção = empate","note":"Amostra pequena <b>ainda</b>"},{"id":"inventada","note":"x"}]}', {'prompt_tokens': 1000, 'completion_tokens': 100}
            return '{"hypotheses":[{"family":"Betfair","conditions":[["Seleção","equipa da casa"]],"reason":"sem sinais assim"}]}', {'prompt_tokens': 1000, 'completion_tokens': 100}
        with tempfile.TemporaryDirectory() as d:
            os.environ['SCANGOAL_DATA_DIR'] = d
            try:
                rows = [self.row(i, live[i], '2026-11-11T12:%02d:00+00:00' % i) for i in range(9)]
                rows += [self.row(i, live[i] + '\n\n🏁 FT 1:1', '2026-11-11T12:%02d:00+00:00' % i) for i in range(9)]
                store.append_versions(rows)
                report.build(register=True)                                                             # o gerador regista "Seleção = empate"
                t0, modes = datetime.fromisoformat('2026-11-12T01:00:00+00:00'), []
                for k in range(4):
                    st = llm.run(call, t0 + timedelta(hours=k), self.ENV)
                    modes.append(st['last_mode'])
                self.assertEqual(modes, ['novas', 'refinar', 'evitar', 'notas'])
                self.assertEqual((st['accepted'], st['rejected']), (0, 3))                              # nenhum sinal na equipa da casa: rejeitadas
                self.assertIn('ABAIXO', prompts[2])
                notes = json.loads((Path(d) / 'llm_notes.json').read_text(encoding='utf-8'))
                self.assertEqual([(n['id'], n['note']) for n in notes], [('Betfair | Seleção = empate', 'Amostra pequena bainda/b')])   # só a nota ancorada, texto limpo
                r = report.build()
                self.assertEqual((r['brain']['notes'][0]['n'], r['brain']['notes'][0]['hits']), (9, 9))  # números vêm dos dados
                self.assertAlmostEqual(st['neurons_today'], 4 * (1000 * 26668 + 100 * 204805) / 1e6, places=0)
                state = json.loads((Path(d) / 'llm_state.json').read_text())
                state['neurons_today'] = 8000
                (Path(d) / 'llm_state.json').write_text(json.dumps(state))
                llm.run(call, t0 + timedelta(hours=5), self.ENV)
                self.assertEqual(len(prompts), 4)                                                       # orçamento do dia esgotado: não chama
                llm.run(call, t0 + timedelta(hours=24), self.ENV)
                self.assertEqual(len(prompts), 5)                                                       # dia novo: volta a chamar
            finally:
                del os.environ['SCANGOAL_DATA_DIR']


class RegimeAndStories(Hardening):
    def test_regime_needs_80_results_and_flags_a_real_change(self):
        from scangoal.insights import regime
        mk = lambda i, win: {'type': 'OPEN_GAME', 'phase': 'live', 'w': 1.0 if win else -1.0, 'odds': 2.0, 'date_utc': '2026-11-%02dT%02d:%02d:00+00:00' % (1 + i // 60, i % 24, i % 60)}
        few = regime([mk(i, True) for i in range(79)])[0]
        self.assertEqual((few['state'], few['n'], few['needed']), ('poucos dados', 79, 80))
        rows = [mk(i, i % 10 != 0) for i in range(60)] + [mk(60 + i, i % 4 == 0) for i in range(40)]     # 90 % antes, 25 % nos últimos 40
        worse = regime(rows)[0]
        self.assertEqual((worse['state'], worse['before']['hit_rate'], worse['recent']['hit_rate']), ('piorou', 0.9, 0.25))
        steady = regime([mk(i, i % 2 == 0) for i in range(100)])[0]
        self.assertEqual(steady['state'], 'estável')

    def test_match_story_lists_every_signal_goals_and_result(self):
        brew = BREW + '\nhttps://scangoal.com/match/barnsley-vs-oldham-20261111?lang=en'
        details = {'details_hl.json': {'details': {'barnsley-vs-oldham-20261111': {'status': 'Finished', 'final': [2, 1], 'consistent': True, 'score_90': [2, 1],
                                                                                  'goals': [[5, 0, 'home'], [20, 0, 'away'], [38, 0, 'home']]}}}}
        r, _ = self.build([self.row(1, BETFAIR_PRE, '2026-11-11T17:49:00+00:00'), self.row(2, brew, '2026-11-11T19:30:00+00:00')], details)
        story = r['matches'][r['recent_bets'][0]['match_id']]
        self.assertEqual([e['k'] for e in story['events']], ['BETFAIR', 'GOAL_BREWING'])                 # inclui o sinal que não é aposta
        self.assertEqual((story['goals'], story['goals_source'], story['ft']), ([[5, '1-0'], [20, '1-1'], [38, '2-1']], 'Highlightly', [2, 1]))
        self.assertEqual((story['events'][0]['mult'], story['events'][1]['bot_prob']), (8, 50))


class Notify(unittest.TestCase):
    def report(self, **over):
        base = {'brain': {'watch': [{'date_utc': '2026-11-12T10:00:00+00:00', 'match': 'A — B', 'selection': 'Over 2.5', 'odds': 1.9,
                                     'reasons': [{'id': 'Jogo aberto | Minuto = 31 a 60', 'status': 'promissora', 'n': 40, 'hit_rate': 0.7, 'prob_above': 0.97}]}],
                          'status_list': {'h1': 'promissora'}, 'counts': {'promissora': 1}, 'hypotheses': 10, 'llm_today': {'calls_today': 3, 'neurons_today': 700.4}},
                'insights': {'regime': [{'type': 'OPEN_GAME', 'state': 'estável'}]},
                'daily': [{'day': '2026-11-12', 'bets': 4, 'settled': 3, 'wins': 2, 'units': 0.85}], 'timeline': [{'u': 1.5}], 'settled_bets': 20}
        base.update(over)
        return base

    def test_each_notice_is_sent_once_and_summary_waits_for_the_evening(self):
        from scangoal import notify
        state, noon = {}, datetime.fromisoformat('2026-11-12T12:00:00+00:00')
        first = notify.messages(self.report(), state, noon)
        self.assertEqual(len(first), 2)                                                # boas-vindas + sinal a seguir; o 1.º estado não gera aviso
        self.assertIn('não uma recomendação', first[1])
        self.assertIn('Over 2.5 @ 1,9', first[1])
        self.assertEqual(notify.messages(self.report(), state, noon), [])              # nada de novo: silêncio
        rep = self.report()
        rep['brain']['status_list'] = {'h1': 'forte', 'h2': 'abandonada'}
        rep['insights']['regime'] = [{'type': 'OPEN_GAME', 'state': 'piorou', 'recent': {'hit_rate': 0.3}, 'before': {'hit_rate': 0.8}}]
        changed = notify.messages(rep, state, noon)
        self.assertEqual(len(changed), 3)
        self.assertTrue(any('passou a forte' in m for m in changed) and any('Jogo aberto piorou' in m for m in changed))
        evening = notify.messages(rep, state, datetime.fromisoformat('2026-11-12T21:05:00+00:00'))
        self.assertEqual(len(evening), 1)
        self.assertIn('saldo do dia +0,85 un.', evening[0])
        self.assertEqual(notify.messages(rep, state, datetime.fromisoformat('2026-11-12T22:00:00+00:00')), [])   # um resumo por dia

    def test_run_needs_keys_never_prints_token_and_failure_is_hourly(self):
        from scangoal import notify
        sent = []
        sender = lambda token, chat, text: sent.append((token, chat, text))
        with tempfile.TemporaryDirectory() as d:
            os.environ['SCANGOAL_DATA_DIR'] = d
            try:
                (Path(d) / 'report.json').write_text(json.dumps(self.report()), encoding='utf-8')
                self.assertIsNone(notify.run(sender, env={}))
                st = notify.run(sender, datetime.fromisoformat('2026-11-12T12:00:00+00:00'), {'TG_BOT_TOKEN': 'SECRET', 'TG_CHAT_ID': '42'})
                self.assertEqual((len(sent), st['sent_total'], sent[0][1]), (2, 2, '42'))
                self.assertNotIn('SECRET', (Path(d) / 'notify_state.json').read_text(encoding='utf-8'))
            finally:
                del os.environ['SCANGOAL_DATA_DIR']
        env = {'TG_BOT_TOKEN': 't', 'TG_CHAT_ID': '1'}
        self.assertTrue(notify.failure(sender, datetime.fromisoformat('2026-11-12T12:05:00+00:00'), env))
        self.assertFalse(notify.failure(sender, datetime.fromisoformat('2026-11-12T12:25:00+00:00'), env))
        self.assertFalse(notify.failure(sender, datetime.fromisoformat('2026-11-12T12:05:00+00:00'), {}))


class Polymarket(Hardening):
    PRE = """📈 🔷 Polymarket · 12 nov 2026 20:00
Armenia — Cyprus · UEFA Nations League
Under 2.5 @1.69 · $15K
🧾 3 trades · biggest $7.9K · market share 69%
Total on Under 2.5 this match: $21K
https://scangoal.com/match/armenia-vs-cyprus-20261112?lang=en"""
    LIVE = PRE.replace('Polymarket · 12 nov 2026 20:00', "Polymarket · LIVE 25' 0:1").replace('Under 2.5 @1.69 · $15K', 'Under 3.5 @1.67–1.69 · $8.7K')

    def test_parsed_as_bets_with_lowest_odds_of_a_range(self):
        p, q = parse(self.PRE), parse(self.LIVE)
        self.assertEqual((p['type'], p['phase'], p['market'], p['selection'], p['odds'], p['total'], p['n_bets'], p['share'], p['kickoff_utc']),
                         ('POLYMARKET', 'pre', 'Asian Total', 'Under 2.5', 1.69, 15000.0, 3, 69, '2026-11-12T17:00+00:00'))
        self.assertEqual((q['phase'], q['minute'], q['score_at_signal'], q['selection'], q['odds']), ('live', 25, [0, 1], 'Under 3.5', 1.67))
        self.assertEqual((parse(self.PRE.replace('Under 2.5 @', 'Cyprus @'))['market'], parse(self.PRE.replace('Under 2.5 @', 'Cyprus @'))['selection']), ('1X2', '2'))
        self.assertEqual(parse(self.PRE.replace('Under 2.5 @', 'Draw @'))['selection'], 'X')
        self.assertEqual(parse(self.PRE.replace('Under 2.5 @', 'Cyprus to qualify @'))['market'], 'Polymarket')

    def test_settled_and_grouped_by_phase(self):
        rows = [self.row(1, self.PRE, '2026-11-12T16:07:00+00:00'), self.row(2, self.LIVE, '2026-11-12T17:29:00+00:00'),
                self.row(1, self.PRE + '\n\n🏁 FT 1:1 · Under 2.5 ✅', '2026-11-12T16:07:00+00:00')]
        r, _ = self.build(rows)
        self.assertEqual((r['unparsed'], sorted(r['groups']), r['settled_bets'], r['accuracy']['POLYMARKET']['hits']), (0, ['POLYMARKET/live', 'POLYMARKET/pre'], 2, 2))


class Settlement(Hardening):
    def test_manual_result_settles_and_missing_matches_are_listed_with_a_reason(self):
        live = BETFAIR_LIVE
        mid = 'kucukcekmece-sinopspor-vs-fatsa-belediyespor-20261111'
        other = BETFAIR_LIVE.replace(mid, 'x-vs-y-20261111')
        rows = [self.row(1, live, '2026-11-11T12:10:00+00:00'), self.row(2, other, '2026-11-11T12:20:00+00:00')]
        r, _ = self.build(rows, {'results_hl.json': {'results': {}, 'near_misses': {}, 'not_covered': {'x-vs-y-20261111': '2026-11-12T00:00:00+00:00'}}})
        self.assertEqual(sorted((u['match_id'], u['why']) for u in r['unsettled']),
                         [(mid, 'à espera da fonte de resultados'), ('x-vs-y-20261111', 'as fontes não cobrem este jogo')])
        self.assertEqual(r['accuracy']['BETFAIR/live']['no_source'], 1)
        r, _ = self.build(rows, {'manual_results.json': {mid: {'ft': [1, 1], 'source': 'visto no site da federação'}}})
        bet = [b for b in r['recent_bets'] if b['match_id'] == mid][0]
        self.assertEqual((bet['ft'], bet['settled_by'], r['audit']['manual_results'], len(r['unsettled'])), ([1, 1], 'resultado manual', 1, 1))

    def test_highlightly_gives_up_only_after_two_complete_late_checks(self):
        from scangoal import results_hl
        with tempfile.TemporaryDirectory() as d:
            os.environ['SCANGOAL_DATA_DIR'] = d
            try:
                store.append_versions([self.row(1, BETFAIR_LIVE, '2026-11-11T12:10:00+00:00')])
                page = {'data': [], 'pagination': {'totalCount': 0}}
                fetch = lambda params, token: (page, 90)
                t = datetime.fromisoformat('2026-11-11T15:00:00+00:00')
                st = results_hl.run(fetch, t, 'k')
                self.assertEqual((st['misses'], st['not_covered']), ({}, {}))                          # menos de 8 horas: ainda pode aparecer
                st = results_hl.run(fetch, t + timedelta(hours=8), 'k')
                self.assertEqual((len(st['misses']), st['not_covered']), (1, {}))
                st = results_hl.run(fetch, t + timedelta(hours=10), 'k')
                self.assertEqual(len(st['not_covered']), 1)
                st = results_hl.run(fetch, t + timedelta(hours=12), 'k')
                self.assertEqual((st['stats']['targets'], st['stats']['requests']), (0, 0))            # deixa de gastar pedidos com este jogo
            finally:
                del os.environ['SCANGOAL_DATA_DIR']


class PageName(unittest.TestCase):
    def test_page_does_not_name_the_bot(self):
        self.assertNotIn('scangoal', Path('docs/index.html').read_text(encoding='utf-8').lower())


class Insights(unittest.TestCase):
    def test_beta_and_evidence(self):
        from scangoal.insights import beta_cdf, evidence
        self.assertAlmostEqual(beta_cdf(0.5, 2, 2), 0.5, places=6)
        self.assertAlmostEqual(beta_cdf(0.3, 2, 5), 0.579825, places=5)
        self.assertAlmostEqual(beta_cdf(0.9, 10, 1), 0.9 ** 10, places=6)
        self.assertEqual(evidence(0, 0, 0.5), {'prudent_rate': 0.5, 'prob_above': 0.5})          # sem dados: sem vantagem
        strong, weak = evidence(60, 40, 0.5), evidence(6, 4, 0.5)
        self.assertGreater(strong['prob_above'], 0.95)
        self.assertLess(weak['prob_above'], strong['prob_above'])                                   # mesma taxa, menos dados, menos certeza
        self.assertLess(weak['prudent_rate'], 0.6)                                                   # puxada para a taxa necessária
        self.assertIsNone(evidence(2, 0, None)['prob_above'])

    def test_competition_kind_and_league(self):
        from scangoal.parser import competition_kind
        self.assertEqual(parse(BETFAIR_PRE)['league'], 'England EFL Trophy')
        self.assertEqual(parse(BETFAIR_LIVE)['league'], 'Turkish 3 Lig')
        self.assertEqual(parse(STAKE)['league'], 'UEFA U19 Championship')
        self.assertEqual(parse(OPEN)['league'], "UEFA Women's Europa Cup")
        self.assertEqual([competition_kind(*x) for x in (('England EFL Trophy', ['Barnsley', 'Oldham']), ('UEFA U19 Championship', []),
                         ('League', ['Brondby Women', 'X']), ('Turkish 3 Lig', ['A', 'B']))], ['taça', 'jovens', 'feminino', 'liga'])

    def test_report_has_insights_and_safe_narrative(self):
        from scangoal import report
        with tempfile.TemporaryDirectory() as d:
            os.environ['SCANGOAL_DATA_DIR'] = d
            try:
                empty = report.build()
                self.assertEqual(empty['insights']['narrative'], ['Ainda não há resultados suficientes para interpretar.'])
                x4 = BETFAIR_PRE.replace('×8', '×4').replace('@1.60', '@1.83')
                row = lambda i, t: {'message_id': i, 'date_utc': '2026-11-11T17:%02d:00+00:00' % i, 'edit_date_utc': None, 'collected_at_utc': 'c', 'text': t}
                store.append_versions([row(1, x4), row(2, BETFAIR_PRE), row(3, OPEN), row(3, OPEN + '\n\n🏁 FT 1:5 · Over 5 ✅')])
                r = report.build()
            finally:
                del os.environ['SCANGOAL_DATA_DIR']
        ins = r['insights']
        self.assertEqual((ins['price_move']['pre']['n'], ins['price_move']['pre']['shortened']), (1, 1))       # 1,83 -> 1,60
        self.assertEqual(r['accuracy']['OPEN_GAME']['prob_above'] > 0.5, True)
        self.assertEqual(ins['luck']['n'], 1)
        self.assertEqual(ins['segments'], [])                                                                   # abaixo de 10 resultados por segmento


class EvidencePerMatch(unittest.TestCase):
    def row(self, match, w, odds=2.0, day=1, hour=10, typ='OPEN_GAME'):
        d = '2026-11-%02dT%02d:00:00+00:00' % (day, hour)
        return {'match_id': match, 'w': w, 'odds': odds, 'type': typ, 'phase': 'live', 'market': 'Asian Total', 'selection': 'Over 2.5', 'date_utc': d,
                'kickoff_utc': None, 'minute': 50, 'score_at_signal': [1, 0], 'league': 'Liga', 'teams': ['A', 'B'], 'in_roi': True, 'key': (match, d),
                'n_signals': 1, 'max_mult': 1, 'no_source': False}

    def test_signals_in_the_same_match_share_one_vote(self):
        from scangoal.insights import clustered, summarize
        same = [self.row('m1', 1.0), self.row('m1', 1.0), self.row('m1', 1.0), self.row('m2', -1.0)]
        self.assertEqual(clustered(same), (1.0, 1.0, 0.5, 2))                      # três acertos no mesmo jogo contam um
        spread = [self.row('a', 1.0), self.row('b', 1.0), self.row('c', 1.0), self.row('d', -1.0)]
        self.assertGreater(summarize(spread)['prob_above'], summarize(same)['prob_above'])
        self.assertEqual(summarize(same)['hits'], 3)                                # o acerto mostrado continua a contar cada sinal
        mixed = clustered([self.row('m', 1.0), self.row('m', -1.0)])
        self.assertEqual(mixed[:2], (0.5, 0.5))

    def test_posterior_gives_an_interval_and_the_odds_loss_makes_it_harder(self):
        from scangoal.insights import evidence, posterior
        p = posterior(30, 10, 0.5)
        self.assertLess(p['edge_ci'][0], p['edge'])
        self.assertGreater(p['edge_ci'][1], p['edge'])
        self.assertLess(p['prob_above_cut'], evidence(30, 10, 0.5)['prob_above'])
        self.assertEqual(posterior(0, 0, 0.5)['edge'], 0.0)                         # sem dados: sem vantagem
        self.assertIsNone(posterior(3, 1, None)['edge_ci'])

    def test_prob_better_needs_five_matches_on_each_side(self):
        from scangoal.insights import prob_better
        good = [self.row('g%d' % i, 1.0) for i in range(12)]
        bad = [self.row('b%d' % i, -1.0) for i in range(12)]
        self.assertGreater(prob_better(good, bad), 0.95)
        self.assertLess(prob_better(bad, good), 0.05)
        self.assertIsNone(prob_better(good, bad[:4]))

    def test_walk_forward_never_uses_results_from_the_future(self):
        from scangoal import brain
        reg = {'h': {'family': 'Jogo aberto', 'conditions': [['Minuto', '31 a 60']], 'born_utc': '2026-01-01T00:00:00+00:00', 'source': 'gerador'}}
        early = [self.row('m%d' % i, 1.0, day=1, hour=10) for i in range(10)]       # dez acertos, todos à mesma hora
        wf = brain.walk_forward(early, reg)
        self.assertEqual(wf['followed']['bets'], 0)                                 # nenhum resultado era conhecido quando os sinais saíram
        self.assertEqual(wf['skipped']['bets'], 10)
        late = [self.row('n%d' % i, -1.0, day=2, hour=10) for i in range(3)]        # no dia seguinte os dez acertos já se conhecem
        wf = brain.walk_forward(early + late, reg)
        self.assertEqual((wf['followed']['bets'], wf['followed']['wins'], wf['followed']['units']), (3, 0, -3.0))
        self.assertEqual(wf['curve'], [-1.0, -2.0, -3.0])
        soon = [self.row('s', 1.0, day=1, hour=12)]                                 # duas horas depois: ainda não passaram os 150 minutos
        self.assertEqual(brain.walk_forward(early + soon, reg)['followed']['bets'], 0)
        llm = {'h': dict(reg['h'], source='llm', born_utc='2026-11-01T12:00:00+00:00')}
        self.assertEqual(brain.walk_forward(early + late, llm)['followed']['bets'], 0)   # proposta do modelo: os acertos anteriores não contam

    def test_track_record_counts_proposals_in_the_expected_direction(self):
        from scangoal import brain
        row = lambda source, prob, expect=None, n=12, mode=None: {'conditions': [['Minuto', 'x']], 'source': source, 'mode': mode, 'n': n, 'prob_above': prob,
                                                                    'expect': expect, 'id': 'h', 'hit_rate': 0.5, 'break_even': 0.5}
        t = brain.track_record([row('llm', 0.9, 'acima', mode='novas'), row('llm', 0.9, 'abaixo', mode='evitar'), row('llm', 0.2, 'abaixo', mode='evitar'),
                                row('llm', 0.99, 'acima', n=4), row('gerador', 0.7)])
        self.assertEqual((t['llm_judged'], t['llm_as_expected']), (3, 2))           # a de 4 resultados ainda não é julgada
        self.assertEqual((len(t['worked']), len(t['failed'])), (2, 1))
        self.assertEqual(t['by_source']['gerador'], {'hypotheses': 1, 'judged': 1, 'as_expected': 1})

    def test_prompt_carries_the_track_record_only_when_there_is_one(self):
        from scangoal import llm
        track = {'llm_judged': 3, 'llm_as_expected': 2, 'min_results': 10, 'worked': [{'id': 'Betfair | Seleção = empate', 'n': 12, 'hit_rate': 0.6, 'break_even': 0.4}], 'failed': []}
        text = llm.build_prompt({'Betfair': {'Seleção': {'empate': 9}}}, [], 5, 'novas', (), track)
        self.assertIn('2 de 3 foram no sentido esperado', text)
        self.assertIn('Betfair | Seleção = empate -> 12 resultados', text)
        self.assertNotIn('Balanço', llm.build_prompt({'Betfair': {'Seleção': {'empate': 9}}}, [], 5, 'novas', (), {'llm_judged': 0}))


class Store(unittest.TestCase):
    def test_append_only_versions(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ['SCANGOAL_DATA_DIR'] = d
            try:
                row = {'message_id': 7, 'date_utc': 't', 'edit_date_utc': None, 'collected_at_utc': 'c'}
                self.assertEqual(store.append_versions([dict(row, text=STAKE)]), 1)
                self.assertEqual(store.append_versions([dict(row, text=STAKE)]), 0)       # idempotente
                self.assertEqual(store.append_versions([dict(row, text=STAKE_FT)]), 1)    # edição = nova versão
                lines = [json.loads(l) for l in (Path(d) / 'messages.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertEqual([('ft' in parse(l['text'])) for l in lines], [False, True])
            finally:
                del os.environ['SCANGOAL_DATA_DIR']


if __name__ == '__main__':
    unittest.main()
