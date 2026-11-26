"""Offline, portable prototype: immutable compressed blobs, verified releases and atomic HEAD."""
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import zlib
from contextlib import contextmanager

DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_ENTRY = 100 * 1024 * 1024


class IntegrityError(RuntimeError):
    pass


class StaleRevision(IntegrityError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def serialized(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic(path, data):
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            temp = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def safe_name(name):
    path = PurePosixPath(name)
    return bool(path.parts and not path.is_absolute() and path.as_posix() == name
                and all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", piece) for piece in path.parts))


class SegmentStore:
    def __init__(self, root):
        self.root = Path(root)
        self.objects, self.manifests = self.root / "objects", self.root / "manifests"
        for folder in (self.objects, self.manifests):
            folder.mkdir(parents=True, exist_ok=True)
        sync_directory(self.root)

    @contextmanager
    def locked(self):
        with (self.root / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def pointer(self, name="HEAD"):
        try:
            pin = (self.root / name).read_text("ascii").strip()
        except FileNotFoundError:
            return None
        if not DIGEST.fullmatch(pin):
            raise IntegrityError("invalid pointer")
        return pin

    def manifest(self, pin):
        if not isinstance(pin, str) or not DIGEST.fullmatch(pin):
            raise IntegrityError("invalid manifest id")
        try:
            raw = (self.manifests / f"{pin}.json").read_bytes()
            if sha(raw) != pin:
                raise IntegrityError("manifest hash mismatch")
            value = json.loads(raw)
            if (not isinstance(value, dict) or set(value) != {"schema", "seq", "previous", "files"}
                    or type(value["schema"]) is not int or value["schema"] != 1
                    or type(value["seq"]) is not int or value["seq"] < 1
                    or (value["previous"] is not None and not DIGEST.fullmatch(value["previous"]))
                    or not isinstance(value["files"], dict) or not value["files"]):
                raise IntegrityError("invalid manifest")
            return value
        except (OSError, ValueError, TypeError) as exc:
            raise IntegrityError("cannot read manifest") from exc

    def payload(self, ref):
        try:
            digest, size = ref["sha256"], ref["bytes"]
            if (not DIGEST.fullmatch(digest) or type(size) is not int
                    or size < 0 or size > MAX_ENTRY):
                raise IntegrityError("invalid object reference")
            with gzip.open(self.objects / f"{digest}.gz", "rb") as f:
                data = f.read(size + 1)
                if len(data) != size or f.read(1) or sha(data) != digest:
                    raise IntegrityError("object hash or length mismatch")
            return data
        except (OSError, EOFError, KeyError, TypeError, ValueError, zlib.error) as exc:
            raise IntegrityError("cannot read object") from exc

    def load_release(self, pin):
        manifest = self.manifest(pin)
        if not all(safe_name(name) for name in manifest["files"]):
            raise IntegrityError("unsafe path")
        return manifest, {name: self.payload(ref) for name, ref in manifest["files"].items()}

    def active(self, minimum=0):
        """Read an entire release or an explicitly marked fallback, never mix revisions."""
        for name in ("HEAD", "PREVIOUS"):
            try:
                pin = self.pointer(name)
                if pin is not None:
                    manifest, files = self.load_release(pin)
                    if manifest["seq"] >= minimum:
                        return name, pin, manifest, files
            except IntegrityError:
                continue
        raise IntegrityError("no verified release meets the revision floor")

    def publish(self, seq, files, expected, *, before_switch=lambda: None):
        if type(seq) is not int or seq < 1 or not files:
            raise ValueError("expected a positive revision with files")
        refs = {}
        for name, data in sorted(files.items()):
            if not safe_name(name) or not isinstance(data, bytes) or len(data) > MAX_ENTRY:
                raise ValueError("invalid publication file")
            refs[name] = {"sha256": sha(data), "bytes": len(data)}
        with self.locked():
            head = self.pointer()
            previous = self.manifest(head) if head else None
            if previous and seq == previous["seq"] and refs == previous["files"]:
                self.load_release(head)
                return "unchanged", head
            if head != expected or (previous and seq <= previous["seq"]):
                raise StaleRevision("a different revision already owns HEAD")
            for name, ref in refs.items():
                dest = self.objects / (ref["sha256"] + ".gz")
                if dest.exists():
                    self.payload(ref)
                else:
                    atomic(dest, gzip.compress(files[name], compresslevel=6, mtime=0))
            value = dict(schema=1, seq=seq, previous=head, files=refs)
            raw = serialized(value)
            pin = sha(raw)
            path = self.manifests / f"{pin}.json"
            if path.exists():
                if path.read_bytes() != raw:
                    raise IntegrityError("existing manifest hash mismatch")
            else:
                atomic(path, raw)
            if head:
                self.load_release(head)
                atomic(self.root / "PREVIOUS", (head + "\n").encode())
            before_switch()
            atomic(self.root / "HEAD", (pin + "\n").encode())
            return "published", pin

    def history(self):
        pin, last = self.pointer(), None
        while pin:
            manifest, files = self.load_release(pin)
            if last is not None and manifest["seq"] >= last:
                raise IntegrityError("nondecreasing history")
            yield pin, manifest, files
            last, pin = manifest["seq"], manifest["previous"]
