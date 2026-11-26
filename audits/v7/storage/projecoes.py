"""Storage scenarios from recorded measurements, in GiB; never a provider bill."""
import csv
import json
from pathlib import Path

here = Path(__file__).resolve().parent
measured = json.loads((here / "crescimento-git.json").read_bytes())["windows"]
thin = json.loads((here / "packs-incrementais.json").read_bytes())
differential = json.loads((here / "packs-diferenciais-snapshots.json").read_bytes())
archive = json.loads((here / "simulacao-cinco-anos.json").read_bytes())["simulation"]["series"]
files = "data/report.json + docs/report.json (blobs únicos)"
reports_per_day = [measured[n]["groups"][files]["objects"] / measured[n]["days"] for n in ("1", "3", "7")]
observed = (min(reports_per_day), max(reports_per_day))
gib = 2**30
per_day = {
    "relatorio_estimativa_anterior": (2.6 * gib / 365, 3.7 * gib / 365),
    "git_pack_total_janelas_7d_a_24h": (measured["7"]["total_packed"] / measured["7"]["days"],
                                       measured["1"]["total_packed"] / measured["1"]["days"]),
    "git_envio_thin_janelas_7d_a_24h": (thin["7"]["bytes"] / thin["7"]["days"],
                                        thin["1"]["bytes"] / thin["1"]["days"]),
    "relatorio_isolado_delta_60_revisoes": tuple(n * differential["growth_bytes_per_revision"]["report"] for n in observed),
    "incremento_snapshots_nomes_estaveis": tuple(n * differential["snapshot_increment_bytes"]["stable"] for n in observed),
    "incremento_snapshots_nomes_hash": tuple(n * differential["snapshot_increment_bytes"]["hashed"] for n in observed),
}
with (here / "projecoes-cinco-anos.csv").open("w", newline="") as f:
    writer = csv.writer(f, lineterminator="\n")
    writer.writerow(("cenario", "dias", "min_GiB", "max_GiB"))
    for days in (30, 90, 365, 1825):
        for scenario, (low, high) in per_day.items():
            writer.writerow((scenario, days, f"{low*days/gib:.6f}", f"{high*days/gib:.6f}"))
        simulation = archive[str(days)]["stored_bytes"] / gib
        writer.writerow(("modelo_arquivo_segmentado_so_diario", days, f"{simulation:.6f}", f"{simulation:.6f}"))
