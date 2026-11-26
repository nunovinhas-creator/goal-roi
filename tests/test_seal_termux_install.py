"""Instalação Termux da cópia selada (ops/seal/termux): corre num HOME falso com crontab simulado e um remoto Git local.
Verifica: crontab existente intacto (incluindo brain-sync), uma só linha nossa e idempotente, minuto afastado do
brain-sync, recusa de pastas do Brain/Obsidian, ciclo real com selo verificado, registo de falhas, lock contra
sobreposição, desinstalação só da nossa linha."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "ops/seal/termux"
CRONTAB = """#!/bin/sh
f="$HOME/.crontab"
if [ "$1" = "-l" ]; then [ -f "$f" ] || exit 1; cat "$f"; else cat > "$f"; fi
"""


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout


@unittest.skipUnless(shutil.which("flock") and shutil.which("xz"), "flock/xz indisponíveis")
class TermuxInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        (self.home / "brain").mkdir(parents=True)
        (self.home / "brain" / "brain-sync.sh").write_text("#!/bin/sh\necho brain\n")
        (self.home / "Obsidian" / "Vault" / ".obsidian").mkdir(parents=True)
        self.brain_cron = "20 * * * * $HOME/brain/brain-sync.sh >> $HOME/brain.log 2>&1\n@reboot termux-wake-lock\n"
        (self.home / ".crontab").write_text(self.brain_cron)
        stubs = base / "stubs"
        stubs.mkdir()
        (stubs / "crontab").write_text(CRONTAB)
        (stubs / "crontab").chmod(0o755)
        # remoto local com ops/seal/seal.py e data/
        src = base / "src"
        src.mkdir()
        git(src, "init", "-q", "-b", "main")
        git(src, "config", "user.email", "t@example.invalid"); git(src, "config", "user.name", "t")
        (src / "ops/seal").mkdir(parents=True); (src / "data").mkdir(); (src / "scangoal").mkdir()
        shutil.copyfile(ROOT / "ops/seal/seal.py", src / "ops/seal/seal.py")
        (src / "data/messages.jsonl").write_text('{"id": 1}\n')
        (src / "scangoal/prospective_protocol.json").write_text("{}")
        git(src, "add", "-A"); git(src, "commit", "-qm", "c1")
        git(base, "clone", "-q", "--bare", str(src), str(base / "remote.git"))
        git(base / "remote.git", "config", "uploadpack.allowFilter", "true")
        self.src, self.remote = src, base / "remote.git"
        self.env = {**os.environ, "HOME": str(self.home), "PATH": str(stubs) + os.pathsep + os.environ["PATH"], "TERMUX_TEST": "1",
                    "GOAL_ROI_REPO_URL": "file://" + str(self.remote), "PREFIX": "/usr"}
        self.base = self.home / "goal-roi-selos"

    def tearDown(self):
        for root, dirs, _ in os.walk(self.tmp.name):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o755)
        self.tmp.cleanup()

    def sh(self, script, *args, env=None):
        return subprocess.run(["sh", str(SCRIPTS / script), *args], env=env or self.env, capture_output=True, text=True, timeout=300)

    def cron(self):
        return (self.home / ".crontab").read_text()

    def test_install_is_idempotent_and_keeps_existing_crontab(self):
        for _ in range(2):
            r = self.sh("install.sh")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        lines = self.cron().splitlines()
        ours = [l for l in lines if "# goal-roi-selos" in l]
        self.assertEqual(len(ours), 1)
        self.assertEqual("\n".join(l for l in lines if "# goal-roi-selos" not in l) + "\n", self.brain_cron)
        minute = int(ours[0].split()[0])
        self.assertGreaterEqual(min((minute - 20) % 60, (20 - minute) % 60), 20)  # brain-sync ao minuto 20
        self.assertIn(" 5,17 * * * ", ours[0])
        self.assertFalse((self.home / "brain").joinpath("goal-roi-selos").exists())
        self.assertEqual((self.home / "brain/brain-sync.sh").read_text(), "#!/bin/sh\necho brain\n")

    def test_root_brain_sync_does_not_block_dedicated_sibling(self):
        root_script = self.home / "brain-sync.sh"
        root_script.write_text("#!/bin/sh\necho brain root\n")
        r = self.sh("install.sh")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(root_script.read_text(), "#!/bin/sh\necho brain root\n")
        self.assertTrue((self.base / "bin/goal-roi-selo").is_file())

    def test_cron_without_safe_minute_fails_closed(self):
        for cron in ("*/5 * * * * $HOME/brain-sync.sh\n",
                     "0 * * * * $HOME/brain-sync.sh\n20 * * * * $HOME/brain-sync.sh\n40 * * * * $HOME/brain-sync.sh\n"):
            (self.home / ".crontab").write_text(cron)
            r = self.sh("install.sh")
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(self.cron(), cron)

    def test_refuses_symlink_alias_into_protected_brain(self):
        alias = self.home / "alias"
        alias.symlink_to(self.home / "brain", target_is_directory=True)
        r = self.sh("install.sh", env={**self.env, "GOAL_ROI_SEALS_HOME": str(alias / "selos")})
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.home / "brain/selos").exists())
        self.assertEqual(self.cron(), self.brain_cron)

    def test_refuses_base_inside_brain_or_obsidian(self):
        for inside in (self.home / "brain" / "selos", self.home / "Obsidian" / "Vault" / "selos", self.home / "Obsidian"):
            r = self.sh("install.sh", env={**self.env, "GOAL_ROI_SEALS_HOME": str(inside)})
            self.assertNotEqual(r.returncode, 0, inside)
            self.assertFalse(inside.joinpath("bin").exists())
        self.assertEqual(self.cron(), self.brain_cron)

    def test_cycle_seals_verifies_and_logs(self):
        self.assertEqual(self.sh("install.sh").returncode, 0)
        run = subprocess.run([str(self.base / "bin/goal-roi-selo")], env=self.env, capture_output=True, text=True, timeout=300)
        self.assertEqual(run.returncode, 0, (self.base / "logs/selo.log").read_text())
        v = subprocess.run(["python3", str(self.base / "bin/seal.py"), "verify", "--dest", str(self.base / "selos")], capture_output=True, text=True)
        self.assertEqual(v.returncode, 0, v.stdout)
        self.assertEqual(json.loads(v.stdout)["last_commit"], git(self.src, "rev-parse", "HEAD").strip())
        self.assertIn("fim", (self.base / "logs/selo.log").read_text())
        self.assertFalse((self.base / "logs/falhas.jsonl").exists())
        out = self.home / "restaurado"
        r = subprocess.run(["python3", str(self.base / "bin/seal.py"), "restore", "--dest", str(self.base / "selos"), "--out", str(out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((out / "data/messages.jsonl").read_text(), '{"id": 1}\n')
        push = subprocess.run(["git", "-C", str(self.base / "repo"), "push", "origin", "HEAD:main"], capture_output=True, text=True)
        self.assertNotEqual(push.returncode, 0)  # clone só de leitura

    def test_failure_is_recorded_with_step_and_code(self):
        self.assertEqual(self.sh("install.sh").returncode, 0)
        sel = self.base / "selos"
        subprocess.run([str(self.base / "bin/goal-roi-selo")], env=self.env, check=True)
        tar = next(sel.glob("seals/*/sources.tar.xz"))
        tar.chmod(0o644); tar.write_bytes(b"adulterado")
        (self.src / "data/messages.jsonl").write_text('{"id": 1}\n{"id": 2}\n')
        git(self.src, "commit", "-qam", "c2"); git(self.src, "push", "-q", str(self.remote), "main")
        run = subprocess.run([str(self.base / "bin/goal-roi-selo")], env=self.env, capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        rec = [json.loads(l) for l in (self.base / "logs/falhas.jsonl").read_text().splitlines()]
        self.assertEqual(rec[-1]["passo"], "selar")
        self.assertEqual(rec[-1]["codigo"], 2)

    def test_overlapping_run_is_skipped(self):
        self.assertEqual(self.sh("install.sh").returncode, 0)
        holder = subprocess.Popen(["flock", str(self.base / ".lock"), "sleep", "5"])
        try:
            import time; time.sleep(0.5)
            run = subprocess.run([str(self.base / "bin/goal-roi-selo")], env=self.env, capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0)
            self.assertIn("ainda a correr", (self.base / "logs/selo.log").read_text())
            self.assertFalse(list((self.base / "selos").glob("seals/*")))
        finally:
            holder.kill()

    def test_uninstall_removes_only_our_line(self):
        self.assertEqual(self.sh("install.sh").returncode, 0)
        r = self.sh("uninstall.sh")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.cron().strip(), self.brain_cron.strip())
        self.assertTrue((self.base / "selos").exists())
        self.assertEqual(self.sh("uninstall.sh", "--purge").returncode, 0)
        self.assertFalse(self.base.exists())
        self.assertTrue((self.home / "brain/brain-sync.sh").exists())


if __name__ == "__main__":
    unittest.main()
