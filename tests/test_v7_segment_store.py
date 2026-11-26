from concurrent.futures import ThreadPoolExecutor
import gzip
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest

from audits.v7.storage.segment_store import IntegrityError, SegmentStore, StaleRevision, sha


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = SegmentStore(self.root)
        _, self.original = self.repo.publish(1, {"summary.json": b"release-1", "science.json": b"fixed"}, None)

    def test_interruption_leaves_complete_old_release_then_recovers_once(self):
        code = (
            "import os,signal,sys; from audits.v7.storage.segment_store import SegmentStore; "
            "SegmentStore(sys.argv[1]).publish(2,{'summary.json':b'release-2'},sys.argv[2],"
            "before_switch=lambda:os.kill(os.getpid(),signal.SIGKILL))"
        )
        job = subprocess.run([sys.executable, "-c", code, str(self.root), self.original], check=False)
        self.assertEqual(job.returncode, -signal.SIGKILL)
        self.assertEqual(self.repo.active()[3], {"summary.json": b"release-1", "science.json": b"fixed"})
        _, next_pin = self.repo.publish(2, {"summary.json": b"release-2"}, self.original)
        self.assertEqual(self.repo.publish(2, {"summary.json": b"release-2"}, next_pin), ("unchanged", next_pin))
        self.assertEqual([r[1]["seq"] for r in self.repo.history()], [2, 1])

    def test_concurrent_writers_have_one_winner(self):
        def contender(content):
            try:
                return self.repo.publish(2, {"summary.json": content}, self.original)[0]
            except StaleRevision:
                return "stale"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(contender, (b"a", b"b")), ("published", "stale"))
        self.assertEqual(self.repo.active()[2]["seq"], 2)
        with self.assertRaises(StaleRevision):
            self.repo.publish(1, {"summary.json": b"old"}, self.repo.pointer())

    def test_truncated_or_same_length_objects_fall_back_atomically(self):
        self.repo.publish(2, {"summary.json": b"release-2"}, self.original)
        expected = self.repo.objects / (sha(b"release-2") + ".gz")
        good = expected.read_bytes()
        for bad in (good[:10], gzip.compress(b"altered-2")):
            expected.write_bytes(bad)
            name, pin, _, files = self.repo.active()
            self.assertEqual((name, pin, files["summary.json"]), ("PREVIOUS", self.original, b"release-1"))
            with self.assertRaises(IntegrityError):
                self.repo.active(minimum=2)
        expected.write_bytes(good)
        self.assertEqual(self.repo.active(minimum=2)[3]["summary.json"], b"release-2")

    def test_immutable_objects_deduplicate_only_verified_bytes(self):
        _, current = self.repo.publish(2, {"science.json": b"fixed", "same.json": b"fixed"}, self.original)
        self.assertEqual(len(list(self.repo.objects.glob("*.gz"))), 2)
        self.assertEqual(self.repo.active()[1], current)
        with self.assertRaises(ValueError):
            self.repo.publish(3, {"../wrong": b"x"}, current)

    def test_invalid_head_uses_previous_and_no_mixed_render(self):
        self.repo.publish(2, {"summary.json": b"new", "science.json": b"new"}, self.original)
        (self.repo.root / "HEAD").write_text("truncated")
        channel, _, manifest, items = self.repo.active()
        self.assertEqual((channel, manifest["seq"], items["science.json"]), ("PREVIOUS", 1, b"fixed"))


if __name__ == "__main__":
    unittest.main()
