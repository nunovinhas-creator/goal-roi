"""Exercise the live LLM entry point with a bare Git remote and a fake HTTP transport."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import chdir
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Barrier, local
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from scangoal import llm, report
from scangoal.llm_budget import MAX_TENTHS, REF, STATE_FILE, BudgetUnavailable, GitBudget


FIRST_DAY = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
NEXT_DAY = FIRST_DAY + timedelta(days=1)
ACCOUNT = "1" * 32
OTHER_ACCOUNT = "2" * 32
ENV = {
    "CLOUDFLARE_ACCOUNT_ID": ACCOUNT,
    "MASTER_BRAIN_LLM_API_KEY": "offline-fixture",
    "GOAL_ROI_LLM_FREE_CONFIRMED": "true",
    "GITHUB_REF_NAME": "main",
    "LLM_BUDGET_MODE": "git",
}


class WorkersAIBudgetIntegration(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.remote = self.root / "remote.git"
        self.enterContext(patch.dict(os.environ, {"GIT_ALLOW_PROTOCOL": "file"}))
        self.git(self.root, "init", "--bare", "-q", self.remote)
        self.runners = [self.root / name for name in ("runner-a", "runner-b")]
        for runner in self.runners:
            self.git(self.root, "init", "-q", runner)
            self.git(runner, "remote", "add", "origin", self.remote)
        # Use separate data directories so the process-local file lock cannot serialize the test.
        self.runner_state = local()
        self.enterContext(patch.object(llm, "data_dir", side_effect=lambda: self.runner_state.directory))
        self.enterContext(patch.object(llm, "opportunity_digest", return_value=None))
        self.enterContext(patch.object(llm, "MODELS", list(llm.PRICES)[:1]))
        stamp = FIRST_DAY.isoformat()
        self.enterContext(patch.object(report, "build", return_value={"brain": {"top": []}}))
        self.enterContext(patch.dict(report.LAST, {"research_signals": [{
            "match_id": "offline-match", "type": "OPEN_GAME", "league": "Fixture league",
            "odds": 2.2, "minute": 48, "date_utc": stamp, "signal_seen_utc": stamp,
            "market": "Asian Total", "selection": "Over 2.5", "score_at_signal": [1, 0],
        }]}))
        self.expected = NEXT_DAY
        self.requests = []
        self.transport = self.enterContext(patch.object(llm, "urlopen", side_effect=self.send))

    @staticmethod
    def git(directory, *args, data=None, check=True):
        return subprocess.run(
            ["git", "-C", str(directory), "-c", "user.name=Fixture", "-c",
             "user.email=fixture@example.invalid", *args], input=data, capture_output=True,
            check=check, timeout=12,
        )

    def run_llm(self, index=0, *, at=NEXT_DAY, env=ENV, budget=None):
        runner = self.runners[index]
        self.runner_state.directory = runner / "data"
        return llm.run(now=at, env=env, budget=budget or GitBudget(env["CLOUDFLARE_ACCOUNT_ID"], runner))

    def tip(self, ref=REF):
        return self.git(self.remote, "rev-parse", "--verify", "-q", ref, check=False).stdout.decode().strip()

    def read_remote(self):
        return json.loads(self.git(self.remote, "show", f"{REF}:{STATE_FILE}").stdout)

    def state(self, *, day=FIRST_DAY, sealed=True, holds=None, account=ACCOUNT):
        return {"schema": 1, "account": hashlib.sha256(account.encode()).hexdigest(),
                "day": day.date().isoformat(), "sealed": sealed, "holds": holds or {}}

    def install_fixture(self, state, ref=REF):
        raw = state if isinstance(state, bytes) else json.dumps(state).encode()
        blob = self.git(self.remote, "hash-object", "-w", "--stdin", data=raw).stdout.strip()
        tree = self.git(self.remote, "mktree", data=b"100644 blob " + blob + b"\tbudget.json\n").stdout.strip()
        parent = self.tip(ref)
        args = ["-p", parent] if parent else []
        sha = self.git(self.remote, "commit-tree", tree.decode(), "-m", "Offline fixture", *args)
        self.git(self.remote, "update-ref", ref, sha.stdout.decode().strip())

    def send(self, request, *, timeout):
        # Read directly from the remote, through Git plumbing rather than GitBudget or local telemetry.
        self.assertEqual(request.full_url, f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/v1/chat/completions")
        remote = self.read_remote()
        self.assertEqual(remote["day"], self.expected.date().isoformat())
        self.assertEqual(remote["account"], self.state()["account"])
        self.assertFalse(remote["sealed"])
        self.assertGreater(len(remote["holds"]), len(self.requests))
        self.requests.append((self.tip(), remote["holds"].copy(), json.loads(request.data)["model"]))
        response = {"choices": [{"message": {"content": '{"hypotheses":[]}'}}]}
        return io.BytesIO(json.dumps(response).encode())

    def synchronize_pushes(self, budgets):
        barrier = Barrier(len(budgets))

        def synchronize(git):
            def synchronized(*args, **kwargs):
                if args[0] == "push":
                    barrier.wait(timeout=8)
                return git(*args, **kwargs)
            return synchronized

        for budget in budgets:
            self.enterContext(patch.object(budget, "git", side_effect=synchronize(budget.git)))

    def run_initializer(self, account=ACCOUNT):
        module_root = str(Path(llm.__file__).resolve().parents[1])
        return subprocess.run(
            [sys.executable, "-m", "scripts.initialize_workers_ai_budget"], cwd=self.runners[0],
            env=dict(os.environ, PYTHONPATH=module_root, CLOUDFLARE_ACCOUNT_ID=account),
            capture_output=True, text=True, timeout=20,
        )

    def test_initializer_creates_and_reports_only_redacted_state(self):
        main = "refs/heads/main"
        self.install_fixture({"production": True}, main)
        main_tip = self.tip(main)
        day_before = datetime.now(timezone.utc).date()
        completed = self.run_initializer()
        day_after = datetime.now(timezone.utc).date()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        summary = json.loads(completed.stdout)
        self.assertEqual(summary, {"ref": REF, "sha": self.tip(), "schema": 1,
                                   "day": self.read_remote()["day"], "sealed": True, "hold_count": 0})
        self.assertIn(summary["day"], {day_before.isoformat(), day_after.isoformat()})
        self.assertEqual(self.read_remote(), dict(self.state(), day=summary["day"]))
        self.assertNotIn(ACCOUNT, completed.stdout + completed.stderr)
        self.assertNotIn(self.state()["account"], completed.stdout + completed.stderr)
        self.assertEqual(self.tip(main), main_tip)
        self.assertEqual(self.git(self.remote, "rev-list", "--count", REF).stdout.strip(), b"1")
        self.transport.assert_not_called()

    def test_initializer_does_not_overwrite_existing_active_or_corrupt_journal(self):
        holds = {"a" * 32: 500}
        original = self.state(day=NEXT_DAY, sealed=False, holds=holds)
        self.install_fixture(original)
        budget = GitBudget(ACCOUNT, self.runners[0])
        before = self.tip()
        self.assertEqual(budget.initialize(FIRST_DAY.date()), (before, original))
        self.assertEqual(self.tip(), before)
        self.install_fixture(b"invalid journal")
        before = self.tip()
        with self.assertRaises(BudgetUnavailable):
            budget.initialize(FIRST_DAY.date())
        self.assertEqual(self.tip(), before)
        self.transport.assert_not_called()

    def test_concurrent_initializers_keep_a_single_root(self):
        budgets = [GitBudget(ACCOUNT, runner) for runner in self.runners]
        self.synchronize_pushes(budgets)

        def initialize(index):
            # Different UTC dates guarantee there are two distinct candidate commits.
            return budgets[index].initialize(FIRST_DAY.date() + timedelta(days=index))

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(initialize, range(2)))
        self.assertEqual(results, [(self.tip(), self.read_remote())] * 2)
        self.assertEqual(self.git(self.remote, "rev-list", "--count", REF).stdout.strip(), b"1")
        self.assertTrue(self.read_remote()["sealed"])
        self.assertEqual(self.read_remote()["holds"], {})
        self.transport.assert_not_called()

    def test_initializer_rejects_missing_account_without_creating_any_ref(self):
        completed = self.run_initializer(account="")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("missing or invalid", completed.stderr)
        self.assertEqual(self.tip(), "")
        self.transport.assert_not_called()

    def test_missing_journal_seals_without_request(self):
        self.assertEqual(self.tip(), "")
        self.run_llm(at=FIRST_DAY)
        self.transport.assert_not_called()
        self.assertEqual(self.read_remote(), self.state())
        tree = self.git(self.remote, "ls-tree", "--name-only", REF).stdout.decode().splitlines()
        self.assertEqual(tree, [STATE_FILE])

    def test_existing_sealed_day_cannot_be_changed_or_spent(self):
        self.install_fixture(self.state())
        before = self.tip()
        self.run_llm(at=FIRST_DAY)
        self.run_llm(1, at=FIRST_DAY + timedelta(hours=2))
        self.transport.assert_not_called()
        self.assertEqual(self.tip(), before)
        self.assertEqual(self.read_remote(), self.state())

    def test_first_request_next_day_observes_remote_reservation(self):
        self.install_fixture(self.state())
        before = self.tip()
        self.runner_state.directory = self.runners[0] / "data"
        with chdir(self.runners[0]):
            self.assertEqual(llm.run(now=NEXT_DAY, env=ENV)["last_outcome"], "ok")
        self.transport.assert_called_once()
        self.assertEqual(len(self.read_remote()["holds"]), 1)
        self.assertEqual(self.git(self.remote, "rev-parse", REF + "^").stdout.decode().strip(), before)

    def test_concurrent_runners_cannot_both_use_last_capacity(self):
        self.install_fixture(self.state())
        budgets = [GitBudget(ACCOUNT, runner) for runner in self.runners]
        self.synchronize_pushes(budgets)

        def collect(index):
            return self.run_llm(index, budget=budgets[index])["last_outcome"]

        with patch.object(llm, "DAILY_NEURONS", 100), patch.object(llm, "reserve", return_value=100):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(collect, range(2)))
        self.assertEqual(outcomes.count("ok"), 1)
        self.transport.assert_called_once()
        self.assertEqual(list(self.read_remote()["holds"].values()), [1000])

    def test_new_process_cannot_reuse_a_hold_from_an_exited_runner(self):
        self.install_fixture(self.state())
        module_root = str(Path(llm.__file__).resolve().parents[1])
        reservation = subprocess.run(
            [sys.executable, "-c", """
from datetime import date
import os
import sys
from scangoal.llm_budget import GitBudget, MAX_TENTHS
held = GitBudget(sys.argv[1]).reserve(date.fromisoformat(sys.argv[2]), MAX_TENTHS, MAX_TENTHS)
os._exit(27 if held else 28)
""", ACCOUNT, NEXT_DAY.date().isoformat()], cwd=self.runners[0],
            env=dict(os.environ, PYTHONPATH=module_root), capture_output=True, timeout=20,
        )
        self.assertEqual(reservation.returncode, 27, reservation.stderr.decode())
        before = self.tip()
        self.run_llm(1)
        self.transport.assert_not_called()
        self.assertEqual(self.tip(), before)
        self.assertEqual(list(self.read_remote()["holds"].values()), [MAX_TENTHS])

    def test_failed_push_never_calls_transport(self):
        self.install_fixture(self.state())
        before = self.tip()
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        self.assertEqual(self.run_llm()["last_outcome"], "budget_unverified")
        self.transport.assert_not_called()
        self.assertEqual(self.tip(), before)

    def test_lost_push_confirmation_keeps_charge_and_blocks_request(self):
        self.install_fixture(self.state())
        before = self.tip()
        budget = GitBudget(ACCOUNT, self.runners[0])
        original_git = budget.git

        def lose_confirmation(*args, **kwargs):
            result = original_git(*args, **kwargs)
            if args[0] == "push" and result.returncode == 0:
                return subprocess.CompletedProcess(result.args, 1, b"", b"")
            return result

        with patch.object(budget, "git", side_effect=lose_confirmation):
            self.assertEqual(self.run_llm(budget=budget)["last_outcome"], "budget_unverified")
        self.transport.assert_not_called()
        self.assertNotEqual(self.tip(), before)
        self.assertEqual(len(self.read_remote()["holds"]), 1)

    def test_lost_confirmation_is_not_a_conflict_if_another_writer_advances(self):
        self.install_fixture(self.state())
        budget = GitBudget(ACCOUNT, self.runners[0])
        next_writer = GitBudget(ACCOUNT, self.runners[1])
        original_git = budget.git
        pushed = []

        def lose_confirmation_after_another_writer(*args, **kwargs):
            result = original_git(*args, **kwargs)
            if args[0] == "push" and not pushed and result.returncode == 0:
                pushed.append(self.tip())
                self.assertTrue(next_writer.reserve(NEXT_DAY.date(), 1, MAX_TENTHS))
                return subprocess.CompletedProcess(result.args, 1, b"", b"")
            return result

        with patch.object(budget, "git", side_effect=lose_confirmation_after_another_writer):
            self.assertEqual(self.run_llm(budget=budget)["last_outcome"], "budget_unverified")
        self.transport.assert_not_called()
        self.assertEqual(len(self.read_remote()["holds"]), 2)

    def test_unavailable_git_blocks_transport(self):
        budget = GitBudget(ACCOUNT, self.runners[0], remote=str(self.root / "unavailable.git"))
        self.assertEqual(self.run_llm(budget=budget)["last_outcome"], "budget_unverified")
        self.transport.assert_not_called()
        self.assertEqual(self.tip(), "")

    def test_fetch_timeout_after_advertisement_blocks_transport(self):
        self.install_fixture(self.state())
        before = self.tip()
        original_run = subprocess.run

        def timeout_on_fetch(command, **kwargs):
            if command[0] == "git" and "fetch" in command:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return original_run(command, **kwargs)

        with patch.object(subprocess, "run", side_effect=timeout_on_fetch):
            self.assertEqual(self.run_llm()["last_outcome"], "budget_unverified")
        self.transport.assert_not_called()
        self.assertEqual(self.tip(), before)

    def test_corrupt_journal_blocks_transport_and_is_not_overwritten(self):
        self.install_fixture(b'{"schema":1,"day":')
        before = self.tip()
        self.assertEqual(self.run_llm()["last_outcome"], "budget_unverified")
        self.transport.assert_not_called()
        self.assertEqual(self.tip(), before)

    def test_duplicate_balance_key_is_invalid(self):
        existing = self.state(day=NEXT_DAY, sealed=False, holds={"a" * 32: MAX_TENTHS})
        duplicate = json.dumps(existing)[:-1] + ', "holds": {}}'
        self.install_fixture(duplicate.encode())
        before = self.tip()
        self.assertEqual(self.run_llm()["last_outcome"], "budget_unverified")
        self.transport.assert_not_called()
        self.assertEqual(self.tip(), before)

    def test_insufficient_balance_blocks_transport(self):
        self.install_fixture(self.state(day=NEXT_DAY, sealed=False, holds={"3" * 32: MAX_TENTHS - 1}))
        before = self.tip()
        self.run_llm()
        self.transport.assert_not_called()
        self.assertEqual(self.tip(), before)

    def test_rollover_uses_utc_and_does_not_send_in_guard_window(self):
        self.install_fixture(self.state(day=FIRST_DAY, sealed=False, holds={"a" * 32: MAX_TENTHS}))
        before = self.tip()
        late_utc = FIRST_DAY.replace(hour=23, minute=58)
        local_clock = timezone(timedelta(hours=2))
        self.assertEqual(self.run_llm(at=late_utc.astimezone(local_clock))["last_outcome"], "skipped_utc_boundary")
        self.transport.assert_not_called()
        self.assertEqual(self.tip(), before)
        next_utc = NEXT_DAY.replace(hour=0, minute=1)
        self.assertEqual(self.run_llm(1, at=next_utc.astimezone(local_clock))["last_outcome"], "ok")
        self.transport.assert_called_once()
        self.assertNotIn("a" * 32, self.read_remote()["holds"])

    def test_crossing_midnight_after_reservation_keeps_hold_and_skips_request(self):
        self.install_fixture(self.state(day=FIRST_DAY - timedelta(days=1)))
        before = FIRST_DAY.replace(hour=23, minute=54)
        after = NEXT_DAY.replace(hour=0, minute=0, second=1)

        class Clock(datetime):
            times = iter((before, before, after, after))

            @classmethod
            def now(cls, _timezone):
                return next(cls.times)

        with patch.object(llm, "datetime", Clock):
            self.assertEqual(self.run_llm(at=None)["last_outcome"], "skipped_utc_boundary")
        self.transport.assert_not_called()
        self.assertEqual(self.read_remote()["day"], FIRST_DAY.date().isoformat())
        self.assertEqual(len(self.read_remote()["holds"]), 1)

    def test_other_account_is_denied_by_the_journal(self):
        self.install_fixture(self.state())
        before = self.tip()
        state = self.run_llm(env=dict(ENV, CLOUDFLARE_ACCOUNT_ID=OTHER_ACCOUNT))
        self.assertEqual(state["last_outcome"], "budget_unverified")
        self.transport.assert_not_called()
        self.assertEqual(self.tip(), before)

    def test_live_entry_point_rejects_a_budget_for_another_account(self):
        self.install_fixture(self.state(account=OTHER_ACCOUNT))
        before = self.tip()
        budget = GitBudget(OTHER_ACCOUNT, self.runners[0])
        self.assertIsNone(self.run_llm(budget=budget))
        self.transport.assert_not_called()
        self.assertEqual(self.tip(), before)

    def test_no_request_with_disabled_gate_or_without_published_budget(self):
        self.install_fixture(self.state())
        before = self.tip()
        env = dict(ENV, GOAL_ROI_LLM_FREE_CONFIRMED="false")
        self.assertIsNone(llm.run(now=NEXT_DAY, env=env))
        self.assertIsNone(llm.run(now=NEXT_DAY, env=dict(ENV, LLM_BUDGET_MODE="off")))
        with self.assertRaisesRegex(llm.LLMError, "unverified remote reservation"):
            llm.request(ACCOUNT, ENV["MASTER_BRAIN_LLM_API_KEY"], next(iter(llm.PRICES)), "fixture")
        self.transport.assert_not_called()
        self.assertEqual(self.tip(), before)

    def test_cloudflare_fallback_requires_a_second_published_hold(self):
        self.install_fixture(self.state())

        def first_http_fails(*args, **kwargs):
            response = self.send(*args, **kwargs)
            if len(self.requests) == 1:
                response.close()
                raise HTTPError(None, 503, "Offline fixture", {}, None)
            return response

        with patch.object(llm, "MODELS", list(llm.PRICES)), patch.object(self.transport, "side_effect", first_http_fails):
            self.assertEqual(self.run_llm()["last_outcome"], "ok")
        self.assertEqual(self.transport.call_count, 2)
        first, second = self.requests
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(first[1], {key: second[1][key] for key in first[1]})
        self.assertEqual(len(second[1]), 2)
        self.assertNotEqual(first[2], second[2])
