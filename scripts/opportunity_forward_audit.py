"""Read-only diagnostic of the frozen PR #55 rule on the current Goal-ROI checkout.

Usage (from repository root):
    python -m scripts.opportunity_forward_audit

Never sends Telegram, reads credentials, writes data/, or certifies a strategy.
Output contains only aggregated statistics; no message texts or match identifiers.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from scangoal import opportunity_forward, opportunity_results, report
from scangoal.store import data_dir

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "audits/opportunity-lab/polymarket-over25-v1.json"


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def sourced_results(observed, directory):
    """Collect independent, timestamped versions without choosing a score yet."""
    providers = {"highlightly": read_json(directory / "results_hl.json", {}),
                 "bsd": read_json(directory / "results_bsd.json", {})}
    return opportunity_results.merge_sources(observed, providers)


def audit_clock():
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", help="ISO 8601 cutoff with explicit UTC offset")
    args = parser.parse_args()
    now = opportunity_results.utc(args.as_of) if args.as_of is not None else datetime.now(timezone.utc)
    if now is None:
        parser.error("--as-of requires a valid explicit UTC offset")
    return now


def main():
    now = audit_clock()
    directory = data_dir()
    protocol = read_json(PROTOCOL, {})
    state = read_json(directory / "notify_state.json", {})
    activation = ((state.get("migrations") or {}).get("polymarket_over25_v1") or {}).get("at_utc")
    observed = {}
    messages, _, _, _, _ = report.load(result_observations=observed, as_of=now)
    results = sourced_results(observed, directory)
    result = opportunity_forward.evaluate(
        messages, results, protocol,
        activation_utc=activation,
        now=now,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
