"""Staging HTTPS local dos snapshots V7: corre ops/staging/drill.mjs e verifica os resultados esperados.

Sem rede externa, sem custos, nada publicado. Requer node e openssl (presentes na CI ubuntu-24.04).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node") and shutil.which("openssl"), "node/openssl indisponíveis")
class StagingHttps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "r.json"
        subprocess.run(["node", "ops/staging/drill.mjs", "--out", str(out)], cwd=ROOT, check=True, capture_output=True,
                       timeout=600, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        cls.r = {x["name"].split()[0] + ("-" + x["keyMode"] if x["name"].startswith("S4") else ""): x
                 for x in json.loads(out.read_text())["results"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_tls_is_enforced_and_base_read_is_current(self):
        s1 = self.r["S1"]
        self.assertNotEqual(s1["tls_without_ca"], "aceite")
        self.assertEqual(s1["read"]["which"], "current")

    def test_one_publication_within_ttl_keeps_current(self):
        self.assertEqual(self.r["S2"]["after"]["which"], "current")

    def test_shared_cache_keeps_stale_pointer_consistent(self):
        s3 = self.r["S3"]
        self.assertEqual(s3["during"], s3["before"])         # atrasado mas coerente: corpos na mesma cache
        self.assertEqual(s3["stats"]["poisoned_keys"], 0)

    def test_split_cache_tiers_fall_back_to_full_report_and_poison_keys(self):
        s3b = self.r["S3b"]
        self.assertEqual(s3b["during"]["which"], "full_report")
        self.assertEqual(s3b["second_reader"]["which"], "full_report")
        self.assertGreater(s3b["poisoned_after_during"], 0)
        self.assertEqual(s3b["after_ttl"]["which"], "current")

    def test_query_keyed_cache_is_required(self):
        self.assertEqual(self.r["S4-full"]["after_pointer_expiry"]["which"], "current")
        self.assertEqual(self.r["S4-path"]["after_pointer_expiry"]["which"], "previous")  # dados atrasados

    def test_corrupted_current_falls_back_to_previous(self):
        self.assertEqual(self.r["S5"]["read"]["which"], "previous")

    def test_nothing_written_to_docs_or_data(self):
        status = subprocess.run(["git", "status", "--porcelain", "--", "docs", "data"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(status.stdout, "")


if __name__ == "__main__":
    unittest.main()
