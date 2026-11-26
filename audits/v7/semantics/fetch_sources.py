"""Descarrega os termos oficiais listados em regras.json, confere o SHA-256 e verifica cada citação no texto.

    python3 audits/v7/semantics/fetch_sources.py --dir <pasta> [--offline]

Com --offline usa os PDFs já presentes em <pasta>. Requer pdftotext. Só leitura pública; sem credenciais.
Uma alteração do SHA-256 significa que a Kalshi publicou outra versão: as citações têm de ser revistas por humano.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import urllib.request

HERE = Path(__file__).resolve().parent


def norm(text):
    return re.sub(r"\s+", " ", text).strip()


def check(directory, offline=False):
    rules = json.loads((HERE / "regras.json").read_text(encoding="utf-8"))
    out = {"fontes": {}, "citacoes": {}}
    texts = {}
    for key, src in rules["fontes"].items():
        if src["estado"] != "oficial":
            out["fontes"][key] = src["estado"]
            continue
        path = Path(directory) / (key + ".pdf")
        if not offline:
            with urllib.request.urlopen(src["url"], timeout=60) as r:
                path.write_bytes(r.read())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        out["fontes"][key] = "igual" if digest == src["sha256"] else "MUDOU " + digest
        texts[key] = norm(subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True, check=True).stdout)
    for rid, rule in rules["regras"].items():
        out["citacoes"][rid] = norm(rule["citacao"]) in texts.get(rule["fonte"], "")
    return out


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--dir", required=True)
    cli.add_argument("--offline", action="store_true")
    a = cli.parse_args()
    Path(a.dir).mkdir(parents=True, exist_ok=True)
    print(json.dumps(check(a.dir, a.offline), ensure_ascii=False, indent=1))
