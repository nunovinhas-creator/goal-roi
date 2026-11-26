"""Pacote do staging remoto (opção A): só dados sintéticos, layout publicável, cliente íntegro. Nada é publicado."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from audits.v7.snapshots import publisher_v2 as P  # noqa: E402


@unittest.skipUnless(shutil.which("node"), "node indisponível")
class RemotePackage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.site = Path(cls.tmp.name) / "site"
        subprocess.run([sys.executable, "ops/staging/remote/prepare_site.py", "--out", str(cls.site), "--revisions", "4"],
                       cwd=ROOT, check=True, capture_output=True, timeout=600)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_only_synthetic_data(self):
        real_names = set(json.loads((ROOT / "data/team_aliases.json").read_text(encoding="utf-8")))
        text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in self.site.rglob("*") if p.is_file())
        for marker in ["scangoal", "Betfair", "t.me/"] + sorted(real_names):
            self.assertNotIn(marker, text, marker)
        report_like = [p for p in (self.site / "snapshots").rglob("dashboard-summary.json")]
        self.assertTrue(report_like)

    def test_snapshot_layout_is_publishable_and_verifies(self):
        snaps = self.site / "snapshots"
        names = [p.relative_to(snaps).as_posix() for p in snaps.rglob("*") if p.is_file()]
        self.assertEqual([n for n in names if not P.PUBLISHABLE.fullmatch(n)], [])
        for rev in P.read_pointer(snaps)["history"]:
            P.verify_revision(rev, snaps)

    def test_manifest_hashes_and_nojekyll(self):
        import hashlib
        m = json.loads((self.site / "SITE-MANIFEST.json").read_text())
        self.assertTrue(m["synthetic"])
        for rel, digest in m["files"].items():
            self.assertEqual(hashlib.sha256((self.site / rel).read_bytes()).hexdigest(), digest, rel)
        self.assertTrue((self.site / ".nojekyll").exists())

    def test_measure_script_refuses_non_pages_base(self):
        r = subprocess.run(["node", "ops/staging/remote/measure_remote.mjs", "--base", "https://example.com/x"], cwd=ROOT, capture_output=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
