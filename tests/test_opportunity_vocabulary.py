"""PR #57: linha exata Polymarket só para investigação LLM, sem alargar o gerador.

Os testes não leem a rede, não acedem a Telegram e não alteram o registo da produção.
"""
import json
import unittest

from scangoal import brain, insights, llm


def polymarket(selection="Over 2.5", *, market="Asian Total", phase="pre"):
    return {
        "type": "POLYMARKET",
        "in_roi": True,
        "match_id": "alpha-vs-beta-20261119",
        "teams": ["Alpha", "Beta"],
        "league": "Example League",
        "date_utc": "2026-11-19T12:00:00+00:00",
        "kickoff_utc": "2026-11-19T18:00:00+00:00",
        "market": market,
        "selection": selection,
        "phase": phase,
        "odds": 1.85,
        "share": 79,
        "total": 9900,
        "n_bets": 1,
    }


class PolymarketExactSelectionTests(unittest.TestCase):
    def test_over_25_is_not_the_same_as_other_totals(self):
        def vals(row):
            return [v for k, v in brain.atoms(row, set()) if k == brain.POLYMARKET_EXACT_SELECTION]

        self.assertEqual(vals(polymarket()), ["Over 2.5"])
        self.assertEqual(vals(polymarket("Over 3.5")), ["Over 3.5"])
        self.assertEqual(vals(polymarket("Under 2.5")), ["Under 2.5"])
        self.assertEqual(vals(polymarket("Over 2.25")), ["Over 2.25"])
        self.assertEqual(vals(polymarket("Over 2.75")), ["Over 2.75"])

    def test_exact_market_only_and_no_inferred_or_dirty_selection(self):
        for market, selection in (
            ("1X2", "Over 2.5"),
            ("Polymarket", "Over 2.5"),
            ("Asian Total", "Over 2.5 FT"),
            ("Asian Total", "Over 2.5 or 3.5"),
            ("Asian Total", "Over 2,5"),
            ("Asian Total", ""),
            ("Asian Total", None),
            ("Asian Total", "2.5"),
        ):
            with self.subTest(market=market, selection=selection):
                b = polymarket(selection, market=market)
                atoms = brain.atoms(b, set())
                self.assertFalse(any(k == brain.POLYMARKET_EXACT_SELECTION for k, _ in atoms))

    def test_other_families_untouched(self):
        s = polymarket()
        s["type"] = "STAKE_LOAD"
        s["after_kickoff_min"] = -20
        self.assertFalse(any(k == brain.POLYMARKET_EXACT_SELECTION for k, _ in brain.atoms(s, set())))

    def test_llm_sees_specific_line_and_four_conjunctions(self):
        s = polymarket()
        vocab = llm.vocabulary([s])
        self.assertEqual(vocab["Polymarket"]["Seleção exata"], {"Over 2.5": 1})
        conds = [
            ["Momento", "antes do jogo"],
            ["Seleção exata", "Over 2.5"],
            ["Quota de mercado", "menos de 80 %"],
            ["Valor da aposta", "menos de 10 mil $"],
        ]
        found, rejected = llm.accept(
            json.dumps({"hypotheses": [{"family": "Polymarket", "conditions": conds, "reason": "só para testes prospetivos"}]}),
            vocab, {}, support=lambda family, rows: 5,
        )
        self.assertEqual(rejected, 0)
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0][1]["conditions"]), 4)
        self.assertEqual(found[0][1]["source"], "llm")

    def test_generator_three_condition_budget_does_not_expand(self):
        s = polymarket()
        conditions = brain.candidates([s])
        self.assertTrue(conditions)
        self.assertTrue(all(len(parts) <= brain.MAX_CONDITIONS for _, parts in conditions))
        self.assertTrue(all(k != brain.POLYMARKET_EXACT_SELECTION
                            for (_, parts) in conditions for k, _ in parts))

    def test_no_new_postsignal_characteristic(self):
        self.assertNotIn(brain.POLYMARKET_EXACT_SELECTION, brain.POST)


if __name__ == "__main__":
    unittest.main()
