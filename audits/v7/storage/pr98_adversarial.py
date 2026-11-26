"""Reproduce three failure modes on the unmodified, isolated PR #98."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch

from scangoal import dashboard_snapshots as snapshots


def setup(source, report_root, target):
    for name in (snapshots.BUILDER, Path("docs/v6/v6-core.js"), Path("docs/v6/v6-markets.js"), Path("scangoal/prospective_protocol.json")):
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target / name)
    for name in ("docs/report.json", "data/prospective_report.json"):
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(report_root / name, target / name)


def revise(path, field, value):
    data = json.loads(path.read_bytes())
    data[field] = value
    path.write_text(json.dumps(data, ensure_ascii=False))


def verify_live(target):
    out = target / snapshots.OUT
    snapshots.verify(json.loads((out / snapshots.MANIFEST).read_bytes()), out,
                     snapshots._sha(target / "docs/report.json"))


def survey(source, report_root):
    results = {}
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        for scenario in ("corruption", "interruption", "prospective"):
            target = base / scenario
            setup(source, report_root, target)
            assert snapshots.build(target) == "published"
            verify_live(target)
            out = target / snapshots.OUT
            pointer = out / snapshots.MANIFEST
            if scenario == "corruption":
                (out / snapshots.FILES[0]).write_text("{}")
                status = snapshots.build(target)
                assert status == "unchanged"
                try:
                    verify_live(target)
                except snapshots.SnapshotError:
                    results["corrupted_body_not_repaired_on_same_revision"] = True
                else:
                    raise AssertionError("precondition: body really corrupted")
            elif scenario == "interruption":
                old = pointer.read_bytes()
                original = os.replace
                revise(target / "docs/report.json", "generated_at_utc", "2026-11-26T11:59:59+00:00")
                def interrupt(source_path, target_path):
                    if target_path == out / snapshots.FILES[1]:
                        raise OSError("interruption after first body")
                    return original(source_path, target_path)
                with patch.object(snapshots.os, "replace", side_effect=interrupt):
                    try:
                        snapshots.build(target)
                    except OSError:
                        pass
                    else:
                        raise AssertionError("fault not reached")
                assert pointer.read_bytes() == old
                try:
                    snapshots.verify(json.loads(old), out, json.loads(old)["report_sha256"])
                except snapshots.SnapshotError:
                    results["interruption_leaves_old_manifest_and_mixed_bodies"] = True
                else:
                    raise AssertionError("precondition: first body changed")
                assert snapshots.build(target) == "published"
                verify_live(target)
                results["new_build_recovers_after_interruption"] = True
            else:
                revise(target / "docs/report.json", "generated_at_utc", "2026-11-26T12:00:01+00:00")
                prospective = target / "data/prospective_report.json"
                def change_after_build(*args, **kwargs):
                    built = subprocess.run(*args, **kwargs)
                    before = json.loads(prospective.read_bytes())
                    revise(prospective, "ledger_entries", before["ledger_entries"] + 1)
                    return built
                assert snapshots.build(target, runner=change_after_build) == "published"
                manifest = json.loads(pointer.read_bytes())
                assert manifest["prospective_sha256"] != snapshots._sha(prospective)
                results["changed_secondary_input_not_rejected"] = True
    return results


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--pr", required=True, type=Path)
    cli.add_argument("--report-root", required=True, type=Path)
    args = cli.parse_args()
    print(json.dumps(survey(args.pr.resolve(), args.report_root.resolve()), indent=2))


if __name__ == "__main__":
    main()
