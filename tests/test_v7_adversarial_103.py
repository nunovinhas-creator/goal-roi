"""Auditoria adversarial dos snapshots (PR #105): as constatações A1–A6 passam a testes aprovados contra o
publicador V7.2 (audits/v7/snapshots/publisher_v2.py, layout "cas"). Não há expectedFailure.

As asserções das constatações originais mantêm-se (o "previous" anunciado continua íntegro após interrupção; uma
revisão íntegra sobrevive a corrupção + interrupção; um leitor atrasado lê o seu ponteiro; o ponteiro é uma linha;
o layout cabe na lista publicável e o lock fica fora da pasta servida; o recuo é distinguível). Acrescentam-se:
interrupção e SIGKILL em todos os renames, publicadores concorrentes no mesmo anfitrião e entre clones Git,
recuperação de corrupção, limitação de frequência, compatibilidade com o Pages e a comparação com N ranhuras.

    python3 -m pytest -q tests/test_v7_adversarial_103.py
"""
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from audits.v7.snapshots import publisher_v2 as P
from tests.test_v7_snapshot_publisher import fake_builder

ROOT = Path(__file__).resolve().parents[1]
RENAMES_PER_PUBLISH = len(P.NAMES) + 1  # seis objetos + ponteiro


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Fx:
    def __init__(self, base, **kw):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)
        self.out = self.base / "site" / "snapshots"
        self.inputs = {k: self.base / (k + ".json") for k in P.INPUTS}
        self.clock = Clock()
        self.pub = P.Publisher(self.out, clock=self.clock, **kw)
        self.n = 0
        self.revise()

    def revise(self):
        self.n += 1
        self.inputs["report"].write_text(json.dumps({"generated_at_utc": "rev-%04d" % self.n}))
        self.inputs["prospective"].write_text(json.dumps({"ledger_entries": self.n}))
        self.inputs["protocol"].write_text(json.dumps({"frozen": True}))

    def build(self, builder=fake_builder):
        return self.pub.build(self.inputs, builder)

    def step(self, dt=60):
        self.clock.t += dt
        self.revise()
        return self.build()

    def pointer(self):
        return P.read_pointer(self.out)

    def body(self, rev, name):
        return self.out / rev["files"][name]["path"]


def interrupted(fx, stop):
    """Publica e interrompe (OSError) no rename número `stop`. Devolve True se interrompeu."""
    original, calls = os.replace, {"n": 0}

    def replace(src, dst):
        calls["n"] += 1
        if calls["n"] == stop:
            raise OSError("interrupção simulada")
        return original(src, dst)

    with patch.object(P.os, "replace", side_effect=replace):
        try:
            fx.build()
        except OSError:
            return True
    return False


class Restore:
    """Repõe a pasta servida no estado inicial antes de cada ponto de interrupção (cada ponto é um ensaio próprio)."""

    def __init__(self, fx):
        self.fx, self.saved = fx, Path(tempfile.mkdtemp()) / "saved"
        shutil.copytree(fx.out, self.saved)

    def __call__(self):
        shutil.rmtree(self.fx.out)
        shutil.copytree(self.saved, self.fx.out)


def corrupt(path):
    path.write_bytes(path.read_bytes().replace(b"x", b"y", 1))


def _concurrent(base, n):
    fx = Fx.__new__(Fx)
    fx.base = Path(base)
    fx.out = fx.base / "site" / "snapshots"
    fx.inputs = {k: fx.base / ("in%d" % n) / (k + ".json") for k in P.INPUTS}
    fx.inputs["report"].parent.mkdir(exist_ok=True)
    fx.clock = Clock(1_800_000_000.0 + n)
    fx.pub = P.Publisher(fx.out, clock=fx.clock)
    fx.n = n
    fx.inputs["report"].write_text(json.dumps({"generated_at_utc": "conc-%d" % n}))
    fx.inputs["prospective"].write_text("{}")
    fx.inputs["protocol"].write_text("{}")
    return fx.build()


class CasInvariants(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fx(self.tmp.name)
        self.fx.build()
        self.fx.step()                     # current = rev 2, previous = rev 1
        self.pointer = self.fx.pointer()
        self.fx.clock.t += 60
        self.fx.revise()                   # rev 3 pronta a publicar

    def tearDown(self):
        self.tmp.cleanup()

    # A1 — o "previous" anunciado continua íntegro, em qualquer ponto de interrupção.
    def test_a1_previous_advertised_by_pointer_stays_intact_after_interruption(self):
        restore = Restore(self.fx)
        for stop in range(1, RENAMES_PER_PUBLISH + 1):
            with self.subTest(stop=stop):
                restore()
                self.assertTrue(interrupted(self.fx, stop))
                pointer = self.fx.pointer()
                self.assertEqual(pointer, self.pointer)
                P.verify_revision(pointer["previous"], self.fx.out)
                P.verify_revision(pointer["current"], self.fx.out)
        self.assertEqual(self.fx.build(), "published")
        P.verify_revision(self.fx.pointer()["previous"], self.fx.out)

    # A2 — com "current" corrompida, uma interrupção não deixa zero revisões íntegras.
    def test_a2_some_verified_revision_survives_corruption_plus_interruption(self):
        corrupt(self.fx.body(self.pointer["current"], "science"))
        self.assertEqual(P.read_verified(self.fx.out), self.pointer["previous"])
        restore = Restore(self.fx)
        for stop in range(1, RENAMES_PER_PUBLISH + 1):
            with self.subTest(stop=stop):
                restore()
                self.assertTrue(interrupted(self.fx, stop))
                self.assertIsNotNone(P.read_verified(self.fx.out))
        self.assertEqual(self.fx.build(), "published")
        pointer = self.fx.pointer()
        self.assertEqual(pointer["previous"], self.pointer["previous"])  # a corrompida sai do histórico
        self.assertNotIn(self.pointer["current"], pointer["history"])

    # A3 — leitor com o ponteiro obtido antes de várias publicações continua a ler o que o ponteiro descreve.
    def test_a3_reader_holding_pointer_across_two_publications_still_verifies(self):
        held = self.fx.pointer()
        for _ in range(20):
            self.assertEqual(self.fx.step(), "published")
        for key in ("current", "previous"):
            P.verify_revision(held[key], self.fx.out)

    # A3b — fora da retenção os objetos saem; o leitor deteta-o e volta a pedir o ponteiro.
    def test_a3b_reader_beyond_retention_detects_and_refetches(self):
        held = self.fx.pointer()
        self.fx.step()                                   # a revisão retida deixa de ser a atual
        self.fx.step(dt=self.fx.pub.retain_s + 1)        # substituída há mais do que a retenção: recolhida
        with self.assertRaises(P.SnapshotError):
            P.verify_revision(held["current"], self.fx.out)
        self.assertIsNotNone(P.read_verified(self.fx.out))

    # A3c — período calmo seguido de rajada: a retenção conta desde a substituição, não desde a publicação.
    def test_a3c_quiet_period_then_burst_keeps_held_revision(self):
        self.fx.clock.t += 3 * self.fx.pub.retain_s
        held = self.fx.pointer()
        self.fx.step(dt=30)
        self.fx.step(dt=30)
        P.verify_revision(held["current"], self.fx.out)

    # A4 — ponteiro numa só linha.
    def test_a4_pointer_is_single_line_so_concurrent_rebases_conflict(self):
        text = (self.fx.out / P.POINTER).read_text(encoding="utf-8")
        self.assertNotIn("\n", text)

    # A5 — tudo o que fica na pasta servida é publicável; o lock está fora dela.
    def test_a5_output_layout_is_publishable_and_lock_is_outside(self):
        for stop in range(1, RENAMES_PER_PUBLISH):
            interrupted(self.fx, stop)
        self.fx.build()
        names = sorted(p.relative_to(self.fx.out).as_posix() for p in self.fx.out.rglob("*") if p.is_file())
        self.assertEqual([n for n in names if not P.PUBLISHABLE.fullmatch(n)], [])
        self.assertTrue(self.fx.pub.lock.exists())
        self.assertNotIn(self.fx.out, self.fx.pub.lock.parents)

    # A6 — o recuo é distinguível pelo leitor.
    def test_a6_previous_fallback_is_distinguishable_from_current(self):
        corrupt(self.fx.body(self.pointer["current"], "learning"))
        rev = P.read_verified(self.fx.out)
        self.assertNotEqual(rev["inputs"], self.pointer["current"]["inputs"])
        self.assertLess(rev["published_at"], self.pointer["current"]["published_at"])

    def test_malformed_pointer_cannot_redirect_repair(self):
        self.fx.n -= 2
        self.fx.revise()
        outside = self.fx.out.parent / "outside.json"
        outside.write_bytes(b"do not modify")
        pointer = self.fx.pointer()
        pointer["current"]["files"]["summary"]["path"] = "../outside.json"
        pointer["history"][0] = pointer["current"]
        raw = json.dumps(pointer).encode()
        (self.fx.out / P.POINTER).write_bytes(raw)
        self.assertIsNone(P.read_verified(self.fx.out))
        with self.assertRaises(P.SnapshotError):
            self.fx.build()
        self.assertEqual(outside.read_bytes(), b"do not modify")
        self.assertEqual((self.fx.out / P.POINTER).read_bytes(), raw)

    def test_malformed_history_is_refused_without_replacing_pointer(self):
        for history in (None, {}, [None], []):
            pointer = dict(self.pointer, history=history)
            raw = json.dumps(pointer).encode()
            (self.fx.out / P.POINTER).write_bytes(raw)
            self.assertIsNone(P.read_verified(self.fx.out))
            with self.assertRaises(P.SnapshotError):
                self.fx.build()
            self.assertEqual((self.fx.out / P.POINTER).read_bytes(), raw)

    def test_symlink_object_is_not_repaired_outside_store(self):
        self.fx.n -= 2
        self.fx.revise()
        body = self.fx.body(self.pointer["current"], "summary")
        outside = self.fx.out.parent / "outside.json"
        outside.write_bytes(b"keep")
        body.unlink()
        body.symlink_to(outside)
        with self.assertRaises(P.SnapshotError):
            self.fx.build()
        self.assertEqual(outside.read_bytes(), b"keep")

    def test_corrupted_current_same_inputs_is_repaired(self):
        self.fx.n -= 2
        self.fx.revise()  # volta às entradas da revisão atual (rev 2)
        corrupt(self.fx.body(self.pointer["current"], "signals"))
        self.assertEqual(self.fx.build(), "repaired")
        P.verify_revision(self.fx.pointer()["current"], self.fx.out)

    def test_shared_object_corrupted_is_rewritten_by_next_publish(self):
        def stable_science(inputs, staging):
            meta = fake_builder(inputs, staging)
            (staging / "dashboard-science.json").write_text('{"stable": true}')
            return meta
        self.fx.build(stable_science)
        shared = self.fx.body(self.fx.pointer()["current"], "science")
        corrupt_bytes = b'{"stable": fals}'
        shared.write_bytes(corrupt_bytes)
        self.fx.clock.t += 60
        self.fx.revise()
        self.assertEqual(self.fx.build(stable_science), "published")
        P.verify_revision(self.fx.pointer()["current"], self.fx.out)

    def test_sigkill_at_every_rename(self):
        restore = Restore(self.fx)
        for stop in range(1, RENAMES_PER_PUBLISH + 1):
            with self.subTest(stop=stop):
                restore()
                before = (self.fx.out / P.POINTER).read_bytes()
                code = (
                    "import os,sys,json;sys.path.insert(0,%r)\n"
                    "from tests.test_v7_adversarial_103 import Fx, Clock\n"
                    "from audits.v7.snapshots import publisher_v2 as P\n"
                    "from pathlib import Path\n"
                    "fx=Fx.__new__(Fx);fx.base=Path(%r);fx.out=fx.base/'site'/'snapshots'\n"
                    "fx.inputs={k:fx.base/(k+'.json') for k in P.INPUTS};fx.clock=Clock(%r)\n"
                    "fx.pub=P.Publisher(fx.out,clock=fx.clock)\n"
                    "orig=os.replace;n=[0]\n"
                    "def r(a,b):\n n[0]+=1\n if n[0]==%d: os.kill(os.getpid(),9)\n return orig(a,b)\n"
                    "P.os.replace=r;fx.build()\n") % (str(ROOT), self.tmp.name, self.fx.clock.t, stop)
                proc = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                self.assertEqual(proc.returncode, -9)
                after = (self.fx.out / P.POINTER).read_bytes()
                self.assertEqual(after, before)  # o último rename é o do ponteiro: ainda não aconteceu
                for rev in json.loads(after)["history"]:
                    P.verify_revision(rev, self.fx.out)
        self.assertEqual(self.fx.build(), "published")
        names = [p.name for p in self.fx.out.rglob("*") if p.is_file()]
        self.assertFalse([n for n in names if n.endswith(".tmp")])

    def test_rate_limit_defers_and_keeps_current_intact(self):
        fx = Fx(Path(self.tmp.name) / "rl", min_interval_s=600)
        self.assertEqual(fx.build(), "published")
        first = fx.pointer()
        self.assertEqual(fx.step(dt=60), "deferred")
        self.assertEqual(fx.pointer(), first)
        P.verify_revision(first["current"], fx.out)
        self.assertEqual(fx.step(dt=600), "published")

    def test_clock_behind_the_last_publication_never_defers(self):
        fx = Fx(Path(self.tmp.name) / "skew", min_interval_s=600)
        fx.build()
        self.assertEqual(fx.step(dt=-120), "published")   # outro anfitrião com relógio atrasado

    def test_rate_limit_never_defers_a_corrupted_current(self):
        fx = Fx(Path(self.tmp.name) / "rl2", min_interval_s=600)
        fx.build()
        corrupt(fx.body(fx.pointer()["current"], "summary"))
        self.assertEqual(fx.step(dt=60), "published")

    def test_bounded_storage_under_frequent_publication(self):
        for _ in range(100):
            self.fx.step(dt=60)
        history = self.fx.pointer()["history"]
        self.assertLessEqual(len(history), self.fx.pub.retain_s // 60 + 2)
        objects = [p for p in (self.fx.out / "objects").rglob("*.json")]
        self.assertEqual(len(objects), len({e["path"] for r in history for e in r["files"].values()}))

    def test_github_pages_compatible_names(self):
        # Pages com Jekyll ignora nomes começados por "_" ou "."; nenhum ficheiro publicado pode depender disso.
        for p in self.fx.out.rglob("*"):
            rel = p.relative_to(self.fx.out).as_posix()
            self.assertFalse(any(part.startswith(("_", ".")) for part in rel.split("/")), rel)
            if p.is_file():
                self.assertTrue(P.PUBLISHABLE.fullmatch(rel), rel)


class ConcurrentPublishers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_host_publishers_with_different_inputs_never_mix(self):
        ctx = multiprocessing.get_context("fork")
        with ctx.Pool(4) as pool:
            results = pool.starmap(_concurrent, [(self.tmp.name, n) for n in range(8)])
        self.assertEqual(results.count("published"), 8)
        out = Path(self.tmp.name) / "site" / "snapshots"
        pointer = P.read_pointer(out)
        for rev in pointer["history"]:
            P.verify_revision(rev, out)
            self.assertTrue(all(json.loads((out / e["path"]).read_text())["rev"] == rev["report_generated_at_utc"]
                                for e in rev["files"].values()), "revisão misturada")

    def test_cross_clone_publishers_conflict_on_pointer_and_retry_cleanly(self):
        base = Path(self.tmp.name)
        git = lambda cwd, *a: subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True)
        git(base, "init", "-q", "--bare", "remote.git")
        clones = {}
        for name in ("a", "b"):
            subprocess.run(["git", "clone", "-q", str(base / "remote.git"), str(base / name)], check=True, capture_output=True)
            git(base / name, "config", "user.email", "t@example.invalid"); git(base / name, "config", "user.name", name)
        fx0 = Fx(base / "seed"); fx0.out = base / "a" / "snapshots"; fx0.pub = P.Publisher(fx0.out, clock=fx0.clock, lock=base / "a.lock")
        fx0.build(); git(base / "a", "add", "-A"); git(base / "a", "commit", "-qm", "r1"); git(base / "a", "push", "-q", "origin", "HEAD:main")
        git(base / "b", "pull", "-q", "origin", "main")
        for name, label in (("a", "ra"), ("b", "rb")):
            fx = Fx(base / ("in-" + name)); fx.inputs["report"].write_text(json.dumps({"generated_at_utc": label}))
            fx.pub = P.Publisher(base / name / "snapshots", clock=fx.clock, lock=base / (name + ".lock")); fx.out = fx.pub.out
            self.assertEqual(fx.build(), "published")
            git(base / name, "add", "-A"); git(base / name, "commit", "-qm", label)
            clones[name] = fx
        self.assertEqual(git(base / "a", "push", "-q", "origin", "HEAD:main").returncode, 0)
        git(base / "b", "fetch", "-q", "origin", "main")
        rebase = git(base / "b", "rebase", "FETCH_HEAD")
        self.assertNotEqual(rebase.returncode, 0, "o ponteiro tem de entrar em conflito")
        self.assertIn("dashboard-manifest.json", git(base / "b", "diff", "--name-only", "--diff-filter=U").stdout)
        git(base / "b", "rebase", "--abort")
        # Repetição a partir do estado remoto (como publication.py faz depois de abortar).
        git(base / "b", "reset", "-q", "--hard", "FETCH_HEAD")
        self.assertEqual(clones["b"].build(), "published")
        out = base / "b" / "snapshots"
        pointer = P.read_pointer(out)
        self.assertEqual([r["report_generated_at_utc"] for r in pointer["history"][:2]], ["rb", "ra"])
        for rev in pointer["history"]:
            P.verify_revision(rev, out)


class SlotsComparison(unittest.TestCase):
    """Três ranhuras corrigem A1/A2 mas não chegam para leitores atrasados ≥ 3 publicações; com duas ranhuras e a
    escolha segura não há revisão anterior anunciada (nenhum recuo)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_three_slots_fix_a1_a2(self):
        fx = Fx(self.tmp.name, layout="slots", slots=3)
        fx.build(); fx.step()
        pointer = fx.pointer()
        fx.clock.t += 60; fx.revise()
        restore = Restore(fx)
        for stop in range(1, RENAMES_PER_PUBLISH + 1):
            restore()
            self.assertTrue(interrupted(fx, stop))
            P.verify_revision(pointer["previous"], fx.out)
            P.verify_revision(pointer["current"], fx.out)

    def test_three_slots_are_not_enough_for_delayed_readers(self):
        fx = Fx(self.tmp.name, layout="slots", slots=3)
        fx.build(); fx.step()
        held = fx.pointer()
        fx.step()
        P.verify_revision(held["current"], fx.out)       # 1 publicação depois: ainda legível
        fx.step(); fx.step()
        with self.assertRaises(P.SnapshotError):         # 3 publicações depois: ranhura reutilizada
            P.verify_revision(held["current"], fx.out)

    def test_two_slots_cannot_announce_a_previous_revision(self):
        fx = Fx(self.tmp.name, layout="slots", slots=2)
        fx.build(); fx.step(); fx.step()
        pointer = fx.pointer()
        self.assertIsNone(pointer["previous"])           # sem ranhura livre para manter uma anterior íntegra
        corrupt(fx.body(pointer["current"], "summary"))
        self.assertIsNone(P.read_verified(fx.out))       # corrupção da atual = nenhuma revisão legível


@unittest.skipUnless(shutil.which("node"), "node indisponível")
class NodeClient(unittest.TestCase):
    """Corre na CI os testes do cliente (C1–C3) e do cliente V7 original."""

    def test_client_suites(self):
        r = subprocess.run(["node", "--test", "tests/test_v7_adversarial_client.mjs", "tests/test_v7_client_integrity.mjs"],
                           cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])
        self.assertIn("# todo 0", r.stdout)
        self.assertIn("# fail 0", r.stdout)


if __name__ == "__main__":
    unittest.main()
