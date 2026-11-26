"""Exercise the real bounded Telegram backfill against an archived conversation, offline."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from scangoal import reader, store


async def drill(rows, hours):
    when = max(datetime.fromisoformat(row["date_utc"]) for row in rows)
    cutoff = when - timedelta(hours=hours)
    before = [row["message_id"] for row in rows if datetime.fromisoformat(row["date_utc"]) <= cutoff]
    assert before
    after_id = max(before)
    latest = {}
    for row in rows:
        if row["message_id"] > after_id:
            latest[row["message_id"]] = row
    current = set(sorted(latest)[-60:])
    lower_live = min(current)
    eligible = {i: row for i, row in latest.items() if after_id < i < lower_live}
    assert len(eligible) > 100
    now = when.isoformat()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        raw = path / "messages.jsonl"
        raw.write_bytes(b"live-journal-sentinel\n")
        evidence = path / "collection_windows.json"
        state = reader.record_window(path, {"last_run_utc": cutoff.isoformat()}, after_id, lower_live, current, now)
        state_file = evidence.read_bytes()
        calls = Counter()

        async def fail(*args, **kwargs):
            calls["telegram_unavailable"] += 1
            raise TimeoutError("offline fixture")

        async def expired(*args, **kwargs):
            calls["expired_credentials"] += 1
            raise PermissionError("expired fixture")

        async def available(client, entity, limit, stamp, *, min_id, offset_id):
            calls["pages_served"] += 1
            keys = sorted((i for i in eligible if min_id < i < offset_id), reverse=True)[:limit]
            return [eligible[i] for i in keys], min(keys, default=None), set(keys)

        for mock, error in ((fail, TimeoutError), (expired, PermissionError)):
            with patch.object(reader, "fetch", mock):
                try:
                    await reader.recover_page(None, None, path, state, now)
                except error:
                    pass
                else:
                    raise AssertionError("fault was not reported")
            assert evidence.read_bytes() == state_file and not list((path / "collection_backfill").glob("*.json"))
        write = reader.write_json
        def crash_after_page(dest, value):
            if dest == evidence:
                raise OSError("crash between page and cursor")
            return write(dest, value)

        with patch.object(reader, "fetch", available):
            with patch.object(reader, "write_json", crash_after_page):
                try:
                    await reader.recover_page(None, None, path, state, now)
                except OSError:
                    pass
                else:
                    raise AssertionError("fault was not reached")
            state = json.loads(evidence.read_bytes())
            page = next((path / "collection_backfill").glob("*.json"))
            good = page.read_bytes()
            page.write_bytes(good[:20])
            try:
                await reader.recover_page(None, None, path, state, now)
            except ValueError:
                calls["corrupt_page_blocked"] += 1
            else:
                raise AssertionError("corrupt replay was accepted")
            assert calls["pages_served"] == 1 and evidence.read_bytes() == state_file
            page.write_bytes(good)
            await reader.recover_page(None, None, path, state, now)
            assert calls["pages_served"] == 1
            while state["intervals"][0]["status"] == "pending":
                await reader.recover_page(None, None, path, state, now)
                assert calls["pages_served"] <= len(eligible) // 100 + 2
            out = list((path / "collection_backfill").glob("*.json"))
            actual = [row["message_id"] for page in out for row in json.loads(page.read_bytes())["rows"]]
            assert len(actual) == len(set(actual)) == len(eligible) and set(actual) == set(eligible)
            served = calls["pages_served"]
            await reader.recover_page(None, None, path, json.loads(evidence.read_bytes()), now)
            assert calls["pages_served"] == served
        assert raw.read_bytes() == b"live-journal-sentinel\n"
        assert not (path / "prospective_ledger.jsonl").exists()
        return {"hours_simulated": hours, "visible_rows_in_gap": len(eligible), "rows_recovered": len(actual),
                "live_window_excluded": len(current), "duplicate_rows": len(actual) - len(set(actual)),
                "calls": calls, "cursor_status": state["intervals"][0]["status"], "live_journal_unchanged": True}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--archive", type=Path, required=True)
    rows = [row for _, row in store.message_rows(cli.parse_args().archive)]
    print(json.dumps([asyncio.run(drill(rows, hours)) for hours in (24, 72)], indent=2))


if __name__ == "__main__":
    main()
