#!/bin/sh
# Diagnóstico estático, só leitura. Não imprime conteúdo dos ficheiros.
# 0 = sem indícios no âmbito lido; 1 = indícios a rever; 2 = leitura incompleta/uso incorreto.
# Não prova ausência de dependências dinâmicas ou de consumidores externos.
exec python3 - "$@" <<'PY'
import os
from pathlib import Path
import re
import stat
import sys

DIRECT = re.compile(r"(?<![\w])(?:report\.json|bets\.csv)")
ORIGIN = re.compile(r"raw\.githubusercontent\.com/nunovinhas-creator/goal-roi(?:/|$)|nunovinhas-creator\.github\.io/goal-roi(?:/|$)")
# Heurística conservadora para concatenação, junção de strings e variáveis simples.
DYNAMIC = re.compile(r"""(?<![\w])(?:report|bets)['"\x60]\s*(?:[+,.]\s*)?['"\x60]\.?(?:json|csv)\b""", re.I)
PROSPECTIVE = re.compile(r"prospective_report\.json")
SKIP = {".git", "node_modules", "__pycache__", ".venv"}
hits, incomplete = False, False

def error(path, reason):
    global incomplete
    incomplete = True
    print(f"{path}: leitura incompleta ({reason})", file=sys.stderr)

def scan(path):
    global hits
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            error(path, "ligação simbólica não seguida")
            return
        if not stat.S_ISREG(mode):
            error(path, "não é ficheiro regular")
            return
        with path.open("rb") as stream:
            if b"\0" in stream.read(8192):
                return  # binário: não é código textual
            stream.seek(0)
            for number, raw in enumerate(stream, 1):
                line = raw.decode("utf-8")
                stripped = PROSPECTIVE.sub("", line)
                if DIRECT.search(stripped) or DYNAMIC.search(stripped):
                    reason = "referência a relatório gerado"
                elif ORIGIN.search(stripped) and not PROSPECTIVE.search(line):
                    reason = "origem Goal-ROI a rever"
                else:
                    continue
                hits = True
                print(f"{path}:{number}: {reason}")
    except (OSError, UnicodeError) as exc:
        error(path, type(exc).__name__)

if len(sys.argv) < 2:
    print("uso: check_consumers.sh <pasta-ou-ficheiro> [...]", file=sys.stderr)
    sys.exit(2)
for raw in sys.argv[1:]:
    root = Path(raw)
    if root.is_symlink() or not root.is_dir():
        scan(root)
        continue
    for folder, dirs, files in os.walk(root, followlinks=False, onerror=lambda e: error(e.filename, type(e).__name__)):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        for d in list(dirs):
            p = Path(folder) / d
            if p.is_symlink():
                error(p, "pasta ligada não seguida")
                dirs.remove(d)
        for f in sorted(files):
            scan(Path(folder) / f)
if incomplete:
    sys.exit(2)
if not hits:
    print("nenhum indício no âmbito textual lido; dependências dinâmicas não excluídas")
sys.exit(1 if hits else 0)
PY
