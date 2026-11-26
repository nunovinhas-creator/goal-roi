import unittest

from audits.v7.parser.semantic_probe import kalshi_semantics, negated, polymarket_semantics, resolve_team


class SemanticProbeTests(unittest.TestCase):
    def test_alias_needs_one_side(self):
        self.assertEqual(resolve_team("PSG", ["Paris Saint Germain", "Roma"]), (0, "sigla"))
        self.assertEqual(resolve_team("Al Nassr", ["Al Ahli", "Al Nasr Riyadh"])[0], None)
        self.assertEqual(resolve_team("FC Köln", ["Bayern Munchen", "Cologne"]), (None, "sem_evidencia_textual"))

    def test_kalshi_hypotheses_are_not_admissions(self):
        teams = ["Bayern Munchen", "Cologne"]
        self.assertEqual(kalshi_semantics("12", teams)[0], "codigo_12_dupla_hipotese")
        self.assertEqual(kalshi_semantics("Bayern Munich", teams)[0], "alias_vencedor")
        self.assertEqual(kalshi_semantics("Bayern Munich −1.5", teams)[0], "alias_mais_handicap_sinal_unicode")
        self.assertEqual(negated("not 2:0", teams)[0], "nao_resultado_exato")
        self.assertEqual(negated("not Bayern Munich", teams)[0], "nao_equipa_alias")

    def test_polymarket_spread_contradiction_is_flagged(self):
        teams = ["Sabah Baku", "Barcelona"]
        self.assertEqual(polymarket_semantics("Handicap → Spread: FC Barcelona (-3.5) (Sabah FK)", teams),
                         "spread_contraditorio")
        self.assertEqual(polymarket_semantics("Handicap → Spread: FC Barcelona (-3.5) (FC Barcelona)", teams),
                         "spread_coerente")
        self.assertEqual(polymarket_semantics("Barcelona FC Total Over 1.5", teams), "total_equipa")


if __name__ == "__main__":
    unittest.main()
