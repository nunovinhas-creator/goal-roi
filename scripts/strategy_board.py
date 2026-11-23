"""Print the read-only strategy board (issue #56) from repository-local data.

Run: python -m scripts.strategy_board [--as-of ISO] [--select-shortlist]
Never sends Telegram, calls the network, writes data/, registers hypotheses or certifies anything.
--select-shortlist reproduces the frozen shortlist selection rule on the same clock (audit only).
"""
import argparse
import json
from datetime import datetime, timezone

from scangoal import brain, opportunity_results, report, strategy_board


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--as-of', help='ISO 8601 cutoff with explicit UTC offset')
    parser.add_argument('--select-shortlist', action='store_true')
    args = parser.parse_args()
    now = opportunity_results.utc(args.as_of) if args.as_of else datetime.now(timezone.utc)
    if now is None:
        parser.error('--as-of requires a valid explicit UTC offset')
    full = []
    original = strategy_board.build

    def capture(*a, **k):
        return original(*a, **dict(k, full=full))

    brain.strategy_board.build = capture
    try:
        board = report.build(as_of=now)['brain']['strategy_board']
    finally:
        brain.strategy_board.build = original
    if args.select_shortlist:
        board = {'as_of_utc': now.isoformat(), 'criteria_sha256': board['criteria_sha256'],
                 'selected': [{k: c[k] for k in ('id', 'family', 'phases', 'confirmed', 'roi_odds_minus_2pct',
                                                  'se_day_clustered', 'decision_days')}
                              for c in strategy_board.select_shortlist(full)]}
    print(json.dumps(board, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
