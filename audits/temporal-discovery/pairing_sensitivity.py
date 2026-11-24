"""Why the frozen v1 matching found zero strict pairs (read-only sensitivity analysis).

    PYTHONPATH=. python audits/temporal-discovery/pairing_sensitivity.py <data-dir> > /tmp/pairing.json

Nothing here changes the frozen rule. Relaxed variants are analyses for a
future, separately registered protocol version.
"""
import json
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from scangoal import chronology, event_audit
from scangoal.discovery import engine
from scangoal.discovery.replay import Budget, Replay, stratum

NAMES = ['kickoff_day', 'source', 'phase', 'native_market', 'period', 'line', 'selection',
         'context', 'odds_band', 'point_price', 'prefix_density']
CONTRACT = [1, 2, 3, 4, 5, 6]


def greedy(cases, controls, keep, lag):
    used, n = set(), 0
    for c in sorted(cases, key=lambda d: d.at):
        for k in sorted(controls, key=lambda d: abs(d.at - c.at)):
            if k.fixture in used or (lag is not None and abs(k.at - c.at) > lag):
                continue
            if all(c.stratum[i] == k.stratum[i] for i in keep):
                used.add(k.fixture)
                n += 1
                break
    return n


def candidates(replay):
    _, records = engine.discover(replay)
    tracked = [r for r in records if r.get('track_prospectively')]
    mismatch, distance, rows = Counter(), Counter(), []
    for r in tracked:
        decisions, pairs = replay.decide(r['rule'], Budget(10 ** 9))
        cases = [d for d in decisions if d.state == 'case']
        controls = [d for d in decisions if d.state == 'control']
        full = [(c, k) for c in cases for k in controls if c.stratum == k.stratum]
        for c in cases:
            best = min(controls, key=lambda k: sum(a != b for a, b in zip(c.stratum, k.stratum)))
            diff = [n for n, a, b in zip(NAMES, c.stratum, best.stratum) if a != b]
            distance[len(diff)] += 1
            mismatch.update(diff)
        rows.append(dict(id=r['id'], steps=len(r['rule']['steps']), cases=len(cases), controls=len(controls),
                         strict_pairs=len(pairs), same_stratum_any_time=len(full),
                         same_stratum_within={m: sum(abs(c.at - k.at) <= timedelta(minutes=m) for c, k in full)
                                              for m in (15, 60, 360, 1440)},
                         contract_only_15m=greedy(cases, controls, CONTRACT, timedelta(minutes=15)),
                         contract_only_any_time=greedy(cases, controls, CONTRACT, None),
                         distinct_case_strata=len({d.stratum for d in cases})))
    totals = Counter()
    for row in rows:
        totals.update({k: v for k, v in row.items() if isinstance(v, int) and k != 'steps'})
        totals.update({f'same_stratum_within_{m}m': v for m, v in row['same_stratum_within'].items()})
    return dict(tracked=len(tracked), totals=dict(totals), mismatched_components_vs_nearest_control=dict(mismatch.most_common()),
                components_differing_from_nearest_control=dict(sorted(distance.items())), candidates=rows)


def supply(replay):
    """Upper bound: disjoint same-stratum pairs among each fixture's first quote anchor."""
    first = {}
    for at, e in replay.anchors:
        first.setdefault(e['fixture_id'], (at, e))
    rows = []
    for f, (at, e) in first.items():
        anchor, prefix = replay.prefix(e, at, 21600)
        rows.append((at, stratum(anchor, at, replay.kickoff_known(f, at), prefix)))
    span = (max(a for a, _ in rows) - min(a for a, _ in rows)).total_seconds() / 86400

    def pairs(mask, lag):
        groups = defaultdict(list)
        for at, k in rows:
            groups[tuple(k[i] for i in mask)].append(at)
        n = 0
        for ts in groups.values():
            ts.sort()
            used = set()
            for i, t in enumerate(ts):
                if i in used:
                    continue
                for j in range(i + 1, len(ts)):
                    if j not in used and (lag is None or ts[j] - t < lag):
                        used |= {i, j}
                        n += 1
                        break
        return n
    masks = {'full_v1_stratum': list(range(11)), 'without_density_and_odds_band': [0, 1, 2, 3, 4, 5, 6, 7, 9],
             'contract_and_day': [0, *CONTRACT], 'contract': CONTRACT, 'phase_market_period': [2, 3, 4]}
    lags = {'15m_frozen': timedelta(minutes=15), '60m': timedelta(hours=1), '6h': timedelta(hours=6), 'any': None}
    counts = Counter(k for _, k in rows)
    return dict(first_anchor_fixtures=len(rows), span_days=round(span, 1), distinct_full_strata=len(counts),
                singleton_full_strata=sum(v == 1 for v in counts.values()),
                pairs={m: {l: pairs(mask, lag) for l, lag in lags.items()} for m, mask in masks.items()})


def main():
    data = Path(sys.argv[1] if len(sys.argv) > 1 else 'data')
    as_of = chronology.parse_utc(sys.argv[2] if len(sys.argv) > 2 else '2026-11-24T08:22:00Z')
    replay = Replay(event_audit.build(data, as_of, quote_only=True))
    json.dump(dict(as_of_utc=replay.audit['as_of_utc'], anchors=len(replay.anchors), supply=supply(replay),
                   candidates=candidates(replay)), sys.stdout, indent=1, sort_keys=True)
    print()


if __name__ == '__main__':
    main()
