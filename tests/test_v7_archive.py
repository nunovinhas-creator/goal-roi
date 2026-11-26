import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "audits/v7/storage"))
import archive_v7 as A  # noqa: E402
import restore_drill as R  # noqa: E402
from segment_store import IntegrityError, SegmentStore  # noqa: E402


def make_repo(base, revisions=6):
    repo = Path(base) / "repo"
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "data").mkdir()
    (repo / "docs").mkdir()
    lines = []
    for i in range(revisions):
        lines += [json.dumps({"message_id": 100 * i + k, "sha256": "%064x" % (100 * i + k), "text": "x" * 300})
                  for k in range(80)]
        (repo / "data/messages.jsonl").write_text("\n".join(lines) + "\n")
        (repo / "data/hypotheses.json").write_text(json.dumps({"rev": i, "rows": list(range(4000))}, indent=1))
        (repo / "data/prospective_report.json").write_text(json.dumps({"ledger_entries": i}))
        (repo / "docs/report.json").write_text(json.dumps({"generated_at_utc": "2026-11-26T%02d:00:00+00:00" % i,
                                                           "blob": "y" * 50000}, indent=1))
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "r%d" % i], check=True, env=env)
    return repo


class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = make_repo(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_replay_verify_index_retention_export(self):
        with tempfile.TemporaryDirectory() as t:
            out = A.run(self.repo, 6, Path(t) / "s", keep_views=2)
        self.assertEqual(out["revisions_verified_byte_identical"], 12)
        self.assertTrue(out["index_rebuilt_identical"])
        self.assertEqual(out["index_messages"], {"ids": 480, "versions": 480, "journal_prefix_rewrites": 0})
        self.assertEqual(out["retention_views"]["kept"], 2)
        self.assertEqual(out["retention_views"]["kept_verified"], 2)
        self.assertEqual(out["sources_verified_after_retention"], 6)
        self.assertTrue(out["export_last_revision_identical_to_git"])

    def test_cdc_blocks_are_reused_when_file_only_grows(self):
        data = b"".join(b'{"i":%d,"pad":"%s"}\n' % (i, b"z" * 200) for i in range(5000))
        a, b = list(A.cdc(data)), list(A.cdc(data + b'{"i":-1}\n'))
        self.assertEqual(b"".join(a), data)
        self.assertGreaterEqual(len(set(map(A.sha, a)) & set(map(A.sha, b))), len(a) - 1)

    def test_restore_drill_scenarios(self):
        res = R.drill(self.repo, 6)
        self.assertTrue(res["perda_total_git"]["identico_ao_git"])
        self.assertEqual(res["objeto_corrompido"], {"detetado": True, "reparado": True})
        self.assertEqual(res["head_corrompido"]["recuo"], "PREVIOUS")
        self.assertTrue(res["espelho_incompleto"]["recusado"])
        self.assertFalse(res["espelho_incompleto"]["destino_criado"])
        self.assertTrue(res["espelho_atrasado"]["identico_a_revisao_declarada"])
        self.assertGreater(res["espelho_atrasado"]["revisoes_em_falta"], 0)
        self.assertTrue(res["restauro_interrompido"]["destino_ausente_apos_interrupcao"])
        self.assertEqual(res["restauro_interrompido"]["temporarios_restantes"], [])
        self.assertTrue(res["restauro_interrompido"]["repeticao_identica"])

    def test_corrupted_meta_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            R.build_archive(self.repo, A.commits_for(A.Git(self.repo), 3), Path(t) / "a")
            store = SegmentStore(Path(t) / "a" / "sources")
            _, _, manifest, _ = store.active()
            meta = next(iter(manifest["files"].values()))
            R.flip(store.objects / (meta["sha256"] + ".gz"))
            with self.assertRaises(IntegrityError):
                store.load_release(store.pointer())
            self.assertNotEqual(R.missing_objects(store), [])


if __name__ == "__main__":
    unittest.main()
