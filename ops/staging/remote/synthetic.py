"""Gera um relatório e uma entrada prospetiva 100 % sintéticos para o staging remoto (opção A).

    python3 ops/staging/remote/synthetic.py --out <pasta> --rev <n>

Não lê nenhum ficheiro de dados reais: equipas "Equipa A01…", competições "Liga Sintética", valores determinísticos por
revisão. Escreve <pasta>/report.json e <pasta>/prospective.json. Recusa escrever em docs/ ou data/.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = "Q7 Todas as apostas (referência)"
BASE = datetime(2030, 1, 1, tzinfo=timezone.utc)
MARK = "SINTÉTICO — sem dados reais"


def report(rev):
    rng = random.Random(rev)
    gen = BASE + timedelta(minutes=10 * rev)
    groups = {"SINTETICO_%d" % i: {"bets": 20 + i + rev, "settled": 10 + i} for i in range(3)}
    bets = sum(g["bets"] for g in groups.values())
    settled = sum(g["settled"] for g in groups.values())
    timeline = [{"d": (gen - timedelta(hours=h)).isoformat(), "units": round(rng.uniform(-1, 1), 2)} for h in range(24, 0, -1)]
    return {"generated_at_utc": gen.isoformat(), "synthetic": MARK, "unique_bets": bets, "settled_bets": settled, "groups": groups,
            "questions": {REFERENCE: {"bets": bets, "settled": settled}}, "audit": {"synthetic": True}, "brain": {}, "insights": {},
            "timeline": timeline, "daily": [{"d": (gen - timedelta(days=d)).date().isoformat(), "bets": rng.randint(0, 9)} for d in range(7)],
            "matches": {"sintetico-%02d" % i: {"teams": ["Equipa A%02d" % i, "Equipa B%02d" % i], "events": []} for i in range(5)}}


def prospective(rev, schema):
    return {"schema": schema, "synthetic": MARK, "ledger_entries": rev, "games_registered": 0, "signals_registered": 0,
            "signals_registered_before_result": 0, "signals_result_known_at_registration": 0, "games_with_independent_result": 0,
            "games_with_conflicting_results": 0, "games_without_result_after_72h": 0,
            "H1": {"status": "insufficient_games", "games_with_result": 0, "games_in_population": 0},
            "H2": {"status": "insufficient_games", "games_settled": 0, "wins": 0, "losses": 0}, "D1": {},
            "verdict": "RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA (dados sintéticos)"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rev", type=int, required=True)
    ap.add_argument("--schema", default="goal_roi_prospective_report_v1")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    for forbidden in (ROOT / "docs", ROOT / "data"):
        if out == forbidden or forbidden in out.parents:
            raise SystemExit("recusado: não escrever em docs/ nem em data/")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report(a.rev), ensure_ascii=False), encoding="utf-8")
    (out / "prospective.json").write_text(json.dumps(prospective(a.rev, a.schema), ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"rev": a.rev, "out": str(out)}))


if __name__ == "__main__":
    main()
