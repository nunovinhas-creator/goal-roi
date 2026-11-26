"""Cópia independente selada das fontes do Goal-ROI, para correr a cada 12 h fora do GitHub (custo 0).

    python3 ops/seal/seal.py seal    --repo <clone> --dest <pasta> [--fetch] [--ref origin/main]
    python3 ops/seal/seal.py verify  --dest <pasta> [--max-age-h 13]
    python3 ops/seal/seal.py restore --dest <pasta> --out <pasta-nova> [--seq N]
    python3 ops/seal/seal.py prune   --dest <pasta> [--keep-days 30]

Selo = um commit exato, nunca a árvore de trabalho:
- `sources.tar.xz`: os ficheiros de `data/` (exceto a vista `data/report.json`) e o protocolo
  `scangoal/prospective_protocol.json`, lidos com `git show <commit>:<caminho>`; tar determinístico.
- `MANIFEST.json`: seq, commit, data do commit, SHA-256 e tamanho de cada ficheiro, SHA-256 do tar e o
  SHA-256 do selo anterior (cadeia).
- `SEAL`: SHA-256 do MANIFEST.json.
Escrita numa pasta temporária, fsync e rename atómico; pastas e ficheiros ficam só de leitura.
Não escreve no repositório, não faz push e não usa credenciais (o --fetch usa o acesso de leitura que o clone já tem).
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

VIEWS = {"data/report.json"}
EXTRA = ("scangoal/prospective_protocol.json",)
SCHEMA = "goal_roi_seal_v1"


class SealError(RuntimeError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def seals_dir(dest):
    return Path(dest) / "seals"


def list_seals(dest):
    root = seals_dir(dest)
    if not root.is_dir():
        return []
    return sorted((p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: int(p.name.split("-")[0]))


def tombstones(dest):
    path = Path(dest) / "PRUNED.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def read_seal(path):
    manifest = (path / "MANIFEST.json").read_bytes()
    seal = (path / "SEAL").read_text().strip()
    if sha(manifest) != seal:
        raise SealError("%s: SEAL não coincide com o MANIFEST" % path.name)
    data = json.loads(manifest)
    tar = (path / "sources.tar.xz").read_bytes()
    if sha(tar) != data["tar_sha256"]:
        raise SealError("%s: sources.tar.xz alterado" % path.name)
    with tarfile.open(fileobj=io.BytesIO(lzma.decompress(tar))) as t:
        names = sorted(m.name for m in t.getmembers())
        if names != sorted(data["files"]):
            raise SealError("%s: lista de ficheiros difere do manifesto" % path.name)
        for m in t.getmembers():
            body = t.extractfile(m).read()
            if sha(body) != data["files"][m.name]["sha256"] or len(body) != data["files"][m.name]["bytes"]:
                raise SealError("%s: %s não coincide com o manifesto" % (path.name, m.name))
    return data, seal


def deterministic_tar(files, mtime):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode, info.uid, info.gid, info.uname, info.gname = len(files[name]), mtime, 0o444, 0, 0, "", ""
            t.addfile(info, io.BytesIO(files[name]))
    return lzma.compress(buf.getvalue(), preset=6)


def seal(repo, dest, ref="HEAD", fetch=False, fail_before_rename=False):
    repo, dest = Path(repo), Path(dest)
    if fetch:
        git(repo, "fetch", "-q", "origin", "main")
    commit = git(repo, "rev-parse", ref + "^{commit}").decode().strip()
    existing = list_seals(dest)
    prev_hash, seq = None, 1
    if existing:
        last, prev_hash = read_seal(existing[-1])
        seq = last["seq"] + 1
        if last["commit"] == commit:
            return {"status": "unchanged", "seq": last["seq"], "commit": commit}
        # Só avança: um commit que não descende do último selado indica ref errada ou história reescrita.
        if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", last["commit"], commit], capture_output=True).returncode:
            raise SealError("o commit %s não descende do último selado %s" % (commit[:12], last["commit"][:12]))
    elif tombstones(dest):
        t = tombstones(dest)[-1]
        prev_hash, seq = t["seal"], t["seq"] + 1
    names = [n for n in git(repo, "ls-tree", "-r", "--name-only", commit, "data").decode().splitlines() if n not in VIEWS]
    names += [n for n in EXTRA if git(repo, "ls-tree", "--name-only", commit, n).strip()]
    files = {n: git(repo, "show", "%s:%s" % (commit, n)) for n in names}
    when = int(git(repo, "show", "-s", "--format=%ct", commit))
    tar = deterministic_tar(files, when)
    manifest = {"schema": SCHEMA, "seq": seq, "commit": commit, "commit_utc": datetime.fromtimestamp(when, timezone.utc).isoformat(),
                "sealed_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "prev_seal": prev_hash,
                "tar_sha256": sha(tar), "files": {n: {"sha256": sha(b), "bytes": len(b)} for n, b in sorted(files.items())}}
    body = json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=1).encode()
    root = seals_dir(dest)
    root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=root))
    try:
        for name, data in (("sources.tar.xz", tar), ("MANIFEST.json", body), ("SEAL", (sha(body) + "\n").encode())):
            with open(tmp / name, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp / name, 0o444)
        fsync_dir(tmp)
        if fail_before_rename:
            raise SealError("interrupção simulada")
        final = root / ("%06d-%s" % (seq, commit[:12]))
        os.rename(tmp, final)
        os.chmod(final, 0o555)
        fsync_dir(root)
    except BaseException:
        if tmp.exists():
            os.chmod(tmp, 0o755)
            shutil.rmtree(tmp, ignore_errors=True)
        raise
    read_seal(final)
    return {"status": "sealed", "seq": seq, "commit": commit, "seal": sha(body), "tar_bytes": len(tar)}


def verify(dest, max_age_h=None, now=None):
    """Verifica todos os selos e a cadeia (incluindo lápides de selos podados). Levanta SealError."""
    links = {t["seq"]: (t["seal"], t["prev"]) for t in tombstones(dest)}
    last = None
    for path in list_seals(dest):
        data, h = read_seal(path)
        if data["seq"] in links and links[data["seq"]][0] != h:
            raise SealError("seq %d duplicado com outro selo" % data["seq"])
        links[data["seq"]] = (h, data["prev_seal"])
        last = data
    if not links:
        raise SealError("nenhum selo")
    seqs = sorted(links)
    if seqs != list(range(seqs[0], seqs[-1] + 1)) or seqs[0] != 1:
        raise SealError("cadeia com selos em falta: %s" % sorted(set(range(1, seqs[-1] + 1)) - set(seqs)))
    for s in seqs[1:]:
        if links[s][1] != links[s - 1][0]:
            raise SealError("cadeia quebrada entre %d e %d" % (s - 1, s))
    if links[1][1] is not None:
        raise SealError("o primeiro selo aponta para um anterior")
    if last is None:
        raise SealError("todos os selos foram podados")
    age_h = ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(last["sealed_utc"])).total_seconds() / 3600
    if max_age_h is not None and age_h > max_age_h:
        raise SealError("último selo com %.1f h (máximo %s h)" % (age_h, max_age_h))
    return {"seals": len(list_seals(dest)), "pruned": len(tombstones(dest)), "last_seq": last["seq"], "last_commit": last["commit"],
            "last_commit_utc": last["commit_utc"], "age_h": round(age_h, 2)}


def restore(dest, out, seq=None):
    """Repõe os ficheiros de um selo verificado numa pasta nova (recusa se já existir). Tudo-ou-nada."""
    verify(dest)
    out = Path(out)
    if out.exists():
        raise SealError("destino já existe: %s" % out)
    candidates = [p for p in list_seals(dest) if seq is None or int(p.name.split("-")[0]) == seq]
    if not candidates:
        raise SealError("selo %s inexistente" % seq)
    data, _ = read_seal(candidates[-1])
    tmp = Path(tempfile.mkdtemp(prefix=".restore-", dir=out.parent))
    try:
        with tarfile.open(fileobj=io.BytesIO(lzma.decompress((candidates[-1] / "sources.tar.xz").read_bytes()))) as t:
            for m in t.getmembers():
                target = (tmp / m.name).resolve()
                if not str(target).startswith(str(tmp.resolve()) + os.sep) or not m.isfile():
                    raise SealError("entrada inválida no tar: %s" % m.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                body = t.extractfile(m).read()
                if sha(body) != data["files"][m.name]["sha256"]:
                    raise SealError("conteúdo diverge: %s" % m.name)
                target.write_bytes(body)
        os.rename(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"restored_seq": data["seq"], "commit": data["commit"], "files": len(data["files"])}


def prune(dest, keep_days=30, now=None):
    """Mantém todos os selos dos últimos keep_days e o último de cada semana ISO; o resto vira lápide."""
    now = now or datetime.now(timezone.utc)
    seals = list_seals(dest)
    keep, weekly = set(), {}
    for p in seals:
        data, _ = read_seal(p)
        t = datetime.fromisoformat(data["sealed_utc"])
        if (now - t).days < keep_days:
            keep.add(p)
        weekly[t.isocalendar()[:2]] = p
    keep |= set(weekly.values())
    if seals:
        keep.add(seals[-1])
    removed = []
    with open(Path(dest) / "PRUNED.jsonl", "a") as log:
        for p in seals:
            if p in keep:
                continue
            data, h = read_seal(p)
            log.write(json.dumps({"seq": data["seq"], "seal": h, "prev": data["prev_seal"], "commit": data["commit"],
                                  "pruned_utc": now.replace(microsecond=0).isoformat()}) + "\n")
            log.flush()
            os.fsync(log.fileno())
            for f in p.iterdir():
                os.chmod(f, stat.S_IWUSR | stat.S_IRUSR)
            os.chmod(p, 0o755)
            shutil.rmtree(p)
            removed.append(data["seq"])
    verify(dest)
    return {"removed": removed, "kept": len(list_seals(dest))}


def main():
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = cli.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seal"); s.add_argument("--repo", required=True); s.add_argument("--dest", required=True)
    s.add_argument("--ref", default="HEAD"); s.add_argument("--fetch", action="store_true")
    v = sub.add_parser("verify"); v.add_argument("--dest", required=True); v.add_argument("--max-age-h", type=float)
    r = sub.add_parser("restore"); r.add_argument("--dest", required=True); r.add_argument("--out", required=True); r.add_argument("--seq", type=int)
    p = sub.add_parser("prune"); p.add_argument("--dest", required=True); p.add_argument("--keep-days", type=int, default=30)
    a = cli.parse_args()
    try:
        if a.cmd == "seal":
            res = seal(a.repo, a.dest, a.ref, a.fetch)
        elif a.cmd == "verify":
            res = verify(a.dest, a.max_age_h)
        elif a.cmd == "restore":
            res = restore(a.dest, a.out, a.seq)
        else:
            res = prune(a.dest, a.keep_days)
    except SealError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(2)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
