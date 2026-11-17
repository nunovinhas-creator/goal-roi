"""Fail-closed tests for the isolated SHADOW preflight. No network/secrets."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from scripts import shadow_preflight as pre

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
OPEN = """🔥 Open game — heading for over
Friendly · 60'
Alpha 2
Beta 0
Rhythm 87/100 · Over 100%
💰 O 2.25: 2.00
https://scangoal.com/match/alpha-vs-beta-20261118?lang=en"""


def row(mid, age, seen, *, edit=None, text=OPEN):
    return {
        "message_id": mid,
        "date_utc": (NOW - timedelta(seconds=age)).isoformat(),
        "collected_at_utc": (NOW - timedelta(seconds=age-seen)).isoformat(),
        "edit_date_utc": edit, "text": text,
    }


class ShadowPreflight(unittest.TestCase):
    def test_timely_and_late_first_sight_distinct(self):
        result = pre.measure([row(1, 1000, 45), row(2, 1000, 121)], NOW)
        self.assertEqual(result["open_game_messages"], 2)
        self.assertEqual(result["first_observed_within_limit"], 1)
        self.assertEqual(result["first_observed_over_limit"], 1)
        self.assertFalse(result["activation_approved"])

    def test_first_version_not_later_edit(self):
        original = row(4, 1000, 25)
        edit = row(4, 1000, 150, edit=NOW.isoformat())
        result = pre.measure([edit, original], NOW)
        self.assertEqual(result["open_game_messages"], 1)
        self.assertEqual(result["first_observed_within_limit"], 1)
        self.assertEqual(result["edited_at_first_observation"], 0)

    def test_no_evidence_never_approves_activation(self):
        result = pre.measure([], NOW)
        self.assertEqual(result["conclusion"], "NOT_READY")
        self.assertEqual(result["scientific_state"], "INCONCLUSIVO")
        self.assertFalse(result["dedicated_runner_capture_measured"])

    def test_out_of_window_is_not_counted(self):
        result = pre.measure([row(1, 25*3600, 15)], NOW)
        self.assertEqual(result["open_game_messages"], 0)

    def test_bad_clock_is_not_called_on_time(self):
        future_seen = row(1, 1000, -10)
        result = pre.measure([future_seen], NOW)
        self.assertEqual(result["valid_first_observations"], 0)
        self.assertEqual(result["invalid_or_unproven_timestamp"], 1)

    def test_non_open_game_is_ignored(self):
        result = pre.measure([row(1, 1000, 45, text="random text")], NOW)
        self.assertEqual(result["open_game_messages"], 0)

    def test_new_report_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"measure.json"
            pre.write_new(path, {"activation_approved": False})
            with self.assertRaises(FileExistsError):
                pre.write_new(path, {"activation_approved": True})
            self.assertFalse(json.loads(path.read_text())["activation_approved"])

    def test_cross_checkout_synthetic_outbox_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/"synthetic"
            proof = Path(tmp)/"restored.json"
            pre.stage(folder)
            pre.restore(folder, proof)
            result = json.loads(proof.read_text())
            self.assertTrue(result["exactly_one_decision"])
            self.assertTrue(result["exactly_one_receipt"])
            self.assertFalse(result["real_production_recovery_verified"])
            self.assertFalse(result["activation_approved"])


if __name__ == "__main__":
    unittest.main()
