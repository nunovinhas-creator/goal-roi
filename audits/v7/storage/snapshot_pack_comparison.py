"""Compare 10→60 revision pack slopes using pinned real reports and PR #98's builder."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile


def run(args, cwd, data=None):
    return subprocess.run(args, cwd=cwd, input=data, capture_output=True, check=True).stdout


def pack_bytes(repo):
    with tempfile.TemporaryFile() as buf:
        subprocess.run(["git", "pack-objects", "--stdout", "--revs", "--delta-base-offset"],
                       cwd=repo, input=b"HEAD\n", stdout=buf, stderr=subprocess.PIPE, check=True)
        return buf.tell()


def measure(main, pr):
    selected, seen = [], set()
    for commit in run(["git", "log", "--first-parent", "--format=%H", "--", "docs/report.json"], main).decode().splitlines():
        blob = run(["git", "rev-parse", f"{commit}:docs/report.json"], main).strip()
        if blob not in seen:
            seen.add(blob)
            selected.append(commit)
        if len(selected) == 60:
            break
    if len(selected) != 60:
        raise RuntimeError("not enough report revisions")
    with tempfile.TemporaryDirectory(prefix="v7-packs-") as tmp:
        root = Path(tmp)
        models = {name: root / name for name in ("report", "stable", "hashed")}
        for folder in models.values():
            folder.mkdir()
            run(["git", "init", "-q"], folder)
        sizes = {}
        for revision, commit in enumerate(reversed(selected), 1):
            raw = run(["git", "show", f"{commit}:docs/report.json"], main)
            prospective = run(["git", "show", f"{commit}:data/prospective_report.json"], main)
            (root / "prospective.json").write_bytes(prospective)
            for mode, folder in models.items():
                (folder / "report.json").write_bytes(raw)
                if mode != "report":
                    run(["node", str(pr / "scripts/build_dashboard_snapshots.cjs"), "--report", str(folder / "report.json"),
                         "--prospective", str(root / "prospective.json"), "--protocol",
                         str(pr / "scangoal/prospective_protocol.json"), "--out", str(folder / "snapshots"),
                         *(["--hashed"] if mode == "hashed" else [])], pr)
                run(["git", "add", "-A"], folder)
                run(["git", "-c", "user.name=Isolated audit", "-c", "user.email=local@example.invalid",
                     "commit", "-qm", f"fixture {revision}"], folder)
            if revision in (10, 60):
                sizes[str(revision)] = {label: pack_bytes(folder) for label, folder in models.items()}
        slopes = {label: (sizes["60"][label] - sizes["10"][label]) / 50 for label in models}
        return {"source_newest": selected[0], "source_oldest": selected[-1],
                "pack_bytes": sizes, "growth_bytes_per_revision": slopes,
                "snapshot_increment_bytes": {label: slopes[label] - slopes["report"] for label in ("stable", "hashed")}}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--main", type=Path, required=True)
    cli.add_argument("--pr", type=Path, required=True)
    args = cli.parse_args()
    print(json.dumps(measure(args.main.resolve(), args.pr.resolve()), indent=2))
