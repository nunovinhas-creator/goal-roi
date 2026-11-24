"""End-to-end calibration of v3 on independent synthetic event worlds.

Each repetition builds a fresh world (world.py), replays it through the
production v3 code (risk set -> layers -> inference -> gates) and compares:

* v3 retention per layer (nominal p <= alpha and all gates);
* a naive unmatched comparison in the spirit of v2: all case decisions
  against fixtures without the antecedent at their first quote, kickoff-week
  sign-flip on the weekly difference of residual means;
* coverage of the v3 95% interval for the true conditional estimand of the
  matched pairs, known from the generator's win probabilities.
"""

import argparse
from collections import defaultdict
from dataclasses import asdict, replace
import json
from math import fsum
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import world  # noqa: E402
from power_v3 import ALPHA, rate  # noqa: E402
from scangoal.discovery import dsl  # noqa: E402
from scangoal.discovery.replay import Decision  # noqa: E402
from scangoal.discovery.v3 import engine, inference  # noqa: E402


def naive(replay, w, cohort):
    """v2-like: no instant, no exact comparator; residual of first-completion cases vs never-exposed first quotes."""
    cases = {d.fixture: d for d in cohort.cases}
    weeks = defaultdict(lambda: ([], []))
    first = {}
    for at, event in replay.anchors:
        if event['source'] == 'Stake' and event['fixture_id'] not in first:
            first[event['fixture_id']] = event
    for fixture, truth in w.truth.items():
        label = None
        if fixture in cases:
            d = cases[fixture]
            odds = dsl.point_odds(d.anchor)
            label, arm = replay.label(d), 0
        elif not truth['exposed'] and fixture in first:
            event = first[fixture]
            odds = dsl.point_odds(event)
            label = replay.label(Decision(event, replay.as_of, None, 'control', (), ()))
            arm = 1
        if label is None or label['payoff'] in (None, 0) or odds is None:
            continue
        week = fixture.split('-')[2]
        weeks[int(week) // 7][arm].append((label['payoff'] > 0) - 1 / odds)
    sums = [fsum(a) / len(a) - fsum(b) / len(b) for a, b in weeks.values() if a and b]
    return inference.sign_flip_p(sums), (fsum(sums) / len(sums) if sums else None)


def retained(layer):
    """Single hypothesis, both layers: the gates of engine.decide at the Holm/Bonferroni bound ALPHA."""
    return (layer['p'] is not None and layer['p'] <= ALPHA and layer['robust_leave_one_out']
            and layer['mean_bounds'][0] is not None and layer['mean_bounds'][0] > 0)


def repetition(scenario):
    replay, w = world.build(scenario)
    evidence, cohort = engine.assess(replay, world.RULE)
    # Target: the antecedent's causal increment on the matched cases (ATT), known
    # to the generator. The raw matched difference also carries residual
    # price/clock imbalance, which the protocol's bias correction removes.
    truth = [w.truth[c.fixture]['increment'] for c, _, _ in cohort.pairs]
    target = fsum(truth) / len(truth) if truth else None
    raw = [(w.truth[c.fixture]['p'] - 1 / m['case_odds']) - (w.truth[k.fixture]['p'] - 1 / m['control_odds'])
           for c, k, m in cohort.pairs]
    low, high = evidence['layers']['prediction']['interval_95']
    naive_p, naive_mean = naive(replay, w, cohort)
    return dict(
        pairs=evidence['counts'].get('matched_pairs', 0),
        prediction=retained(evidence['layers']['prediction']),
        prediction_p=evidence['layers']['prediction']['p'],
        announced=retained(evidence['layers']['announced'])
        and (evidence['layers']['announced']['case_absolute']['cluster_t_on_lower_bound']['interval'][0] or -1) > 0,
        target=target, raw_matched=fsum(raw) / len(raw) if raw else None,
        unadjusted=evidence['layers']['prediction']['bias_correction']['mean_unadjusted'], covered=None if low is None or target is None else low <= target <= high,
        estimate=evidence['layers']['prediction']['mean_observed'],
        naive=naive_p is not None and naive_p <= ALPHA and naive_mean > 0, naive_mean=naive_mean,
        later_exposed=evidence['counts'].get('controls_later_exposed', 0),
        log_odds_gap=evidence['balance']['log_odds_gap'],
        same_competition=evidence['balance']['same_competition_fraction'])


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--repetitions', type=int, default=40)
    cli.add_argument('--scenario', action='append', default=[])
    args = cli.parse_args(argv)
    rows = []
    for name, scenario in world.scenarios().items():
        if args.scenario and name not in args.scenario:
            continue
        runs = [repetition(replace(scenario, seed=i)) for i in range(args.repetitions)]

        def avg(key):
            values = [r[key] for r in runs if r[key] is not None]
            return fsum(values) / len(values) if values else None

        rows.append(dict(scenario=asdict(scenario), mean_pairs=avg('pairs'),
                         retention_prediction=rate([r['prediction'] for r in runs]),
                         retention_announced_with_positive_case_return=rate([r['announced'] for r in runs]),
                         naive_unmatched_retention=rate([r['naive'] for r in runs]),
                         mean_naive_difference=avg('naive_mean'),
                         mean_true_increment_on_matched_cases=avg('target'),
                         mean_true_unadjusted_matched_difference=avg('raw_matched'),
                         mean_v3_unadjusted_estimate=avg('unadjusted'), mean_v3_estimate=avg('estimate'),
                         interval_coverage_of_true_increment=rate(
                             [r['covered'] for r in runs if r['covered'] is not None]),
                         mean_controls_later_exposed=avg('later_exposed'), mean_log_odds_gap=avg('log_odds_gap'),
                         mean_same_competition=avg('same_competition')))
    print(json.dumps(dict(alpha=ALPHA, repetitions=args.repetitions, rule=world.RULE, rows=rows,
                          verdict=engine.VERDICT), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
