"""Attribute unique reachable object bytes using actual compressed Git packs."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


def git(*args, **kwargs):
    return subprocess.check_output(["git", *args], **kwargs)


def packs(indexes=None):
    if indexes is None:
        indexes = sorted((Path(git("rev-parse", "--git-path", "objects").decode().strip()) / "pack").glob("*.idx"))
    for index in indexes:
        yield from git("verify-pack", "-v", str(index)).decode().splitlines()


def pack_sizes(lines):
    variants = {}
    for line in lines:
        parts = line.split()
        if len(parts) < 5 or len(parts[0]) not in (40, 64) or parts[1] not in ("blob", "tree", "commit", "tag"):
            continue
        sha, kind, raw, size, *_ = parts
        pair = (kind, int(raw), int(size))
        if sha not in variants or pair[2] < variants[sha][2]:
            variants[sha] = pair
    return variants


def group(path, kind):
    if kind != "blob":
        return "metadados_git"
    if path in ("data/report.json", "docs/report.json"):
        return "data/report.json + docs/report.json (blobs únicos)"
    if path.startswith(("docs/", "data/")):
        return path
    return path.split("/", 1)[0] if "/" in path else "(raiz)"


def snapshot(sizes, tip, parents, before):
    start = next((row for row in parents if row[1] <= before), None)
    objects = git("rev-list", "--objects", tip, *([f"^{start[0]}"] if start else [])).decode().splitlines()
    measured = defaultdict(lambda: dict(objects=0, raw=0, packed=0))
    for line in objects:
        sha, _, name = line.partition(" ")
        if sha not in sizes:
            raise ValueError("Missing packed object " + sha)
        kind, raw, stored = sizes[sha]
        cell = measured[group(name, kind)]
        cell["objects"] += 1
        cell["raw"] += raw
        cell["packed"] += stored
    window = [row for row in parents if start is None or row[1] > start[1]]
    elapsed = (parents[0][1] - (start or parents[-1])[1]) / 86400
    return dict(start=start[0] if start else None, days=elapsed, commits=len(window),
                collections=sum(r[2].startswith("data: collect") for r in window),
                total_packed=sum(x["packed"] for x in measured.values()),
                groups=dict(sorted(measured.items(), key=lambda pair: -pair[1]["packed"])))


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--pack-list", type=Path, help="Optional previously collected git verify-pack -v output")
    args = cli.parse_args()
    sizes = pack_sizes(args.pack_list.read_text().splitlines() if args.pack_list else packs())
    tip = git("rev-parse", "HEAD").decode().strip()
    parents = [(sha, int(when), subject)
               for sha, when, subject in (line.split("\t", 2) for line in
                                           git("log", "--first-parent", "--format=%H%x09%ct%x09%s", tip).decode().splitlines())]
    now = parents[0][1]
    result = {"source_commit": tip, "cutoff": datetime.fromtimestamp(now, timezone.utc).isoformat(),
              "windows": {}}
    for days in (1, 3, 7):
        result["windows"][str(days)] = snapshot(sizes, tip, parents, now - days * 86400)
    result["windows"]["lifetime"] = snapshot(sizes, tip, parents, 0)
    result["same_report_tip_blob"] = git("rev-parse", "HEAD:data/report.json").splitlines() == git("rev-parse", "HEAD:docs/report.json").splitlines()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
