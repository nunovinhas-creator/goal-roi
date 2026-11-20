"""Read-only SHADOW preflight plus synthetic cross-runner outbox transport.

NO activation, real signals, vendor requests, secrets, GitHub pushes or Telegram.
Only stage/restore fake Git repositories under temporary directories.
"""
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
UTC = timezone.utc
FILES = ("messages.jsonl", "results_hl.json", "results_bsd.json",
         "details_hl.json", "manual_results.json")
ARTIFACTS = ("outbox.zip", "remote.bundle", "source.bundle", "synthetic-data.zip")
MAX_FIXTURE_BYTES = 5_000_000


def parse_time(raw):
    if not isinstance(raw, str):
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return stamp.astimezone(UTC) if stamp.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def measure(rows, now, *, hours=24, capture_seconds=120):
    """Earliest collector observation is a necessary, NOT sufficient, condition."""
    from scangoal.parser import parse
    if (not isinstance(now, datetime) or now.tzinfo is None or
            not 1 <= hours <= 72 or not 1 <= capture_seconds <= 120):
        raise ValueError("invalid preflight observation window")
    first = {}
    inconsistent = set()
    invalid = 0
    for row in rows:
        if not isinstance(row, dict):
            invalid += 1
            continue
        try:
            signal = parse(row.get("text") or "")
        except (TypeError, ValueError, KeyError, AttributeError):
            invalid += 1
            continue
        if signal.get("type") != "OPEN_GAME":
            continue
        mid = row.get("message_id")
        sent, seen = parse_time(row.get("date_utc")), parse_time(row.get("collected_at_utc"))
        if type(mid) is not int or mid < 1 or not sent or not seen:
            invalid += 1
            continue
        if not (now - timedelta(hours=hours) <= sent <= now):
            continue
        old = first.get(mid)
        record = (sent, seen, row.get("edit_date_utc"), row["text"])
        if old is not None and (sent != old[0] or (seen == old[1] and record != old)):
            inconsistent.add(mid)
        if old is None or seen < old[1]:
            first[mid] = record
    valid = [(seen - sent).total_seconds()
             for mid, (sent, seen, edit, _) in first.items()
             if mid not in inconsistent and sent <= seen <= now and edit is None]
    invalid += len(first) - len(valid)
    on_time = sum(x <= capture_seconds for x in valid)
    delays = sorted(valid)
    result = {
        "kind": "shadow_delivery_lower_bound_v1",
        "measured_utc": now.isoformat(),
        "window_hours": hours,
        "capture_limit_seconds": capture_seconds,
        "open_game_messages": len(first),
        "valid_first_observations": len(valid),
        "first_observed_within_limit": on_time,
        "first_observed_over_limit": len(valid) - on_time,
        "invalid_or_unproven_timestamp": invalid,
        "first_observation_p50_seconds": delays[(len(delays)-1)//2] if delays else None,
        "first_observation_max_seconds": max(delays) if delays else None,
        "edited_at_first_observation": sum(v[2] is not None for v in first.values()),
        "remote_git_delivery_measured": False,
        "dedicated_runner_capture_measured": False,
        "outbox_production_recovery_measured": False,
        "activation_approved": False,
        "scientific_state": "INCONCLUSIVO",
        "conclusion": "NOT_READY",
        "interpretation": ("First collector sighting only. Separate runner can see a signal "
                           "no earlier than collector ingestion AND remote Git publication; "
                           "this experiment proves neither the Git push latency nor the "
                           "next runner start delay.")
    }
    return result


def write_new(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        json.dump(data, f, sort_keys=True, ensure_ascii=False, indent=2)
        f.write("\n")


def measure_file(path, output, hours=24):
    with Path(path).open(encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    result = measure(rows, datetime.now(UTC), hours=hours)
    write_new(output, result)
    print(json.dumps({key: value for key, value in result.items()
                      if key in ("open_game_messages", "valid_first_observations",
                                 "first_observed_within_limit",
                                 "first_observed_over_limit", "conclusion",
                                 "remote_git_delivery_measured")}, sort_keys=True))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stage(directory):
    """Synthetic local Git push failure -> real outbox ZIP and Git bundles."""
    sys.path.insert(0, str(ROOT / "tests"))
    import test_shadow_ops as fixture
    from scangoal import shadow_ops as ops

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=False)
    fixture.T = datetime.now(UTC)
    case = fixture.OperationalShadow()
    case.setUp()
    try:
        case.step(60)
        case.message(age=30)
        original_publish = case.pub.publish
        calls = [0]

        def interrupted():
            calls[0] += 1
            if calls[0] == 2:
                raise ops.OpsError("synthetic push outage")
            return original_publish()

        with patch.object(case.pub, "publish", interrupted):
            try:
                ops.tick(case.pub, case.source, case.auth)
            except ops.OpsError:
                pass
            else:
                raise AssertionError("synthetic push outage was not reproduced")
        events = case.ledger()["events"]
        decisions = [e for e in events if e["kind"] == "decision"]
        if len(decisions) != 1:
            raise AssertionError("expected exactly one pending synthetic decision")
        if ops.read(case.pub.state / "receipts.json") != []:
            raise AssertionError("receipt must be absent before failed push recovery")
        ops.export_outbox(case.pub, target / "outbox.zip")
        ops.git(case.remote, "bundle", "create", target / "remote.bundle", "--all")
        ops.git(case.source, "bundle", "create", target / "source.bundle", "--all")
        with zipfile.ZipFile(target / "synthetic-data.zip", "w",
                             compression=zipfile.ZIP_DEFLATED) as z:
            for name in FILES:
                f = case.data / name
                if f.is_file():
                    z.write(f, arcname=name)
        write_new(target / "manifest.json", {
            "fixture_only": True,
            "production_data": False,
            "publication": "local bare Git only",
            "approved_source_sha": case.approved,
            "simulated_clock_utc": case.time.isoformat(),
            "protocol_ident": case.ident,
            "expected_ledger_digest": ops.shadow.digest(events),
            "decision_hash": decisions[0]["hash"],
            "file_sha256": {n: sha256(target/n) for n in ARTIFACTS},
        })
        print("SHADOW_SYNTHETIC_OUTBOX_STAGED")
    finally:
        case.doCleanups()


def restore(directory, output):
    """Fresh process/runner restores saved objects, events and remote receipt."""
    from scangoal import shadow_ops as ops
    target = Path(directory)
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("fixture_only") is not True or manifest.get("production_data") is not False:
        raise ValueError("only the synthetic test bundle is accepted")
    hashes = manifest["file_sha256"]
    if not isinstance(hashes, dict) or set(hashes) != set(ARTIFACTS):
        raise ValueError("missing or unexpected synthetic artifacts")
    for name, expected in hashes.items():
        path = target / name
        if path.is_symlink() or path.stat().st_size > MAX_FIXTURE_BYTES or sha256(path) != expected:
            raise ValueError("artifact checksum or size mismatch")
    with tempfile.TemporaryDirectory(prefix="goal-shadow-runner-") as dirname:
        tmp = Path(dirname)
        remote, source, publisher = tmp/"remote.git", tmp/"source", tmp/"publisher"
        ops.git(tmp, "clone", "--bare", str(target/"remote.bundle"), str(remote))
        ops.git(tmp, "clone", str(target/"source.bundle"), str(source))
        ops.git(tmp, "clone", "--branch", "shadow/h1-h2-operational",
                str(remote), str(publisher))
        data = source / "data"
        data.mkdir()
        with zipfile.ZipFile(target/"synthetic-data.zip") as z:
            names = z.namelist()
            if (set(names) - set(FILES) or len(names) != len(set(names)) or
                    sum(entry.file_size for entry in z.infolist()) > MAX_FIXTURE_BYTES):
                raise ValueError("unexpected synthetic data archive")
            for name in names:
                (data / name).write_bytes(z.read(name))
        auth = tmp/"synthetic-authorization.json"
        synthetic_clock = parse_time(manifest["simulated_clock_utc"])
        if synthetic_clock is None:
            raise ValueError("invalid fixture clock")
        write_new(auth, {
            "authorized": True,
            "installed": True,
            "authorization_reference": "SYNTHETIC_TEST_ONLY",
            "installed_runner_reference": "SYNTHETIC_FRESH_RUNNER",
            "approved_code_sha": manifest["approved_source_sha"],
            "authorized_utc": (synthetic_clock-timedelta(minutes=1)).isoformat(),
        })
        # A fixed synthetic clock checks byte-for-byte recovery, not live timeliness.
        pub = ops.Publisher(publisher, clock=lambda: synthetic_clock + timedelta(seconds=2))
        recovered = ops.restore_outbox(pub, target/"outbox.zip")
        if recovered["state"] != "restored":
            raise AssertionError("outbox not restored")
        current = ops.read(pub.state/"current.json")
        ident = current["protocol_sha256"]
        ledger = ops.read(pub.state/"ledgers"/f"{ident}.json")
        if ops.shadow.digest(ledger["events"]) != manifest["expected_ledger_digest"]:
            raise AssertionError("restored ledger differs from source")
        checked = ops.tick(pub, source, auth)
        if checked["problems"]:
            raise AssertionError("synthetic recovered publication is quarantined")
        after = ops.read(pub.state/"ledgers"/f"{ident}.json")
        if ops.shadow.digest(after["events"]) != manifest["expected_ledger_digest"]:
            raise AssertionError("recovery duplicated or rewrote events")
        receipts = ops.read(pub.state/"receipts.json")
        if len(receipts) != 1 or receipts[0]["decision_hashes"] != [manifest["decision_hash"]]:
            raise AssertionError("recovered remote receipt not unique")
        head = ops.git(publisher, "rev-parse", "HEAD")
        verified = ops.git(publisher, "ls-remote", "origin",
                           "refs/heads/shadow/h1-h2-operational").split()[0]
        if head != verified:
            raise AssertionError("synthetic remote head not verified")
        write_new(output, {
            "fixture_only": True, "separate_runner_process": True,
            "outbox_restore": "passed", "ledger_preserved": True,
            "exactly_one_decision": True, "exactly_one_receipt": True,
            "synthetic_remote_verified": True, "remote_sha": head,
            "real_production_recovery_verified": False,
            "real_capture_latency_verified": False, "activation_approved": False,
        })
    print("SHADOW_SYNTHETIC_FRESH_RUNNER_RESTORED")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="action", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--path", required=True)
    m.add_argument("--output", required=True)
    m.add_argument("--window-hours", type=int, default=24)
    st = sub.add_parser("stage")
    st.add_argument("--dir", required=True)
    re = sub.add_parser("restore")
    re.add_argument("--dir", required=True)
    re.add_argument("--output", required=True)
    a = ap.parse_args()
    if a.action == "measure":
        measure_file(a.path, a.output, a.window_hours)
    elif a.action == "stage":
        stage(a.dir)
    else:
        restore(a.dir, a.output)


if __name__ == "__main__":
    main()
