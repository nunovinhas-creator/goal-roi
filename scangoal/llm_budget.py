"""Durable quota reservations in a dedicated Git ref, independent of the collector checkout."""

from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4


REF = "refs/heads/ops/goal-roi-workers-ai-budget-v1"
STATE_FILE = "budget.json"
MAX_TENTHS = 80_000
OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


class BudgetUnavailable(RuntimeError):
    """The shared balance or successful publication could not be proved."""


class GitBudget:
    def __init__(self, account, directory=None, remote="origin"):
        self.account = hashlib.sha256(account.encode()).hexdigest()
        self.directory = Path.cwd() if directory is None else directory
        self.remote = remote

    def git(self, *args, data=None, check=True):
        try:
            result = subprocess.run(
                ["git", "-C", str(self.directory), "-c", "user.name=goal-roi quota",
                 "-c", "user.email=actions@users.noreply.github.com", *args],
                input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=dict(os.environ, GIT_TERMINAL_PROMPT="0"), timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BudgetUnavailable("Git quota transport unavailable") from exc
        if check and result.returncode:
            raise BudgetUnavailable("Git quota operation failed")
        return result

    def tip(self):
        advertised = self.git("ls-remote", "--refs", self.remote, REF).stdout.decode("ascii").split()
        if not advertised:
            return None
        if len(advertised) != 2 or advertised[1] != REF or not OID.fullmatch(advertised[0]):
            raise BudgetUnavailable("Invalid quota ref")
        return advertised[0]

    def read(self, tip):
        self.git("fetch", "--no-tags", "--quiet", self.remote, tip)
        raw = self.git("show", tip + ":" + STATE_FILE).stdout
        if len(raw) > 1_000_000:
            raise BudgetUnavailable("Quota journal too large")
        try:
            state = json.loads(raw)
            day = date.fromisoformat(state["day"])
            if (type(state["schema"]) is not int or state["schema"] != 1 or state["account"] != self.account
                    or type(state["sealed"]) is not bool or not isinstance(state["holds"], dict)):
                raise ValueError("Invalid journal")
            holds = state["holds"]
            if (any(not re.fullmatch(r"[0-9a-f]{32}", key) or type(value) is not int or value <= 0
                    for key, value in holds.items())
                    or sum(holds.values()) > MAX_TENTHS or (state["sealed"] and holds)):
                raise ValueError("Invalid quota holds")
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise BudgetUnavailable("Invalid quota journal") from exc
        return day, state

    def publish(self, parent, state):
        raw = (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode()
        blob = self.git("hash-object", "-w", "--stdin", data=raw).stdout.strip()
        tree_line = b"100644 blob " + blob + b"\t" + STATE_FILE.encode() + b"\n"
        tree = self.git("mktree", data=tree_line).stdout.decode().strip()
        args = ["commit-tree", tree, "-m", "quota: reserve Workers AI capacity [skip ci]"]
        if parent:
            args.extend(["-p", parent])
        commit = self.git(*args).stdout.decode().strip()
        # The new commit is always a child of the exact advertised tip. The lease is a CAS,
        # including when the ref is absent. It never replaces a concurrent writer's commit.
        result = self.git("push", "--porcelain", "--force-with-lease=" + REF + ":" + (parent or ""),
                          self.remote, commit + ":" + REF, check=False)
        if result.returncode == 0:
            return True
        actual = self.tip()
        if actual is None or actual in (parent, commit):
            raise BudgetUnavailable("Quota publication could not be proved")
        return False

    def reserve(self, day, cost, limit):
        """Publish a new charge before returning permission to send one request."""
        if not 0 < limit <= MAX_TENTHS or not 0 < cost <= limit:
            return False
        for _ in range(3):
            parent = self.tip()
            state = {"schema": 1, "account": self.account, "day": day.isoformat(),
                     "sealed": False, "holds": {}}
            if parent is None:
                # No evidence of earlier usage today. Seal the day even on first install or
                # if an operator has deleted the ref. A future UTC day can start at zero.
                state["sealed"] = True
                if self.publish(parent, state):
                    return False
                continue
            stored_day, previous = self.read(parent)
            if stored_day > day:
                raise BudgetUnavailable("Quota clock precedes the journal")
            if stored_day == day:
                state = previous
            if state["sealed"] or sum(state["holds"].values()) + cost > limit:
                return False
            state["holds"][uuid4().hex] = cost
            if self.publish(parent, state):
                return True
        raise BudgetUnavailable("Quota write conflicts")
