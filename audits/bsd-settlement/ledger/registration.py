"""Reproduce a real prospective append across the BSD code change, offline."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from replay import git_blob, sha256

DATA = (
    "messages.jsonl", "details_hl.json", "results_hl.json", "results_bsd.json", "state.json"
)
LEDGER = "prospective_ledger.jsonl"
WORKER = """
import json
import sys
from datetime import datetime
from scangoal import prospective

def deny_network(event, _arguments):
    if event.startswith('socket.'):
        raise RuntimeError('Network is unavailable in this replay')

sys.addaudithook(deny_network)
now = datetime.fromisoformat(sys.argv[1])
result = prospective.record(now)
rows = prospective.load()
assert prospective.verify(rows)
assert prospective.record(now) == 0
assert prospective.load() == rows
print(json.dumps({"signals_added": result, "chain_verified": True,
                  "conflicting_games": prospective.summary(rows)["games_with_conflicting_results"]}))
"""


def read_entries(raw):
    return [json.loads(line) for line in raw.splitlines()]


def strip_chain(row):
    return {key: value for key, value in row.items() if key not in {"seq", "sha256", "prev_sha256"}}


def run(source, inputs, ledger, now):
    with tempfile.TemporaryDirectory(prefix="goal-roi-bsd-registration-") as temporary:
        isolated = Path(temporary)
        for name, contents in inputs.items():
            (isolated / name).write_bytes(contents)
        ledger_path = isolated / LEDGER
        ledger_path.write_bytes(ledger)
        env = dict(
            os.environ, PYTHONDONTWRITEBYTECODE="1",
            PYTHONPATH=str(source.resolve()), SCANGOAL_DATA_DIR=str(isolated),
        )
        result = json.loads(subprocess.check_output(
            [sys.executable, "-c", WORKER, now.isoformat()], cwd=isolated, env=env
        ))
        assert all((isolated / name).read_bytes() == contents for name, contents in inputs.items())
        after = ledger_path.read_bytes()
        assert after.startswith(ledger)
        return after, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", type=Path)
    parser.add_argument("baseline_checkout", type=Path)
    parser.add_argument("candidate_checkout", type=Path)
    parser.add_argument("future_revision")
    args = parser.parse_args()
    baseline_revision = subprocess.check_output(
        ["git", "-C", str(args.baseline_checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    candidate_revision = subprocess.check_output(
        ["git", "-C", str(args.candidate_checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    before = git_blob(args.history, baseline_revision, f"data/{LEDGER}")
    documented = git_blob(args.history, args.future_revision, f"data/{LEDGER}")
    assert documented.startswith(before)
    recorded = read_entries(documented[len(before):])
    assert [row["kind"] for row in recorded] == ["signal", "signal"]
    now = datetime.fromisoformat(recorded[0]["at_utc"])
    assert {row["at_utc"] for row in recorded} == {now.isoformat()}
    inputs = {name: git_blob(args.history, args.future_revision, f"data/{name}") for name in DATA}

    baseline, old = run(args.baseline_checkout, inputs, before, now)
    candidate, new = run(args.candidate_checkout, inputs, before, now)
    baseline_append = read_entries(baseline[len(before):])
    candidate_append = read_entries(candidate[len(before):])
    assert baseline == documented
    assert [row["kind"] for row in candidate_append] == ["code_version", "signal", "signal"]
    assert list(map(strip_chain, candidate_append[1:])) == list(map(strip_chain, baseline_append))
    assert old == new == {"signals_added": 2, "chain_verified": True, "conflicting_games": 0}
    assert all(not row["result_known_at_registration"] for row in candidate_append[1:])
    patch_manifest = candidate_append[0]["code_sha256"]
    old_manifest = next(row["code_sha256"] for row in reversed(read_entries(before)) if "code_sha256" in row)
    assert set(patch_manifest) == set(old_manifest)
    assert [name for name in patch_manifest if patch_manifest[name] != old_manifest[name]] == ["results_bsd.py"]
    for name, expected in patch_manifest.items():
        assert sha256(git_blob(args.candidate_checkout, candidate_revision, f"scangoal/{name}")) == expected

    print(json.dumps({
        "baseline_revision": baseline_revision,
        "candidate_revision": candidate_revision,
        "production_registration_revision": args.future_revision,
        "recorded_at_utc": now.isoformat(),
        "archived_input_sha256": {name: sha256(contents) for name, contents in inputs.items()},
        "historical_entries": len(read_entries(before)),
        "historical_prefix_preserved": True,
        "baseline_equals_production_ledger_byte_for_byte": True,
        "candidate_registrations_match_baseline_except_chain_fields": True,
        "candidate_append": [
            {"kind": row["kind"], "seq": row["seq"], "sha256": row["sha256"]}
            for row in candidate_append
        ],
        "baseline_ledger_sha256": sha256(baseline),
        "candidate_ledger_sha256": sha256(candidate),
        "idempotent_same_cut": True,
        "both_runs": old,
        "real_documented_signals": [
            {key: row[key] for key in ("key", "match_id", "message_id", "signal_seen_utc",
                                       "result_known_at_registration")}
            for row in recorded
        ],
        "provider_requests": 0,
        "outcomes_or_conflicts_added": 0,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
