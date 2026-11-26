"""Mede o pack das últimas 24 h separado por classe de ficheiro (fontes vs vistas), sem enviar nada.

Responde: quanto pesa por dia uma cópia independente (bundle/pack) só das fontes, se as vistas deixarem o Git."""
import json
import subprocess
import sys

repo = sys.argv[1] if len(sys.argv) > 1 else "."
VIEWS = ("docs/", "data/report.json")


def git(*a, inp=None):
    return subprocess.run(["git", "-C", repo, *a], input=inp, capture_output=True, check=True).stdout


tip = git("rev-parse", "HEAD").decode().strip()
tip_time = int(git("show", "-s", "--format=%ct", tip).decode())
since = git("rev-list", "-1", "--first-parent", "--before=%d" % (tip_time - 86400), tip).decode().strip()
window = git("rev-list", "--first-parent", "--count", "%s..%s" % (since, tip)).decode().strip()
rows = git("rev-list", "--objects", "%s..%s" % (since, tip)).decode().splitlines()
classes = {"fontes": [], "vistas": [], "outros": []}
for row in rows:
    oid, _, path = row.partition(" ")
    if not path:
        continue
    if path.startswith(VIEWS):
        classes["vistas"].append(oid)
    elif path.startswith("data/"):
        classes["fontes"].append(oid)
    else:
        classes["outros"].append(oid)
out = {"tip": tip, "since": since, "first_parent_commits": int(window)}
for name, oids in classes.items():
    # pack não-thin: autocontido, como um bundle diário independente do resto da história
    pack = git("pack-objects", "--stdout", "--window=50", "--depth=50", inp=("\n".join(oids) + "\n").encode())
    out[name] = {"objects": len(oids), "pack_bytes": len(pack)}
print(json.dumps(out, indent=2))
