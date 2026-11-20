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
from scangoal import chronology, llm_eval, opportunity_forward, opportunity_ranking, report
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
    'quarantine': bet['result_quarantined'],
    'late': bet['result_reconciliation'],
    'llm_values': [unit[1] for unit in units.values()],
    'audit': [audit['forward_games'], audit['wins'], audit['quarantined'], audit['roi_odds_minus_2pct']],
    'ranking': [ranking['ranking'][0]['wins'], ranking['ranking'][0]['quarantined']],
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
