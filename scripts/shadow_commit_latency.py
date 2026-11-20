"""Read-only, privacy-preserving lower bound on Git availability of OPEN_GAME.

Uses full local Git history of data/messages.jsonl. Git *commit* timestamp is
a lower bound on remote visibility, never proof of push or runner availability.
No network, secrets, branch writes, Telegram access, or raw message output.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scangoal.parser import parse

UTC = timezone.utc
DATA_PATH = "data/messages.jsonl"
MAX_COMMITS = 800
MAX_DATA_BYTES = 12_000_000


class LatencyError(RuntimeError):
    pass


def when(raw):
    if not isinstance(raw, str):
        return None
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return d.astimezone(UTC) if d.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def git(repo, *args):
    try:
        process = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repo), *args], timeout=30, capture_output=True,
            check=True)
        return process.stdout
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise LatencyError("offline Git history unavailable: " + str(args[0])) from exc


def version_key(item):
    mid = item.get("message_id")
    txt = item.get("text")
    if type(mid) is not int or mid < 1 or not isinstance(txt, str):
        return None
    try:
        sha = hashlib.sha256(txt.encode("utf-8")).hexdigest()
    except UnicodeError:
        return None
    return mid, sha


def _read_rows(raw):
    if len(raw) > MAX_DATA_BYTES:
        raise LatencyError("historical messages exceed bounded input size")
    try:
        lines = [json.loads(line) for line in raw.splitlines() if line.strip()]
    except (ValueError, UnicodeDecodeError):
        raise LatencyError("historical messages invalid") from None
    if not all(isinstance(row, dict) for row in lines):
        raise LatencyError("non-object historical version")
    return lines


def snapshot(repo, sha):
    if not git(repo, "ls-tree", sha, "--", DATA_PATH):
        return []
    obj = f"{sha}:{DATA_PATH}"
    if int(git(repo, "cat-file", "-s", obj)) > MAX_DATA_BYTES:
        raise LatencyError("historical messages exceed bounded input size")
    return _read_rows(git(repo, "show", obj))


def percentile(values, percent):
    if not values:
        return None
    values = sorted(values)
    index = min(len(values)-1, max(0, (len(values)*percent+99)//100-1))
    return round(values[index], 3)


def study(repo, now, *, hours=24, max_commits=MAX_COMMITS):
    """No raw identifiers, team names, texts or direct identifiers in output."""
    repo = Path(repo)
    if (not 1 <= hours <= 72 or not isinstance(now, datetime) or now.tzinfo is None or
            type(max_commits) is not int or not 1 <= max_commits <= 10_000):
        raise LatencyError("invalid measurement period")
    now = now.astimezone(UTC)
    if git(repo, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise LatencyError("full Git history required, shallow checkout cannot prove first presence")
    if git(repo, "status", "--porcelain", "--", DATA_PATH).strip():
        raise LatencyError("source data has uncommitted changes")
    head = git(repo, "rev-parse", "HEAD").decode("ascii").strip()
    rows = snapshot(repo, head)
    start = now-timedelta(hours=hours)
    first = {}
    contradictory = set()
    excluded = 0
    for row in rows:
        key = version_key(row)
        if not key:
            excluded += 1
            continue
        try:
            message = parse(row.get("text") or "")
        except (ValueError, TypeError, KeyError, AttributeError):
            excluded += 1
            continue
        if message.get("type") != "OPEN_GAME":
            continue
        sent, seen = when(row.get("date_utc")), when(row.get("collected_at_utc"))
        if sent is None or seen is None or not (start <= sent <= now):
            if sent is None or seen is None:
                excluded += 1
            continue
        mid = key[0]
        record = {"key":key, "sent":sent, "seen":seen, "edit":row.get("edit_date_utc")}
        old = first.get(mid)
        if old is not None and (old["sent"] != sent or (old["seen"] == seen and old != record)):
            contradictory.add(mid)
        if old is None or seen < old["seen"]:
            first[mid] = record
    # Walk first-parent snapshots in topology order. A date filter can hide an
    # earlier occurrence if a commit has a skewed committer clock or a row was backfilled.
    commits = git(repo, "log", "--first-parent", "--full-history", "--reverse",
                  "--format=%H%x09%cI", head, "--", DATA_PATH).decode("utf-8").splitlines()
    if not commits or len(commits) > max_commits:
        raise LatencyError("missing Git data history or commit budget exceeded")
    earliest = {}
    scanned = 0
    for entry in commits:
        sha, timestamp = entry.split("\t", 1)
        committed = when(timestamp)
        if committed is None:
            raise LatencyError("Git commit timestamp is unparseable")
        candidates = {}
        for row in snapshot(repo, sha):
            mid = row.get("message_id")
            if type(mid) is int and mid in first and mid not in earliest:
                metadata = (version_key(row), when(row.get("date_utc")),
                            when(row.get("collected_at_utc")), row.get("edit_date_utc"))
                candidates.setdefault(mid, []).append(metadata)
        for mid, versions in candidates.items():
            earliest[mid] = (committed, versions)
        scanned += 1
        if len(earliest) == len(first):
            break
    anomaly = 0
    collector_lags, commit_lags, post_collection = [], [], []
    confirmed_committed_120, impossible_commit_120 = 0, 0
    unknown_commit, invalid_clock, collector_120, collector_over_120 = 0, 0, 0, 0
    for record in first.values():
        sent, seen, key = record["sent"], record["seen"], record["key"]
        mid = key[0]
        if mid in contradictory or record["edit"] is not None or not sent <= seen <= now:
            invalid_clock += 1
            continue
        commit = earliest.get(mid)
        if not commit:
            unknown_commit += 1
            continue
        stamp, versions = commit
        expected = (key, sent, seen, None)
        if any(version != expected for version in versions):
            anomaly += 1
            continue
        collect_age = (seen-sent).total_seconds()
        collector_lags.append(collect_age)
        if collect_age <= 120:
            collector_120 += 1
        else:
            collector_over_120 += 1
        if not (seen <= stamp <= now):
            anomaly += 1
            continue
        duration = (stamp - sent).total_seconds()
        commit_lags.append(duration)
        post_collection.append((stamp-seen).total_seconds())
        if duration <= 120:
            confirmed_committed_120 += 1
        else:
            impossible_commit_120 += 1
    return {
        "measurement":"OPEN_GAME_git_commit_lower_bound_v1",
        "measured_utc":now.isoformat(),
        "window_hours":hours,
        "history_complete_not_shallow":True,
        "data_commits_scanned":scanned,
        "git_history_scope":"first_parent_of_pinned_head",
        "open_game_first_versions":len(first),
        "collector_within_120_seconds":collector_120,
        "collector_over_120_seconds":collector_over_120,
        "commit_timestamp_within_120_seconds":confirmed_committed_120,
        "commit_timestamp_over_120_seconds":impossible_commit_120,
        "unknown_first_commit":unknown_commit,
        "invalid_or_inconsistent_clock":invalid_clock+anomaly,
        "excluded_or_malformed_records":excluded,
        "collector_age_p50_seconds":percentile(collector_lags,50),
        "collector_age_p95_seconds":percentile(collector_lags,95),
        "commit_age_p50_seconds":percentile(commit_lags,50),
        "commit_age_p95_seconds":percentile(commit_lags,95),
        "post_collection_commit_p95_seconds":percentile(post_collection,95),
        "github_remote_push_timestamps_measured":False,
        "independent_runner_availability_measured":False,
        "live_source_authorized":False,
        "complete_120s_delivery_proven":False,
        "production_data_mutated":False,
        "scientific_state":"INCONCLUSIVO",
        "activation_approved":False,
        "interpretation":(
          "Even a commit within 120s proves neither remote push nor downstream read. "
          "If the earliest commit is over 120s, independent Git delivery via this "
          "data path cannot have met the original 120s capture limit, subject to "
          "clock correctness. Unknown or inconsistent evidence fails closed."),
    }


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--hours",type=int,default=24)
    ap.add_argument("--at",help="Optional UTC timestamp; default current UTC")
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    now=when(args.at) if args.at else datetime.now(UTC)
    if now is None:
        ap.error("--at must include timezone")
    report=study(args.repo,now,hours=args.hours)
    dest=Path(args.output)
    dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open("x",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,sort_keys=True,indent=2)
        f.write("\n")
    print(json.dumps(report,sort_keys=True))


if __name__=="__main__":
    main()
