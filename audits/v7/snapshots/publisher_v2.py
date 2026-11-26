"""Publicador de snapshots V7.2 (offline, inativo): corrige as constatações A1–A6 da auditoria adversarial (PR #105).

Dois layouts, para comparação com as mesmas provas:

- "cas" (recomendado): objetos imutáveis endereçados por conteúdo, objects/<sha256>/dashboard-<nome>.json (o SHA no
  diretório e o nome estável no fim do caminho, para que o Git agrupe as versões do mesmo snapshot e guarde deltas). Um objeto nunca é
  reescrito com outro conteúdo; só é reparado (mesmo conteúdo esperado) ou apagado pela recolha de lixo, depois de
  sair da janela de retenção. Leitores atrasados e caches em níveis diferentes encontram sempre os bytes que o seu
  ponteiro descreve enquanto a retenção cobrir o atraso.
- "slots": N ranhuras com nomes estáveis, s<i>/dashboard-<nome>.json. A ranhura de destino é escolhida pela
  integridade verificada: nunca a de uma revisão íntegra ainda anunciada. Se não houver ranhura livre, recusa.

Comum: relatório, entrada prospetiva e protocolo fixados em conjunto (SHA-256 antes e depois do builder);
ponteiro numa só linha (o rebase de publication.py entra em conflito e aborta com dois escritores); rename
atómico com fsync; lock fora da pasta servida; limitação opcional da frequência (min_interval_s); histórico de
revisões verificadas no ponteiro.

Nada aqui é chamado pela recolha, pelo workflow ou pelo Pages. Não ativa snapshots.
"""
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

SCHEMA = "goal_roi_dashboard_pointer_v7_2"
NAMES = ("summary", "signals", "performance", "science", "operations", "learning")
POINTER = "dashboard-manifest.json"
INPUTS = ("report", "prospective", "protocol")
_N = "|".join(NAMES)
CAS_PATH = re.compile(r"^objects/([0-9a-f]{64})/dashboard-(%s)\.json$" % _N)
SLOT_PATH = re.compile(r"^s([0-9])/dashboard-(%s)\.json$" % _N)
# Caminhos publicáveis (para a lista de publication.py num futuro porte), relativos a docs/v6/snapshots/.
PUBLISHABLE = re.compile(r"^(dashboard-manifest\.json|objects/[0-9a-f]{64}/dashboard-(%s)\.json|s[0-9]/dashboard-(%s)\.json)$" % (_N, _N))


class SnapshotError(RuntimeError):
    pass


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha(path):
    return sha_bytes(Path(path).read_bytes())


def digests(inputs):
    return {k: sha(inputs[k]) for k in INPUTS}


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(data, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name("." + dst.name + ".tmp")
    with open(tmp, "wb") as b:
        b.write(data)
        b.flush()
        os.fsync(b.fileno())
    os.replace(tmp, dst)


def read_pointer(out):
    try:
        data = json.loads((Path(out) / POINTER).read_bytes())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None


def validate_metadata(revision):
    """Valida o manifesto sem tocar nos caminhos indicados por ele."""
    if not isinstance(revision, dict) or revision.get("layout") not in ("cas", "slots"):
        raise SnapshotError("revisão malformada")
    files = revision.get("files")
    if not isinstance(files, dict) or set(files) != set(NAMES):
        raise SnapshotError("a revisão não lista exatamente os seis snapshots")
    inputs = revision.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != set(INPUTS) or any(
            not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in inputs.values()):
        raise SnapshotError("hashes das entradas inválidos")
    when = revision.get("published_at")
    if type(when) not in (int, float) or not math.isfinite(when):
        raise SnapshotError("instante de publicação inválido")
    slots = set()
    for name in NAMES:
        entry = files[name]
        if not isinstance(entry, dict):
            raise SnapshotError("entrada inválida: %s" % name)
        path, digest, size = entry.get("path"), entry.get("sha256"), entry.get("bytes")
        if not isinstance(path, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or type(size) is not int or size < 0:
            raise SnapshotError("metadados inválidos: %s" % name)
        m = (CAS_PATH if revision["layout"] == "cas" else SLOT_PATH).fullmatch(path)
        if not m or m.group(2) != name:
            raise SnapshotError("caminho inesperado: %s" % name)
        if revision["layout"] == "cas" and m.group(1) != digest:
            raise SnapshotError("objeto com nome diferente do conteúdo: %s" % name)
        slots.add(m.group(1))
    if revision["layout"] == "slots" and len(slots) != 1:
        raise SnapshotError("revisão mistura ranhuras")


def validate_pointer(pointer):
    if not isinstance(pointer, dict) or pointer.get("schema") != SCHEMA:
        raise SnapshotError("ponteiro inválido")
    history = pointer.get("history")
    if not isinstance(history, list) or not history or pointer.get("current") != history[0] or pointer.get("previous") != (history[1] if len(history) > 1 else None):
        raise SnapshotError("histórico do ponteiro incoerente")
    for revision in history:
        validate_metadata(revision)
        if revision["layout"] != history[0]["layout"]:
            raise SnapshotError("histórico mistura layouts")


def checked_body(out, path):
    root = Path(out).resolve()
    body = root / path
    # Uma ligação simbólica pode redirecionar a leitura/reparação para fora do CAS.
    if body.resolve() != body:
        raise SnapshotError("ligação simbólica no caminho de snapshot")
    return body


def verify_revision(revision, out):
    """Valida metadados, caminhos, tamanho e SHA-256 de todos os corpos."""
    validate_metadata(revision)
    for name in NAMES:
        entry = revision["files"][name]
        try:
            body = checked_body(out, entry["path"])
            if not body.is_file() or body.stat().st_size != entry["bytes"] or sha(body) != entry["sha256"]:
                raise SnapshotError("corpo não coincide com o manifesto: %s" % name)
        except (OSError, RuntimeError) as exc:
            if isinstance(exc, SnapshotError):
                raise
            raise SnapshotError("corpo ilegível: %s" % name) from exc


def _intact(revision, out):
    try:
        verify_revision(revision, out)
        return True
    except SnapshotError:
        return False


def read_verified(out):
    """Para leitores: a revisão mais recente íntegra do histórico do ponteiro (current, previous, …) ou None."""
    pointer = read_pointer(out)
    try:
        validate_pointer(pointer)
    except SnapshotError:
        return None
    for rev in pointer["history"]:
        if _intact(rev, out):
            return rev
    return None


def verify_live(out, inputs):
    pointer = read_pointer(out)
    validate_pointer(pointer)
    verify_revision(pointer["current"], out)
    if pointer["current"].get("inputs") != digests(inputs):
        raise SnapshotError("o ponteiro não descreve as entradas atuais")
    return pointer


class Publisher:
    def __init__(self, out, layout="cas", slots=3, min_interval_s=0, retain_s=1800, keep_min=2, lock=None, clock=time.time):
        if layout not in ("cas", "slots") or (layout == "slots" and not 2 <= slots <= 10):
            raise ValueError("layout inválido")
        self.out, self.layout, self.slots = Path(out), layout, slots
        self.min_interval_s, self.retain_s, self.keep_min, self.clock = min_interval_s, retain_s, keep_min, clock
        # O lock fica fora da pasta servida pelo Pages (constatação A5).
        self.lock = Path(lock) if lock else self.out.parent / (self.out.name + ".lock")

    @contextlib.contextmanager
    def _locked(self):
        self.out.mkdir(parents=True, exist_ok=True)
        with open(self.lock, "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def _paths(self, bodies, history):
        if self.layout == "cas":
            return {n: "objects/%s/dashboard-%s.json" % (sha_bytes(b), n) for n, b in bodies.items()}
        # Ranhura livre = não usada por nenhuma revisão íntegra ainda anunciada (A1, A2). Sem livre: recusa.
        busy = {r["files"][NAMES[0]]["path"].split("/")[0] for r in history if _intact(r, self.out)}
        used_order = [r["files"][NAMES[0]]["path"].split("/")[0] for r in history]
        free = [s for s in ("s%d" % i for i in range(self.slots)) if s not in busy]
        if not free:
            raise SnapshotError("nenhuma ranhura livre sem destruir uma revisão íntegra; nada publicado")
        free.sort(key=lambda s: (s in used_order, -used_order.index(s) if s in used_order else 0))
        slot = free[0]
        return {n: "%s/dashboard-%s.json" % (slot, n) for n in bodies}

    def _write(self, bodies, paths):
        for name in NAMES:
            dst = checked_body(self.out, paths[name])
            if self.layout == "cas" and dst.is_file() and sha(dst) == sha_bytes(bodies[name]):
                continue  # objeto imutável já presente e íntegro
            _atomic_write(bodies[name], dst)
            _fsync_dir(dst.parent)

    def _gc(self, history):
        if self.layout != "cas":
            return []
        keep = {e["path"] for r in history for e in r["files"].values()}
        removed = []
        for path in sorted((self.out / "objects").rglob("*")):
            rel = path.relative_to(self.out).as_posix()
            if path.is_file() and rel not in keep:
                path.unlink()
                removed.append(rel)
        for d in sorted((p for p in (self.out / "objects").glob("*") if p.is_dir()), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()
        return removed

    def build(self, inputs, builder):
        """Devolve "published", "unchanged", "repaired" ou "deferred". Qualquer erro deixa o ponteiro anterior intacto."""
        with self._locked(), tempfile.TemporaryDirectory(prefix="v7-snap2-") as tmp:
            # Restos de uma escrita interrompida (SIGKILL): temporários ocultos nunca referenciados.
            for stray in self.out.rglob(".*.tmp"):
                stray.unlink()
            staging = Path(tmp)
            frozen = staging / "in"
            frozen.mkdir()
            copies = {k: frozen / (k + ".json") for k in INPUTS}
            for k in INPUTS:
                shutil.copyfile(inputs[k], copies[k])
            before = digests(copies)
            if digests(inputs) != before:
                raise SnapshotError("uma entrada mudou durante a cópia; nada publicado")
            meta = builder(copies, staging) or {}
            if digests(inputs) != before or digests(copies) != before:
                raise SnapshotError("uma entrada mudou durante a construção; nada publicado")
            bodies = {}
            for name in NAMES:
                body = staging / ("dashboard-%s.json" % name)
                if not body.is_file():
                    raise SnapshotError("o builder não escreveu %s" % name)
                bodies[name] = body.read_bytes()
            pointer = read_pointer(self.out)
            if (self.out / POINTER).exists() or (self.out / POINTER).is_symlink():
                validate_pointer(pointer)
            history = list(pointer["history"]) if pointer else []
            current = pointer.get("current") if pointer else None
            if current and current.get("layout") != self.layout:
                raise SnapshotError("layout diferente do publicado; migração exige decisão própria")
            same = bool(current) and current.get("inputs") == before and all(
                current["files"][n]["sha256"] == sha_bytes(bodies[n]) and current["files"][n]["bytes"] == len(bodies[n]) for n in NAMES)
            if same:
                if _intact(current, self.out):
                    return "unchanged"
                # Mesma revisão com corpo corrompido: repõe os bytes esperados nos mesmos caminhos.
                for name in NAMES:
                    dst = checked_body(self.out, current["files"][name]["path"])
                    if not dst.is_file() or sha(dst) != current["files"][name]["sha256"]:
                        _atomic_write(bodies[name], dst)
                        _fsync_dir(dst.parent)
                verify_revision(current, self.out)
                return "repaired"
            now = self.clock()
            age = now - current.get("published_at", 0) if current else None
            # Só adia com limite configurado e relógio coerente: um relógio atrasado (outro anfitrião) nunca bloqueia.
            if self.min_interval_s > 0 and current and _intact(current, self.out) and 0 <= age < self.min_interval_s:
                return "deferred"  # a revisão atual fica, íntegra e coerente; a frescura é declarada pelo leitor
            paths = self._paths(bodies, history)
            self._write(bodies, paths)
            revision = {"layout": self.layout, "inputs": before, "published_at": now,
                        "report_generated_at_utc": meta.get("report_generated_at_utc"),
                        "files": {n: {"path": paths[n], "sha256": sha_bytes(bodies[n]), "bytes": len(bodies[n])} for n in NAMES}}
            verify_revision(revision, self.out)
            new_slot = paths[NAMES[0]].split("/")[0]
            kept = []
            superseded_at = now  # uma revisão pode estar num ponteiro em cache até retain_s depois de ser substituída
            for r in history:
                replaced, superseded_at = superseded_at, r.get("published_at", 0)
                if not _intact(r, self.out):
                    continue
                if self.layout == "slots":
                    if r["files"][NAMES[0]]["path"].split("/")[0] == new_slot:
                        continue
                elif len(kept) + 1 >= self.keep_min and now - replaced > self.retain_s:
                    continue
                kept.append(r)
            if self.layout == "slots":
                kept = kept[:self.slots - 2]  # deixa sempre uma ranhura livre para a publicação seguinte
            history = [revision] + kept
            staged = staging / POINTER
            staged.write_text(json.dumps({"schema": SCHEMA, "current": revision, "previous": kept[0] if kept else None,
                                          "history": history}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                              encoding="utf-8")
            _atomic_write(staged.read_bytes(), self.out / POINTER)
            _fsync_dir(self.out)
            self._gc(history)
            return "published"
