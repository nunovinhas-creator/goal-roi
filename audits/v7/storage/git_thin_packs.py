"""Generate real outgoing thin packs over measured history windows, with no pushes."""
import json
from pathlib import Path
import subprocess
import tempfile

source = Path(__file__).with_name("crescimento-git.json")
stats = json.loads(source.read_bytes())
tip = stats["source_commit"]
out = {}
for key in ("1", "3", "7"):
    window = stats["windows"][key]
    with tempfile.TemporaryFile() as stream:
        subprocess.run(["git", "pack-objects", "--stdout", "--revs", "--thin", "--delta-base-offset",
                        "--window=10", "--depth=50"],
                       input=f"{tip}\n^{window['start']}\n".encode(), stdout=stream, check=True)
        out[key] = {"days": window["days"], "bytes": stream.tell()}
print(json.dumps(out, indent=2))
