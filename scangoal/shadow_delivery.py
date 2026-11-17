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
import zipfile
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
        # Never publish raw Telegram content into a repository branch before
        # a separate data-protection and live-source authorization review.
        if origin != "synthetic":
            raise DeliveryError("live source publication is not authorized")
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
        present = [path for path in sorted(allowed) if (self.repo/path).is_file()]
        if present:
            _git(self.repo, "add", "--", *present)
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


    def export_outbox(self, destination, max_zip_bytes=5_000_000):
        """Atomically export original unpushed Git commits and append-only journals.

        Runner orchestration MUST save this ZIP even if a publish attempt failed,
        and MUST restore it before reading fresh events. This module does not do
        any GitHub Actions artifact upload by itself.
        """
        dest = Path(destination)
        if dest.exists():
            raise DeliveryError("outbox destination must be new")
        if self.repo == dest.resolve() or self.repo in dest.resolve().parents:
            raise DeliveryError("outbox must be outside publisher checkout")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.locked():
            events, receipts = self._journal()
            remote = _git(self.repo, "ls-remote", "origin", "refs/heads/"+BRANCH)
            if not remote:
                raise DeliveryError("remote branch missing")
            remote_sha = remote.split()[0]
            head = _git(self.repo, "rev-parse", "HEAD")
            files = {}
            for relative in (EVENTS, RECEIPTS):
                path = self.repo/relative
                if path.is_file():
                    files[str(relative)] = path.read_bytes()
            with tempfile.TemporaryDirectory(prefix="shadow-bridge-bundle-") as temp:
                bundle = Path(temp)/"pending.bundle"
                _git(self.repo, "bundle", "create", bundle, "refs/heads/"+BRANCH)
                bundle_bytes = bundle.read_bytes()
                payload = {"pending.bundle":bundle_bytes, **files}
                manifest = {
                    "version": 1, "branch": BRANCH,
                    "remote_at_export_sha": remote_sha,
                    "local_head_sha": head,
                    "files_sha256": {key:hashlib.sha256(raw).hexdigest()
                                     for key,raw in payload.items()},
                    "events":len(events), "receipts":len(receipts),
                    "synthetic_only_until_source_adapter_approved":True,
                }
                fd, tmp = tempfile.mkstemp(prefix=".shadow-bridge-",dir=str(dest.parent))
                os.close(fd)
                try:
                    with zipfile.ZipFile(tmp,"w",compression=zipfile.ZIP_DEFLATED) as z:
                        z.writestr("manifest.json",_canon(manifest))
                        for name, raw in payload.items():
                            z.writestr(name,raw)
                    if Path(tmp).stat().st_size>max_zip_bytes:
                        raise DeliveryError("outbox archive exceeds fixed free-storage budget")
                    with open(tmp,"rb") as src:
                        os.fsync(src.fileno())
                    os.link(tmp,dest)
                finally:
                    Path(tmp).unlink(missing_ok=True)
        return {"status":"exported","archive_sha256":hashlib.sha256(dest.read_bytes()).hexdigest(),
                "events":len(events), "receipts":len(receipts),
                "production_runner_recovery_verified":False}

    def restore_outbox(self, archive, max_zip_bytes=5_000_000):
        """Fail-closed restore into a fresh checkout before reading new signals."""
        archive = Path(archive)
        if archive.stat().st_size>max_zip_bytes:
            raise DeliveryError("outbox ZIP too large")
        with self.locked(), zipfile.ZipFile(archive) as z:
            names = z.namelist()
            expected_names = {"manifest.json","pending.bundle",str(EVENTS),str(RECEIPTS)}
            if len(names)!=len(set(names)) or set(names)-expected_names:
                raise DeliveryError("unexpected ZIP members")
            if sum(i.file_size for i in z.infolist()) > MAX_BYTES*2:
                raise DeliveryError("oversized expanded ZIP")
            manifest = json.loads(z.read("manifest.json"))
            if manifest.get("version")!=1 or manifest.get("branch")!=BRANCH:
                raise DeliveryError("wrong delivery outbox schema/branch")
            hashes = manifest.get("files_sha256")
            if not isinstance(hashes,dict) or "pending.bundle" not in hashes:
                raise DeliveryError("missing required bundle hash")
            if set(hashes)!=(set(names)-{"manifest.json"}):
                raise DeliveryError("missing or additional files")
            payload = {name:z.read(name) for name in hashes}
            if any(hashlib.sha256(raw).hexdigest()!=hashes[name]
                   for name,raw in payload.items()):
                raise DeliveryError("outbox file hashes do not match")
            archived_events = [json.loads(line) for line in
                               payload.get(str(EVENTS),b"").splitlines() if line]
            archived_receipts = [json.loads(line) for line in
                                 payload.get(str(RECEIPTS),b"").splitlines() if line]
            _validate_events(archived_events)
            _validate_receipts(archived_receipts,archived_events)
            if (len(archived_events)!=manifest.get("events") or
                    len(archived_receipts)!=manifest.get("receipts")):
                raise DeliveryError("outbox count mismatch")
            existing_events, existing_receipts = self._journal()
            if (archived_events[:len(existing_events)]!=existing_events or
                    archived_receipts[:len(existing_receipts)]!=existing_receipts):
                raise DeliveryError("restore would rewrite existing journal prefix")
            remote = _git(self.repo,"ls-remote","origin","refs/heads/"+BRANCH)
            if not remote or remote.split()[0]!=manifest.get("remote_at_export_sha"):
                raise DeliveryError("remote moved; quarantine archived outbox")
            current = _git(self.repo,"rev-parse","HEAD")
            if current != manifest.get("remote_at_export_sha"):
                raise DeliveryError("restore checkout not at exporter remote base")
            with tempfile.TemporaryDirectory(prefix="shadow-bridge-import-") as dirname:
                bundle=Path(dirname)/"pending.bundle"
                bundle.write_bytes(payload["pending.bundle"])
                _git(self.repo,"bundle","verify",bundle)
                _git(self.repo,"fetch",bundle,"refs/heads/"+BRANCH)
                fetched=_git(self.repo,"rev-parse","FETCH_HEAD")
                if fetched!=manifest.get("local_head_sha"):
                    raise DeliveryError("bundle head does not match export")
                if fetched!=current:
                    _git(self.repo,"merge","--ff-only","FETCH_HEAD")
            for relative, entries in ((EVENTS,archived_events),
                                      (RECEIPTS,archived_receipts)):
                if str(relative) in payload:
                    _atomic(self.repo/relative,entries)
            verified_events,verified_receipts=self._journal()
            if verified_events!=archived_events or verified_receipts!=archived_receipts:
                raise DeliveryError("restored data diverged")
            return {"status":"restored","events":len(verified_events),
                    "receipts":len(verified_receipts),
                    "resumption_requires_publication":True,
                    "production_runner_recovery_verified":False}


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


def inspect_remote_delivery(repo, observed_utc):
    """Read-only evidence from an independently cloned delivery branch.

    A positive transport age requires the actual downstream runner observation,
    NOT merely the producer's own reported capture time. This is deliberately
    insufficient to certify SHADOW: no source authentication or result gate.
    """
    pub = DeliveryPublisher(repo)
    seen = _utc(observed_utc)
    remote = _git(pub.repo,"ls-remote","origin","refs/heads/"+BRANCH)
    local_sha = _git(pub.repo,"rev-parse","HEAD")
    if not remote or remote.split()[0] != local_sha:
        raise DeliveryError("downstream checkout not identical to remote")
    if _git(pub.repo,"status","--porcelain","--",str(EVENTS.parent)):
        raise DeliveryError("downstream checkout contains uncommitted delivery data")
    events, receipts = pub._journal()
    reports=[]
    for e in events:
        covered = next((r for r in receipts if r["covers_sequence"]>=e["sequence"]),None)
        sent = _utc(e["sent_utc"])
        queued = _utc(e["bridge_queued_utc"])
        source = _utc(e["source_seen_utc"])
        receipt_at = _utc(covered["verified_utc"]) if covered else None
        chronology_ok = (sent<=source<=queued<=seen and
                         (receipt_at is None or source<=receipt_at<=seen))
        age = (seen-sent).total_seconds() if chronology_ok else None
        reports.append({
            "sequence":e["sequence"],"event_hash":e["hash"],
            "remote_receipt_present":bool(covered),
            "end_to_end_age_seconds":age,
            "within_120s_transport": bool(covered and chronology_ok and 0<=age<=120),
            "runner_observed_utc":seen.isoformat(),
            "scientifically_admitted":False,
            "source_authenticated":False,
        })
    return {
        "remote_head_sha":local_sha,
        "events":len(events),
        "with_receipt":sum(v["remote_receipt_present"] for v in reports),
        "within_120s_transport":sum(v["within_120s_transport"] for v in reports),
        "late_or_unproven":sum(not v["within_120s_transport"] for v in reports),
        "observations":reports,
        "real_live_delivery_coverage_proven":False,
        "certification_activated":False,
    }
