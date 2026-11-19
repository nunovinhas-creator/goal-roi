"""Read-only diagnostic of the frozen PR #55 rule on the current Goal-ROI checkout.

Usage (from repository root):
    python -m scripts.opportunity_forward_audit

Never sends Telegram, reads credentials, writes data/, or certifies a strategy.
Output contains only aggregated statistics; no message texts or match identifiers.
"""
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
    found = {mid: list(rows) for mid, rows in observed.items()}
    for filename, source in (("results_hl.json", "highlightly"), ("results_bsd.json", "bsd")):
        doc = read_json(directory / filename, {})
        if not isinstance(doc, dict) or not isinstance(doc.get("results"), dict):
            continue
        for mid, info in doc["results"].items():
            if isinstance(info, dict):
                row = opportunity_results.collected(
                    source, mid, info.get("ft"), info.get("matched_at_utc"),
                    period=info.get("period", "regulation"),
                )
                found.setdefault(mid, []).append(row)
    return {mid: tuple(rows) for mid, rows in found.items()}


def main():
    directory = data_dir()
    protocol = read_json(PROTOCOL, {})
    state = read_json(directory / "notify_state.json", {})
    activation = ((state.get("migrations") or {}).get("polymarket_over25_v1") or {}).get("at_utc")
    observed = {}
    messages, _, _, _, _ = report.load(result_observations=observed)
    results = sourced_results(observed, directory)
    result = opportunity_forward.evaluate(
        messages, results, protocol,
        activation_utc=activation,
        now=datetime.now(timezone.utc),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
