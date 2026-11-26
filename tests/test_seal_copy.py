"""Cópia selada a cada 12 h (ops/seal/seal.py): selagem, cadeia, adulteração, interrupção, poda e restauração."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ops" / "seal"))
import seal as S  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class SealTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo, self.dest = base / "repo", base / "copia"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.invalid"); git(self.repo, "config", "user.name", "t")
        (self.repo / "data").mkdir(); (self.repo / "scangoal").mkdir()
        (self.repo / "scangoal/prospective_protocol.json").write_text('{"frozen": true}\n')
        self.commits = [self.commit(i) for i in range(3)]

    def tearDown(self):
        for root, dirs, files in os.walk(self.tmp.name):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o755)
        self.tmp.cleanup()

    def commit(self, i):
        (self.repo / "data/messages.jsonl").write_text("".join('{"id": %d}\n' % k for k in range(i + 1)))
        (self.repo / "data/report.json").write_text('{"view": %d}' % i)  # vista: fica fora do selo
        (self.repo / "data/state.json").write_text('{"run": %d}' % i)
        git(self.repo, "add", "-A"); git(self.repo, "commit", "-qm", "c%d" % i)
        return git(self.repo, "rev-parse", "HEAD")

    def seal_all(self):
        return [S.seal(self.repo, self.dest, c) for c in self.commits]

    def test_seal_chain_and_unchanged(self):
        res = self.seal_all()
        self.assertEqual([r["seq"] for r in res], [1, 2, 3])
        self.assertEqual(S.seal(self.repo, self.dest, self.commits[-1])["status"], "unchanged")
        v = S.verify(self.dest)
        self.assertEqual((v["seals"], v["last_commit"]), (3, self.commits[-1]))
        manifest = json.loads((S.list_seals(self.dest)[0] / "MANIFEST.json").read_text())
        self.assertNotIn("data/report.json", manifest["files"])
        self.assertIn("scangoal/prospective_protocol.json", manifest["files"])

    def test_seal_reads_commit_not_worktree(self):
        (self.repo / "data/state.json").write_text("não commitado")
        S.seal(self.repo, self.dest, self.commits[-1])
        out = Path(self.tmp.name) / "r"
        S.restore(self.dest, out)
        self.assertEqual((out / "data/state.json").read_text(), '{"run": 2}')

    def test_restore_is_byte_identical_to_git_tree(self):
        self.seal_all()
        for seq, commit in enumerate(self.commits, 1):
            out = Path(self.tmp.name) / ("r%d" % seq)
            S.restore(self.dest, out, seq)
            for name in ("data/messages.jsonl", "data/state.json", "scangoal/prospective_protocol.json"):
                self.assertEqual((out / name).read_bytes(), subprocess.run(["git", "-C", str(self.repo), "show", "%s:%s" % (commit, name)],
                                                                            capture_output=True, check=True).stdout)
            self.assertFalse((out / "data/report.json").exists())

    def test_tampered_tar_is_detected(self):
        self.seal_all()
        p = S.list_seals(self.dest)[1] / "sources.tar.xz"
        os.chmod(p, 0o644); data = bytearray(p.read_bytes()); data[-20] ^= 1; p.write_bytes(bytes(data))
        with self.assertRaises(S.SealError):
            S.verify(self.dest)
        with self.assertRaises(S.SealError):
            S.restore(self.dest, Path(self.tmp.name) / "r")
        self.assertFalse((Path(self.tmp.name) / "r").exists())

    def test_rewritten_manifest_breaks_chain(self):
        self.seal_all()
        d = S.list_seals(self.dest)[1]
        for f in ("MANIFEST.json", "SEAL"):
            os.chmod(d / f, 0o644)
        m = json.loads((d / "MANIFEST.json").read_text()); m["commit_utc"] = "2000-01-01T00:00:00+00:00"
        body = json.dumps(m, sort_keys=True, ensure_ascii=False, indent=1).encode()
        (d / "MANIFEST.json").write_bytes(body); (d / "SEAL").write_text(S.sha(body) + "\n")  # selo coerente, cadeia não
        with self.assertRaisesRegex(S.SealError, "cadeia quebrada"):
            S.verify(self.dest)

    def test_missing_seal_is_detected(self):
        self.seal_all()
        d = S.list_seals(self.dest)[1]
        os.chmod(d, 0o755)
        for f in d.iterdir():
            os.chmod(f, 0o644)
        import shutil; shutil.rmtree(d)
        with self.assertRaisesRegex(S.SealError, "em falta"):
            S.verify(self.dest)

    def test_interrupted_seal_leaves_no_partial_seal(self):
        S.seal(self.repo, self.dest, self.commits[0])
        with self.assertRaises(S.SealError):
            S.seal(self.repo, self.dest, self.commits[1], fail_before_rename=True)
        self.assertEqual(len(S.list_seals(self.dest)), 1)
        self.assertEqual([p.name for p in S.seals_dir(self.dest).iterdir() if p.name.startswith(".tmp")], [])
        self.assertEqual(S.seal(self.repo, self.dest, self.commits[1])["seq"], 2)
        S.verify(self.dest)

    def test_prune_keeps_chain_verifiable(self):
        self.seal_all()
        later = datetime.now(timezone.utc) + timedelta(days=60)
        res = S.prune(self.dest, keep_days=30, now=later)  # mesma semana ISO: fica só o último
        self.assertEqual(res["kept"], 1)
        self.assertEqual(S.verify(self.dest)["pruned"], 2)
        self.commits.append(self.commit(3))
        self.assertEqual(S.seal(self.repo, self.dest, self.commits[-1])["seq"], 4)
        S.verify(self.dest)

    def test_refuses_commit_that_is_not_a_descendant(self):
        S.seal(self.repo, self.dest, self.commits[-1])
        with self.assertRaisesRegex(S.SealError, "não descende"):
            S.seal(self.repo, self.dest, self.commits[0])
        self.assertEqual(len(S.list_seals(self.dest)), 1)

    def test_stale_copy_fails_max_age(self):
        self.seal_all()
        with self.assertRaisesRegex(S.SealError, "último selo"):
            S.verify(self.dest, max_age_h=13, now=datetime.now(timezone.utc) + timedelta(hours=14))

    def test_cli_exit_codes(self):
        cmd = [sys.executable, str(Path(S.__file__)), "verify", "--dest", str(self.dest)]
        self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 2)
        S.seal(self.repo, self.dest, self.commits[0])
        self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
