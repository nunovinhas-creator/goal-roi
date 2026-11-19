"""Print a read-only, non-certifying ranking for already registered opportunities.

Run: python -m scripts.opportunity_ranking
Requires only repository-local public data; does not call the network or modify
notifications, reports, the hypothesis registry, CI, or production.
"""
import json
from datetime import datetime, timezone

from scangoal import opportunity_ranking, report
from scangoal.store import data_dir
from scripts.opportunity_forward_audit import PROTOCOL, read_json, sourced_results


def main():
    directory = data_dir()
    protocol = read_json(PROTOCOL, {})
    state = read_json(directory / "notify_state.json", {})
    activation = ((state.get("migrations") or {}).get("polymarket_over25_v1") or {}).get("at_utc")
    observations = {}
    messages, bot_ft, _, _, _ = report.load(observations)
    evidence = sourced_results(bot_ft, observations, directory)
    ranking = opportunity_ranking.build(
        messages, evidence, protocol,
        activation_utc=activation,
        now=datetime.now(timezone.utc),
    )
    print(json.dumps(ranking, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
