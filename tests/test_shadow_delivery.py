"""Independent bridge tests. All Git remotes are local disposable bare repositories."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scangoal import shadow_delivery as bridge
from scangoal import shadow_ops as ops

T=datetime(2026,11,18,12,tzinfo=timezone.utc)
OPEN="""🔥 Open game — heading for over
Friendly · 60'
Alpha 2
Beta 0
Rhythm 87/100 · Over 100%
💰 O 2.25: 2.00
https://scangoal.com/match/alpha-vs-beta-20261118?lang=en"""


class SignalDelivery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.remote=self.root/"remote.git"
        ops.git(self.root,"init","--bare",self.remote)
        self.repo=self.root/"publisher"
        ops.git(self.root,"clone",self.remote,self.repo)
        ops.git(self.repo,"checkout","--orphan",bridge.BRANCH)
        (self.repo/"seed").write_text("synthetic fixture")
        ops.git(self.repo,"add","seed")
        ops.git(self.repo,"-c","user.name=fixture","-c","user.email=fixture@example.test",
                "commit","-m","seed bridge")
        ops.git(self.repo,"push","origin","HEAD:refs/heads/"+bridge.BRANCH)
        self.now=T
        self.pub=bridge.DeliveryPublisher(self.repo,clock=lambda:self.now)

    def row(self,mid=1,age=30,seen=10,text=OPEN,edit=None):
        return {
            "message_id":mid,
            "date_utc":(self.now-timedelta(seconds=age)).isoformat(),
            "collected_at_utc":(self.now-timedelta(seconds=seen)).isoformat(),
            "edit_date_utc":edit,
            "text":text,
        }

    def test_enqueue_and_verified_two_phase_remote_receipt(self):
        entry=self.pub.append(self.row())
        self.assertEqual(entry["status"],"queued")
        self.assertTrue(entry["under_120s_before_git"])
        self.assertFalse(entry["scientifically_admitted"])
        result=self.pub.publish()
        self.assertEqual(result["status"],"published")
        remote_head=ops.git(self.remote,"rev-parse","refs/heads/"+bridge.BRANCH)
        self.assertEqual(remote_head,result["remote_final_sha"])
        self.assertFalse(result["proves_runner_consumption"])
        self.assertEqual(bridge.audit(self.repo)["queued_unreceipted"],0)
        events=self.pub._journal()[0]
        self.assertEqual(len(events),1)
        remote_events=json.loads(ops.git(self.remote,"show",remote_head+":"+str(bridge.EVENTS)).splitlines()[0])
        self.assertEqual(remote_events,events[0])
        self.assertEqual(len(self.pub._journal()[1]),1)

    def test_duplicate_same_version_remains_one(self):
        row=self.row()
        self.pub.append(row)
        self.assertEqual(self.pub.append(row)["status"],"duplicate")
        self.assertEqual(len(self.pub._journal()[0]),1)

    def test_edited_version_preserved_as_separate_event(self):
        self.pub.append(self.row())
        self.pub.append(self.row(text=OPEN+" updated",edit=self.now.isoformat()))
        ev=self.pub._journal()[0]
        self.assertEqual(len(ev),2)
        self.assertEqual(ev[1]["previous_hash"],ev[0]["hash"])
        self.pub.publish()
        self.assertEqual(self.pub._journal()[1][-1]["covers_sequence"],2)

    def test_late_before_git_not_claimed_prospective(self):
        r=self.pub.append(self.row(age=124,seen=4))
        self.assertFalse(r["under_120s_before_git"])
        self.assertEqual(self.pub.publish()["status"],"published")
        a=bridge.audit(self.repo)
        self.assertEqual(a["late_before_git"],1)
        self.assertFalse(a["eligible_for_scientific_certificate"])

    def test_non_open_game_does_not_enter_journal(self):
        self.assertEqual(self.pub.append(self.row(text="hello"))["status"],"ignored_non_open_game")
        self.assertEqual(self.pub.publish()["status"],"empty")

    def test_unknown_provenance_and_invalid_chronology_fail_closed(self):
        with self.assertRaises(bridge.DeliveryError):
            self.pub.append(self.row(),origin="user-message")
        with self.assertRaises(bridge.DeliveryError):
            self.pub.append(self.row(age=2,seen=30))
        with self.assertRaises(bridge.DeliveryError):
            self.pub.append(self.row(age=30,seen=-3))
        with self.assertRaises(bridge.DeliveryError):
            self.pub.append(self.row(mid=True))
        self.assertEqual(len(self.pub._journal()[0]),0)

    def test_changed_event_or_truncation_blocks_publish(self):
        self.pub.append(self.row())
        file=self.repo/bridge.EVENTS
        originals=file.read_bytes()
        e=json.loads(originals.splitlines()[0])
        e["text"]="rewritten"
        file.write_text(json.dumps(e)+"\n")
        with self.assertRaises(bridge.DeliveryError):
            self.pub.publish()
        file.write_bytes(originals[:-1])
        with self.assertRaises(bridge.DeliveryError):
            self.pub.publish()
        file.write_bytes(originals)
        self.assertEqual(self.pub.publish()["status"],"published")

    def test_failed_push_preserves_queue_and_retry_receipt(self):
        self.pub.append(self.row())
        original=self.pub._push_confirm
        with patch.object(self.pub,"_push_confirm",side_effect=bridge.DeliveryError("simulated outage")):
            with self.assertRaises(bridge.DeliveryError):
                self.pub.publish()
        self.assertEqual(len(self.pub._journal()[0]),1)
        self.assertEqual(len(self.pub._journal()[1]),0)
        self.assertEqual(self.pub.publish()["status"],"published")
        self.assertEqual(len(self.pub._journal()[1]),1)
        self.assertEqual(self.pub.publish()["status"],"published")
        self.assertEqual(len(self.pub._journal()[1]),1)

    def test_receipt_push_failure_is_recoverable_without_duplicate(self):
        self.pub.append(self.row())
        orig=self.pub._push_confirm
        calls=[0]
        def fault():
            calls[0]+=1
            if calls[0]==2:
                raise bridge.DeliveryError("second phase outage")
            return orig()
        with patch.object(self.pub,"_push_confirm",side_effect=fault):
            with self.assertRaises(bridge.DeliveryError):
                self.pub.publish()
        self.assertEqual(len(self.pub._journal()[1]),1)
        self.pub.publish()
        self.assertEqual(len(self.pub._journal()[1]),1)

    def test_unrelated_staging_is_rejected(self):
        self.pub.append(self.row())
        (self.repo/"not-delivery").write_text("keep out")
        ops.git(self.repo,"add","not-delivery")
        with self.assertRaises(bridge.DeliveryError):
            self.pub.publish()

    def test_wrong_branch_is_rejected(self):
        ops.git(self.repo,"checkout","-b","unrelated")
        with self.assertRaises(bridge.DeliveryError):
            bridge.DeliveryPublisher(self.repo)

    def test_remote_non_fast_forward_rejected_without_force(self):
        self.pub.append(self.row())
        other=self.root/"other"
        ops.git(self.root,"clone",self.remote,other)
        ops.git(other,"checkout",bridge.BRANCH)
        (other/"other").write_text("remote advanced")
        ops.git(other,"add","other")
        ops.git(other,"-c","user.name=fixture","-c","user.email=fixture@example.test",
                "commit","-m","advance")
        ops.git(other,"push","origin","HEAD:refs/heads/"+bridge.BRANCH)
        with self.assertRaises(bridge.DeliveryError):
            self.pub.publish()
        self.assertEqual(len(self.pub._journal()[0]),1)
        self.assertEqual(len(self.pub._journal()[1]),0)

    def test_fresh_runner_reproduces_verified_records(self):
        self.pub.append(self.row())
        self.pub.publish()
        other=self.root/"fresh"
        ops.git(self.root,"clone",self.remote,other)
        ops.git(other,"checkout",bridge.BRANCH)
        replica=bridge.DeliveryPublisher(other)
        events,receipts=replica._journal()
        self.assertEqual(len(events),1)
        self.assertEqual(len(receipts),1)
        self.assertEqual(bridge.audit(other)["queued_unreceipted"],0)
        self.assertFalse(bridge.audit(other)["live_capture_proven"])

    def test_failed_push_outbox_roundtrip_on_fresh_checkout(self):
        self.pub.append(self.row())
        with patch.object(self.pub,"_push_confirm",
                          side_effect=bridge.DeliveryError("synthetic push failed")):
            with self.assertRaises(bridge.DeliveryError):
                self.pub.publish()
        archive=self.root/"pending.zip"
        export=self.pub.export_outbox(archive)
        self.assertEqual(export["status"],"exported")
        fresh=self.root/"fresh_runner"
        ops.git(self.root,"clone",self.remote,fresh)
        ops.git(fresh,"checkout",bridge.BRANCH)
        receiver=bridge.DeliveryPublisher(fresh,clock=lambda:self.now)
        restored=receiver.restore_outbox(archive)
        self.assertEqual(restored["events"],1)
        self.assertEqual(restored["receipts"],0)
        self.assertEqual(receiver.publish()["status"],"published")
        self.assertEqual(len(receiver._journal()[0]),1)
        self.assertEqual(len(receiver._journal()[1]),1)
        self.assertEqual(receiver.publish()["status"],"published")
        self.assertEqual(len(receiver._journal()[1]),1)

    def test_second_phase_failed_outbox_roundtrip(self):
        self.pub.append(self.row())
        orig=self.pub._push_confirm
        calls=[0]
        def outage():
            calls[0]+=1
            if calls[0]==2:
                raise bridge.DeliveryError("synthetic receipt failure")
            return orig()
        with patch.object(self.pub,"_push_confirm",side_effect=outage):
            with self.assertRaises(bridge.DeliveryError):
                self.pub.publish()
        self.assertEqual(len(self.pub._journal()[1]),1)
        archive=self.root/"receipt_pending.zip"
        self.pub.export_outbox(archive)
        fresh=self.root/"runner_receipt"
        ops.git(self.root,"clone",self.remote,fresh)
        ops.git(fresh,"checkout",bridge.BRANCH)
        receiver=bridge.DeliveryPublisher(fresh,clock=lambda:self.now)
        receiver.restore_outbox(archive)
        receiver.publish()
        self.assertEqual(len(receiver._journal()[1]),1)
        self.assertEqual(bridge.audit(fresh)["queued_unreceipted"],0)

    def test_corrupt_outbox_hash_rejected_before_changing_ledger(self):
        import zipfile
        self.pub.append(self.row())
        archive=self.root/"valid.zip"
        self.pub.export_outbox(archive)
        fresh=self.root/"tampered"
        ops.git(self.root,"clone",self.remote,fresh)
        ops.git(fresh,"checkout",bridge.BRANCH)
        invalid=self.root/"changed.zip"
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(invalid,"w") as dest:
            for name in source.namelist():
                body=source.read(name)
                if name==str(bridge.EVENTS):
                    body=body.replace(b"Alpha",b"Omega")
                dest.writestr(name,body)
        receiver=bridge.DeliveryPublisher(fresh)
        with self.assertRaises(bridge.DeliveryError):
            receiver.restore_outbox(invalid)
        self.assertEqual(receiver._journal()[0],[])

    def test_outbox_remote_divergence_quarantined(self):
        self.pub.append(self.row())
        archive=self.root/"pending.zip"
        self.pub.export_outbox(archive)
        other=self.root/"advanced"
        ops.git(self.root,"clone",self.remote,other)
        ops.git(other,"checkout",bridge.BRANCH)
        (other/"advance").write_text("remote advanced")
        ops.git(other,"add","advance")
        ops.git(other,"-c","user.name=fixture","-c","user.email=fixture@example.test",
                "commit","-m","advance")
        ops.git(other,"push","origin","HEAD:refs/heads/"+bridge.BRANCH)
        fresh=self.root/"fresh"
        ops.git(self.root,"clone",self.remote,fresh)
        ops.git(fresh,"checkout",bridge.BRANCH)
        receiver=bridge.DeliveryPublisher(fresh)
        with self.assertRaises(bridge.DeliveryError):
            receiver.restore_outbox(archive)
        self.assertEqual(receiver._journal()[0],[])

    def test_outbox_cannot_be_overwritten(self):
        self.pub.append(self.row())
        path=self.root/"outbox.zip"
        self.pub.export_outbox(path)
        old=path.read_bytes()
        with self.assertRaises(bridge.DeliveryError):
            self.pub.export_outbox(path)
        self.assertEqual(path.read_bytes(),old)


if __name__=="__main__":
    unittest.main()
