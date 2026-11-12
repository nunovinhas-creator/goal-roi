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
