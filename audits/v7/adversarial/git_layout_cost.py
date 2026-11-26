"""Custo Git de cada desenho de snapshots com o builder real e relatórios reais consecutivos de main (offline).

    python3 audits/v7/adversarial/git_layout_cost.py --repo . --revisions 40 [--out resultados-custo-git.json]

Para cada um dos N últimos commits de main que mudaram docs/report.json: constrói os seis snapshots
(scripts/build_dashboard_snapshots.cjs), publica-os em três repositórios Git temporários com publisher_v2
(cas; slots=3; cas com limite de 600 s entre publicações, usando as horas reais dos commits) e faz um commit
por publicação. Mede o pack incremental de cada commit (o que um push envia) e o pack final após git gc.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from audits.v7.snapshots import publisher_v2 as P  # noqa: E402


def git(cwd, *args, **kw):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, **kw).stdout


def node_builder(inputs, staging):
    out = staging / "node"
    subprocess.run(["node", str(ROOT / "scripts/build_dashboard_snapshots.cjs"), "--report", str(inputs["report"]),
                    "--prospective", str(inputs["prospective"]), "--protocol", str(inputs["protocol"]), "--out", str(out)],
                   check=True, capture_output=True, timeout=120)
    for name in P.NAMES:
        shutil.move(str(out / ("dashboard-%s.json" % name)), staging / ("dashboard-%s.json" % name))
    return {"report_generated_at_utc": json.loads(Path(inputs["report"]).read_bytes()).get("generated_at_utc")}


def pack_bytes(repo, rev_range):
    data = subprocess.run(["git", "-C", str(repo), "pack-objects", "--revs", "--thin", "--stdout", "-q"],
                          input=rev_range.encode() + b"\n", capture_output=True, check=True).stdout
    return len(data)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repo", default=str(ROOT))
    cli.add_argument("--ref", default="origin/main")
    cli.add_argument("--revisions", type=int, default=40)
    cli.add_argument("--out", default=str(Path(__file__).with_name("resultados-custo-git.json")))
    a = cli.parse_args()
    commits = git(a.repo, "log", "--format=%H %ct", "-n", str(a.revisions), a.ref, "--", "docs/report.json").decode().split("\n")
    commits = [c.split() for c in commits if c][::-1]
    designs = {"cas": dict(layout="cas"), "slots3": dict(layout="slots", slots=3), "cas_limite_600s": dict(layout="cas", min_interval_s=600)}
    res = {"ref": a.ref, "first": commits[0][0], "last": commits[-1][0], "revisions": len(commits), "designs": {}}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        repos = {}
        for name, kw in designs.items():
            repo = tmp / name
            git(tmp, "init", "-q", name)
            git(repo, "config", "user.email", "t@example.invalid"); git(repo, "config", "user.name", "t")
            (repo / "README").write_text(name); git(repo, "add", "-A"); git(repo, "commit", "-qm", "base")
            clock = {"t": 0.0}
            repos[name] = (repo, P.Publisher(repo / "snapshots", lock=tmp / (name + ".lock"), clock=lambda c=clock: c["t"], **kw), clock, [], [])
        inputs = {"report": tmp / "report.json", "prospective": tmp / "prospective.json", "protocol": tmp / "protocol.json"}
        for sha, ct in commits:
            inputs["report"].write_bytes(git(a.repo, "show", "%s:docs/report.json" % sha))
            inputs["prospective"].write_bytes(git(a.repo, "show", "%s:data/prospective_report.json" % sha))
            inputs["protocol"].write_bytes(git(a.repo, "show", "%s:scangoal/prospective_protocol.json" % sha))
            for name, (repo, pub, clock, statuses, pushes) in repos.items():
                clock["t"] = float(ct)
                statuses.append(pub.build(inputs, node_builder))
                if git(repo, "status", "--porcelain"):
                    git(repo, "add", "-A"); git(repo, "commit", "-qm", sha[:12])
                    pushes.append(pack_bytes(repo, "HEAD^..HEAD"))
        for name, (repo, pub, clock, statuses, pushes) in repos.items():
            git(repo, "gc", "-q")
            pack = sum(p.stat().st_size for p in (repo / ".git/objects/pack").glob("*.pack"))
            tree = sum(p.stat().st_size for p in (repo / "snapshots").rglob("*") if p.is_file())
            res["designs"][name] = {"published": statuses.count("published"), "deferred": statuses.count("deferred"),
                                    "unchanged": statuses.count("unchanged"), "commits": len(pushes),
                                    "push_bytes_mean": round(sum(pushes) / max(1, len(pushes))), "push_bytes_total": sum(pushes),
                                    "pack_after_gc_bytes": pack, "worktree_bytes": tree}
    Path(a.out).write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
