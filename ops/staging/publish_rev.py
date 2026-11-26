"""Publica uma revisão de snapshots na origem do staging local com o publicador V7 e o builder real.

    python3 ops/staging/publish_rev.py --out <pasta-origem> --rev <n> [--design v1|cas|slots3] [--now <s>] [--lock <f>]
                                       [--report docs/report.json] [--prospective data/prospective_report.json]

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
from audits.v7.snapshots import publisher_v2 as P2  # noqa: E402
from audits.v7.snapshots.real_run import node_builder  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--rev", type=int, required=True)
    ap.add_argument("--report", default=str(ROOT / "docs/report.json"))
    ap.add_argument("--prospective", default=str(ROOT / "data/prospective_report.json"))
    ap.add_argument("--design", choices=("v1", "cas", "slots3"), default="v1")
    ap.add_argument("--now", type=float, default=0.0)
    ap.add_argument("--lock")
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
        shutil.copyfile(a.prospective, inputs["prospective"])
        shutil.copyfile(ROOT / "scangoal/prospective_protocol.json", inputs["protocol"])
        if a.design == "v1":
            status = P.build(out, inputs, node_builder(ROOT))
        else:
            kw = dict(layout="cas") if a.design == "cas" else dict(layout="slots", slots=3)
            pub = P2.Publisher(out, lock=a.lock, clock=lambda: a.now, **kw)
            status = pub.build(inputs, node_builder(ROOT))
    pointer = (P if a.design == "v1" else P2).read_pointer(out)
    where = pointer["current"].get("slot") or pointer["current"]["files"]["summary"]["path"].split("/")[0]
    print(json.dumps({"status": status, "where": where, "rev": a.rev}))


if __name__ == "__main__":
    main()
