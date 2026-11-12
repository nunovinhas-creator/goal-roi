import json, os, tempfile, unittest
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
                 'results_hl.json': {'results': {}, 'near_misses': {}, 'not_regular_time': {'m': 'Finished after extra time'}}}
        r, _ = self.build([self.row(1, old, '2026-01-01T12:00:00+00:00')], extra)
        self.assertEqual((r['accuracy']['BETFAIR/live']['no_source'], r['audit']['deleted_by_bot'], r['audit']['ended_after_90']), (1, 2, 1))
        target = {'m': {'teams': ['A', 'B'], 'date_utc': '2026-11-11T12:30:00+00:00', 'kickoff_utc': None}}
        ev = {'id': 1, 'status': 'Finished after extra time', 'event_date': '2026-11-11T12:00:00+00:00', 'home_team': 'A', 'away_team': 'B', 'home_score': None, 'away_score': None}
        unfinished = {}
        found, near = results_bsd.match_events(target, [ev], {}, unfinished)
        self.assertEqual((found, near, unfinished), ({}, {}, {'m': 'Finished after extra time'}))
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
