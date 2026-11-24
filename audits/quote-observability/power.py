"""Planning power from observed v3.1 pairs and their receipt cadence."""

from dataclasses import dataclass
from datetime import date, timedelta
from math import fsum, log
from random import Random

from power_v3 import ALPHA, SEED, label, rate

from scangoal.discovery.v3 import inference, layers, protocol

EFFECTS = (4, 8, 12)
TARGET = .8


@dataclass(frozen=True)
class PairSample:
    observed_day: date
    kickoff_day: date
    source: str
    competition: str
    odds: tuple[float, float]
    clocks: tuple[float, float]
    # Decisive stake, or None when a settled payoff was unavailable.
    stakes: tuple[float | None, float | None]


def _projection(samples: tuple[PairSample, ...], first: date, span: int, days: int) -> list:
    """Repeat the receipt-day schedule, including observed days with no pairs."""
    rows = []
    for shift in range(0, days, span):
        for sample in samples:
            if (sample.observed_day - first).days + shift >= days:
                continue
            kickoff = sample.kickoff_day + timedelta(days=shift)
            clusters = dict(kickoff_week=kickoff.isocalendar()[:2], kickoff_day=kickoff,
                            source=sample.source, competition=sample.competition)
            rows.append((sample, clusters))
    return rows


def _evaluate(rows: list, pp: int, repetitions: int) -> dict:
    rng = Random(f'{SEED}/observed-pairs/{pp}')
    retained = {name: [] for name in ('prediction', 'announced')}
    for _ in range(repetitions):
        simulated = []
        for sample, clusters in rows:
            arms = []
            for index, (odds, stake) in enumerate(zip(sample.odds, sample.stakes)):
                probability = min(1., 1 / odds + (pp / 100 if index == 0 else 0))
                outcome = label(rng, probability, float(stake == 0), float(stake is None))
                if outcome['payoff'] is not None:
                    outcome['payoff'] *= stake
                arms.append(layers.values(outcome, odds))
            case_x, control_x = zip(map(log, sample.odds), sample.clocks)
            simulated.append((arms, clusters, case_x, control_x))
        for name in retained:
            fitted = [(x, control[name][0]) for (_, control), _, _, x in simulated
                      if control[name] is not None and control[name][0] is not None]
            slopes = inference.linear_fit(*zip(*fitted)) if fitted else None
            pairs = []
            for arms, clusters, case_x, control_x in simulated:
                if (value := layers.pair_difference(*arms, name)) is None:
                    continue
                shift = fsum(s * (x - y) for s, x, y in zip(slopes or (), case_x, control_x))
                pairs.append(dict(observed=None if value['observed'] is None else value['observed'] - shift,
                                  lower=value['lower'] - shift, upper=value['upper'] - shift, clusters=clusters))
            result = inference.assess(pairs, protocol.SETTINGS)
            survived = (result['p'] is not None and result['p'] <= ALPHA
                        and result['robust_leave_one_out'] and result['mean_bounds'][0] > 0)
            if survived and name == 'announced':
                absolute = inference.cluster_t([arms[0][name][1] for arms, *_ in simulated],
                                                [clusters['kickoff_week'] for _, clusters, *_ in simulated])
                low = absolute['interval'][0]
                survived = low is not None and low > 0
            retained[name].append(survived)
    return {name: rate(flags) for name, flags in retained.items()}


def calculate(samples: tuple[PairSample, ...], first: date | None, span: int,
              horizons: tuple[int, ...], repetitions: int) -> dict:
    """Project one hypothesis/family. Never pool repeated pairs across rules."""
    if span < 0 or repetitions <= 0 or not horizons or min(horizons) <= 0:
        raise ValueError('invalid power simulation settings')
    if samples and (first is None or span == 0 or
                    any(not 0 <= (sample.observed_day - first).days < span for sample in samples)):
        raise ValueError('pair observations must fall inside the exposure window')
    per_day = len(samples) / span if span else None
    effects = [dict(effect_pp=pp, horizons=[], first_tested_days_at_80pct=dict(prediction=None, announced=None))
               for pp in EFFECTS]
    first_sample_gate = None
    for days in sorted(set(horizons)):
        rows = _projection(samples, first, span, days) if samples else []
        weeks = len({clusters['kickoff_week'] for _, clusters in rows})
        passes = len(rows) >= protocol.SETTINGS.min_pairs and weeks >= protocol.SETTINGS.min_clusters
        if passes and first_sample_gate is None:
            first_sample_gate = days
        for effect in effects:
            estimate = _evaluate(rows, effect['effect_pp'], repetitions) if passes else None
            # A deterministic sample/cluster gate makes retention impossible at
            # this horizon, even before simulated outcomes or missing FT.
            if estimate is None:
                estimate = {name: dict(rate=0., wilson_95=None) for name in ('prediction', 'announced')}
            for name, metrics in estimate.items():
                target_days = effect['first_tested_days_at_80pct']
                if target_days[name] is None and metrics['rate'] >= TARGET:
                    target_days[name] = days
            effect['horizons'].append(dict(days=days, projected_pairs=len(rows), projected_weeks=weeks,
                                           minimum_sample_and_weeks=passes, layers=estimate))
    return dict(observed_pairs=len(samples), exposure_days=span, pairs_per_day=per_day,
                rate_equivalent_days_for_minimum_pairs=(
                    (protocol.SETTINGS.min_pairs * span + len(samples) - 1) // len(samples) if samples else None),
                first_tested_days_passing_sample_and_weeks=first_sample_gate, effects=effects)
