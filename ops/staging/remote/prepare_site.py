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
PROBE = """import {createMonotonicReader} from './client/client_integrity_v2.mjs';
const out = document.getElementById('out');
let storage = null, lock = null, storageMode = 'memory';
try {
  if (navigator.locks && typeof navigator.locks.request === 'function') {
    storage = window.localStorage;
    lock = fn => navigator.locks.request('v7-staging-shared-verified-v2', {mode: 'exclusive'}, fn);
    storageMode = 'shared';
  }
} catch {}
if (!storage) {
  try { storage = window.sessionStorage; storageMode = 'session'; } catch {}
}
const reader = createMonotonicReader({storage, lock, key: 'v7-staging-verified-v2'});
const get = async (p, refresh = false) => {
  const r = await fetch(p, {cache: refresh ? 'reload' : 'no-cache', redirect: 'error', credentials: 'omit', signal: AbortSignal.timeout(10000)});
  if (!r.ok) throw new Error(r.status + ' ' + p);
  return r.text();
};
const pointer = async (refresh = false) => JSON.parse(await get('snapshots/dashboard-manifest.json', refresh));
async function refresh() {
  const t0 = performance.now();
  let initial = null;
  try { initial = await pointer(); } catch {}
  const got = await reader.read(initial, p => get('snapshots/' + p), undefined, () => pointer(true));
  out.textContent = JSON.stringify({which: got.which, stale: got.stale, reason: got.reason,
    refetched: got.refetched, storage_available: !!storage, storage_error: got.storage_error,
    storage_mode: storageMode, freshness: got.freshness,
    observed_published_at: got.observed_published_at,
    visible_published_at: got.revision?.published_at ?? null,
    generated: got.revision?.report_generated_at_utc ?? null, ms: Math.round(performance.now() - t0)}, null, 1);
}
async function loop() {
  try { await refresh(); } catch (e) { out.textContent = 'Medição indisponível: ' + e.message; }
  setTimeout(loop, 60000);
}
await loop();
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
