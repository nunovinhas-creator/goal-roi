"""Monte Carlo power, size and interval coverage of the v3 inference kernel.

Pairs are generated directly (independently of every event bank) and pass
through the production code path: layers.values -> layers.pair_difference ->
inference.assess -> the protocol gates. Each scenario reports Wilson 95%
intervals for the retention rate, so the simulation error is explicit.
Sensitivity rows compare the frozen v3 decision with alternative statistical
assumptions (day clustering only, cluster t, and the v2 tie-discarding sign
test) on the same draws.
"""

import argparse
from dataclasses import asdict, dataclass, replace
import json
from math import comb, fsum, sqrt
from random import Random

from scangoal.discovery.v3 import inference, layers, protocol

SEED = 20281124
ALPHA = protocol.alpha(1) / 2  # one plan of one hypothesis, two layers: Holm/Bonferroni bound 0.0125
LEAGUES = 10


@dataclass(frozen=True)
class Scenario:
    name: str
    days: int = 90
    per_day: int = 6
    p: float = .48
    delta: float = 0.
    odds: float = 2.05
    push: float = 0.
    week_effect_sd: float = 0.      # exposure effect varies by week, mean `delta`
    league_effect_sd: float = 0.    # exposure effect varies by competition, mean `delta`
    common_week_sd: float = 0.      # shock common to both arms within a week
    ft_missing: float = 0.
    selective_missing: float = 0.   # extra FT loss on case losers only
    case_odds: float = 0.           # 0: same price as the comparator
    case_offset: float = 0.         # fair baseline shift of the case arm at its own price


def scenarios():
    base = Scenario('null_independent')
    return [
        base,
        replace(base, name='null_symmetric_ties_push_40pct', push=.40),
        # Fair prices on both arms but very different odds: the prediction
        # difference has mean exactly zero and an asymmetric distribution.
        replace(base, name='null_skewed_fair_prices_3_00_vs_1_50', p=2 / 3, odds=1.5, case_odds=3.0, case_offset=-1 / 3),
        replace(base, name='null_effect_varies_by_week', week_effect_sd=.15),
        replace(base, name='null_effect_varies_by_competition', league_effect_sd=.15),
        replace(base, name='null_common_week_shock', common_week_sd=.10),
        replace(base, name='null_selective_missing_ft_10pct', selective_missing=.10),
        replace(base, name='negative_8pp', delta=-.08),
        replace(base, name='positive_4pp', delta=.04),
        replace(base, name='positive_8pp', delta=.08),
        replace(base, name='positive_12pp', delta=.12),
        replace(base, name='positive_8pp_12_per_day', delta=.08, per_day=12),
        replace(base, name='positive_8pp_45_days', delta=.08, days=45),
        replace(base, name='positive_8pp_push_40pct', delta=.08, push=.40),
        replace(base, name='positive_8pp_effect_varies_by_week', delta=.08, week_effect_sd=.15),
        replace(base, name='positive_8pp_ft_missing_5pct', delta=.08, ft_missing=.05),
        replace(base, name='positive_8pp_ft_missing_20pct', delta=.08, ft_missing=.20),
        replace(base, name='positive_12pp_ft_missing_5pct', delta=.12, ft_missing=.05),
    ]


def label(rng, p, push, missing):
    if rng.random() < missing:
        return dict(status='missing_ft', payoff=None)
    if push and rng.random() < push:
        return dict(status='settled', payoff=0.)
    return dict(status='settled', payoff=1. if rng.random() < p else -1.)


def expected(config):
    """Population mean pair differences for both layers (complete FT, effects averaged out)."""
    decisive = 1 - config.push
    case_odds = config.case_odds or config.odds
    pc, pk = config.p + config.case_offset + config.delta, config.p

    def gross(p, odds):
        return p * (odds - 1) * (1 - layers.HAIRCUT) - (1 - p)

    return dict(prediction=(pc - 1 / case_odds) - (pk - 1 / config.odds),
                announced=decisive * (gross(pc, case_odds) - gross(pk, config.odds)))


def draw(rng, config):
    leagues = [rng.gauss(0, config.league_effect_sd) if config.league_effect_sd else 0. for _ in range(LEAGUES)]
    pairs = {'prediction': [], 'announced': []}
    effect_week = common = 0.
    for day in range(config.days):
        if day % 7 == 0:
            effect_week = rng.gauss(0, config.week_effect_sd) if config.week_effect_sd else 0.
            common = rng.gauss(0, config.common_week_sd) if config.common_week_sd else 0.
        for _ in range(config.per_day):
            league = rng.randrange(LEAGUES)
            clusters = dict(kickoff_week=day // 7, kickoff_day=day, source='Stake', competition=league)
            shift = config.delta + effect_week + leagues[league]
            p_control = config.p + common
            p_case = min(.99, max(.01, p_control + config.case_offset + shift))
            case_label = label(rng, p_case, config.push, config.ft_missing)
            if case_label['payoff'] == -1. and config.selective_missing and rng.random() < config.selective_missing:
                case_label = dict(status='missing_ft', payoff=None)
            control_label = label(rng, p_control, config.push, config.ft_missing)
            a = layers.values(case_label, config.case_odds or config.odds)
            b = layers.values(control_label, config.odds)
            for name in pairs:
                difference = layers.pair_difference(a, b, name)
                if difference is not None:
                    pairs[name].append(dict(difference, clusters=clusters))
    return pairs


def v2_sign_test(pairs):
    """The defective v2 statistic: day means, zero days discarded, binomial tail."""
    days = {}
    for row in pairs:
        days.setdefault(row['clusters']['kickoff_day'], []).append(row['lower'])
    means = [fsum(v) / len(v) for v in days.values()]
    n = sum(m != 0 for m in means)
    positive = sum(m > 0 for m in means)
    return sum(comb(n, k) for k in range(positive, n + 1)) / 2 ** n if n else None


def evaluate(config, repetitions, settings):
    rng = Random(f'{SEED}/{config.name}')
    target = expected(config)
    rows = {name: dict(v3=[], day_only=[], cluster_t_week=[], v2_sign=[], coverage=[], width=[], admissible=[])
            for name in target}
    for _ in range(repetitions):
        pairs = draw(rng, config)
        for name, items in pairs.items():
            result = inference.assess(items, settings)
            gate = (result['p'] is not None and result['robust_leave_one_out']
                    and result['mean_bounds'][0] is not None and result['mean_bounds'][0] > 0)
            row = rows[name]
            row['admissible'].append(result['p'] is not None)
            row['v3'].append(gate and result['p'] <= ALPHA)
            day = result['tests']['kickoff_day']['sign_flip_p']
            row['day_only'].append(day is not None and day <= ALPHA and result['mean_bounds'][0] > 0)
            ct = result['tests']['kickoff_week']['cluster_t']
            row['cluster_t_week'].append(ct['p'] is not None and ct['p'] <= ALPHA)
            sign = v2_sign_test(items)
            row['v2_sign'].append(sign is not None and sign <= ALPHA)
            low, high = result['interval_95']
            if low is not None:
                row['coverage'].append(low <= target[name] <= high)
                row['width'].append(high - low)
    return {name: dict(expected_mean_difference=target[name],
                       admissible_fraction=rate(row['admissible']),
                       retention_v3=rate(row['v3']),
                       sensitivity=dict(day_clusters_only=rate(row['day_only']),
                                        cluster_t_week=rate(row['cluster_t_week']),
                                        v2_tie_discarding_sign_test=rate(row['v2_sign'])),
                       interval_95_coverage_of_population_mean=rate(row['coverage']) if row['coverage'] else None,
                       mean_interval_width=fsum(row['width']) / len(row['width']) if row['width'] else None)
            for name, row in rows.items()}


def rate(flags):
    n, k = len(flags), sum(bool(f) for f in flags)
    if not n:
        return None
    z = 1.959963984540054
    centre = (k / n + z * z / (2 * n)) / (1 + z * z / n)
    half = z * sqrt(k / n * (1 - k / n) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return dict(n=n, k=k, rate=k / n, wilson_95=[max(0., centre - half), min(1., centre + half)])


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--repetitions', type=int, default=200)
    cli.add_argument('--draws', type=int, default=4000, help='sign-flip Monte Carlo draws beyond 16 clusters')
    cli.add_argument('--scenario', action='append', default=[])
    args = cli.parse_args(argv)
    settings = replace(protocol.SETTINGS, draws=args.draws)
    rows = [dict(scenario=asdict(cfg), layers=evaluate(cfg, args.repetitions, settings))
            for cfg in scenarios() if not args.scenario or cfg.name in args.scenario]
    print(json.dumps(dict(seed=SEED, repetitions=args.repetitions, alpha=ALPHA, draws=args.draws,
                          protocol_sha256=protocol.PROTOCOL_SHA256, rows=rows,
                          verdict='RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.'),
                     ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
