"""Offline Git-history latency tests. No network calls, secrets or real data."""
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path

from scripts import shadow_commit_latency as audit

T=datetime(2026,11,18,12,tzinfo=timezone.utc)
OPEN="""🔥 Open game — heading for over
Friendly · 60'
Alpha 2
Beta 0
Rhythm 87/100 · Over 100%
💰 O 2.25: 2.00
https://scangoal.com/match/alpha-vs-beta-20261118?lang=en"""


class GitLatency(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo=Path(self.tmp.name)/"repo"
        self.repo.mkdir()
        self.run_git("init","-b","main")
        self.data=self.repo/"data"
        self.data.mkdir()
        self.file=self.data/"messages.jsonl"

    def run_git(self,*args,env=None):
        return subprocess.run(["git","-C",str(self.repo),*args],
                              check=True,capture_output=True,env=env).stdout.decode().strip()

    def row(self,mid,sent,seen,text=OPEN):
        return {"message_id":mid,"date_utc":sent.isoformat(),
                "collected_at_utc":seen.isoformat(),"edit_date_utc":None,"text":text}

    def commit(self,rows,date):
        self.file.write_text("".join(json.dumps(row)+"\n" for row in rows))
        self.run_git("add","data/messages.jsonl")
        env=dict(os.environ,GIT_AUTHOR_DATE=date.isoformat(),
                 GIT_COMMITTER_DATE=date.isoformat())
        self.run_git("-c","user.name=fixture","-c","user.email=fixture@example.test",
                     "commit","-m","synthetic data",env=env)

    def test_commit_lower_bound_distinguishes_feasible_and_impossible(self):
        a=self.row(1,T-timedelta(seconds=60),T-timedelta(seconds=40))
        b=self.row(2,T-timedelta(seconds=300),T-timedelta(seconds=100))
        self.commit([a],T-timedelta(seconds=20))
        self.commit([a,b],T-timedelta(seconds=10))
        result=audit.study(self.repo,T)
        self.assertEqual(result["open_game_first_versions"],2)
        self.assertEqual(result["collector_within_120_seconds"],1)
        self.assertEqual(result["collector_over_120_seconds"],1)
        self.assertEqual(result["commit_timestamp_within_120_seconds"],1)
        self.assertEqual(result["commit_timestamp_over_120_seconds"],1)
        self.assertEqual(result["data_commits_scanned"],2)
        self.assertFalse(result["complete_120s_delivery_proven"])
        self.assertFalse(result["activation_approved"])
        # Reports have no message IDs, raw text, teams or private strings.
        rendered=json.dumps(result)
        self.assertNotIn("Alpha",rendered)
        self.assertNotIn("scangoal.com/match",rendered)
        self.assertNotIn("message_id",rendered)

    def test_versions_are_deduplicated_by_first_capture(self):
        a=self.row(1,T-timedelta(seconds=60),T-timedelta(seconds=40))
        edit=self.row(1,T-timedelta(seconds=60),T-timedelta(seconds=25),OPEN+" updated")
        self.commit([a],T-timedelta(seconds=20))
        self.commit([a,edit],T-timedelta(seconds=10))
        result=audit.study(self.repo,T)
        self.assertEqual(result["open_game_first_versions"],1)
        self.assertEqual(result["commit_timestamp_within_120_seconds"],1)

    def test_uncommitted_data_is_rejected(self):
        a=self.row(1,T-timedelta(seconds=60),T-timedelta(seconds=40))
        self.commit([a],T-timedelta(seconds=20))
        with self.file.open("a") as f:
            f.write(json.dumps(self.row(2,T-timedelta(seconds=40),T-timedelta(seconds=25)))+"\n")
        with self.assertRaises(audit.LatencyError):
            audit.study(self.repo,T)

    def test_shallow_history_fails_closed(self):
        a=self.row(1,T-timedelta(seconds=60),T-timedelta(seconds=40))
        self.commit([a],T-timedelta(seconds=20))
        self.commit([a,self.row(2,T-timedelta(seconds=40),T-timedelta(seconds=25))],
                    T-timedelta(seconds=5))
        shallow=Path(self.tmp.name)/"shallow"
        subprocess.run(["git","clone","--depth","1",self.repo.resolve().as_uri(),
                        str(shallow)],check=True,capture_output=True)
        with self.assertRaises(audit.LatencyError):
            audit.study(shallow,T)

    def test_commit_before_collector_timestamp_is_unproven(self):
        a=self.row(1,T-timedelta(seconds=60),T-timedelta(seconds=20))
        self.commit([a],T-timedelta(seconds=30))
        result=audit.study(self.repo,T)
        self.assertEqual(result["invalid_or_inconsistent_clock"],1)
        self.assertEqual(result["commit_timestamp_within_120_seconds"],0)
        self.assertFalse(result["complete_120s_delivery_proven"])

    def test_missing_history_or_data_refused(self):
        with self.assertRaises(audit.LatencyError):
            audit.study(self.repo,T)

    def test_non_open_game_is_not_disclosed(self):
        a=self.row(1,T-timedelta(seconds=60),T-timedelta(seconds=30),"confidential")
        self.commit([a],T-timedelta(seconds=10))
        result=audit.study(self.repo,T)
        self.assertEqual(result["open_game_first_versions"],0)
        self.assertNotIn("confidential",json.dumps(result))

    def test_old_clock_cannot_hide_first_occurrence_before_window(self):
        signal = self.row(1, T-timedelta(seconds=100), T-timedelta(seconds=80))
        self.commit([signal], T-timedelta(days=2))
        self.commit([], T-timedelta(seconds=50))
        self.commit([signal], T-timedelta(seconds=20))
        report = audit.study(self.repo, T)
        self.assertEqual(report["commit_timestamp_within_120_seconds"], 0)
        self.assertEqual(report["invalid_or_inconsistent_clock"], 1)

    def test_current_row_cannot_substitute_an_earlier_version_removed_from_head(self):
        original = self.row(1, T-timedelta(seconds=100), T-timedelta(seconds=80))
        changed = self.row(1, T-timedelta(seconds=100), T-timedelta(seconds=50), OPEN+" changed")
        self.commit([original], T-timedelta(seconds=60))
        self.commit([changed], T-timedelta(seconds=20))
        report = audit.study(self.repo, T)
        self.assertEqual(report["collector_within_120_seconds"], 0)
        self.assertEqual(report["commit_timestamp_within_120_seconds"], 0)

    def test_edit_only_row_is_not_counted_as_original(self):
        edited = self.row(1, T-timedelta(seconds=100), T-timedelta(seconds=60))
        edited["edit_date_utc"] = (T-timedelta(seconds=65)).isoformat()
        self.commit([edited], T-timedelta(seconds=50))
        report = audit.study(self.repo, T)
        self.assertEqual(report["collector_within_120_seconds"], 0)
        self.assertEqual(report["commit_timestamp_within_120_seconds"], 0)

if __name__=="__main__":
    unittest.main()
