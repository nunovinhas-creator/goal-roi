"""Day × contract contrasts with pre-FT overlap weights and missing-FT bounds."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import comb, fsum, log

from .. import event_intelligence as events, parser
from . import dsl, study
from .replay import SETTLEMENT

MIN_ARM = 40
MIN_DAYS = 20
MIN_COVERAGE = .80
MAX_BLOCK_WORK = 300_000
LOG_ODDS_LIMIT = .35
PRE_MINUTES_LIMIT = 360
LIVE_MINUTES_LIMIT = 30
GOALS_LIMIT = 2
COST = .02


def contract(d):
    return d.stratum[:7] if all(x is not None for x in d.stratum[1:5]) and d.stratum[6] is not None else None


def covariates(d):
    odds = dsl.point_odds(d.anchor)
    if odds is None or odds < parser.MIN_ODDS:
        return None
    if d.anchor['phase'] == 'pre':
        if d.kickoff is None:
            return None
        return log(odds), (d.kickoff - d.at).total_seconds() / 60, 0, 0
    scores, minute = d.anchor['score'], d.anchor['minute']
    if scores is None or minute is None:
        return None
    return log(odds), minute, sum(scores), scores[0] - scores[1]


def distance(a, b, phase):
    clock = PRE_MINUTES_LIMIT if phase == 'pre' else LIVE_MINUTES_LIMIT
    scales = LOG_ODDS_LIMIT, clock, GOALS_LIMIT, GOALS_LIMIT
    normalized = tuple(abs(x - y) / s for x, y, s in zip(a, b, scales))
    worst = max(normalized)
    return max(0., 1 - worst)


@dataclass(frozen=True)
class Contrast:
    blocks: list
    counts: dict
    operations: int
    exhausted: bool


def overlap(decisions, budget):
    """Freeze membership and weights using only recorded decision fields."""
    grouped = defaultdict(lambda: ([], []))
    counts = Counter()
    used = set()
    for d in decisions:
        if d.fixture in used:
            raise ValueError('fixture reserved twice')
        used.add(d.fixture)
        if d.state not in ('case', 'control'):
            continue
        counts[d.state] += 1
        key, values = contract(d), covariates(d)
        if key is None or values is None:
            counts['missing_covariates_' + d.state] += 1
            continue
        grouped[key][d.state == 'control'].append((d, values))
    blocks = []
    start_ops = budget.used
    for key, (cases, controls) in sorted(grouped.items(), key=lambda item: dsl.encoded(item[0])):
        if not cases or not controls:
            continue
        wa, wb = [0.] * len(cases), [0.] * len(controls)
        for i, (_, a) in enumerate(cases):
            for j, (_, b) in enumerate(controls):
                if not budget.spend():
                    # A partial overlap matrix is not usable evidence.
                    return Contrast([], dict(counts), budget.used - start_ops, True)
                w = distance(a, b, key[2])
                wa[i] += w
                wb[j] += w
        if any(wa):
            pair = [[(d, w, values) for (d, values), w in zip(rows, weights) if w > 0]
                    for rows, weights in ((cases, wa), (controls, wb))]
            for name, rows in zip(('case', 'control'), pair):
                counts['overlap_' + name] += len(rows)
            blocks.append((key, *pair))
    return Contrast(blocks, dict(counts), budget.used - start_ops, False)


def average(values):
    return fsum(values) / len(values) if values else None


def weighted(pairs):
    return fsum(v * w for v, w in pairs) / fsum(w for _, w in pairs)


def net(roi):
    return roi - COST * max(0., roi) - COST


def sign_tail(n, positive):
    return sum(comb(n, k) for k in range(positive, n + 1)) / 2**n


def median_interval(lowers, uppers, alpha=.05):
    """Exact order-statistic interval, widened by the missing-FT bounds."""
    n = len(lowers)
    lower, upper = sorted(lowers), sorted(uppers)
    ranks = [k for k in range(n // 2 + 1)
             if 2 * sum(comb(n, i) for i in range(k)) <= alpha * 2**n]
    k = max(ranks, default=0)
    return [lower[k - 1], upper[n - k]] if k else [None, None]


def balance(cases, controls):
    names = ('log_point_odds', 'lead_or_live_minutes', 'goals', 'goal_difference')
    return {name: weighted([(v[i], w) for _, w, v in cases]) - weighted([(v[i], w) for _, w, v in controls])
            for i, name in enumerate(names)}


def profile(replay, cohort, contrast, *, end=None):
    """Label fixed day blocks; unfinished windows have no inferential p-value."""
    labels = {d.fixture: replay.label(d) for d in cohort.decisions}
    by_day = defaultdict(list)
    total, complete, effective = Counter(), Counter(), Counter()
    covariate_gaps = defaultdict(list)
    evidence = []
    counterexamples = [dict(d.describe(), outcome=labels[d.fixture]) for d in cohort.decisions
                       if d.state == 'case' and (labels[d.fixture]['payoff'] or 0) < 0][:5]
    for key, cases, controls in contrast.blocks:
        for field, gap in balance(cases, controls).items():
            covariate_gaps[field].append(abs(gap))
        arms = []
        for name, group in zip(('case', 'control'), (cases, controls)):
            seen, low, high = [], [], []
            effective[name] += fsum(w for _, w, _ in group) ** 2 / fsum(w * w for _, w, _ in group)
            for d, w, _ in group:
                total[name] += w
                profit = labels[d.fixture]['profit']
                if profit is not None:
                    complete[name] += w
                    seen.append((net(profit), w))
                    low.append((net(profit), w))
                    high.append((net(profit), w))
                else:
                    low.append((net(-1), w))
                    high.append((net(dsl.point_odds(d.anchor) - 1), w))
            arms.append((weighted(seen) if seen else None, weighted(low), weighted(high)))
        a, b = arms
        nominal = a[0] - b[0] if a[0] is not None and b[0] is not None else None
        by_day[key[0]].append((nominal, a[1] - b[2], a[2] - b[1]))
        if len(evidence) < 5:
            evidence.append(dict(contract=key, case=cases[0][0].describe(), control=controls[0][0].describe()))
    days = [tuple(average([row[i] for row in contrasts if row[i] is not None]) for i in range(3))
            for _, contrasts in sorted(by_day.items())]
    low, high = [row[1] for row in days], [row[2] for row in days]
    daily = [row[0] for row in days if row[0] is not None]
    coverage = {}
    fractions = {}
    for name in ('case', 'control'):
        available = contrast.counts.get(name, 0)
        overlap_count = contrast.counts.get('overlap_' + name, 0)
        coverage[name] = dict(reserved=available, overlap=overlap_count,
                              overlap_fraction=overlap_count / available if available else None,
                              observed_weight_fraction=complete[name] / total[name] if total[name] else None,
                              effective_count=round(effective[name], 2))
        fractions[name] = (coverage[name]['overlap_fraction'] or 0, coverage[name]['observed_weight_fraction'] or 0)
    closed = end is not None and replay.as_of >= end + SETTLEMENT
    reasons = []
    if not closed:
        reasons.append('fixed_window_not_closed')
    if contrast.exhausted or cohort.withheld.get('search_incomplete'):
        reasons.append('incomplete_search')
    if any(effective[name] < MIN_ARM for name in fractions):
        reasons.append('overlap_effective_sample_below_minimum')
    if sum(x != 0 for x in low) < MIN_DAYS:
        reasons.append('informative_kickoff_days_below_minimum')
    if any(weight < MIN_COVERAGE for _, weight in fractions.values()):
        reasons.append('ft_coverage_below_minimum')
    informative = sum(x != 0 for x in low)
    p = sign_tail(informative, sum(x > 0 for x in low)) if informative and not reasons else None
    fingerprint = dsl.digest([(key, [[(d.fixture, d.anchor['message_id'], w) for d, w, _ in rows]
                                    for rows in (cases, controls)]) for key, cases, controls in contrast.blocks])
    reservation = dsl.digest([(d.fixture, d.anchor['message_id'], events.iso(d.at), d.state,
                              [e['record_sha256'] for e in d.witness]) for d in cohort.decisions])
    return dict(arm_eligibility=cohort.eligible, reservation_sha256=reservation, withheld=cohort.withheld,
                overlap_counts=contrast.counts, overlap_sha256=fingerprint, overlap_operations=contrast.operations,
                coverage=coverage, blocks=len(contrast.blocks), days=len(days), informative_days=informative,
                mean_day_difference=average(daily), mean_day_missing_ft_bounds=[average(low), average(high)],
                median_day_95pct_interval=median_interval(low, high),
                average_absolute_covariate_gaps={k: average(v) for k, v in sorted(covariate_gaps.items())},
                results={name: study.summary([labels[d.fixture] for d in cohort.decisions if d.state == name])
                         for name in ('case', 'control')},
                nominal_sign_p=p, insufficiency=reasons, examples=evidence, counterexamples=counterexamples)
