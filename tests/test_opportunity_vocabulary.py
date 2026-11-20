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


class ExperimentalProfiles(unittest.TestCase):
    def test_discovers_joint_conditions_from_distinct_games_without_outcomes(self):
        rows = []
        for prefix, share, amount in (("low", 79, 9_999), ("share", 80, 9_999), ("amount", 79, 10_000)):
            for i in range(8):
                row = polymarket()
                row.update(match_id="%s-%d" % (prefix, i), teams=[prefix + str(i), "Beta"], period="regulation",
                           share=share, total=amount, w=None, signal_seen_utc="2026-11-19T12:01:00Z")
                rows.append(row)
        conditions = sorted([
            [brain.POLYMARKET_CONTRACT, "Asian Total / Over 2.5 / regulation"],
            ["Momento", "antes do jogo"], ["Quota de mercado", "menos de 80 %"],
            ["Valor da aposta", "menos de 10 mil $"],
        ])
        hid = brain.hyp_id("Polymarket", [tuple(c) for c in conditions])
        registry = {}
        brain.update_registry(rows, registry)
        self.assertEqual(registry[hid]["conditions"], conditions)
        self.assertTrue(registry[hid]["experimental_profile"])
        self.assertFalse(brain.certifiable(registry[hid]))
        result = next(r for r in brain.evaluate(rows, registry, 300)[0] if r["id"] == hid)
        self.assertEqual((result["status"], result["tradable"]), ("em observação", False))

        repeated = {}
        brain.update_registry([rows[0]] * 20, repeated)
        self.assertFalse(any(h.get("experimental_profile") for h in repeated.values()))

    def test_contract_keeps_period_and_market_distinct(self):
        variants = [
            ("Asian Total", "regulation", "Asian Total / Over 2.5 / regulation"),
            ("1st Half - Asian Total", "first_half", "1st Half - Asian Total / Over 2.5 / first_half"),
            ("Total Corners", "regulation", "Total Corners / Over 2.5 / regulation"),
        ]
        for market, period, expected in variants:
            row = polymarket(market=market)
            row["period"] = period
            self.assertEqual(dict(brain.atoms(row, set()))[brain.POLYMARKET_CONTRACT], expected)


if __name__ == "__main__":
    unittest.main()
