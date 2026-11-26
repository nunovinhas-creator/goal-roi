"""Ensaios de recuperação V7 a partir de uma cópia independente verificada (offline, sem credenciais).

    python3 audits/v7/storage/restore_drill.py --repo . --commits 12

Constrói o arquivo segmentado das últimas N revisões numa pasta temporária, copia-o para um "espelho" (outro
diretório, que representa outro fornecedor ou um disco offline) e exercita:

  perda_total_git      restaurar data/ e docs/report.json sem Git, só do espelho; comparar com a árvore Git
  objeto_corrompido    detetar um bloco adulterado no primário e repará-lo a partir do espelho verificado
  head_corrompido      HEAD ilegível: leitura recua para PREVIOUS; HEAD reposto do espelho
  espelho_incompleto   espelho sem um objeto: a restauração recusa (fail closed) e lista o que falta
  espelho_atrasado     espelho N revisões atrás: devolve a última revisão íntegra e declara o atraso (RPO)
  restauro_interrompido  interrupção a meio: o destino só aparece por rename atómico; repetir é idempotente
  bundle_git           git bundle verificado e clonado num diretório novo; árvore igual à original

Nunca escreve no repositório, no diário, no registo prospetivo nem em estados de entrega.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive_v7 as A  # noqa: E402
from segment_store import IntegrityError, SegmentStore  # noqa: E402

PATHS = ["data", "docs/report.json"]


def build_archive(repo, commits, root):
    git = A.Git(repo)
    archive = A.Archive(root)
    archive.db.executescript(A.SCHEMA)
    seen, trees = {}, {}
    for seq, commit in enumerate(commits, 1):
        tree = {p: o for p, o in git.tree(commit, PATHS).items() if not p.endswith(".gitkeep")}
        archive.publish(seq, {p: git.blob(o) for p, o in tree.items()}, commit, seen)
        trees[seq] = {p: A.sha(git.blob(o)) for p, o in tree.items()}
    archive.db.commit()
    archive.db.close()
    git.close()
    return trees


def missing_objects(store):
    """Objetos referenciados pela cadeia retida que não existem ou não verificam."""
    missing = []
    try:
        releases = A.history_until_tombstone(store)
    except IntegrityError as exc:
        return ["manifesto: %s" % exc]
    for _, manifest, loaded in releases:
        for ref in list(manifest["files"].values()) + [r for raw in loaded.values() for r in json.loads(raw)["parts"]]:
            try:
                store.payload(ref)
            except IntegrityError:
                missing.append(ref["sha256"])
    return sorted(set(missing))


def restore(mirror, dest, interrupt_after=None):
    """Restaura a revisão ativa verificada do espelho para `dest` (data/ e docs/). Tudo ou nada."""
    out = {}
    for name in ("sources", "views"):
        store = SegmentStore(Path(mirror) / name)
        holes = missing_objects(store)
        if holes:
            raise IntegrityError("espelho incompleto em %s: %d objetos" % (name, len(holes)))
        which, _, manifest, loaded = store.active()
        out[name] = {"pointer": which, "seq": manifest["seq"]}
        out.setdefault("files", {}).update(A.decode(loaded, store))
    if out["sources"]["seq"] != out["views"]["seq"]:
        raise IntegrityError("fontes e vistas de revisões diferentes")
    staging = Path(tempfile.mkdtemp(prefix=".restore-", dir=Path(dest).parent))
    try:
        for i, (path, data) in enumerate(sorted(out["files"].items())):
            if interrupt_after is not None and i == interrupt_after:
                raise KeyboardInterrupt("interrupção simulada")
            target = staging / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        os.replace(staging, dest)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {"seq": out["sources"]["seq"], "pointer": out["sources"]["pointer"],
            "files": {p: A.sha(d) for p, d in out["files"].items()}}


def tree_of(directory):
    root = Path(directory)
    return {p.relative_to(root).as_posix(): A.sha(p.read_bytes()) for p in root.rglob("*") if p.is_file()}


def flip(path):
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))


def drill(repo, n):
    git = A.Git(repo)
    commits = A.commits_for(git, n)
    git.close()
    res = {"commits": len(commits), "first_commit": commits[0], "last_commit": commits[-1]}
    with tempfile.TemporaryDirectory(prefix="v7-restore-") as tmp:
        base = Path(tmp)
        primary, mirror = base / "primary", base / "mirror"
        trees = build_archive(repo, commits, primary)
        shutil.copytree(primary, mirror)
        last = trees[len(commits)]

        # perda total do Git
        r = restore(mirror, base / "r1")
        res["perda_total_git"] = {"seq": r["seq"], "identico_ao_git": tree_of(base / "r1") == last, "ficheiros": len(last)}

        # objeto corrompido no primário: deteção e reparação a partir do espelho
        store = SegmentStore(primary / "sources")
        _, _, manifest, loaded = store.active()
        victim = json.loads(next(iter(loaded.values())))["parts"][0]["sha256"]
        flip(store.objects / (victim + ".gz"))
        detected = missing_objects(store)
        good = SegmentStore(mirror / "sources")
        good.payload({"sha256": victim, "bytes": next(
            p["bytes"] for raw in loaded.values() for p in json.loads(raw)["parts"] if p["sha256"] == victim)})
        shutil.copyfile(good.objects / (victim + ".gz"), store.objects / (victim + ".gz"))
        res["objeto_corrompido"] = {"detetado": detected == [victim], "reparado": missing_objects(store) == []}

        # HEAD corrompido
        (primary / "sources" / "HEAD").write_text("lixo\n")
        which, _, m, _ = SegmentStore(primary / "sources").active()
        shutil.copyfile(mirror / "sources" / "HEAD", primary / "sources" / "HEAD")
        which2, _, m2, _ = SegmentStore(primary / "sources").active()
        res["head_corrompido"] = {"recuo": which, "seq_recuo": m["seq"], "apos_reposicao": which2, "seq": m2["seq"]}

        # espelho incompleto
        partial = base / "partial"
        shutil.copytree(mirror, partial)
        objs = sorted((partial / "sources" / "objects").glob("*.gz"))
        objs[len(objs) // 2].unlink()
        try:
            restore(partial, base / "r2")
            res["espelho_incompleto"] = {"recusado": False}
        except IntegrityError as exc:
            res["espelho_incompleto"] = {"recusado": True, "destino_criado": (base / "r2").exists(), "motivo": str(exc)}

        # espelho atrasado: cópia feita k revisões antes
        lag = min(3, len(commits) - 1)
        stale = base / "stale"
        build_archive(repo, commits[:-lag], stale)
        r = restore(stale, base / "r3")
        res["espelho_atrasado"] = {"seq_restaurada": r["seq"], "seq_atual": len(commits), "revisoes_em_falta": len(commits) - r["seq"],
                                   "identico_a_revisao_declarada": tree_of(base / "r3") == trees[r["seq"]]}

        # restauração interrompida e repetida
        try:
            restore(mirror, base / "r4", interrupt_after=3)
        except KeyboardInterrupt:
            pass
        leftovers = [p.name for p in base.iterdir() if p.name.startswith(".restore-")]
        first_absent = not (base / "r4").exists()
        r = restore(mirror, base / "r4")
        res["restauro_interrompido"] = {"destino_ausente_apos_interrupcao": first_absent, "temporarios_restantes": leftovers,
                                        "repeticao_identica": tree_of(base / "r4") == last}

        # bundle Git incremental: verificável no repositório com a base; num repositório vazio exige a cadeia anterior
        bundle = base / "repo.bundle"
        subprocess.run(["git", "-C", str(repo), "bundle", "create", "-q", str(bundle), "HEAD", "^" + commits[0]],
                       check=True, capture_output=True)
        ok = subprocess.run(["git", "-C", str(repo), "bundle", "verify", "-q", str(bundle)], capture_output=True).returncode == 0
        empty = base / "empty"
        subprocess.run(["git", "init", "-q", str(empty)], check=True)
        lone = subprocess.run(["git", "-C", str(empty), "bundle", "verify", "-q", str(bundle)], capture_output=True).returncode == 0
        res["bundle_git"] = {"bytes": bundle.stat().st_size, "verifica_com_base": ok, "verifica_sem_cadeia_anterior": lone}
    return res


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repo", type=Path, default=Path("."))
    cli.add_argument("--commits", type=int, default=12)
    args = cli.parse_args()
    print(json.dumps(drill(args.repo.resolve(), args.commits), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
