"""Fase 1 de "relatórios fora do Git": o site montado é igual a docs/ nos caminhos públicos e o inventário de
consumidores dos relatórios gerados está completo (uma referência nova obriga a rever o plano)."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_pages_site as B  # noqa: E402

# Inventário verificado a 26/11/2026 (ops/reports-out-of-git/README.md). Ficheiro -> número de referências.
INVENTORY = {
    ".github/workflows/collect.yml": 2,
    "scangoal/report.py": 2,
    "scangoal/watch.py": 3,
    "scangoal/notify.py": 4,
    "scangoal/certification.py": 2,
    "scangoal/publication.py": 2,
}
PATTERN = re.compile(r"(?<!prospective_)report\.json|bets\.csv")


class PagesSite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "site"

    def tearDown(self):
        self.tmp.cleanup()

    def test_site_matches_docs_on_every_public_path(self):
        manifest = B.build(self.out, ROOT / "docs/report.json", ROOT / "docs/bets.csv", ROOT / "docs")
        docs = {p.relative_to(ROOT / "docs").as_posix() for p in (ROOT / "docs").rglob("*") if p.is_file()}
        self.assertEqual(set(manifest["files"]), docs)
        for rel in docs:
            self.assertEqual((self.out / rel).read_bytes(), (ROOT / "docs" / rel).read_bytes(), rel)

    def test_missing_reference_is_refused(self):
        docs = Path(self.tmp.name) / "docs"
        docs.mkdir()
        (docs / "index.html").write_text('<script src="falta.js"></script>')
        with self.assertRaisesRegex(B.SiteError, "falta.js"):
            B.build(self.out, ROOT / "docs/report.json", ROOT / "docs/bets.csv", docs)
        self.assertFalse(self.out.exists())

    def test_invalid_report_is_refused(self):
        bad = Path(self.tmp.name) / "r.json"
        bad.write_text('{"truncado":')
        with self.assertRaises(B.SiteError):
            B.build(self.out, bad, ROOT / "docs/bets.csv", ROOT / "docs")
        self.assertFalse(self.out.exists())

    def test_existing_destination_is_refused(self):
        self.out.mkdir()
        with self.assertRaises(B.SiteError):
            B.build(self.out, ROOT / "docs/report.json", ROOT / "docs/bets.csv", ROOT / "docs")

    def test_consumer_inventory_is_complete(self):
        files = subprocess.run(["git", "ls-files", "scangoal", ".github"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        found = {}
        for f in files:
            if f.endswith((".py", ".yml", ".mjs", ".js")):
                n = len(PATTERN.findall((ROOT / f).read_text(encoding="utf-8", errors="replace")))
                if n:
                    found[f] = n
        self.assertEqual(found, INVENTORY, "consumidor novo ou removido: rever ops/reports-out-of-git/README.md antes da fase 2")


if __name__ == "__main__":
    unittest.main()
