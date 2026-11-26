"""Staging HTTPS local dos snapshots: corre ops/staging/drill.mjs para os três desenhos (v1 da PR #103, 3 ranhuras e
CAS da PR #105) e verifica os resultados. Sem rede externa, sem custos, nada publicado. Requer node e openssl."""
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
                       timeout=900, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        cls.r = {(x["design"], x["name"]): x for x in json.loads(out.read_text())["results"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def w(self, design, name, key):
        return self.r[(design, name)][key]["which"]

    def test_tls_is_enforced_for_every_design(self):
        for d in ("v1", "slots3", "cas"):
            self.assertNotEqual(self.r[(d, "S1")]["tls_without_ca"], "aceite")
            self.assertEqual(self.w(d, "S1", "read"), "current")

    def test_cas_survives_every_cache_scenario_with_current_data(self):
        for name, key in (("S2", "after"), ("S3", "during"), ("S3b", "during"), ("S3b", "second_reader"),
                          ("S4-full", "after_pointer_expiry"), ("S4-path", "after_pointer_expiry")):
            self.assertEqual(self.w("cas", name, key), "current", name + "/" + key)
        for name in ("S2", "S3", "S3b", "S4-full", "S4-path"):
            self.assertEqual(self.r[("cas", name)]["stats"]["poisoned_keys"], 0, name)

    def test_v1_and_three_slots_fail_split_cache_tiers(self):
        self.assertEqual(self.w("v1", "S3b", "during"), "full_report")
        self.assertGreater(self.r[("v1", "S3b")]["stats"]["poisoned_keys"], 0)
        self.assertEqual(self.w("slots3", "S3b", "during"), "full_report")

    def test_v1_serves_stale_data_when_cdn_ignores_query(self):
        self.assertEqual(self.w("v1", "S4-path", "after_pointer_expiry"), "previous")

    def test_corruption_never_serves_wrong_data_and_next_publish_repairs(self):
        for d in ("slots3", "cas"):
            s5 = self.r[(d, "S5")]
            self.assertIn(s5["read"]["which"], ("previous", "full_report"))
            self.assertEqual(s5["after_next_publish"]["which"], "current")
            if s5["shared_with_previous"]:
                self.assertEqual(s5["read"]["which"], "full_report")   # objeto partilhado: o recuo não ajuda

    def test_github_pages_publishable_layout(self):
        for (d, _), r in self.r.items():
            if d != "v1":
                self.assertEqual(r["unpublishable_files"], [], (d, r["name"]))

    def test_nothing_written_to_docs_or_data(self):
        status = subprocess.run(["git", "status", "--porcelain", "--", "docs", "data"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(status.stdout, "")


if __name__ == "__main__":
    unittest.main()
