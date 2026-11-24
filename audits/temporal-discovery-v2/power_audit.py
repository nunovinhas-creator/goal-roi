"""Monte Carlo calibration of the frozen v2 tests and audit-only alternatives."""

import argparse
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
import json
from math import comb, fsum, log, sqrt
from random import Random
from statistics import mean

from scangoal.discovery import blocks_v2 as blocks, study

ALPHA = .025  # First publication wave.
FAMILY = 7
SEED = 602410


@dataclass(frozen=True)
class Scenario:
    name: str
    days: int = 90
    per_arm: int = 12
    delta: float = 0.
    odds: float = 2.1
    price_gap: float = 0.
    price_missing: float = 0.
    ft_missing: float = 0.
    selective_ft: float = 0.
    cost: float = blocks.COST
    block_days: int = 1
    arm_shock: float = 0.
    hidden_case_advantage: float = 0.


def scenarios():
    base = Scenario('null_independent')
    return [
        base,
        replace(base, name='negative_8pp', delta=-.08),
        replace(base, name='positive_4pp', delta=.04),
        replace(base, name='positive_8pp', delta=.08),
        replace(base, name='positive_12pp', delta=.12),
        replace(base, name='positive_8pp_20d', delta=.08, days=20),
        replace(base, name='positive_8pp_45d', delta=.08, days=45),
        *(replace(base, name=f'positive_8pp_{n}_per_arm', delta=.08, per_arm=n) for n in (3, 6, 24, 48, 96, 192)),
        replace(base, name='positive_8pp_low_odds', delta=.08, odds=1.55),
        replace(base, name='positive_8pp_high_odds', delta=.08, odds=3.10),
        replace(base, name='positive_8pp_cost_5pct', delta=.08, cost=.05),
        replace(base, name='positive_8pp_quote_50pct', delta=.08, price_missing=.50),
        replace(base, name='positive_8pp_ft_5pct', delta=.08, ft_missing=.05),
        replace(base, name='positive_8pp_ft_20pct', delta=.08, ft_missing=.20),
        replace(base, name='null_selective_missing_losses', selective_ft=.28),
        replace(base, name='null_price_shift_fair', price_gap=.5),
        replace(base, name='null_no_price_overlap', price_gap=1.2),
        replace(base, name='null_temporal_dependence_3d', block_days=3, arm_shock=.18),
        replace(base, name='null_temporal_dependence_9d', block_days=9, arm_shock=.18),
        replace(base, name='positive_8pp_temporal_dependence_3d', delta=.08, block_days=3, arm_shock=.18),
        replace(base, name='null_sequence_hidden_composition', hidden_case_advantage=.12),
        replace(base, name='null_asymmetric_ties', per_arm=1),
    ]


@lru_cache(maxsize=None)
def p_value(n, positive):
    return blocks.sign_tail(n, positive)


@lru_cache(maxsize=None)
def order_rank(n):
    return max((k for k in range(n // 2 + 1)
                if 2 * sum(comb(n, i) for i in range(k)) <= .05 * 2**n), default=0)


def sign(days, *, ties_fail=False):
    n = len(days) if ties_fail else sum(x != 0 for x in days)
    return p_value(n, sum(x > 0 for x in days)) if n else None


def binomial(n, p):
    return [comb(n, k) * p**k * (1 - p)**(n - k) for k in range(n + 1)]


def matched_expected_chance(config):
    baseline, other = config.odds, config.odds + config.price_gap
    return .48 * (1 + (baseline - 1) * (1 - config.cost)) / (1 + (other - 1) * (1 - config.cost))


def priced_arm_distribution(config, odds, chance):
    counts = binomial(config.per_arm, 1 - config.price_missing)
    return [(round((1 + (odds - 1) * (1 - config.cost)) * wins / n, 12), count * prob / (1 - counts[0]))
            for n, count in enumerate(counts) if n and count
            for wins, prob in enumerate(binomial(n, chance)) if prob]


@lru_cache(maxsize=None)
def population_day_median(config):
    """Median under the complete priced population, prior to FT censoring."""
    if config.name == 'null_asymmetric_ties':
        return 0.
    if blocks.distance((log(config.odds), 0, 0, 0), (log(config.odds + config.price_gap), 0, 0, 0), 'pre') == 0:
        return None
    shocks = (-config.arm_shock, config.arm_shock) if config.arm_shock else (0.,)
    weighted = {}
    for shock in shocks:
        control = priced_arm_distribution(config, config.odds, .48 - shock)
        case = priced_arm_distribution(config, config.odds + config.price_gap,
                                       matched_expected_chance(config) + config.delta + config.hidden_case_advantage + shock)
        for ca, pa in case:
            for co, po in control:
                diff = round(ca - co, 11)
                weighted[diff] = weighted.get(diff, 0.) + pa * po / len(shocks)
    mass, passed = sorted(weighted.items()), 0.
    midpoint = .5 * fsum(weighted.values())
    for idx, (value, weight) in enumerate(mass):
        passed += weight
        if abs(passed - midpoint) <= 1e-12:
            return (value + mass[idx + 1][0]) / 2
        if passed > midpoint:
            return value
    raise AssertionError('median requires positive probability mass')


def net_bounds(config, price, n, wins, losses):
    up, down = (price - 1) * (1 - config.cost) - config.cost, -1 - config.cost
    unknown = n - wins - losses
    known = wins * up + losses * down
    observed = wins + losses
    return (known / observed if observed else None, (known + unknown * down) / n, (known + unknown * up) / n)


def price_sample(rng, config, chance, *, case):
    prices, wins, losses = 0, 0, 0
    for _ in range(config.per_arm):
        if config.price_missing and rng.random() < config.price_missing:
            continue
        prices += 1
        won = rng.random() < chance
        missing = config.ft_missing + (config.selective_ft if case and not won else 0.)
        if missing and rng.random() < missing:
            continue
        wins += won
        losses += not won
    return prices, wins, losses


def replicate(rng, config):
    if config.name == 'null_asymmetric_ties':
        # IID days with median exactly 0, positive 25% of days and never negative.
        # This is a calibration probe of the inference kernel, with adequate
        # independent fixtures/FT per day stipulated separately.
        daily = [float(rng.random() < .25) for _ in range(config.days)]
        return finish(daily, daily, daily, [[v] for v in daily], (1., 1.), (1., 1.), (90, 90), 0., 0.)

    ratio = (config.odds, config.odds + config.price_gap)
    weight = blocks.distance((log(ratio[0]), 0, 0, 0), (log(ratio[1]), 0, 0, 0), 'pre')
    # Price gap by itself gives the same expected net return under the null.
    # A causal effect, latent composition and serial shocks are separate.
    control_base = .48
    fair_case = matched_expected_chance(config)
    target = config.delta * (1 + (ratio[1] - 1) * (1 - config.cost))
    low, high, observed, by_cluster = [], [], [], []
    price_counts, effective, weights, proven = ([0, 0] for _ in range(4))
    for day in range(config.days):
        if day % config.block_days == 0:
            shock = rng.choice((-config.arm_shock, config.arm_shock)) if config.arm_shock else 0.
            by_cluster.append([])
        control = price_sample(rng, config, control_base - shock, case=False)
        case = price_sample(rng, config, fair_case + config.delta + config.hidden_case_advantage + shock, case=True)
        for i, row in enumerate((control, case)):
            price_counts[i] += row[0]
        if not (control[0] and case[0] and weight):
            continue
        for i, (row, opposite) in enumerate(((control, case), (case, control))):
            effective[i] += row[0]
            weights[i] += row[0] * opposite[0] * weight
            proven[i] += (row[1] + row[2]) * opposite[0] * weight
        c0, l0, u0 = net_bounds(config, ratio[0], *control)
        c1, l1, u1 = net_bounds(config, ratio[1], *case)
        low.append(l1 - u0)
        high.append(u1 - l0)
        if c0 is not None and c1 is not None:
            observed.append(c1 - c0)
        by_cluster[-1].append(low[-1])
    return finish(low, high, observed, by_cluster, [n / (config.days * config.per_arm) for n in price_counts],
                  [a / b if b else 0. for a, b in zip(proven, weights)], effective, target,
                  population_day_median(config))


def finish(low, high, observed, by_cluster, price, ft, effective, target, median_target):
    eligible = min(effective) >= blocks.MIN_ARM and min(ft) >= blocks.MIN_COVERAGE
    definite = len([x for x in low if x != 0]) >= blocks.MIN_DAYS
    cluster = [mean(v) for v in by_cluster if v]
    rank = order_rank(len(low))
    interval = (sorted(low)[rank - 1], sorted(high)[-rank]) if rank else None

    def covers(value):
        return interval[0] - 1e-10 <= value <= interval[1] + 1e-10 if interval and value is not None else None

    return dict(
        p=sign(low) if eligible and definite else None,
        p_ties_fail=sign(low, ties_fail=True) if eligible and len(low) >= blocks.MIN_DAYS else None,
        p_cluster=sign(cluster, ties_fail=True) if eligible and len(cluster) >= blocks.MIN_DAYS else None,
        p_complete_case=sign(observed) if min(effective) >= blocks.MIN_ARM and len(observed) >= blocks.MIN_DAYS else None,
        coverage=covers(median_target), covers_injected_mean=covers(target),
        width=interval[1] - interval[0] if interval else None,
        positive_lower=bool(low) and mean(low) > 0.,
        positive_complete=bool(observed) and mean(observed) > 0.,
        price=price, ft=ft, effective=effective, day_count=len(low), expected_shift=target,
    )


def wilson(n, successes):
    rate = successes / n
    z = 1.95996398454
    width = z * sqrt(rate * (1 - rate) / n + z**2 / (4 * n**2))
    return [(rate + z**2 / (2 * n) + s * width) / (1 + z**2 / n) for s in (-1, 1)]


def rate(decisions):
    used = [int(d) for d in decisions if d is not None]
    return dict(n=len(used), retained=sum(used), fraction=mean(used) if used else None,
                simulation_95=wilson(len(used), sum(used)) if used else None)


def estimated(config, repetitions):
    rng = Random(SEED)
    return [replicate(rng, config) for _ in range(repetitions)]


def retained(row, key, alpha=ALPHA):
    p = row[key]
    return p is not None and p <= alpha and row['positive_complete' if key == 'p_complete_case' else 'positive_lower']


def summarize(config, data, null):
    rng = Random(SEED + 1)
    families = [[row, *[rng.choice(null) for _ in range(FAMILY - 1)]] for row in data]
    corrected = [study.holm([a['p'] for a in fam]) for fam in families]
    return dict(
        scenario=asdict(config), fixtures_scheduled=config.days * config.per_arm * 2,
        expected_roi_difference=data[0]['expected_shift'],
        admissible_fraction=mean(r['p'] is not None for r in data),
        comparisons={key: rate(retained(row, key) for row in data)
                     for key in ('p', 'p_ties_fail', 'p_cluster', 'p_complete_case')},
        holm_primary=rate(retained(f[0], 'p', ALPHA) and p[0] <= ALPHA for f, p in zip(families, corrected)),
        any_holm_null=rate(any(retained(r, 'p', ALPHA) and p <= ALPHA for r, p in zip(f[1:], adjusted[1:]))
                           for f, adjusted in zip(families, corrected)),
        any_holm_retained=rate(any(retained(r, 'p', ALPHA) and p <= ALPHA for r, p in zip(f, adjusted))
                               for f, adjusted in zip(families, corrected)),
        bonferroni_seven=rate(retained(row, 'p', ALPHA / FAMILY) for row in data),
        interval_coverage=rate(r['coverage'] for r in data),
        interval_covers_injected_mean=rate(r['covers_injected_mean'] for r in data),
        population_complete_day_median=population_day_median(config),
        mean_interval_width=mean([r['width'] for r in data if r['width'] is not None]) if any(
            r['width'] is not None for r in data) else None,
        mean_price_coverage=[mean(row['price'][arm] for row in data) for arm in range(2)],
        mean_ft_weight_coverage=[mean(row['ft'][arm] for row in data) for arm in range(2)],
        mean_effective_fixtures=[mean(row['effective'][arm] for row in data) for arm in range(2)],
        mean_days=mean(r['day_count'] for r in data),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=400)
    parser.add_argument('--scenario', action='append', default=[])
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error('repetitions must be positive')
    baseline = estimated(scenarios()[0], args.repetitions)
    rows = [summarize(cfg, estimated(cfg, args.repetitions), baseline)
            for cfg in scenarios() if not args.scenario or cfg.name in args.scenario]
    print(json.dumps(dict(seed=SEED, repetitions=args.repetitions, alpha=ALPHA, family=FAMILY,
                          rows=rows, verdict=study.VERDICT), allow_nan=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
