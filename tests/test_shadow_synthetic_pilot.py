"""Synthetic pilot assertions: no network, no real GitHub branches or Telegram."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import shadow_synthetic_pilot as pilot

NOW=datetime(2026,11,18,12,tzinfo=timezone.utc)


class IsolatedPilot(unittest.TestCase):
    def test_restores_and_confirms_receipt_before_120s(self):
        result=pilot.pilot(NOW,observe_after_seconds=45)
        self.assertTrue(result["outbox_restored_on_fresh_checkout"])
        self.assertTrue(result["remote_receipt_verified"])
        self.assertTrue(result["no_duplicate_events"])
        self.assertEqual(result["within_120s_transport"],1)
        self.assertEqual(result["late_or_unproven"],0)
        self.assertFalse(result["real_remote_delivery_proven"])
        self.assertFalse(result["source_authenticated"])
        self.assertEqual(result["scientific_certification"],"not_activated")

    def test_late_delivery_is_not_accepted(self):
        result=pilot.pilot(NOW,observe_after_seconds=150)
        self.assertEqual(result["within_120s_transport"],0)
        self.assertEqual(result["late_or_unproven"],1)
        self.assertFalse(result["real_runner_liveness_proven"])

    def test_invalid_clock_and_delay_rejected(self):
        with self.assertRaises(ValueError):
            pilot.pilot(NOW.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            pilot.pilot(NOW,observe_after_seconds=-1)

    def test_output_never_discloses_fixture_or_message_ids(self):
        result=pilot.pilot(NOW)
        rendered=json.dumps(result)
        for forbidden in ("Alpha","scangoal.com/match","message_id",
                          "TG_SESSION","TG_BOT_TOKEN"):
            self.assertNotIn(forbidden,rendered)

    def test_cli_never_overwrites_previous_report(self):
        with tempfile.TemporaryDirectory() as dirname:
            output=Path(dirname)/"proof.json"
            output.write_text("existing")
            with patch("sys.argv",["pilot","--output",str(output)]):
                with self.assertRaises(FileExistsError):
                    pilot.main()
            self.assertEqual(output.read_text(),"existing")


if __name__=="__main__":
    unittest.main()
