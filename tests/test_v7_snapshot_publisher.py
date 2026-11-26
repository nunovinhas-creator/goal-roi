import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from audits.v7.snapshots import publisher as P

ROOT = Path(__file__).resolve().parents[1]


def fake_builder(inputs, staging):
    report = json.loads(Path(inputs["report"]).read_bytes())
    prospective = Path(inputs["prospective"]).read_bytes()
    for name in P.NAMES:
        body = {"kind": name, "rev": report["generated_at_utc"], "p": len(prospective), "pad": "x" * (100 + len(name))}
        (staging / ("dashboard-%s.json" % name)).write_text(json.dumps(body))
    return {"report_generated_at_utc": report["generated_at_utc"]}


class Fixture:
    def __init__(self, base):
        self.base = Path(base)
        self.out = self.base / "out"
        self.inputs = {k: self.base / (k + ".json") for k in P.INPUTS}
        self.revise("2026-11-26T10:00:00+00:00", 1)

    def revise(self, generated, entries):
        self.inputs["report"].write_text(json.dumps({"generated_at_utc": generated}))
        self.inputs["prospective"].write_text(json.dumps({"ledger_entries": entries}))
        self.inputs["protocol"].write_text(json.dumps({"frozen": True}))

    def build(self, builder=fake_builder):
        return P.build(self.out, self.inputs, builder)


def concurrent_build(base, generated):
    fx = Fixture.__new__(Fixture)
    fx.base = Path(base); fx.out = fx.base / "out"
    fx.inputs = {k: fx.base / (k + ".json") for k in P.INPUTS}
    return fx.build()


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx = Fixture(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_publish_unchanged_and_alternating_slots(self):
        self.assertEqual(self.fx.build(), "published")
        self.assertEqual(self.fx.build(), "unchanged")
        self.fx.revise("2026-11-26T10:10:00+00:00", 2)
        self.assertEqual(self.fx.build(), "published")
        pointer = P.verify_live(self.fx.out, self.fx.inputs)
        self.assertEqual((pointer["current"]["slot"], pointer["previous"]["slot"]), ("b", "a"))
        P.verify_revision(pointer["previous"], self.fx.out)  # a anterior continua íntegra

    def test_corruption_on_same_revision_is_repaired(self):
        self.fx.build()
        body = self.fx.out / "a" / "dashboard-signals.json"
        body.write_text("{}")
        with self.assertRaises(P.SnapshotError):
            P.verify_live(self.fx.out, self.fx.inputs)
        self.assertEqual(self.fx.build(), "repaired")
        P.verify_live(self.fx.out, self.fx.inputs)

    def test_same_length_tampering_is_rejected(self):
        self.fx.build()
        body = self.fx.out / "a" / "dashboard-summary.json"
        data = body.read_bytes()
        body.write_bytes(data.replace(b"x", b"y", 1))
        self.assertEqual(len(body.read_bytes()), len(data))
        with self.assertRaises(P.SnapshotError):
            P.verify_live(self.fx.out, self.fx.inputs)

    def test_interruption_at_every_rename_never_mixes_revisions(self):
        self.fx.build()
        self.fx.revise("2026-11-26T10:10:00+00:00", 2)
        self.fx.build()  # current=b, previous=a
        old_pointer = (self.fx.out / P.POINTER).read_bytes()
        self.fx.revise("2026-11-26T10:20:00+00:00", 3)
        original = os.replace
        for stop in range(1, len(P.NAMES) + 2):
            with self.subTest(stop=stop):
                calls = {"n": 0}

                def interrupt(src, dst):
                    calls["n"] += 1
                    if calls["n"] == stop:
                        raise OSError("interrupção simulada")
                    return original(src, dst)

                with patch.object(P.os, "replace", side_effect=interrupt):
                    with self.assertRaises(OSError):
                        self.fx.build()
                self.assertEqual((self.fx.out / P.POINTER).read_bytes(), old_pointer)
                pointer = json.loads(old_pointer)
                P.verify_revision(pointer["current"], self.fx.out)  # a revisão servida nunca fica misturada
                self.assertEqual(P.read_verified(self.fx.out)["slot"], "b")
        self.assertEqual(self.fx.build(), "published")
        P.verify_live(self.fx.out, self.fx.inputs)

    def test_sigkill_before_pointer_swap(self):
        self.fx.build()
        old_pointer = (self.fx.out / P.POINTER).read_bytes()
        self.fx.revise("2026-11-26T10:10:00+00:00", 2)
        code = (
            "import os,sys,json;sys.path.insert(0,%r)\n"
            "from tests.test_v7_snapshot_publisher import Fixture\n"
            "from audits.v7.snapshots import publisher as P\n"
            "fx=Fixture.__new__(Fixture);from pathlib import Path;fx.base=Path(%r);fx.out=fx.base/'out'\n"
            "fx.inputs={k:fx.base/(k+'.json') for k in P.INPUTS}\n"
            "orig=os.replace;n=[0]\n"
            "def r(a,b):\n n[0]+=1\n if n[0]==4: os.kill(os.getpid(),9)\n return orig(a,b)\n"
            "P.os.replace=r;fx.build()\n") % (str(ROOT), self.tmp.name)
        proc = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(proc.returncode, -9)
        self.assertEqual((self.fx.out / P.POINTER).read_bytes(), old_pointer)
        P.verify_revision(json.loads(old_pointer)["current"], self.fx.out)
        self.assertEqual(self.fx.build(), "published")
        P.verify_live(self.fx.out, self.fx.inputs)

    def test_secondary_input_changed_during_build_is_rejected(self):
        self.fx.build()
        old_pointer = (self.fx.out / P.POINTER).read_bytes()
        self.fx.revise("2026-11-26T10:10:00+00:00", 2)

        def racing_builder(inputs, staging):
            meta = fake_builder(inputs, staging)
            Path(inputs["prospective"]).write_text(json.dumps({"ledger_entries": 3}))
            return meta

        with self.assertRaises(P.SnapshotError):
            self.fx.build(racing_builder)
        self.assertEqual((self.fx.out / P.POINTER).read_bytes(), old_pointer)
        # o ponteiro antigo já não descreve as entradas atuais: o leitor recusa-o e usa o relatório completo
        with self.assertRaises(P.SnapshotError):
            P.verify_live(self.fx.out, self.fx.inputs)
        self.assertEqual(self.fx.build(), "published")
        self.assertEqual(P.verify_live(self.fx.out, self.fx.inputs)["current"]["inputs"]["prospective"],
                         P.sha(self.fx.inputs["prospective"]))

    def test_previous_revision_is_fallback_when_current_corrupted(self):
        self.fx.build()
        self.fx.revise("2026-11-26T10:10:00+00:00", 2)
        self.fx.build()
        (self.fx.out / "b" / "dashboard-learning.json").write_text("{}")
        self.assertEqual(P.read_verified(self.fx.out)["slot"], "a")

    def test_concurrent_builders_leave_a_valid_pointer(self):
        ctx = multiprocessing.get_context("fork")
        with ctx.Pool(4) as pool:
            results = pool.starmap(concurrent_build, [(self.tmp.name, None)] * 8)
        self.assertEqual(results.count("published"), 1)
        self.assertEqual(results.count("unchanged"), 7)
        P.verify_live(self.fx.out, self.fx.inputs)


if __name__ == "__main__":
    unittest.main()
