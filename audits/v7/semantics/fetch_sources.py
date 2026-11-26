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


def md_text(raw):
    """Markdown oficial -> texto: tira escapes (\\$), negrito e citações (>) antes de comparar."""
    text = raw.replace("\\$", "$").replace("**", "")
    return norm(re.sub(r"(?m)^\s*>\s?", "", text))


def article_text(raw):
    """Artigo do centro de ajuda: só o corpo <article>, sem marcação."""
    import html
    m = re.search(r"(?s)<article.*?</article>", raw)
    body = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", m.group(0) if m else raw)
    return norm(html.unescape(re.sub(r"<[^>]+>", " ", body)))


def check(directory, offline=False):
    rules = json.loads((HERE / "regras.json").read_text(encoding="utf-8"))
    out = {"fontes": {}, "citacoes": {}}
    texts = {}
    for key, src in rules["fontes"].items():
        if src["estado"] != "oficial":
            out["fontes"][key] = src["estado"]
            continue
        kind = src.get("tipo", "pdf")
        path = Path(directory) / (key + {"pdf": ".pdf", "md": ".md", "html_article": ".html"}[kind])
        if not offline:
            with urllib.request.urlopen(src["url"], timeout=60) as r:
                path.write_bytes(r.read())
        raw = path.read_bytes()
        if kind == "pdf":
            texts[key] = norm(subprocess.run(["pdftotext", str(path), "-"], capture_output=True, text=True, check=True).stdout)
        elif kind == "md":
            texts[key] = md_text(raw.decode("utf-8"))
        else:
            texts[key] = article_text(raw.decode("utf-8"))
        if "sha256" in src:
            digest = hashlib.sha256(raw).hexdigest()
            out["fontes"][key] = "igual" if digest == src["sha256"] else "MUDOU " + digest
        else:  # páginas HTML dinâmicas: o hash do HTML muda; confere-se o texto citado
            out["fontes"][key] = "sem_hash_estavel"
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
