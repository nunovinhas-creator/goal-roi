"""Synthetic end-to-end SHADOW bridge pilot. NO Telegram, no production Git remote.

Builds a disposable local bare Git repository, simulates a failed first push,
exports outbox, restores on a fresh checkout, publishes both receipts, then
checks delivery from an independent consumer checkout.
"""
import argparse
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scangoal import shadow_delivery as delivery
from scangoal import shadow_ops as ops

UTC=timezone.utc
FIXTURE="""🔥 Open game — heading for over
Friendly · 60'
Alpha 2
Beta 0
Rhythm 87/100 · Over 100%
💰 O 2.25: 2.00
https://scangoal.com/match/alpha-vs-beta-20261118?lang=en"""


def pilot(now=None, *, observe_after_seconds=45):
    now=now or datetime.now(UTC)
    if now.tzinfo is None or not 0<=observe_after_seconds<=3600:
        raise ValueError("invalid pilot clock or delay")
    now=now.astimezone(UTC)
    with tempfile.TemporaryDirectory(prefix="goal-roi-shadow-pilot-") as temp:
        root=Path(temp)
        remote=root/"remote.git"
        ops.git(root,"init","--bare",remote)
        writer=root/"writer"
        ops.git(root,"clone",remote,writer)
        ops.git(writer,"checkout","--orphan",delivery.BRANCH)
        (writer/"synthetic-seed").write_text("not a real signal\n")
        ops.git(writer,"add","synthetic-seed")
        ops.git(writer,"-c","user.name=shadow-pilot",
                "-c","user.email=shadow@example.test",
                "commit","-m","synthetic initial branch")
        ops.git(writer,"push","origin","HEAD:refs/heads/"+delivery.BRANCH)
        pub=delivery.DeliveryPublisher(writer,clock=lambda:now)
        row={"message_id":1,
             "date_utc":(now-timedelta(seconds=30)).isoformat(),
             "collected_at_utc":(now-timedelta(seconds=10)).isoformat(),
             "edit_date_utc":None,"text":FIXTURE}
        queued=pub.append(row)
        if queued["status"]!="queued":
            raise RuntimeError("synthetic fixture rejected")
        with patch.object(pub,"_push_confirm",
                          side_effect=delivery.DeliveryError("injected offline outage")):
            try:
                pub.publish()
            except delivery.DeliveryError:
                pass
            else:
                raise RuntimeError("outage injection did not fail")
        archive=root/"durable-outbox.zip"
        pub.export_outbox(archive)
        recovered=root/"recovered"
        ops.git(root,"clone",remote,recovered)
        ops.git(recovered,"checkout",delivery.BRANCH)
        receiver=delivery.DeliveryPublisher(recovered,clock=lambda:now)
        restored=receiver.restore_outbox(archive)
        published=receiver.publish()
        if published["status"]!="published":
            raise RuntimeError("restored delivery was not published")
        consumer=root/"consumer"
        ops.git(root,"clone",remote,consumer)
        ops.git(consumer,"checkout",delivery.BRANCH)
        proof=delivery.inspect_remote_delivery(
            consumer,now+timedelta(seconds=observe_after_seconds))
        return {
            "kind":"synthetic_shadow_incremental_pilot_v1",
            "synthetic":True,"production_data_accessed":False,
            "live_telegram_connected":False,"remote_type":"disposable_local_bare_git",
            "injected_first_push_failure":True,
            "outbox_restored_on_fresh_checkout":restored["status"]=="restored",
            "remote_receipt_verified":proof["with_receipt"]==1,
            "events":proof["events"],
            "within_120s_transport":proof["within_120s_transport"],
            "late_or_unproven":proof["late_or_unproven"],
            "no_duplicate_events":proof["events"]==1,
            "scientific_certification":"not_activated",
            "real_runner_liveness_proven":False,
            "real_remote_delivery_proven":False,
            "source_authenticated":False,
            "production_workflow_modified":False,
        }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--observe-after-seconds",type=int,default=45)
    p.add_argument("--output",required=True)
    args=p.parse_args()
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    result=pilot(observe_after_seconds=args.observe_after_seconds)
    with output.open("x",encoding="utf-8") as f:
        json.dump(result,f,sort_keys=True,indent=2)
        f.write("\n")
    print(json.dumps(result,sort_keys=True))


if __name__=="__main__":
    main()
