"""Publica uma revisão de snapshots na origem do staging local com o publicador V7 e o builder real.

    python3 ops/staging/publish_rev.py --out <pasta-origem> --rev <n> [--report docs/report.json]

Copia o relatório, a entrada prospetiva e o protocolo para uma pasta temporária, marca a revisão em
generated_at_utc (só na cópia) e chama audits/v7/snapshots/publisher.build. Nunca escreve em docs/ nem em data/.
Imprime {"status": ..., "slot": ...}.
"""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from audits.v7.snapshots import publisher as P  # noqa: E402
from audits.v7.snapshots.real_run import node_builder  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--rev", type=int, required=True)
    ap.add_argument("--report", default=str(ROOT / "docs/report.json"))
    a = ap.parse_args()
    out = Path(a.out).resolve()
    for forbidden in (ROOT / "docs", ROOT / "data"):
        if out == forbidden or forbidden in out.parents:
            raise SystemExit("recusado: a origem do staging não pode estar em docs/ nem em data/")
    with tempfile.TemporaryDirectory(prefix="v7-staging-in-") as tmp:
        base = Path(tmp)
        inputs = {"report": base / "report.json", "prospective": base / "prospective.json", "protocol": base / "protocol.json"}
        report = json.loads(Path(a.report).read_bytes())
        # Instantes posteriores ao relatório original: o builder recusa liquidações posteriores à geração.
        stamp = datetime.fromisoformat(report["generated_at_utc"]) + timedelta(minutes=10 * a.rev)
        report["generated_at_utc"] = stamp.isoformat()
        inputs["report"].write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        shutil.copyfile(ROOT / "data/prospective_report.json", inputs["prospective"])
        shutil.copyfile(ROOT / "scangoal/prospective_protocol.json", inputs["protocol"])
        status = P.build(out, inputs, node_builder(ROOT))
    pointer = P.read_pointer(out)
    print(json.dumps({"status": status, "slot": pointer["current"]["slot"], "rev": a.rev}))


if __name__ == "__main__":
    main()
