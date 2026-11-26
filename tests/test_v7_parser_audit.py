import unittest

from scangoal import parser
from audits.v7.parser.parser_audit import diagnostic, parse_betfair_live, parse_kalshi


def classify(text):
    original = parser.parse(text)
    return original, diagnostic(text.splitlines(), original)


class AuditParserTests(unittest.TestCase):
    def test_kalshi_volume_is_diagnostic_only(self):
        original, (family, classification, fields) = classify(
            "📊 🟩 Kalshi · LIVE 44' 0:0\nCasa — Fora · Liga\nUnder 2.5 @1.45–1.49 · $7.6K")
        self.assertEqual((original["type"], family, classification), (None, "Kalshi", "kalshi_estrutura"))
        self.assertEqual((fields["market"], fields["quote"], fields["bet"]), ("total_jogo", "intervalo", False))
        self.assertEqual((fields["odds_range"], fields["signal_volume_usd"], fields["minute"], fields["score"]),
                         (["1.45", "1.49"], "7600.0", 44, [0, 0]))

    def test_unknown_contracts_do_not_guess(self):
        choices = {"not Casa": "contrato_negado", "1H Under 1.5": "primeira_parte",
                   "Casa team total over 1.5": "total_equipa", "Casa To Advance": "apuramento",
                   "Casas": "selecao_sem_vinculo_literal"}
        for selection, market in choices.items():
            with self.subTest(selection=selection):
                _, fields = parse_kalshi(["📊 🟩 Kalshi · 20 nov 2026 01:30", "Casa — Fora · Liga",
                                          f"{selection} @2.0 · $6K"])
                self.assertEqual((fields["market"], fields["bet"]), (market, False))
                self.assertEqual(fields["kickoff"], "2026-11-19T22:30+00:00")

    def test_kalshi_rejects_invalid_date_reversed_interval_and_teams(self):
        for lines, expected in (
            (["📊 🟩 Kalshi · 31 feb 2026 01:00", "Casa — Fora", "X @2.0 · $6K"], "calendario"),
            (["📊 🟩 Kalshi · LIVE 8' 0:0", "Casa — Casa", "X @2.0 · $6K"], "equipa"),
            (["📊 🟩 Kalshi · LIVE 8' 0:0", "Casa — Fora", "X @2.0–1.8 · $6K"], "cotacao"),
        ):
            with self.subTest(lines=lines):
                self.assertEqual(parse_kalshi(lines), (expected, {}))

    def test_betfair_market_at_score_is_not_global_volume(self):
        text = ("💷 🟨 Betfair · LIVE\nCasa — Fora · Liga\n1 · Casa @4.00 · $289K\n"
                "📊 market share 74% · 1X2 market at 0:1 $1.2M · live")
        old, (family, category, matched) = classify(text)
        self.assertEqual((old["type"], old["error"], old["bet"]), ("BETFAIR", "fields_missing", False))
        self.assertEqual((family, category), ("Betfair", "betfair_estrutura"))
        self.assertEqual((matched["score"], matched["scope"], matched["volume_m"], matched["team_literal"]),
                         ([0, 1], "mercado_no_marcador", True, True))
        self.assertEqual((matched["odds"], matched["selection_volume_usd"], matched["volume_at_score_usd"],
                          matched["share_pct"], matched["bet"]), ("4.00", "289000", "1200000.0", 74, False))
        self.assertNotIn("global_market_volume", matched)
        old_text = text.replace("1X2 market at 0:1 $1.2M · live", "whole 1X2 market $390K")
        self.assertTrue(parser.parse(old_text)["bet"])
        self.assertEqual(parse_betfair_live(old_text.splitlines())[0], "cotacao_ou_volume")

    def test_actual_double_icons_and_missing_live_clock(self):
        for brand, quote, volume, family in (
            ("📊📊 🟩 Kalshi ×4 · LIVE 71' 0:0", "X @2.0 · $6K", "", "Kalshi"),
            ("💷💷 🟨 Betfair ×4 · LIVE", "1 · Casa @2.0 · $600K",
             "📊 market share 60% · 1X2 market at 1:0 $1M · live", "Betfair"),
        ):
            text = "\n".join([brand, "Casa — Fora · Liga", quote, volume]).strip()
            old, (source, diagnostic_only, fields) = classify(text)
            self.assertFalse(old["bet"])
            self.assertEqual((source, diagnostic_only, fields["bet"]),
                             (family, family.casefold() + "_estrutura", False))
        self.assertEqual(parse_kalshi(["📊 🟩 Kalshi · LIVE", "Casa — Fora", "X @2.0 · $6K"]),
                         ("relogio_ausente", {}))

    def test_polymarket_silent_scope_and_missing_clock_stay_ineligible(self):
        for header, quote, category in (
            ("LIVE 44' 0:0", "not Casa", "polymarket_contrato_negado"),
            ("LIVE", "AH1(-0.5)", "polymarket_sem_relogio_ou_cabecalho"),
        ):
            original, (_, result, fields) = classify(f"🔷 Polymarket · {header}\nCasa — Fora · Liga\n{quote} @2.0 · $10K")
            self.assertEqual((original["type"], original["bet"], result, fields),
                             ("POLYMARKET", False, category, {}))

    def test_result_note_without_reference_is_only_a_note(self):
        old, (_, category, fields) = classify("🏁 Signal result\nCasa 1\nFora 0\nGoal 15′ ✅")
        self.assertIsNone(old["type"])
        self.assertEqual((category, fields), ("nota_resultado_signal", {"placar": True}))


if __name__ == "__main__":
    unittest.main()
