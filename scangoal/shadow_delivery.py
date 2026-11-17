"""Optional, offline-testable SHADOW signal-delivery bridge.

This module has NO Telegram client, no scheduler and no automatic entry point.
A separate, expressly authorized source adapter must supply message rows later.
Git publication is distinct from SHADOW scientific admission and certification.
"""
import fcntl
import hashlib
import json
import math
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path

from . import shadow_ops
from .parser import parse

BRANCH = "shadow/signal-delivery"
EVENTS = Path("research/shadow-delivery/events.jsonl")
RECEIPTS = Path("research/shadow-delivery/receipts.jsonl")
UTC = timezone.utc
MAX_TEXT = 20_000
MAX_BYTES = 25_000_000


class DeliveryError(RuntimeError):
    pass


def _canon(item):
    return json.dumps(item, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(item):
    return hashlib.sha256(_canon(item)).hexdigest()


def _utc(value):
    if isinstance(value, datetime):
        d = value
    elif isinstance(value, str):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise DeliveryError("invalid UTC timestamp") from None
    else:
        raise DeliveryError("missing UTC timestamp")
    if d.tzinfo is None:
        raise DeliveryError("timestamp is not timezone aware")
    return d.astimezone(UTC)


def _read_lines(path, max_bytes=MAX_BYTES):
    if not path.exists():
        return []
    if path.stat().st_size > max_bytes:
        raise DeliveryError("delivery journal size exceeds budget")
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        raise DeliveryError("incomplete delivery journal; do not reinterpret a partial record")
    try:
        items = [json.loads(line) for line in data.splitlines() if line]
    except (ValueError, UnicodeError):
        raise DeliveryError("invalid delivery journal") from None
    if not all(isinstance(item, dict) for item in items):
        raise DeliveryError("invalid journal record")
    return items


def _validate_events(events):
    previous = "0"*64
    seen = set()
    for seq, event in enumerate(events, 1):
        body = {k: v for k, v in event.items() if k != "hash"}
        if event.get("sequence") != seq or event.get("previous_hash") != previous:
            raise DeliveryError("event journal chain broken")
        if event.get("hash") != _hash(body):
            raise DeliveryError("event hash mismatch")
        key = (event.get("message_id"), event.get("source_sha256"))
        if key in seen:
            raise DeliveryError("duplicate event version in journal")
        seen.add(key)
        previous = event["hash"]
    return previous


def _validate_receipts(receipts, events):
    seen = set()
    previous = "0"*64
    for seq, entry in enumerate(receipts, 1):
        body = {k: v for k, v in entry.items() if k != "hash"}
        if entry.get("sequence") != seq or entry.get("previous_hash") != previous:
            raise DeliveryError("receipt chain broken")
        if entry.get("hash") != _hash(body):
            raise DeliveryError("receipt hash mismatch")
        covered = entry.get("covers_sequence")
        if type(covered) is not int or not 1 <= covered <= len(events):
            raise DeliveryError("receipt covers nonexistent event")
        if entry.get("covers_hash") != events[covered-1]["hash"]:
            raise DeliveryError("receipt and event journal mismatch")
        if covered in seen or (seen and covered <= max(seen)):
            raise DeliveryError("duplicate or backwards receipt")
        _utc(entry.get("verified_utc"))
        seen.add(covered)
        previous = entry["hash"]
    return previous


def _atomic(path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".delivery-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            for obj in items:
                f.write(_canon(obj)+b"\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(filename, path)
        d = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(d)
        finally:
            os.close(d)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def _git(repo, *args):
    try:
        return shadow_ops.git(repo, *args)
    except (shadow_ops.OpsError, shadow_ops.DeadlineExceeded, OSError) as exc:
        raise DeliveryError("isolated Git operation failed") from exc


class DeliveryPublisher:
    """Dedicated checkout; no implicit publish, worker, activation or secrets."""

    def __init__(self, repo, clock=None):
        self.repo = Path(repo).resolve()
        self.clock = clock or (lambda: datetime.now(UTC))
        if _git(self.repo, "branch", "--show-current") != BRANCH:
            raise DeliveryError("a dedicated shadow/signal-delivery checkout is required")
        if _git(self.repo, "rev-parse", "--is-shallow-repository") != "false":
            raise DeliveryError("full Git history required for durable delivery")
        self.lockfile = Path(_git(self.repo, "rev-parse", "--absolute-git-dir"))/"delivery.lock"

    @contextmanager
    def locked(self):
        with self.lockfile.open("a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DeliveryError("concurrent delivery publisher") from None
            yield

    def _journal(self):
        events = _read_lines(self.repo/EVENTS)
        receipts = _read_lines(self.repo/RECEIPTS)
        _validate_events(events)
        _validate_receipts(receipts, events)
        return events, receipts

    def append(self, row, *, origin="synthetic"):
        """Append a unique original or edited OPEN_GAME version, without publishing."""
        if origin not in ("synthetic", "dedicated-readonly-source"):
            raise DeliveryError("unrecognized producer provenance")
        if not isinstance(row, dict):
            raise DeliveryError("source row must be a dictionary")
        text = row.get("text")
        if not isinstance(text, str) or not text or len(text.encode("utf-8"))>MAX_TEXT:
            raise DeliveryError("invalid or oversized message")
        try:
            parsed = parse(text)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise DeliveryError("unparseable message") from exc
        if parsed.get("type") != "OPEN_GAME":
            return {"status": "ignored_non_open_game", "scientifically_admitted": False}
        mid = row.get("message_id")
        if type(mid) is not int or mid < 1:
            raise DeliveryError("message id must be a positive integer")
        sent, seen = _utc(row.get("date_utc")), _utc(row.get("collected_at_utc"))
        now = _utc(self.clock())
        if sent > seen or seen > now+timedelta(seconds=1):
            raise DeliveryError("invalid source-to-capture chronology")
        edit = row.get("edit_date_utc")
        if edit is not None:
            _utc(edit)
        source_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self.locked():
            events, _ = self._journal()
            if any(e["message_id"] == mid and e["source_sha256"] == source_sha for e in events):
                return {"status": "duplicate", "scientifically_admitted": False}
            event = {
                "sequence": len(events)+1,
                "previous_hash": events[-1]["hash"] if events else "0"*64,
                "message_id": mid,
                "source_sha256": source_sha,
                "text": text,
                "sent_utc": sent.isoformat(),
                "source_seen_utc": seen.isoformat(),
                "bridge_queued_utc": now.isoformat(),
                "edit_utc": _utc(edit).isoformat() if edit else None,
                "origin": origin,
                "capture_age_seconds": (now-sent).total_seconds(),
                "under_120s_before_git": bool(0 <= (now-sent).total_seconds() <= 120),
            }
            event["hash"] = _hash(event)
            if len(_canon(event))+sum(len(_canon(e))+1 for e in events) > MAX_BYTES:
                raise DeliveryError("journal capacity exhausted; rotate with a reviewed version")
            _atomic(self.repo/EVENTS, events+[event])
            return {"status": "queued", "sequence": event["sequence"],
                    "hash": event["hash"], "under_120s_before_git": event["under_120s_before_git"],
                    "scientifically_admitted": False}

    def _commit(self):
        allowed = {str(EVENTS), str(RECEIPTS)}
        _git(self.repo, "add", "--", *sorted(allowed))
        staged = set(filter(None, _git(self.repo, "diff", "--cached", "--name-only").splitlines()))
        if staged - allowed:
            raise DeliveryError("unrelated staged files; refusing to commit")
        if staged:
            _git(self.repo, "-c", "user.name=goal-roi-shadow-bridge",
                 "-c", "user.email=shadow@users.noreply.github.com",
                 "commit", "-m", "shadow: append-only signal delivery [skip ci]")
        return _git(self.repo, "rev-parse", "HEAD")

    def _push_confirm(self):
        sha = _git(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "push", "origin", "HEAD:refs/heads/"+BRANCH)
        line = _git(self.repo, "ls-remote", "origin", "refs/heads/"+BRANCH)
        if not line or line.split()[0] != sha:
            raise DeliveryError("remote push unverified")
        return sha

    def publish(self):
        """Two-phase receipt. A failed push leaves local commits for durable recovery.

        The producer MUST arrange persistence of a failed runner's checkout/outbox.
        Publishing to Git alone does not prove scientific eligibility or 120s capture.
        """
        with self.locked():
            events, receipts = self._journal()
            if not events:
                return {"status": "empty", "scientifically_admitted": False}
            self._commit()
            source_sha = self._push_confirm()  # barrier before verification receipt
            covered = receipts[-1]["covers_sequence"] if receipts else 0
            if covered < len(events):
                moment = _utc(self.clock())
                receipt = {
                    "sequence": len(receipts)+1,
                    "previous_hash": receipts[-1]["hash"] if receipts else "0"*64,
                    "covers_sequence": len(events),
                    "covers_hash": events[-1]["hash"],
                    "remote_data_commit_sha": source_sha,
                    "verified_utc": moment.isoformat(),
                    "delivery_age_last_seconds": (moment - _utc(events[-1]["sent_utc"])).total_seconds(),
                    "remote_verification": "ls-remote exact sha",
                }
                receipt["hash"] = _hash(receipt)
                _atomic(self.repo/RECEIPTS, receipts+[receipt])
                self._commit()
            final_sha = self._push_confirm()  # receipt itself must be remote
            events2, receipts2 = self._journal()
            if len(events2) != len(events) or receipts2[-1]["covers_sequence"] != len(events):
                raise DeliveryError("journal changed during publication")
            return {
                "status": "published",
                "remote_final_sha": final_sha,
                "last_event_sequence": len(events),
                "last_receipt_hash": receipts2[-1]["hash"],
                "delivery_age_last_seconds": receipts2[-1]["delivery_age_last_seconds"],
                "scientifically_admitted": False,
                "proves_runner_consumption": False,
            }


def audit(repo, now=None):
    """Read-only remote/local state check; NO inference of scientific admission."""
    pub = DeliveryPublisher(repo)
    events, receipts = pub._journal()
    remote = _git(pub.repo, "ls-remote", "origin", "refs/heads/"+BRANCH)
    local = _git(pub.repo, "rev-parse", "HEAD")
    remote_sha = remote.split()[0] if remote else None
    covered = receipts[-1]["covers_sequence"] if receipts else 0
    return {
        "events": len(events), "receipts": len(receipts),
        "queued_unreceipted": len(events)-covered,
        "remote_matches_local": remote_sha == local,
        "min_source_age_seconds": min((e["capture_age_seconds"] for e in events), default=None),
        "late_before_git": sum(not e["under_120s_before_git"] for e in events),
        "eligible_for_scientific_certificate": False,
        "live_capture_proven": False,
        "git_to_runner_latency_measured": False,
    }
