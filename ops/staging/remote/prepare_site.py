"""Prepara (não publica) o site do staging remoto, opção A: GitHub Pages num repositório separado, dados sintéticos.

    python3 ops/staging/remote/prepare_site.py --out <pasta-nova> [--revisions 6]

Gera K revisões sintéticas, publica-as com publisher_v2 (layout cas) em <pasta>/snapshots/ e copia o cliente V7.2 e uma
página de medição (index.html + probe.mjs). Escreve .nojekyll e SITE-MANIFEST.json (SHA-256 de cada ficheiro).
Nada sai da máquina. A publicação (criar o repositório e ativar o Pages) exige autorização explícita: ver README.md.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from audits.v7.snapshots import publisher_v2 as P  # noqa: E402

INDEX = """<!doctype html><html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Staging sintético</title><meta name="robots" content="noindex,nofollow"></head>
<body><h1>Staging sintético dos snapshots V7.2</h1><p>Dados 100 % sintéticos. Não é um produto nem uma previsão.</p>
<pre id="out">a medir…</pre><script type="module" src="probe.mjs"></script></body></html>
"""
PROBE = """import {loadVerified} from './client/client_integrity_v2.mjs';
const t0 = performance.now();
const get = async p => { const r = await fetch(p, {cache: 'no-cache'}); if (!r.ok) throw new Error(r.status + ' ' + p); return r.text(); };
const pointer = JSON.parse(await get('snapshots/dashboard-manifest.json'));
const got = await loadVerified(pointer, p => get('snapshots/' + p), undefined, async () => JSON.parse(await get('snapshots/dashboard-manifest.json')));
document.getElementById('out').textContent = JSON.stringify({which: got ? got.which : 'full_report', refetched: got && got.refetched,
  generated: got && got.revision.report_generated_at_utc, ms: Math.round(performance.now() - t0)}, null, 1);
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--revisions", type=int, default=6)
    a = ap.parse_args()
    out = Path(a.out).resolve()
    if out.exists():
        raise SystemExit("destino já existe: %s" % out)
    if ROOT in out.parents or out == ROOT:
        raise SystemExit("recusado: o site de staging fica fora do repositório")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        site = tmp / "site"
        (site / "client").mkdir(parents=True)
        for rev in range(1, a.revisions + 1):
            data = tmp / ("in%d" % rev)
            subprocess.run([sys.executable, str(HERE / "synthetic.py"), "--out", str(data), "--rev", str(rev)], check=True, capture_output=True)
            subprocess.run([sys.executable, str(HERE.parent / "publish_rev.py"), "--out", str(site / "snapshots"), "--rev", str(rev),
                            "--design", "cas", "--now", str(600.0 * rev), "--lock", str(tmp / "lock"),
                            "--report", str(data / "report.json"), "--prospective", str(data / "prospective.json")],
                           check=True, capture_output=True, cwd=ROOT)
        for f in ("client_integrity.mjs", "client_integrity_v2.mjs"):
            shutil.copyfile(ROOT / "audits/v7/snapshots" / f, site / "client" / f)
        (site / "index.html").write_text(INDEX, encoding="utf-8")
        (site / "probe.mjs").write_text(PROBE, encoding="utf-8")
        (site / ".nojekyll").write_text("")
        files = {p.relative_to(site).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(site.rglob("*")) if p.is_file()}
        (site / "SITE-MANIFEST.json").write_text(json.dumps({"synthetic": True, "files": files}, indent=1) + "\n")
        P.verify_revision(P.read_pointer(site / "snapshots")["current"], site / "snapshots")
        shutil.copytree(site, out)
    print(json.dumps({"out": str(out), "files": len(files) + 1}))


if __name__ == "__main__":
    main()
