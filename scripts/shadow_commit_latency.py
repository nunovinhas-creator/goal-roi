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
MAX_COMMITS = 400
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
            ["git", "-C", str(repo), *args], timeout=30, capture_output=True,
            check=True)
        return process.stdout
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise LatencyError("offline Git history unavailable: " + str(args[0])) from exc


def version_key(item):
    mid = item.get("message_id")
    txt = item.get("text")
    if type(mid) is not int or not isinstance(txt, str):
        return None
    sha = hashlib.sha256(txt.encode("utf-8")).hexdigest()
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


def percentile(values, percent):
    if not values:
        return None
    values = sorted(values)
    index = min(len(values)-1, max(0, (len(values)*percent+99)//100-1))
    return round(values[index], 3)


def study(repo, now, *, hours=24, max_commits=MAX_COMMITS):
    """No raw identifiers, team names, texts or direct identifiers in output."""
    repo = Path(repo)
    if hours < 1 or hours > 72 or not isinstance(now, datetime) or now.tzinfo is None:
        raise LatencyError("invalid measurement period")
    now = now.astimezone(UTC)
    if git(repo, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise LatencyError("full Git history required, shallow checkout cannot prove first presence")
    if git(repo, "status", "--porcelain", "--", DATA_PATH).strip():
        raise LatencyError("source data has uncommitted changes")
    raw = git(repo, "show", "HEAD:"+DATA_PATH)
    rows = _read_rows(raw)
    start = now-timedelta(hours=hours)
    first = {}
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
        # First version collected per ID. Do not substitute later edits silently.
        old = first.get(mid)
        if old is None or seen < old["seen"]:
            first[mid] = {"key":key, "sent":sent, "seen":seen}
    # Include a one-hour baseline; greater history cannot be silently ignored.
    since = (start-timedelta(hours=1)).isoformat()
    commits = git(repo, "log", "--reverse",
                  "--format=%H%x09%cI", "--since="+since,
                  "--", DATA_PATH).decode("utf-8").splitlines()
    if len(commits) > max_commits:
        raise LatencyError("too many commits; expand analysis budget explicitly")
    if not commits:
        raise LatencyError("no Git data commits within observation window")
    keys = {r["key"] for r in first.values()}
    first_commit = {}
    anomaly = 0
    for entry in commits:
        parts = entry.split("\t", 1)
        if len(parts)!=2 or len(parts[0])!=40:
            raise LatencyError("invalid Git history record")
        sha, timestamp = parts
        committed = when(timestamp)
        if committed is None:
            raise LatencyError("Git commit timestamp is unparseable")
        raw_commit = git(repo, "show", sha+":"+DATA_PATH)
        for row in _read_rows(raw_commit):
            key = version_key(row)
            if key in keys and key not in first_commit:
                first_commit[key] = (sha, committed)
    collector_lags, commit_lags, post_collection = [], [], []
    confirmed_committed_120, impossible_commit_120 = 0, 0
    unknown_commit, invalid_clock, collector_120, collector_over_120 = 0, 0, 0, 0
    for record in first.values():
        sent, seen, key = record["sent"], record["seen"], record["key"]
        if seen < sent or seen > now:
            invalid_clock += 1
            continue
        collect_age = (seen-sent).total_seconds()
        collector_lags.append(collect_age)
        if collect_age <= 120:
            collector_120 += 1
        else:
            collector_over_120 += 1
        commit = first_commit.get(key)
        if not commit:
            unknown_commit += 1
            continue
        stamp = commit[1]
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
        "data_commits_scanned":len(commits),
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
