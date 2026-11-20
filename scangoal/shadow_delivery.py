"""Optional, offline-testable SHADOW signal-delivery bridge.

This module has NO Telegram client, no scheduler and no automatic entry point.
A separate, expressly authorized source adapter must supply message rows later.
Git publication is distinct from SHADOW scientific admission and certification.
"""
import fcntl
import hashlib
import json
import os
import re
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import shadow_ops
from .parser import parse

BRANCH = "shadow/signal-delivery"
EVENTS = Path("research/shadow-delivery/events.jsonl")
RECEIPTS = Path("research/shadow-delivery/receipts.jsonl")
UTC = timezone.utc
MAX_TEXT = 20_000
MAX_BYTES = 25_000_000
COMMIT_NAME = "goal-roi-shadow-bridge"
COMMIT_EMAIL = "shadow@users.noreply.github.com"
COMMIT_MESSAGE = "shadow: append-only signal delivery [skip ci]"
ZERO_HASH = "0" * 64
EVENT_FIELDS = {
    "sequence", "previous_hash", "message_id", "source_sha256", "sent_utc",
    "source_seen_utc", "bridge_queued_utc", "edit_utc", "origin",
    "capture_age_seconds", "under_120s_before_git", "hash",
}
RECEIPT_FIELDS = {
    "sequence", "previous_hash", "covers_sequence", "covers_hash",
    "remote_data_commit_sha", "verified_utc", "delivery_age_last_seconds",
    "remote_verification", "hash",
}


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
    return _decode_lines(path.read_bytes())


def _decode_lines(data):
    if len(data) > MAX_BYTES:
        raise DeliveryError("delivery journal size exceeds budget")
    if data and not data.endswith(b"\n"):
        raise DeliveryError("incomplete delivery journal; do not reinterpret a partial record")
    try:
        items = [json.loads(line) for line in data.splitlines() if line]
    except (ValueError, UnicodeError):
        raise DeliveryError("invalid delivery journal") from None
    try:
        canonical = all(isinstance(item, dict) for item in items) and (
            b"".join(_canon(item) + b"\n" for item in items) == data)
    except (ValueError, UnicodeError):
        canonical = False
    if not canonical:
        raise DeliveryError("invalid or noncanonical journal record")
    return items


def _validate_events(events):
    previous = ZERO_HASH
    versions = set()
    message_ids = set()
    for seq, event in enumerate(events, 1):
        if set(event) != EVENT_FIELDS or event["origin"] != "synthetic":
            raise DeliveryError("unexpected event schema or origin")
        if (type(event["message_id"]) is not int or event["message_id"] < 1 or
                not _hex(event["source_sha256"], 64)):
            raise DeliveryError("invalid event version")
        sent, seen, queued = (_utc(event[key]) for key in
                              ("sent_utc", "source_seen_utc", "bridge_queued_utc"))
        edit = None if event["edit_utc"] is None else _utc(event["edit_utc"])
        age = (queued - sent).total_seconds()
        if (not sent <= seen <= queued or (edit is not None and not sent <= edit <= seen) or
                event["capture_age_seconds"] != age or
                event["under_120s_before_git"] is not (edit is None and age <= 120)):
            raise DeliveryError("invalid event chronology or derived age")
        body = {k: v for k, v in event.items() if k != "hash"}
        if event.get("sequence") != seq or event.get("previous_hash") != previous:
            raise DeliveryError("event journal chain broken")
        if event.get("hash") != _hash(body):
            raise DeliveryError("event hash mismatch")
        key = (event.get("message_id"), event.get("source_sha256"))
        if key in versions:
            raise DeliveryError("duplicate event version in journal")
        mid = event["message_id"]
        if mid in message_ids and edit is None:
            raise DeliveryError("additional version has no edit timestamp")
        versions.add(key)
        message_ids.add(mid)
        previous = event["hash"]
    return previous


def _validate_receipts(receipts, events):
    previous = ZERO_HASH
    last_covered = 0
    last_verified = None
    for seq, entry in enumerate(receipts, 1):
        if (set(entry) != RECEIPT_FIELDS or not _hex(entry["remote_data_commit_sha"], 40) or
                entry["remote_verification"] != "ls-remote exact sha"):
            raise DeliveryError("unexpected receipt schema")
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
        if covered <= last_covered:
            raise DeliveryError("duplicate or backwards receipt")
        verified = _utc(entry["verified_utc"])
        if ((last_verified is not None and verified < last_verified) or
                any(verified < _utc(e["bridge_queued_utc"]) for e in events[last_covered:covered]) or
                entry["delivery_age_last_seconds"] !=
                (verified - _utc(events[covered - 1]["sent_utc"])).total_seconds()):
            raise DeliveryError("invalid receipt chronology or derived age")
        last_covered, last_verified = covered, verified
        previous = entry["hash"]
    return previous


def _hex(value, size):
    return isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{size}}}", value) is not None


def _extends(base, current):
    if any(new[:len(old)] != old for old, new in zip(base, current)):
        raise DeliveryError("append-only delivery journal prefix changed")


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
        return shadow_ops.git(repo, "-c", "protocol.allow=never",
                              "-c", "protocol.file.allow=always",
                              "-c", "core.hooksPath=/dev/null",
                              "--no-replace-objects", *args)
    except (shadow_ops.OpsError, shadow_ops.DeadlineExceeded, OSError, UnicodeError):
        raise DeliveryError("isolated Git operation failed") from None


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
        directory = self.repo / EVENTS.parent
        if directory.parent.is_symlink() or directory.is_symlink():
            raise DeliveryError("delivery directory must not contain symlinks")
        paths = [self.repo / path for path in (EVENTS, RECEIPTS)]
        if any(p.is_symlink() or (p.exists() and not p.is_file()) for p in paths):
            raise DeliveryError("delivery journal must be a regular file")
        return self._validated(*(_read_lines(p) for p in paths))

    @staticmethod
    def _validated(events, receipts):
        _validate_events(events)
        _validate_receipts(receipts, events)
        return events, receipts

    def _committed(self, sha):
        journals = []
        for path in (EVENTS, RECEIPTS):
            entry = _git(self.repo, "ls-tree", sha, "--", path)
            if not entry:
                journals.append([])
                continue
            if not entry.startswith("100644 blob "):
                raise DeliveryError("committed delivery journal must be a regular file")
            size = int(_git(self.repo, "cat-file", "-s", f"{sha}:{path}"))
            if size > MAX_BYTES:
                raise DeliveryError("committed delivery journal exceeds size budget")
            # The Git helper strips whitespace. A canonical journal ends in a single LF.
            content = _git(self.repo, "show", f"{sha}:{path}").encode("utf-8")
            if size and size != len(content) + 1:
                raise DeliveryError("committed delivery journal has invalid framing")
            journals.append(_decode_lines(content + b"\n" if size else b""))
        return self._validated(*journals)

    def _remote_head(self):
        for mode in ([], ["--push"]):
            urls = _git(self.repo, "remote", "get-url", *mode, "--all", "origin").splitlines()
            if len(urls) != 1:
                raise DeliveryError("exactly one local Git remote is required")
            url = urlsplit(urls[0])
            if url.scheme == "file" and not (url.netloc or url.query or url.fragment):
                path = Path(unquote(url.path))
            elif not url.scheme and not url.netloc and ":" not in urls[0]:
                path = Path(urls[0])
            else:
                raise DeliveryError("only a local bare Git remote is allowed")
            path = (self.repo / path).resolve()
            if _git(path, "rev-parse", "--is-bare-repository") != "true":
                raise DeliveryError("only a local bare Git remote is allowed")
            if mode:
                if path != fetch_path:
                    raise DeliveryError("fetch and push must use the same local remote")
            else:
                fetch_path = path
        line = _git(self.repo, "ls-remote", "origin", "refs/heads/" + BRANCH)
        if not line:
            raise DeliveryError("remote delivery branch missing")
        return line.split()[0]

    def _verify_receipt_data(self, head, events, receipts, *, committed):
        for index, receipt in enumerate(receipts):
            data_sha = receipt["remote_data_commit_sha"]
            if committed and data_sha == head:
                raise DeliveryError("receipt must follow its data commit")
            _git(self.repo, "merge-base", "--is-ancestor", data_sha, head)
            expected = (events[:receipt["covers_sequence"]], receipts[:index])
            if self._committed(data_sha) != expected:
                raise DeliveryError("receipt does not match its referenced data commit")

    def _check_pending(self, base, head):
        _git(self.repo, "merge-base", "--is-ancestor", base, head)
        previous = base
        journal = self._committed(base)
        allowed = {str(EVENTS), str(RECEIPTS)}
        for entry in _git(self.repo, "rev-list", "--reverse", "--parents", f"{base}..{head}").splitlines():
            sha, *parents = entry.split()
            if parents != [previous]:
                raise DeliveryError("pending Git history must be linear")
            paths = _git(self.repo, "diff", "--no-renames", "--name-only", previous, sha).splitlines()
            if not paths or set(paths) - allowed:
                raise DeliveryError("unrelated commits in pending delivery history")
            metadata = _git(self.repo, "show", "-s", "--format=%an%n%ae%n%cn%n%ce%n%B", sha)
            if metadata != "\n".join((COMMIT_NAME, COMMIT_EMAIL, COMMIT_NAME,
                                       COMMIT_EMAIL, COMMIT_MESSAGE)):
                raise DeliveryError("unexpected pending delivery commit metadata")
            updated = self._committed(sha)
            _extends(journal, updated)
            previous, journal = sha, updated
        self._verify_receipt_data(head, *journal, committed=True)
        return journal

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
        raw_edit = row.get("edit_date_utc")
        edit = _utc(raw_edit) if raw_edit is not None else None
        if edit is not None and not sent <= edit <= seen:
            raise DeliveryError("invalid edit chronology")
        source_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self.locked():
            now = _utc(self.clock())
            if not sent <= seen <= now:
                raise DeliveryError("invalid source-to-capture chronology")
            events, _ = self._journal()
            if any(e["message_id"] == mid and e["source_sha256"] == source_sha for e in events):
                return {"status": "duplicate", "scientifically_admitted": False}
            event = {
                "sequence": len(events)+1,
                "previous_hash": events[-1]["hash"] if events else "0"*64,
                "message_id": mid,
                "source_sha256": source_sha,
                "sent_utc": sent.isoformat(),
                "source_seen_utc": seen.isoformat(),
                "bridge_queued_utc": now.isoformat(),
                "edit_utc": edit.isoformat() if edit is not None else None,
                "origin": origin,
                "capture_age_seconds": (now-sent).total_seconds(),
                "under_120s_before_git": edit is None and (now-sent).total_seconds() <= 120,
            }
            event["hash"] = _hash(event)
            if len(_canon(event))+1+sum(len(_canon(e))+1 for e in events) > MAX_BYTES:
                raise DeliveryError("journal capacity exhausted; rotate with a reviewed version")
            _validate_events(events+[event])
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
            _git(self.repo, "-c", f"user.name={COMMIT_NAME}", "-c", f"user.email={COMMIT_EMAIL}",
                 "-c", f"author.name={COMMIT_NAME}", "-c", f"author.email={COMMIT_EMAIL}",
                 "-c", f"committer.name={COMMIT_NAME}", "-c", f"committer.email={COMMIT_EMAIL}",
                 "commit", "--no-gpg-sign", "-m", COMMIT_MESSAGE)
        return _git(self.repo, "rev-parse", "HEAD")

    def _push_confirm(self):
        sha = _git(self.repo, "rev-parse", "HEAD")
        remote = self._remote_head()
        self._check_pending(remote, sha)
        _git(self.repo, "push", "--no-follow-tags", "origin", f"{sha}:refs/heads/"+BRANCH)
        if self._remote_head() != sha:
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
            head = _git(self.repo, "rev-parse", "HEAD")
            _extends(self._committed(head), (events, receipts))
            self._verify_receipt_data(head, events, receipts, committed=False)
            self._remote_head()
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
                _validate_receipts(receipts+[receipt], events)
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
            remote_sha = self._remote_head()
            base = _git(self.repo, "rev-parse", "HEAD")
            _extends(self._check_pending(remote_sha, base), (events, receipts))
            self._verify_receipt_data(base, events, receipts, committed=False)
            head = self._commit()
            if self._check_pending(remote_sha, head) != (events, receipts):
                raise DeliveryError("outbox does not match committed journal")
            files = {}
            for relative in (EVENTS, RECEIPTS):
                path = self.repo/relative
                if path.is_file():
                    files[str(relative)] = path.read_bytes()
            with tempfile.TemporaryDirectory(prefix="shadow-bridge-bundle-") as temp:
                payload = dict(files)
                if head != remote_sha:
                    bundle = Path(temp)/"pending.bundle"
                    _git(self.repo, "bundle", "create", bundle,
                         "refs/heads/"+BRANCH, "^"+remote_sha)
                    payload["pending.bundle"] = bundle.read_bytes()
                manifest = {
                    "version": 2, "branch": BRANCH,
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
                        archive_hash = hashlib.file_digest(src, "sha256").hexdigest()
                    os.link(tmp,dest)
                    dir_fd = os.open(dest.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                finally:
                    Path(tmp).unlink(missing_ok=True)
        return {"status":"exported","archive_sha256":archive_hash,
                "events":len(events), "receipts":len(receipts),
                "production_runner_recovery_verified":False}

    def restore_outbox(self, archive, max_zip_bytes=5_000_000):
        """Validate the archive and pending Git history before updating the checkout."""
        try:
            with self.locked(), Path(archive).open("rb") as stream:
                if os.fstat(stream.fileno()).st_size > max_zip_bytes:
                    raise DeliveryError("outbox ZIP too large")
                with zipfile.ZipFile(stream) as z:
                    manifest, pending, journal = self._read_outbox(z)
                base, head = manifest["remote_at_export_sha"], manifest["local_head_sha"]
                current = _git(self.repo, "rev-parse", "HEAD")
                if self._remote_head() != base or current not in (base, head):
                    raise DeliveryError("remote or checkout moved; quarantine archived outbox")
                if _git(self.repo, "status", "--porcelain", "--untracked-files=all"):
                    raise DeliveryError("restore requires a clean checkout")
                if pending is not None:
                    with tempfile.TemporaryDirectory(prefix="shadow-bridge-import-") as dirname:
                        bundle = Path(dirname)/"pending.bundle"
                        bundle.write_bytes(pending)
                        _git(self.repo, "bundle", "verify", bundle)
                        if _git(self.repo, "bundle", "list-heads", bundle) != f"{head} refs/heads/{BRANCH}":
                            raise DeliveryError("bundle head does not match export")
                        # Import objects without changing the branch, index or worktree.
                        _git(self.repo, "fetch", "--no-tags", "--no-write-fetch-head",
                             bundle, "refs/heads/"+BRANCH)
                if self._check_pending(base, head) != journal:
                    raise DeliveryError("archived journals do not match the Git bundle")
                if self._remote_head() != base:
                    raise DeliveryError("remote moved during restore; quarantine archived outbox")
                if current != head:
                    _git(self.repo, "merge", "--ff-only", head)
                if self._journal() != journal:
                    raise DeliveryError("restored checkout does not match validated journal")
        except (OSError, ValueError, zipfile.BadZipFile, KeyError, TypeError):
            raise DeliveryError("invalid or unreadable delivery outbox") from None
        return {"status":"restored","events":len(journal[0]),"receipts":len(journal[1]),
                "resumption_requires_publication":True,
                "production_runner_recovery_verified":False}

    def _read_outbox(self, z):
        names = z.namelist()
        expected = {"manifest.json", "pending.bundle", str(EVENTS), str(RECEIPTS)}
        if (len(names) != len(set(names)) or set(names) - expected or
                "manifest.json" not in names):
            raise DeliveryError("unexpected ZIP members")
        if sum(i.file_size for i in z.infolist()) > MAX_BYTES * 2:
            raise DeliveryError("oversized expanded ZIP")
        manifest = json.loads(z.read("manifest.json"))
        if (not isinstance(manifest, dict) or manifest.get("version") != 2 or
                manifest.get("branch") != BRANCH or
                manifest.get("synthetic_only_until_source_adapter_approved") is not True):
            raise DeliveryError("wrong delivery outbox schema/branch")
        base, head = manifest.get("remote_at_export_sha"), manifest.get("local_head_sha")
        if not (_hex(base, 40) and _hex(head, 40)):
            raise DeliveryError("invalid outbox commit IDs")
        hashes = manifest.get("files_sha256")
        if (not isinstance(hashes, dict) or set(hashes) != set(names) - {"manifest.json"} or
                ("pending.bundle" in hashes) != (head != base)):
            raise DeliveryError("missing or additional outbox files")
        payload = {name:z.read(name) for name in hashes}
        if any(hashlib.sha256(raw).hexdigest() != hashes[name] for name, raw in payload.items()):
            raise DeliveryError("outbox file hashes do not match")
        journal = self._validated(*(_decode_lines(payload.get(str(path), b""))
                                    for path in (EVENTS, RECEIPTS)))
        if len(journal[0]) != manifest.get("events") or len(journal[1]) != manifest.get("receipts"):
            raise DeliveryError("outbox count mismatch")
        return manifest, payload.get("pending.bundle"), journal


def audit(repo, now=None):
    """Read-only remote/local state check; NO inference of scientific admission."""
    pub = DeliveryPublisher(repo)
    events, receipts = pub._journal()
    remote_sha = pub._remote_head()
    local = _git(pub.repo, "rev-parse", "HEAD")
    pub._verify_receipt_data(local, events, receipts, committed=False)
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

    The caller supplies a simulated observation timestamp. Git verifies
    checkout identity and receipt content for the local fixture only.
    """
    pub = DeliveryPublisher(repo)
    remote = pub._remote_head()
    local_sha = _git(pub.repo,"rev-parse","HEAD")
    if remote != local_sha:
        raise DeliveryError("downstream checkout not identical to remote")
    if _git(pub.repo,"status","--porcelain","--",str(EVENTS.parent)):
        raise DeliveryError("downstream checkout contains uncommitted delivery data")
    events, receipts = pub._committed(local_sha)
    pub._verify_receipt_data(local_sha, events, receipts, committed=True)
    seen = _utc(observed_utc)
    reports=[]
    for e in events:
        covered = next((r for r in receipts if r["covers_sequence"]>=e["sequence"]),None)
        sent = _utc(e["sent_utc"])
        queued = _utc(e["bridge_queued_utc"])
        source = _utc(e["source_seen_utc"])
        receipt_at = _utc(covered["verified_utc"]) if covered else None
        chronology_ok = (e["edit_utc"] is None and sent <= source <= queued <= seen and
                         (receipt_at is None or queued <= receipt_at <= seen))
        age = (seen-sent).total_seconds() if chronology_ok else None
        reports.append({
            "sequence":e["sequence"],
            "remote_receipt_present":bool(covered),
            "end_to_end_age_seconds":age,
            "within_120s_transport": bool(covered and chronology_ok and 0<=age<=120),
            "simulated_observed_utc":seen.isoformat(),
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
        "timing_basis":"synthetic_clock_assumptions",
        "real_live_delivery_coverage_proven":False,
        "certification_activated":False,
    }
