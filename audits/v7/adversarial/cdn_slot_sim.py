"""Simulação offline: ponteiro em cache CDN × reutilização de ranhuras do publicador V7 (PR #103).

Modelo (sem rede, sem fornecedores):
- Publicações nos instantes reais em que docs/report.json mudou em main (7 dias, ficheiro publicacoes-report-7d.txt).
  Pressupõe-se que cada mudança do relatório gera uma revisão de snapshots (o caso "unchanged" só ocorre com
  entradas idênticas).
- O ponteiro (dashboard-manifest.json) é servido com Cache-Control max-age=TTL (Pages/Fastly: 600 s). Um leitor recebe
  uma cópia com idade uniforme em [0, TTL] (pior caso: a CDN guardou a cópia no início da janela).
- Os corpos são pedidos com ?v=<sha16> (client_integrity.mjs): chave nova = falha de cache = bytes atuais da origem.
- Com S ranhuras em rotação e k publicações depois de a CDN ter obtido o ponteiro: a ranhura de "previous" é
  reescrita na publicação S-1 e a de "current" na publicação S. Logo current sobrevive se k <= S-1 e previous morre
  sempre antes de current: o recuo para "previous" nunca salva uma leitura atrasada pela CDN.
- Resultado por leitura: "current" íntegra ou "relatório completo" (~4 MB descarregados).

Também mede o efeito de limitar a publicação de snapshots a uma por janela mínima (rate limit).

    python3 audits/v7/adversarial/cdn_slot_sim.py [--ttl 600] [--samples 200000]
"""
import argparse
import bisect
import json
from pathlib import Path
import random

HERE = Path(__file__).resolve().parent


def load_times(path):
    return [int(x) for x in Path(path).read_text().split()]


def rate_limit(times, min_gap):
    out = []
    for t in times:
        if not out or t - out[-1] >= min_gap:
            out.append(t)
    return out


def simulate(times, ttl, slots, samples, seed=7):
    rng = random.Random(seed)
    lo, hi = times[0] + ttl, times[-1]
    res = {"current": 0, "full_report": 0}
    for _ in range(samples):
        now = rng.uniform(lo, hi)
        fetched = now - rng.uniform(0, ttl)
        k = bisect.bisect_right(times, now) - bisect.bisect_right(times, fetched)
        if k <= slots - 1:
            res["current"] += 1
        else:
            res["full_report"] += 1
    return {k: round(v / samples, 4) for k, v in res.items()}


def simulate_cas(times, ttl, retain_s, keep_min, samples, seed=7):
    """CAS com retenção contada desde a substituição: a revisão atual de um ponteiro obtido em f só é recolhida numa
    publicação p com p - (instante em que foi substituída) > retain_s e pelo menos keep_min revisões mais recentes."""
    rng = random.Random(seed)
    lo, hi = times[0] + ttl, times[-1]
    lost = 0
    for _ in range(samples):
        now = rng.uniform(lo, hi)
        fetched = now - rng.uniform(0, ttl)
        r = bisect.bisect_right(times, fetched) - 1            # revisão atual quando o ponteiro foi obtido
        if r + 1 >= len(times) or times[r + 1] > now:
            continue                                           # ainda é a atual
        superseded = times[r + 1]
        later = [p for p in times[r + 1:bisect.bisect_right(times, now)]]
        if any(p - superseded > retain_s and i + 1 >= keep_min for i, p in enumerate(later)):
            lost += 1
    return {"current": round(1 - lost / samples, 4), "full_report": round(lost / samples, 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--times", default=str(HERE / "publicacoes-report-7d.txt"))
    ap.add_argument("--ttl", type=int, default=600)
    ap.add_argument("--samples", type=int, default=200000)
    ap.add_argument("--out", default=str(HERE / "resultados-cdn-ranhuras.json"))
    a = ap.parse_args()
    times = load_times(a.times)
    gaps = sorted(b - x for x, b in zip(times, times[1:]))
    out = {"ttl_s": a.ttl, "publications": len(times), "span_h": round((times[-1] - times[0]) / 3600, 1),
           "gap_s": {"p10": gaps[len(gaps) // 10], "p50": gaps[len(gaps) // 2], "p90": gaps[9 * len(gaps) // 10]},
           "share_gap_below_ttl": round(sum(g < a.ttl for g in gaps) / len(gaps), 4),
           "share_two_pubs_within_ttl": round(sum(times[i + 2] - times[i] < a.ttl for i in range(len(times) - 2)) / (len(times) - 2), 4),
           "scenarios": []}
    for label, min_gap in (("sem limite", 0), ("limite 300 s", 300), ("limite 600 s", 600), ("limite 900 s", 900)):
        ts = rate_limit(times, min_gap)
        for slots in (2, 3, 4):
            r = simulate(ts, a.ttl, slots, a.samples)
            out["scenarios"].append({"rate_limit": label, "slots": slots, "publications": len(ts), **r})
    for retain in (300, 600, 1800):
        r = simulate_cas(times, a.ttl, retain, 2, a.samples)
        out["scenarios"].append({"rate_limit": "sem limite", "layout": "cas", "retain_s": retain, "publications": len(times), **r})
    Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
