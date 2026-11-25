"""Replay a prospective code change and rollback over a pinned ledger, offline."""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


def git_blob(repository, revision, path):
    return subprocess.check_output(
        ["git", "-C", str(repository), "show", f"{revision}:{path}"]
    )


def sha256(body):
    return hashlib.sha256(body).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("baseline_revision")
    parser.add_argument("candidate_revision")
    args = parser.parse_args()
    sys.path.insert(0, str(args.repository.resolve()))
    from scangoal import prospective

    originals = {
        name: git_blob(args.repository, args.baseline_revision, f"scangoal/{name}")
        for name in prospective.CODE
    }
    candidates = {
        name: git_blob(args.repository, args.candidate_revision, f"scangoal/{name}")
        for name in prospective.CODE
    }
    changed = [name for name in originals if originals[name] != candidates[name]]
    assert changed == ["results_bsd.py"], changed
    assert Path(prospective.__file__).read_bytes() == originals["prospective.py"]

    ledger = git_blob(args.repository, args.baseline_revision, f"data/{prospective.LEDGER_NAME}")
    initial = [json.loads(line) for line in ledger.splitlines()]
    protocol = "prospective_protocol.json"
    protocol_sha = sha256(originals[protocol])
    assert initial[0]["protocol_sha256"] == protocol_sha
    original_manifest = {name: sha256(contents) for name, contents in originals.items()}
    candidate_manifest = {name: sha256(contents) for name, contents in candidates.items()}
    recorded_manifest = next(entry["code_sha256"] for entry in reversed(initial) if "code_sha256" in entry)
    assert recorded_manifest == original_manifest
    prospective.verify(initial)
    last_time = datetime.fromisoformat(initial[-1]["at_utc"])

    stages = {}
    with tempfile.TemporaryDirectory(prefix="goal-roi-bsd-prospective-") as temporary:
        root = Path(temporary)
        source = root / "scangoal"
        source.mkdir()
        ledger_path = root / prospective.LEDGER_NAME
        ledger_path.write_bytes(ledger)
        protocol_path = source / protocol

        def install(files):
            for name, contents in files.items():
                (source / name).write_bytes(contents)

        def stage(label, minutes, expected):
            before = ledger_path.read_bytes()
            now = last_time + timedelta(minutes=minutes)
            assert prospective.record(now, signals=[], providers={}) == 0
            after = ledger_path.read_bytes()
            assert after.startswith(before)
            history = prospective.load()
            prospective.verify(history)
            appended = [json.loads(line) for line in after[len(before):].splitlines()]
            assert [entry["kind"] for entry in appended] == (
                ["code_version"] if expected else []
            )
            if expected:
                assert appended[0]["code_sha256"] == expected
            stages[label] = {
                "entries": len(history),
                "history_prefix_identical": after.startswith(ledger),
                "prefix_before_stage_identical": after.startswith(before),
                "appended": [
                    {"kind": entry["kind"], "seq": entry["seq"], "sha256": entry["sha256"]}
                    for entry in appended
                ],
                "head_sha256": history[-1]["sha256"],
                "ledger_sha256": sha256(after),
                "chain_verified": True,
            }

        with patch.object(prospective, "HERE", source), patch.object(
            prospective, "PROTOCOL", protocol_path
        ), patch.object(prospective, "data_dir", return_value=root):
            install(originals)
            stage("baseline_repeat", 1, None)
            install(candidates)
            stage("apply_code_change", 2, candidate_manifest)
            stage("repeat_code_change", 3, None)
            install(originals)
            stage("rollback_code_change", 4, original_manifest)
            stage("repeat_rollback", 5, None)

            before_invalid_protocol = ledger_path.read_bytes()
            protocol_path.write_bytes(originals[protocol] + b"\n")
            try:
                prospective.record(last_time + timedelta(minutes=6), signals=[], providers={})
            except prospective.LedgerError as error:
                assert str(error) == "protocol_changed_since_activation"
            else:
                raise AssertionError("Changed protocol accepted")
            assert ledger_path.read_bytes() == before_invalid_protocol
            prospective.verify(prospective.load())
            stages["change_protocol_control"] = {
                "blocked": True,
                "ledger_bytes_identical": True,
                "chain_verified": True,
            }

    print(json.dumps({
        "baseline_revision": args.baseline_revision,
        "candidate_revision": args.candidate_revision,
        "historical_entries": len(initial),
        "historical_ledger_sha256": sha256(ledger),
        "historical_head_sha256": initial[-1]["sha256"],
        "activation_matches_frozen_protocol": True,
        "protocol_sha256": protocol_sha,
        "manifest_files": list(originals),
        "manifest_diff": changed,
        "results_bsd_sha256": {
            "baseline_and_rollback": original_manifest["results_bsd.py"],
            "candidate": candidate_manifest["results_bsd.py"],
        },
        "stages": stages,
        "provider_requests": 0,
        "outcomes_injected": 0,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
