"""Read-only diagnostic of the frozen PR #55 rule on the current Goal-ROI checkout.

Usage (from repository root):
    python -m scripts.opportunity_forward_audit

Never sends Telegram, reads credentials, writes data/, or certifies a strategy.
Output contains only aggregated statistics; no message texts or match identifiers.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from scangoal import opportunity_forward, report
from scangoal.store import data_dir

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "audits/opportunity-lab/polymarket-over25-v1.json"


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def sourced_results(bot_ft, observed, directory):
    """Require consistent full-time scores and a provable first observation time."""
    found = {}

    def add(mid, ft, known):
        if isinstance(mid, str) and isinstance(ft, (list, tuple)) and len(ft) == 2:
            found.setdefault(mid, []).append((list(ft), known))

    for mid, ft in bot_ft.items():
        add(mid, ft, (observed.get("ft") or {}).get(mid))
    for filename in ("results_hl.json", "results_bsd.json"):
        doc = read_json(directory / filename, {})
        if not isinstance(doc, dict):
            continue
        for mid, info in (doc.get("results") or {}).items():
            if isinstance(info, dict):
                add(mid, info.get("ft"), info.get("matched_at_utc"))

    out = {}
    for mid, rows in found.items():
        different = {tuple(ft) for ft, _ in rows if all(type(v) is int and v >= 0 for v in ft)}
        if len(different) != 1:
            out[mid] = {"conflict": True}
            continue
        known = sorted((t for _, t in rows if isinstance(t, str)
                        and t.endswith(("+00:00", "Z"))))
        if known:
            out[mid] = {"ft": list(next(iter(different))), "known_utc": known[0]}
        else:
            out[mid] = {"ft": list(next(iter(different))), "known_utc": None}
    return out


def main():
    directory = data_dir()
    protocol = read_json(PROTOCOL, {})
    state = read_json(directory / "notify_state.json", {})
    activation = ((state.get("migrations") or {}).get("polymarket_over25_v1") or {}).get("at_utc")
    observed = {}
    messages, ft, _, _, _ = report.load(observed)
    results = sourced_results(ft, observed, directory)
    result = opportunity_forward.evaluate(
        messages, results, protocol,
        activation_utc=activation,
        now=datetime.now(timezone.utc),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
