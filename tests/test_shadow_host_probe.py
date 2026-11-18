"""Regression tests for credential-free heartbeat probe; no external services."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.shadow_host_probe import heartbeat

NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)


class ZeroSecretProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "uptime.json"

    def test_initial_and_regular_heartbeats(self):
        first = heartbeat(self.path, NOW, startup=True)
        self.assertEqual(first["heartbeats"], 1)
        second = heartbeat(self.path, NOW + timedelta(seconds=20))
        self.assertEqual(second["largest_gap_seconds"], 20)
        self.assertEqual(second["gaps_over_120_seconds"], 0)
        self.assertEqual(second["process_starts"], 1)

    def test_exact_boundary_and_gap(self):
        heartbeat(self.path, NOW, startup=True)
        heartbeat(self.path, NOW + timedelta(seconds=120))
        self.assertEqual(json.loads(self.path.read_text())["gaps_over_120_seconds"], 0)
        late = heartbeat(self.path, NOW + timedelta(seconds=240, microseconds=1))
        self.assertEqual(late["gaps_over_120_seconds"], 1)

    def test_restart_is_recorded_and_state_preserved(self):
        heartbeat(self.path, NOW, startup=True)
        result = heartbeat(self.path, NOW + timedelta(seconds=130), startup=True)
        self.assertEqual(result["process_starts"], 2)
        self.assertEqual(result["heartbeats"], 2)
        self.assertEqual(result["gaps_over_120_seconds"], 1)

    def test_clock_regression_does_not_rewrite_state(self):
        heartbeat(self.path, NOW, startup=True)
        old = self.path.read_bytes()
        with self.assertRaises(ValueError):
            heartbeat(self.path, NOW - timedelta(seconds=1))
        self.assertEqual(self.path.read_bytes(), old)

    def test_missing_state_fails_closed(self):
        with self.assertRaises(ValueError):
            heartbeat(self.path, NOW)
        self.assertFalse(self.path.exists())

    def test_no_identifiers_secrets_or_real_approval(self):
        result = heartbeat(self.path, NOW, startup=True)
        data = json.dumps(result)
        self.assertTrue(result["synthetic_only"])
        self.assertFalse(result["live_source_connected"])
        self.assertEqual(result["scientific_state"], "not_activated")
        for needle in ("telegram", "TG_SESSION", "message_id", "token", "Alpha", "scangoal"):
            self.assertNotIn(needle, data)


if __name__ == "__main__":
    unittest.main()
