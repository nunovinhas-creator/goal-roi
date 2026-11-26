"""Round-trip one real archive and simulate 1825 distinct daily releases with its sizes."""
import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import time

from segment_store import SegmentStore, sha


def size(path):
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def chunks(data, limit=256 * 1024):
    block, n = [], 0
    for record in data.splitlines(keepends=True):
        if block and n + len(record) > limit:
            yield b"".join(block)
            block, n = [], 0
        block.append(record)
        n += len(record)
    if block:
        yield b"".join(block)


def simulate(repo, days):
    journal = (repo / "data/messages.jsonl").read_bytes()
    report = (repo / "docs/report.json").read_bytes()
    assert journal.endswith(b"\n")
    with tempfile.TemporaryDirectory(prefix="v7-store-") as t:
        directory = Path(t)
        real = SegmentStore(directory / "real")
        files = {f"archive/part-{i:05d}.jsonl": block for i, block in enumerate(chunks(journal))}
        files["site/report.json"] = report
        started = time.perf_counter()
        _, pin = real.publish(1, files, None)
        _, _, manifest, loaded = real.active()
        read_ms = round(1000 * (time.perf_counter() - started), 1)
        assert loaded == files
        assert b"".join(loaded[name] for name in sorted(loaded) if name.startswith("archive/")) == journal
        logical = {key: len(value) for key, value in files.items()}

        model = SegmentStore(directory / "model")
        # One real journal block per day, distinct synthetic IDs/timestamps in an envelope.
        # All 1825 blocks are stored independently, rather than getting an unrealistic benefit from SHA dedup.
        sample = list(chunks(journal))
        expected, counts, raw = {}, {}, 0
        head = None
        first = date(2026, 1, 1)
        start = time.perf_counter()
        for index in range(1, days + 1):
            block = sample[(index - 1) % len(sample)]
            label = (first + timedelta(days=index - 1)).isoformat()
            frame = (json.dumps({"synthetic_day": label}) + "\n").encode() + block
            raw += len(frame)
            expected[index] = sha(frame)
            status, head = model.publish(index, {"archive/day.jsonl": frame}, head)
            assert status == "published"
            if index in (30, 90, 365, days):
                counts[str(index)] = {"raw_bytes": raw, "stored_bytes": size(model.root)}
        assert all(sha(files["archive/day.jsonl"]) == expected[m["seq"]] for _, m, files in model.history())
        assert sum(1 for _ in model.manifests.glob("*.json")) == days
        elapsed = time.perf_counter() - start
        return {"real": {"archive_sha256": sha(journal), "report_sha256": sha(report), "release": pin,
                         "segments": len(logical) - 1, "raw_bytes": sum(logical.values()),
                         "stored_bytes": size(real.root), "write_read_ms": read_ms, "verified": True},
                "simulation": {"days": days, "manifests_verified": days,
                               "series": counts, "elapsed_seconds": round(elapsed, 2),
                               "payload_model": "real structure and volume reused with independent daily envelopes"}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--days", type=int, default=1825)
    args = p.parse_args()
    if args.days <= 0:
        p.error("--days must be positive")
    print(json.dumps(simulate(args.repo.resolve(), args.days), indent=2))


if __name__ == "__main__":
    main()
