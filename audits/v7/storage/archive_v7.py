"""Arquivo segmentado V7 sobre a história Git real: todas as fontes, índice, retenção e exportação.

Offline e só de leitura sobre o repositório. Escreve apenas na pasta --store (temporária por omissão).

Para cada commit de recolha (primeiro pai) das últimas N revisões:
  * fontes (data/*: diário, registo prospetivo, hipóteses, resultados, estados) vão para o armazém `sources`,
    que nunca apaga nada. Ficheiros .jsonl são partidos em blocos de linhas com fronteiras deterministas a partir do
    início, pelo que um ficheiro que só cresce reutiliza os blocos anteriores (deduplicação por SHA-256);
  * vistas derivadas (docs/report.json e docs/*.json) vão para o armazém `views`, com retenção configurável.
Cada revisão guarda `meta/files.json` com SHA-256 e tamanho de cada ficheiro original e a lista de blocos.

Provas:
  * cada revisão retida é reexportada e comparada byte a byte com o blob Git correspondente;
  * o índice SQLite é derivado: é reconstruído do zero a partir do armazém e comparado com o índice incremental;
  * a retenção das vistas apaga só manifestos/objetos não referenciados pelas revisões retidas e regista lápides
    com o SHA-256 do que saiu; as fontes ficam intactas e verificadas.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from segment_store import IntegrityError, SegmentStore, atomic, serialized, sha  # noqa: E402

BLOCK = 256 * 1024
VIEW_PREFIX = "docs/"


def chunks(data, limit=BLOCK):
    block, n = [], 0
    for record in data.splitlines(keepends=True):
        if block and n + len(record) > limit:
            yield b"".join(block)
            block, n = [], 0
        block.append(record)
        n += len(record)
    if block:
        yield b"".join(block)


CDC_MIN, CDC_MAX, CDC_MASK = 16 * 1024, 256 * 1024, (1 << 12) - 1


def cdc(data):
    """Fragmentação definida pelo conteúdo, em fronteiras de linha: uma alteração local só muda o bloco onde ocorre
    (e no máximo o seguinte), pelo que os restantes blocos são reutilizados entre revisões."""
    block, n = [], 0
    for line in data.splitlines(keepends=True):
        block.append(line)
        n += len(line)
        h = int.from_bytes(hashlib.blake2b(line, digest_size=4).digest(), "big")
        if n >= CDC_MAX or (n >= CDC_MIN and h & CDC_MASK == 0):
            yield b"".join(block)
            block, n = [], 0
    if block:
        yield b"".join(block)


VIEWS = {"data/report.json"}


def is_view(path):
    return path.startswith(VIEW_PREFIX) or path in VIEWS


class Git:
    def __init__(self, repo):
        self.repo = Path(repo)
        self.proc = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE)

    def run(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True).stdout

    def blob(self, oid):
        self.proc.stdin.write(oid.encode() + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().split()
        if header[1] != b"blob":
            raise ValueError("not a blob")
        data = self.proc.stdout.read(int(header[2]))
        self.proc.stdout.read(1)
        return data

    def tree(self, commit, paths):
        out = self.run("ls-tree", "-r", commit, "--", *paths)
        rows = {}
        for line in out.decode().splitlines():
            meta, path = line.split("\t", 1)
            _, kind, oid = meta.split()
            if kind == "blob":
                rows[path] = oid
        return rows

    def close(self):
        self.proc.stdin.close()
        self.proc.wait()


def put(store, data):
    """Grava um bloco imutável endereçado por SHA-256 (gzip). Se já existir, verifica-o em vez de o reutilizar às cegas."""
    ref = {"sha256": sha(data), "bytes": len(data)}
    dest = store.objects / (ref["sha256"] + ".gz")
    if dest.exists():
        store.payload(ref)
    else:
        atomic(dest, gzip.compress(data, compresslevel=6, mtime=0))
    return ref


def encode(files, store):
    """Ficheiros originais -> blocos no armazém + uma entrada meta/<caminho>.json por ficheiro (árvore).

    O manifesto da revisão só lista os metas (um por ficheiro); um ficheiro inalterado reutiliza o mesmo meta."""
    entries = {}
    for path, data in sorted(files.items()):
        blocks = cdc(data) if len(data) > CDC_MIN else [data]
        parts = [put(store, block) for block in blocks]
        entries["meta/%s.json" % path] = serialized({"path": path, "sha256": sha(data), "bytes": len(data), "parts": parts})
    return entries


def decode(loaded, store):
    out = {}
    for name, raw in loaded.items():
        info = json.loads(raw)
        if name != "meta/%s.json" % info.get("path"):
            raise IntegrityError("meta fora do lugar: %s" % name)
        data = b"".join(store.payload(ref) for ref in info["parts"])
        if len(data) != info["bytes"] or sha(data) != info["sha256"]:
            raise IntegrityError("reconstrução diverge: %s" % info["path"])
        out[info["path"]] = data
    return out


def referenced(store, releases):
    refs = set()
    for _, manifest, loaded in releases:
        refs.update(r["sha256"] for r in manifest["files"].values())
        for raw in loaded.values():
            refs.update(r["sha256"] for r in json.loads(raw)["parts"])
    return refs


def stored_bytes(root):
    return sum(p.stat().st_size for p in Path(root).rglob("*") if p.is_file())


SCHEMA = """
CREATE TABLE revisions(store TEXT, seq INTEGER, pin TEXT, git_commit TEXT, PRIMARY KEY(store, seq));
CREATE TABLE files(store TEXT, seq INTEGER, path TEXT, sha256 TEXT, bytes INTEGER, PRIMARY KEY(store, seq, path));
CREATE TABLE messages(message_id TEXT PRIMARY KEY, first_seq INTEGER, versions INTEGER, last_text_sha256 TEXT);
CREATE INDEX files_by_sha ON files(sha256);
"""


def index_revision(db, store, seq, pin, commit, decoded_meta, journal_new=None):
    db.execute("INSERT INTO revisions VALUES (?,?,?,?)", (store, seq, pin, commit))
    db.executemany("INSERT INTO files VALUES (?,?,?,?,?)",
                   [(store, seq, p, i["sha256"], i["bytes"]) for p, i in sorted(decoded_meta.items())])
    for row in journal_new or ():
        db.execute("INSERT INTO messages VALUES (?,?,1,?) ON CONFLICT(message_id) DO UPDATE SET "
                   "versions=versions+1, last_text_sha256=excluded.last_text_sha256",
                   (str(row.get("message_id")), seq, row.get("sha256")))


def journal_rows(data):
    for line in data.splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def journal_delta(state, data):
    """Linhas novas de um diário só de acréscimo. Uma reescrita do prefixo é contada e reindexada do início."""
    start = state.get("bytes", 0)
    if len(data) < start or sha(data[:start]) != state.get("prefix", sha(b"")):
        state["rewrites"] = state.get("rewrites", 0) + 1
        start = 0
    state["bytes"], state["prefix"] = len(data), sha(data)
    return list(journal_rows(data[start:]))


def dump(db):
    return [list(db.execute("SELECT * FROM %s ORDER BY 1,2,3" % t)) for t in ("revisions", "files", "messages")]


def commits_for(git, n, stride=1):
    out = git.run("rev-list", "--first-parent", "HEAD", "--", "data", "docs/report.json").decode().split()
    return list(reversed(out[::stride][:n]))


class Archive:
    def __init__(self, root):
        self.root = Path(root)
        self.sources = SegmentStore(self.root / "sources")
        self.views = SegmentStore(self.root / "views")
        self.db = sqlite3.connect(self.root / "index.sqlite")

    def publish(self, seq, files, commit, journal_seen):
        src = {p: d for p, d in files.items() if not is_view(p)}
        views = {p: d for p, d in files.items() if is_view(p)}
        pins = {}
        for name, store, part in (("sources", self.sources, src), ("views", self.views, views)):
            if not part:
                continue
            entries = encode(part, store)
            status, pin = store.publish(seq, entries, store.pointer())
            pins[name] = pin
            meta = {p: {"sha256": sha(d), "bytes": len(d)} for p, d in part.items()}
            new = []
            if name == "sources" and "data/messages.jsonl" in part:
                new = journal_delta(journal_seen, part["data/messages.jsonl"])
            index_revision(self.db, name, seq, pin, commit, meta, new)
        return pins

    def verify_all(self, expected, stores=("sources", "views")):
        """expected: {(store, seq): {path: sha256}} vindo dos blobs Git."""
        checked = 0
        for name, store in (("sources", self.sources), ("views", self.views)):
            if name not in stores:
                continue
            for pin, manifest, loaded in store.history():
                files = decode(loaded, store)
                want = expected.get((name, manifest["seq"]))
                if want is None:
                    continue
                if {p: sha(d) for p, d in files.items()} != want:
                    raise IntegrityError("revisão %s/%s diverge do Git" % (name, manifest["seq"]))
                checked += 1
        return checked

    def rebuild_index(self, path, commits):
        db = sqlite3.connect(path)
        db.executescript(SCHEMA)
        for name, store in (("sources", self.sources), ("views", self.views)):
            revs = sorted(store.history(), key=lambda r: r[1]["seq"])
            state = {}
            for pin, manifest, loaded in revs:
                files = decode(loaded, store)
                meta = {p: {"sha256": sha(d), "bytes": len(d)} for p, d in files.items()}
                new = []
                if name == "sources" and "data/messages.jsonl" in files:
                    new = journal_delta(state, files["data/messages.jsonl"])
                index_revision(db, name, manifest["seq"], pin, commits[manifest["seq"]], meta, new)
        db.commit()
        return db


def retain_views(store, keep):
    """Mantém as últimas `keep` revisões das vistas; regista lápides e remove objetos não referenciados."""
    chain = list(store.history())
    kept, dropped = chain[:keep], chain[keep:]
    keep_refs = referenced(store, kept)
    tombstones = []
    for pin, manifest, _ in dropped:
        tombstones.append({"seq": manifest["seq"], "manifest": pin,
                           "files": {k: v["sha256"] for k, v in manifest["files"].items()}})
    freed = 0
    if dropped:
        # O manifesto mais antigo retido continua a apontar para o anterior: a cadeia termina numa lápide.
        atomic(store.root / "TOMBSTONES.json", serialized(tombstones))
        for pin, _, _ in dropped:
            path = store.manifests / ("%s.json" % pin)
            freed += path.stat().st_size
            path.unlink()
        for obj in store.objects.glob("*.gz"):
            if obj.name[:-3] not in keep_refs:
                freed += obj.stat().st_size
                obj.unlink()
        if store.pointer("PREVIOUS") in {p for p, _, _ in dropped}:
            (store.root / "PREVIOUS").unlink()
    return {"kept": len(kept), "dropped": len(dropped), "freed_bytes": freed}


def history_until_tombstone(store):
    out = []
    pin = store.pointer()
    tomb = set()
    if (store.root / "TOMBSTONES.json").exists():
        tomb = {t["manifest"] for t in json.loads((store.root / "TOMBSTONES.json").read_bytes())}
    while pin and pin not in tomb:
        manifest, loaded = store.load_release(pin)
        out.append((pin, manifest, loaded))
        pin = manifest["previous"]
    return out


def git_disk_bytes(git, oids):
    if not oids:
        return 0
    out = subprocess.run(["git", "-C", str(git.repo), "cat-file", "--batch-check=%(objectsize:disk)"],
                         input="\n".join(sorted(oids)).encode() + b"\n", check=True, capture_output=True).stdout
    return sum(int(x) for x in out.split())


def run(repo, n, store_root, keep_views, stride=1):
    git = Git(repo)
    commits = commits_for(git, n, stride)
    paths = ["data", "docs/report.json"]
    archive = Archive(store_root)
    archive.db.executescript(SCHEMA)
    expected, commit_of, raw_total, oids = {}, {}, 0, {"sources": set(), "views": set()}
    journal_seen = {}
    t0 = time.perf_counter()
    for seq, commit in enumerate(commits, 1):
        tree = git.tree(commit, paths)
        files = {p: git.blob(o) for p, o in tree.items() if not p.endswith(".gitkeep")}
        raw_total += sum(len(d) for d in files.values())
        for p, o in tree.items():
            oids["views" if is_view(p) else "sources"].add(o)
        archive.publish(seq, files, commit, journal_seen)
        commit_of[seq] = commit
        if seq == 1:
            first_oids = set(tree.values())
            first_stored = {k: stored_bytes(Path(store_root) / k) for k in ("sources", "views")}
        expected[("sources", seq)] = {p: sha(d) for p, d in files.items() if not is_view(p)}
        expected[("views", seq)] = {p: sha(d) for p, d in files.items() if is_view(p)}
    archive.db.commit()
    write_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    checked = archive.verify_all(expected)
    verify_s = time.perf_counter() - t1
    rebuilt = archive.rebuild_index(Path(store_root) / "index-rebuilt.sqlite", commit_of)
    index_equal = dump(rebuilt) == dump(archive.db)
    q = archive.db.execute("SELECT COUNT(*), SUM(versions) FROM messages").fetchone()
    t2 = time.perf_counter()
    lookup = archive.db.execute("SELECT seq FROM files WHERE store='sources' AND path='data/prospective_ledger.jsonl' "
                                "ORDER BY seq DESC LIMIT 1").fetchone()
    query_ms = round(1000 * (time.perf_counter() - t2), 3)
    before = {k: stored_bytes(Path(store_root) / k) for k in ("sources", "views")}
    marginal = {k: {"segmented_bytes_per_revision": round((before[k] - first_stored[k]) / max(1, len(commits) - 1)),
                    "git_disk_bytes_per_revision": round(git_disk_bytes(git, oids[k] - first_oids) / max(1, len(commits) - 1))}
                for k in ("sources", "views")}
    retention = retain_views(archive.views, keep_views)
    after_views = stored_bytes(Path(store_root) / "views")
    kept_views = history_until_tombstone(archive.views)
    for pin, manifest, loaded in kept_views:
        if {p: sha(d) for p, d in decode(loaded, archive.views).items()} != expected[("views", manifest["seq"])]:
            raise IntegrityError("vista retida diverge")
    sources_after = archive.verify_all(expected, stores=("sources",))
    # exportação da última revisão para os caminhos originais (compatibilidade com Brain, Android e V6.1)
    _, _, _, head_files = archive.sources.active()
    _, _, _, head_views = archive.views.active()
    exported = {**decode(head_files, archive.sources), **decode(head_views, archive.views)}
    last_tree = git.tree(commits[-1], paths)
    export_identical = all(sha(exported[p]) == sha(git.blob(o)) for p, o in last_tree.items() if not p.endswith(".gitkeep"))
    result = {
        "commits": len(commits), "stride": stride, "cdc": {"min": CDC_MIN, "max": CDC_MAX, "mask_bits": CDC_MASK.bit_length()}, "first_commit": commits[0], "last_commit": commits[-1],
        "raw_bytes_all_revisions": raw_total,
        "git_disk_bytes_unique_blobs": {k: git_disk_bytes(git, v) for k, v in oids.items()},
        "stored_bytes": before, "stored_bytes_after_first_revision": first_stored, "marginal": marginal, "index_bytes": (Path(store_root) / "index.sqlite").stat().st_size,
        "revisions_verified_byte_identical": checked,
        "index_rebuilt_identical": index_equal,
        "index_messages": {"ids": q[0], "versions": q[1], "journal_prefix_rewrites": journal_seen.get("rewrites", 0)}, "index_query_ms": query_ms, "index_last_ledger_seq": lookup[0] if lookup else None,
        "retention_views": {**retention, "views_bytes_after": after_views, "kept_verified": len(kept_views)},
        "sources_verified_after_retention": sources_after,
        "export_last_revision_identical_to_git": export_identical,
        "seconds": {"write": round(write_s, 2), "verify": round(verify_s, 2)},
    }
    git.close()
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repo", type=Path, default=Path("."))
    cli.add_argument("--commits", type=int, default=200)
    cli.add_argument("--keep-views", type=int, default=24)
    cli.add_argument("--store", type=Path)
    cli.add_argument("--stride", type=int, default=1, help="usar 1 commit em cada N (cadência de arquivo)")
    cli.add_argument("--cdc-bits", type=int, default=12)
    cli.add_argument("--cdc-min", type=int, default=16 * 1024)
    args = cli.parse_args()
    global CDC_MIN, CDC_MASK
    CDC_MIN, CDC_MASK = args.cdc_min, (1 << args.cdc_bits) - 1
    if args.store:
        args.store.mkdir(parents=True, exist_ok=False)
        print(json.dumps(run(args.repo.resolve(), args.commits, args.store, args.keep_views, args.stride), indent=2))
    else:
        with tempfile.TemporaryDirectory(prefix="v7-archive-") as tmp:
            print(json.dumps(run(args.repo.resolve(), args.commits, Path(tmp) / "s", args.keep_views, args.stride), indent=2))


if __name__ == "__main__":
    main()
