"""Ensaios do publicador V7 com o builder real da PR #98 (só leitura do checkout) e o relatório do corte.

    python3 audits/v7/snapshots/real_run.py --pr <checkout-pr98> --report-root <checkout-corte>

Escreve apenas numa pasta temporária. Reproduz os quatro cenários da auditoria (corrupção, interrupção, entrada
prospetiva alterada, adulteração com igual comprimento) e mede o tempo de publicação.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from audits.v7.snapshots import publisher as P  # noqa: E402


def node_builder(pr):
    script = Path(pr) / "scripts/build_dashboard_snapshots.cjs"

    def run(inputs, staging):
        out = staging / "node"
        subprocess.run(["node", str(script), "--report", str(inputs["report"]), "--prospective", str(inputs["prospective"]),
                        "--protocol", str(inputs["protocol"]), "--out", str(out)], check=True, capture_output=True, timeout=90)
        manifest = json.loads((out / "dashboard-manifest.json").read_bytes())
        if manifest.get("report_sha256") != P.sha(inputs["report"]):
            raise P.SnapshotError("o builder descreve outro relatório")
        for name in P.NAMES:
            shutil.move(str(out / ("dashboard-%s.json" % name)), staging / ("dashboard-%s.json" % name))
        return {"report_generated_at_utc": manifest.get("report_generated_at_utc")}
    return run


def revise(path, field, delta=None, value=None):
    data = json.loads(Path(path).read_bytes())
    data[field] = value if value is not None else data[field] + delta
    Path(path).write_text(json.dumps(data, ensure_ascii=False))


def survey(pr, report_root):
    builder, results, timings = node_builder(pr), {}, []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        inputs = {"report": base / "report.json", "prospective": base / "prospective.json", "protocol": base / "protocol.json"}
        shutil.copyfile(Path(report_root) / "docs/report.json", inputs["report"])
        shutil.copyfile(Path(report_root) / "data/prospective_report.json", inputs["prospective"])
        shutil.copyfile(Path(pr) / "scangoal/prospective_protocol.json", inputs["protocol"])
        out = base / "out"

        def timed(**kw):
            t = time.perf_counter()
            status = P.build(out, inputs, kw.get("builder", builder))
            timings.append(round(time.perf_counter() - t, 3))
            return status

        results["first_publish"] = timed()
        results["second_build"] = timed()
        # 1. corrupção na mesma revisão
        (out / "a/dashboard-signals.json").write_text("{}")
        results["corrupted_same_revision"] = timed()
        P.verify_live(out, inputs)
        results["corrupted_same_revision_verified_after"] = True
        # 2. interrupção em cada rename da nova revisão
        revise(inputs["report"], "generated_at_utc", value="2026-11-26T11:59:59+00:00")
        old = (out / P.POINTER).read_bytes()
        original, mixed = os.replace, 0
        for stop in range(1, len(P.NAMES) + 2):
            calls = [0]

            def interrupt(src, dst):
                calls[0] += 1
                if calls[0] == stop:
                    raise OSError("interrupção")
                return original(src, dst)
            with patch.object(P.os, "replace", side_effect=interrupt):
                try:
                    P.build(out, inputs, builder)
                except OSError:
                    pass
            if (out / P.POINTER).read_bytes() != old:
                mixed += 1
            try:
                P.verify_revision(json.loads(old)["current"], out)
            except P.SnapshotError:
                mixed += 1
        results["interruption_points_tested"] = len(P.NAMES) + 1
        results["interruption_mixed_revisions"] = mixed
        results["after_interruption"] = timed()
        P.verify_live(out, inputs)
        # 3. entrada prospetiva alterada durante o builder
        revise(inputs["report"], "generated_at_utc", value="2026-11-26T12:00:01+00:00")
        before_ptr = (out / P.POINTER).read_bytes()

        def racing(copies, staging):
            meta = builder(copies, staging)
            revise(inputs["prospective"], "ledger_entries", delta=1)
            return meta
        try:
            P.build(out, inputs, racing)
            results["prospective_change_rejected"] = False
        except P.SnapshotError:
            results["prospective_change_rejected"] = (out / P.POINTER).read_bytes() == before_ptr
        results["after_prospective_change"] = timed()
        pointer = P.verify_live(out, inputs)
        results["pointer_prospective_matches_live"] = pointer["current"]["inputs"]["prospective"] == P.sha(inputs["prospective"])
        # 4. adulteração com igual comprimento
        body = out / pointer["current"]["slot"] / "dashboard-summary.json"
        data = body.read_bytes()
        i = data.index(b"0")
        body.write_bytes(data[:i] + b"1" + data[i + 1:])
        try:
            P.verify_live(out, inputs)
            results["same_length_tampering_rejected"] = False
        except P.SnapshotError:
            results["same_length_tampering_rejected"] = True
        results["fallback_to_previous_slot"] = (P.read_verified(out) or {}).get("slot") != pointer["current"]["slot"]
        results["previous_revision_intact"] = P.read_verified(out) is not None
    results["publish_seconds"] = timings
    return results


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--pr", required=True, type=Path)
    cli.add_argument("--report-root", required=True, type=Path)
    args = cli.parse_args()
    print(json.dumps(survey(args.pr.resolve(), args.report_root.resolve()), indent=2))


if __name__ == "__main__":
    main()
