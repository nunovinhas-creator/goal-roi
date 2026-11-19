"""LLM phase 4: all opportunity context is numeric, prospective and non-certifying."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scangoal import llm

ID = "polymarket_pre_over25_share_lt80_total_lt10000_v1"
RANKING = {
    "status": "exploratory_only",
    "certification": "not_activated",
    "statistical_claim": "none",
    "ranking": [{
        "rule_id": ID, "settled": 5, "forward_games": 8,
        "roi_odds_minus_2pct": 0.79, "certified": False,
        "match": "PRIVATE SHOULD NEVER LEAK",
    }],
    "baseline": {"roi_odds_minus_2pct": -0.23},
}


def prompt_with(report):
    return llm.build_prompt(
        {"Polymarket": {"Seleção exata": {"Over 2.5": 9}}},
        [], 7, mode="refinar", opportunity=report,
    )


class OpportunityPromptTests(unittest.TestCase):
    def test_small_sample_hides_roi_and_avoids_winner_bias(self):
        prompt = prompt_with(RANKING)
        self.assertIn("5 jogos futuros liquidados", prompt)
        self.assertIn("amostra insuficiente", prompt)
        self.assertNotIn("0.790", prompt)
        self.assertNotIn("-0.230", prompt)
        self.assertNotIn("PRIVATE", prompt)
        self.assertNotIn("match", prompt)
        self.assertIn("SEM conclusão de ROI", prompt)

    def test_thirty_forward_games_get_descriptive_only_metrics(self):
        report = copy.deepcopy(RANKING)
        report["ranking"][0]["settled"] = 30
        prompt = prompt_with(report)
        self.assertIn("30 jogos futuros", prompt)
        self.assertIn("+0.790", prompt)
        self.assertIn("-0.230", prompt)
        self.assertIn("não independente", prompt)
        self.assertIn("nenhuma vantagem certificada", prompt)
        self.assertNotIn("PRIVATE", prompt)

    def test_unverified_forged_and_non_numeric_reports_fail_closed(self):
        reports = [
            None, {}, {"status": "not_activated"},
            dict(RANKING, certification="active"),
            dict(RANKING, statistical_claim="certified"),
        ]
        r = copy.deepcopy(RANKING)
        r["ranking"][0]["certified"] = True
        reports.append(r)
        r = copy.deepcopy(RANKING)
        r["ranking"][0]["rule_id"] = "some-other-rule"
        reports.append(r)
        r = copy.deepcopy(RANKING)
        r["ranking"][0]["settled"] = 30
        r["baseline"]["roi_odds_minus_2pct"] = float("nan")
        reports.append(r)
        r = copy.deepcopy(RANKING)
        r["ranking"][0]["settled"] = 30
        r["ranking"][0]["roi_odds_minus_2pct"] = "lucrativo"
        reports.append(r)
        for report in reports:
            with self.subTest(report=str(report)[:60]):
                self.assertEqual(llm.opportunity_prompt(report), [])
                self.assertNotIn("PR #55", prompt_with(report))

    def test_without_activation_requires_no_calls_or_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"SCANGOAL_DATA_DIR": directory}):
                before = list(Path(directory).iterdir())
                self.assertIsNone(llm.opportunity_digest("2026-11-19T16:00:00+00:00"))
                self.assertEqual(list(Path(directory).iterdir()), before)

    def test_notes_mode_receives_only_safe_aggregates(self):
        report = copy.deepcopy(RANKING)
        prompt = llm.build_prompt(
            {"Polymarket": {"Seleção exata": {"Over 2.5": 9}}},
            [], 7, mode="notas", opportunity=report,
        )
        self.assertIn("amostra insuficiente", prompt)
        self.assertIn('"notes"', prompt)
        self.assertNotIn("PRIVATE", prompt)


if __name__ == "__main__":
    unittest.main()
