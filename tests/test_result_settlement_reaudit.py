"""Persisted replay of a timely FT followed by new evidence after settlement."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class HistoricalSettlementReaudit(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        text = "\n".join((
            "📈 🔷 Polymarket · 19 nov 2026 21:00",
            "Alpha — Beta · Example",
            "Over 2.5 @1.85 · $9.9K",
            "🧾 2 trades · biggest $4.4K · market share 79%",
            "https://scangoal.com/match/game-1?lang=en",
        ))
        message = {"message_id": 42, "date_utc": "2026-11-19T15:35:00Z",
                   "collected_at_utc": "2026-11-19T15:36:00Z", "text": text}
        (self.directory / "messages.jsonl").write_text(json.dumps(message) + "\n", encoding="utf-8")
        self.write_result("hl", [2, 1], "2026-11-20T03:00:00Z")

    def write_result(self, source, score, stamp):
        observation = {"ft": score, "period": "regulation", "observed_at": stamp, "available_at": stamp}
        (self.directory / ("results_" + source + ".json")).write_text(
            json.dumps({"ft_versions": {"game-1": [observation]}}), encoding="utf-8",
        )

    def replay(self, now):
        script = """
import json
import sys
from scangoal import brain, chronology, llm_eval, opportunity_forward, opportunity_ranking, report
from scripts.opportunity_forward_audit import PROTOCOL, read_json, sourced_results
from scangoal.store import data_dir

now = chronology.parse_utc(sys.argv[1])
born = chronology.parse_utc('2026-11-19T15:20:00Z')
observed = {}
messages, *_ = report.load(result_observations=observed)
evidence = sourced_results(observed, data_dir())
protocol = read_json(PROTOCOL, {})
audit = opportunity_forward.evaluate(messages, evidence, protocol, activation_utc=born.isoformat(), now=now)
ranking = opportunity_ranking.build(messages, evidence, protocol, activation_utc=born.isoformat(), now=now)
report.build(now=now)
bet, = report.LAST['research_signals']
units = llm_eval.admitted([bet], born)
print(json.dumps({
    'known': chronology.iso(chronology.result_known(bet)),
    'first': bet.get('match_result_first_seen_utc'),
    'won': bet['w'],
    'quarantine': bet.get('result_quarantined'),
    'late': bet.get('result_reconciliation'),
    'llm_values': [unit[1] for unit in units.values()],
    'audit': [audit['forward_games'], audit['wins'], audit['quarantined'], audit['roi_odds_minus_2pct']],
    'ranking': [ranking['ranking'][0]['wins'], ranking['ranking'][0]['quarantined']],
    'brain': {key: brain.economic_summary([bet], born, now)[key]
              for key in ('wins', 'losses', 'quarantined', 'imputed_losses')},
    'benchmark_quarantine': ranking['baseline']['quarantined'],
    'benchmark': ranking['baseline']['wins'],
}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script, now],
            cwd=Path(__file__).resolve().parents[1],
            env=dict(os.environ, SCANGOAL_DATA_DIR=str(self.directory)),
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_agreeing_source_on_november_24_preserves_november_20_availability(self):
        before = self.replay("2026-11-23T04:00:00Z")
        self.write_result("bsd", [2, 1], "2026-11-24T04:00:00Z")
        self.assertEqual(self.replay("2026-11-24T05:00:00Z"), before)
        self.assertEqual(before["known"], "2026-11-20T03:00:00+00:00")
        self.assertEqual(before["first"], before["known"])
        self.assertEqual(before["audit"], [1, 1, 0, 0.813])
        self.assertEqual(before["ranking"], [1, 0])
        self.assertEqual(before["benchmark"], 1)
        self.assertEqual(len(before["llm_values"]), 1)
        self.assertAlmostEqual(before["llm_values"][0], 0.813)

    def test_late_disagreement_is_audited_without_rewriting_primary_result(self):
        self.write_result("bsd", [1, 1], "2026-11-24T04:00:00Z")
        replay = self.replay("2026-11-24T05:00:00Z")
        self.assertEqual(replay["won"], 1)
        self.assertTrue(replay["late"]["conflict"])
        self.assertEqual(replay["audit"], [1, 1, 0, 0.813])

    def test_disagreement_before_deadline_is_quarantined_after_restart(self):
        self.write_result("bsd", [1, 1], "2026-11-21T04:00:00Z")
        replay = self.replay("2026-11-24T05:00:00Z")
        self.assertIsNone(replay["won"])
        self.assertTrue(replay["quarantine"])
        self.assertEqual(replay["audit"], [1, 0, 1, None])
        self.assertEqual(replay["ranking"], [0, 1])
        self.assertEqual(replay["benchmark"], 0)
        self.assertEqual(replay["llm_values"], [])

    def test_telegram_note_and_highlightly_disagree_before_deadline(self):
        note = {"message_id": 43, "date_utc": "2026-11-19T20:00:00Z",
                "collected_at_utc": "2026-11-20T02:55:00Z",
                "text": "🏁 Full time: Alpha 2:1 Beta"}
        with (self.directory / "messages.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(note) + "\n")
        self.write_result("hl", [1, 1], "2026-11-20T04:00:00Z")

        before = self.replay("2026-11-20T02:59:59Z")
        early = self.replay("2026-11-20T03:30:00Z")
        conflict = self.replay("2026-11-20T04:00:00Z")
        closed = self.replay("2026-11-24T12:00:00Z")
        self.assertIsNone(before["won"], before)
        self.assertEqual(early["known"], "2026-11-20T03:00:00+00:00", early)
        self.assertEqual(early["audit"], [1, 1, 0, 0.813], early)
        self.assertEqual(early["ranking"], [1, 0])
        self.assertEqual(early["benchmark"], 1)
        self.assertEqual(early["brain"]["wins"], 1)
        self.assertEqual(len(early["llm_values"]), 1)
        self.assertEqual(conflict, closed)
        self.assertIsNone(conflict["won"], conflict)
        self.assertTrue(conflict["quarantine"])
        self.assertEqual(conflict["audit"], [1, 0, 1, None])
        self.assertEqual(conflict["ranking"], [0, 1])
        self.assertEqual(conflict["benchmark_quarantine"], 1)
        self.assertEqual(conflict["brain"], dict(wins=0, losses=0, quarantined=1, imputed_losses=0))
        self.assertEqual(conflict["llm_values"], [])
        self.assertEqual(self.replay("2026-11-20T03:30:00Z"), early)

    def test_live_handicap_keeps_its_contract_specific_bot_mark(self):
        from scangoal import chronology, opportunity_results, report
        from test_chronological_validity import sig

        now = chronology.parse_utc("2026-11-20T05:00:00Z")
        seen = "2026-11-19T19:00:00Z"
        bet = sig("game-1", 1, 0, phase="live", market="Asian Handicap",
                  selection="1 (-0.5)", kickoff_utc="2026-11-19T18:00:00Z",
                  date_utc=seen, signal_seen_utc=seen, result_known_source="bot_mark",
                  result_known_utc="2026-11-20T03:00:00Z", settled_by="marca do bot")
        scores = {"game-1": [
            opportunity_results.collected("telegram", "43", [2, 1], "2026-11-20T02:55:00Z",
                                          published="2026-11-19T20:00:00Z"),
            opportunity_results.collected("highlightly", "44", [1, 1], "2026-11-20T03:55:00Z"),
        ]}
        result, = report.research_bets([bet], scores, now)
        self.assertEqual((result["canonical_match_id"], result["w"], result["settled_by"]),
                         ("game-1", 1, "marca do bot"))
