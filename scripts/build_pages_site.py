"""Monta o site do Pages numa pasta, a partir de docs/ e de um relatório gerado, sem depender de commits do relatório.

    python3 scripts/build_pages_site.py --out <pasta> [--report data/report.json] [--bets docs/bets.csv] [--docs docs]

Copia os ficheiros estáticos de docs/ (exceto report.json e bets.csv) e coloca o relatório e o CSV nos mesmos
caminhos públicos de hoje (/report.json, /bets.csv). Valida o JSON, exige que todas as referências locais das
páginas existam e escreve site-manifest.json com o SHA-256 de cada ficheiro. Só lê o repositório; nada publica.
Usado pela fase 1 descrita em ops/reports-out-of-git/README.md.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

GENERATED = {"report.json", "bets.csv"}
REF = re.compile(r"""(?:src|href)=["']([^"'#?]+)|fetch\(\s*['"]([^'"]+)['"]|['"](\.\./report\.json|snapshots/[a-z-]+\.json)['"]""")


class SiteError(RuntimeError):
    pass


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def local_refs(site):
    """Referências relativas nas páginas e scripts, resolvidas a partir do ficheiro que as contém."""
    out = set()
    for page in list(site.rglob("*.html")) + list(site.rglob("*.js")):
        for m in REF.finditer(page.read_text(encoding="utf-8", errors="replace")):
            ref = next(g for g in m.groups() if g)
            if re.match(r"^[a-z]+:|^//|^data:", ref) or ref.startswith("/"):
                continue
            out.add((page.relative_to(site).as_posix(), (page.parent / ref).resolve()))
    return out


def build(out, report, bets, docs="docs", optional=("v6/snapshots/",)):
    out, docs = Path(out), Path(docs)
    if out.exists():
        raise SiteError("destino já existe: %s" % out)
    try:
        data = json.loads(Path(report).read_bytes())
    except ValueError as e:
        raise SiteError("relatório inválido: %s" % e) from None
    if not isinstance(data, dict) or "generated_at_utc" not in data:
        raise SiteError("relatório sem generated_at_utc")
    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        for f in sorted(docs.rglob("*")):
            rel = f.relative_to(docs)
            if f.is_dir() or rel.as_posix() in GENERATED:
                continue
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, tmp / rel)
        tmp.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(report, tmp / "report.json")
        shutil.copyfile(bets, tmp / "bets.csv")
        missing = sorted("%s -> %s" % (page, ref.relative_to(tmp.resolve()) if str(ref).startswith(str(tmp.resolve())) else ref)
                         for page, ref in local_refs(tmp) if not ref.exists()
                         and not any(str(ref).startswith(str((tmp / o).resolve())) for o in optional))
        if missing:
            raise SiteError("referências sem ficheiro: %s" % missing)
        files = {p.relative_to(tmp).as_posix(): {"sha256": sha(p), "bytes": p.stat().st_size} for p in sorted(tmp.rglob("*")) if p.is_file()}
        manifest = {"report_generated_at_utc": data["generated_at_utc"], "files": files}
        (tmp / "site-manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        tmp.rename(out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return manifest


def main():
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--out", required=True)
    cli.add_argument("--report", default="data/report.json")
    cli.add_argument("--bets", default="docs/bets.csv")
    cli.add_argument("--docs", default="docs")
    a = cli.parse_args()
    try:
        m = build(a.out, a.report, a.bets, a.docs)
    except SiteError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(2)
    print(json.dumps({"files": len(m["files"]), "report_generated_at_utc": m["report_generated_at_utc"]}))


if __name__ == "__main__":
    main()
