"""Protótipo V7 (offline, inativo) de publicação de snapshots com duas ranhuras e ponteiro atómico.

Corrige, fora da PR #98 e sem a alterar, os quatro defeitos reproduzidos pela auditoria V7:

1. Corrupção persistente: o caminho "unchanged" verifica os corpos vivos por SHA-256 antes de decidir;
   um corpo corrompido da mesma revisão é reescrito ("repaired").
2. Mistura após interrupção: cada revisão é escrita completa na ranhura inativa (a/ ou b/), verificada e só
   depois o ponteiro (dashboard-manifest.json) é trocado por rename atómico. Antes da troca, o ponteiro continua a
   descrever a ranhura antiga, cujos corpos não foram tocados.
3. Entrada prospetiva divergente: relatório, entrada prospetiva e protocolo são fixados em conjunto (SHA-256 antes e
   depois do builder); qualquer mudança aborta a publicação e o manifesto regista os três digests.
4. Verificação por comprimento: aqui só existe verificação por SHA-256 (o cliente correspondente está em
   client_integrity.mjs).

O ponteiro guarda também a revisão anterior verificada (`previous`), que permanece íntegra na outra ranhura e serve
de recuo. Os nomes dos ficheiros são estáveis (a/dashboard-<nome>.json, b/dashboard-<nome>.json), pelo que o Git
continua a guardar deltas do mesmo caminho.

Nada aqui é chamado pela recolha, pelo workflow ou pelo Pages.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

SCHEMA = "goal_roi_dashboard_pointer_v7_proto"
NAMES = ("summary", "signals", "performance", "science", "operations", "learning")
POINTER = "dashboard-manifest.json"
SLOTS = ("a", "b")
INPUTS = ("report", "prospective", "protocol")


class SnapshotError(RuntimeError):
    pass


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digests(inputs):
    return {k: sha(inputs[k]) for k in INPUTS}


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(src, dst):
    tmp = dst.with_name(dst.name + ".tmp")
    with open(src, "rb") as a, open(tmp, "wb") as b:
        shutil.copyfileobj(a, b)
        b.flush()
        os.fsync(b.fileno())
    os.replace(tmp, dst)


@contextlib.contextmanager
def _locked(out):
    out.mkdir(parents=True, exist_ok=True)
    with open(out / ".lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def read_pointer(out):
    try:
        data = json.loads((Path(out) / POINTER).read_bytes())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None


def verify_revision(revision, out):
    """Levanta SnapshotError se algum corpo da revisão não coincidir com o SHA-256 e o tamanho registados."""
    if not isinstance(revision, dict) or revision.get("slot") not in SLOTS:
        raise SnapshotError("revisão malformada")
    files = revision.get("files")
    if not isinstance(files, dict) or sorted(files) != sorted(NAMES):
        raise SnapshotError("a revisão não lista exatamente os seis snapshots")
    for name in NAMES:
        entry = files[name]
        expected = "%s/dashboard-%s.json" % (revision["slot"], name)
        if not isinstance(entry, dict) or entry.get("path") != expected:
            raise SnapshotError("caminho inesperado: %s" % name)
        body = Path(out) / expected
        if not body.is_file() or body.stat().st_size != entry.get("bytes") or sha(body) != entry.get("sha256"):
            raise SnapshotError("corpo não coincide com o manifesto: %s" % name)


def verify_live(out, inputs):
    """Verifica o ponteiro, os corpos e que os três digests correspondem às entradas atuais."""
    pointer = read_pointer(out)
    if pointer is None:
        raise SnapshotError("sem ponteiro válido")
    current = pointer.get("current")
    verify_revision(current, out)
    if current.get("inputs") != digests(inputs):
        raise SnapshotError("o ponteiro não descreve as entradas atuais")
    return pointer


def read_verified(out):
    """Para leitores: devolve a revisão atual se íntegra, senão a anterior se íntegra, senão None."""
    pointer = read_pointer(out)
    if pointer is None:
        return None
    for key in ("current", "previous"):
        try:
            verify_revision(pointer.get(key), out)
            return pointer[key]
        except SnapshotError:
            continue
    return None


def _install(staging, out, slot, files):
    target = out / slot
    target.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        _atomic_write(staging / ("dashboard-%s.json" % name), target / ("dashboard-%s.json" % name))
    _fsync_dir(target)
    for name in NAMES:
        files[name] = {"path": "%s/dashboard-%s.json" % (slot, name), **files[name]}


def build(out, inputs, builder):
    """Publica uma revisão. `builder(inputs, staging)` escreve os seis corpos e devolve os metadados da revisão.

    Devolve "published", "unchanged" ou "repaired". Qualquer erro deixa o ponteiro anterior intacto.
    """
    out = Path(out)
    with _locked(out), tempfile.TemporaryDirectory(prefix="v7-snap-") as tmp:
        staging = Path(tmp)
        # O builder lê cópias congeladas: a revisão deriva exatamente dos bytes cujos digests ficam registados.
        frozen = staging / "in"
        frozen.mkdir()
        copies = {}
        for key in INPUTS:
            copies[key] = frozen / (key + ".json")
            shutil.copyfile(inputs[key], copies[key])
        before = digests(copies)
        if digests(inputs) != before:
            raise SnapshotError("uma entrada mudou durante a cópia; nada publicado")
        meta = builder(copies, staging) or {}
        if digests(inputs) != before or digests(copies) != before:
            raise SnapshotError("uma entrada mudou durante a construção; nada publicado")
        files = {}
        for name in NAMES:
            body = staging / ("dashboard-%s.json" % name)
            if not body.is_file():
                raise SnapshotError("o builder não escreveu %s" % name)
            files[name] = {"sha256": sha(body), "bytes": body.stat().st_size}
        pointer = read_pointer(out)
        current = pointer.get("current") if pointer else None
        same = bool(current) and current.get("inputs") == before and all(
            current["files"].get(n, {}).get("sha256") == files[n]["sha256"] for n in NAMES)
        if same:
            try:
                verify_revision(current, out)
                return "unchanged"
            except SnapshotError:
                # mesma revisão, corpo corrompido: reescrita no lugar com os bytes esperados; a anterior fica intacta
                _install(staging, out, current["slot"], files)
                verify_revision(current, out)
                return "repaired"
        slot = SLOTS[1] if current and current.get("slot") == SLOTS[0] else SLOTS[0]
        _install(staging, out, slot, files)
        revision = {"slot": slot, "inputs": before, "files": files,
                    "report_generated_at_utc": meta.get("report_generated_at_utc")}
        verify_revision(revision, out)
        previous = None
        if current:
            try:
                verify_revision(current, out)
                previous = current
            except SnapshotError:
                previous = None
        staged_pointer = staging / POINTER
        staged_pointer.write_text(json.dumps({"schema": SCHEMA, "current": revision, "previous": previous},
                                             ensure_ascii=False, sort_keys=True), encoding="utf-8")
        _atomic_write(staged_pointer, out / POINTER)
        _fsync_dir(out)
        return "published"
