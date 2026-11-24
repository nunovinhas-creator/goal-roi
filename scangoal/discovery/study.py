"""Descriptive economics and fixed-window prospective evidence."""

from collections import Counter, defaultdict
from datetime import timedelta
from math import comb, fsum

from .. import event_intelligence as events
from . import dsl
from .replay import SETTLEMENT

WINDOW = timedelta(days=90)
MIN_FORWARD_PAIRS = 50
MIN_FORWARD_DAYS = 20
MIN_COVERAGE = .80
MAX_FORWARD_WORK = 400_000
VERDICT = 'RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.'


def mean(values):
    values = list(values)
    return round(fsum(values) / len(values), 6) if values else None


def summary(labels):
    n = len(labels)
    states = Counter(row['status'] for row in labels)
    payoffs = Counter(row['payoff'] for row in labels if row['payoff'] is not None)
    win, loss = payoffs[1.0] + payoffs[.5], payoffs[-1.0] + payoffs[-.5]
    quoted = [row for row in labels if row['profit'] is not None]
    return dict(fixtures=n, statuses=dict(states), wins=win, losses=loss, pushes=payoffs[0],
                half_wins=payoffs[.5], half_losses=payoffs[-.5], ft_proven=sum(row['ft'] is not None for row in labels),
                price_available=sum(row['priced'] for row in labels), priced_settled=len(quoted),
                price_coverage=round(sum(row['priced'] for row in labels) / n, 6) if n else None,
                hit_rate=round(win / (win + loss), 6) if win + loss else None,
                theoretical_roi=mean(row['profit'] for row in quoted),
                theoretical_roi_with_2pct_winning_haircut_and_002_fee=mean(
                    row['profit'] - .02 * max(row['profit'], 0) - .02 for row in quoted))


def sign_p(daily):
    # The day, rather than the alert or the pair, is the inference unit.
    # Discard exact zero day averages. Sign symmetry and independent days
    # remain assumptions of this sensitivity analysis.
    positive = sum(v > 0 for v in daily)
    negative = sum(v < 0 for v in daily)
    n = positive + negative
    if not n:
        return None
    return min(1., 2 * sum(comb(n, k) for k in range(min(positive, negative) + 1)) / 2**n)


def holm(pvalues):
    adjusted = [1.] * len(pvalues)
    running = 0.
    for rank, index in enumerate(sorted(range(len(pvalues)), key=lambda i: pvalues[i] if pvalues[i] is not None else 1.)):
        p = pvalues[index] if pvalues[index] is not None else 1.
        running = max(running, min(1., (len(pvalues) - rank) * p))
        adjusted[index] = running
    return adjusted


def _example(d, label):
    return dict(d.describe(), outcome=label)


def profile(replay, decisions, pairs, *, start=None, end=None, examples=6):
    labels = {d.fixture: replay.label(d) for d in decisions}
    states = Counter(d.state for d in decisions)
    arms = {name: [labels[d.fixture] for d in decisions if d.state == name] for name in ('case', 'control')}
    pairs_priced, daily = [], defaultdict(list)
    matched_hit_differences = []
    pair_example = []
    paired = {name: [] for name in ('case', 'control')}
    for a, b in pairs:
        case, baseline = labels[a.fixture], labels[b.fixture]
        paired['case'].append(case)
        paired['control'].append(baseline)
        if len(pair_example) < examples:
            pair_example.append(dict(case=_example(a, case), control=_example(b, baseline),
                                     paired_utc=events.iso(max(a.at, b.at))))
        if case['payoff'] and baseline['payoff']:
            matched_hit_differences.append(int(case['payoff'] > 0) - int(baseline['payoff'] > 0))
        if case['profit'] is not None and baseline['profit'] is not None:
            day = (a.kickoff or a.at).date()
            difference = case['profit'] - baseline['profit']
            pairs_priced.append(difference)
            daily[day].append(difference)
    daily_means = [mean(values) for _, values in sorted(daily.items())]
    first_period = last_period = None
    if daily:
        middle = ((start + (end - start) / 2).date() if start is not None else
                  sorted(daily)[len(daily) // 2])
        first_period = mean(mean(v) for day, v in daily.items() if day < middle)
        last_period = mean(mean(v) for day, v in daily.items() if day >= middle)

    # These are bounded real counterexamples, including failed coverage.
    losses = [_example(d, labels[d.fixture]) for d in decisions if d.state == 'case'
              and (labels[d.fixture]['payoff'] or 0) < 0][:examples]
    winning_controls = [_example(d, labels[d.fixture]) for d in decisions if d.state == 'control'
                        and (labels[d.fixture]['payoff'] or 0) > 0][:examples]
    missing = [_example(d, labels[d.fixture]) for d in decisions
               if d.state == 'case' and labels[d.fixture]['profit'] is None][:examples]

    strata = defaultdict(lambda: defaultdict(list))
    for d in decisions:
        if d.state in arms:
            source, phase, market, period = d.stratum[1:5]
            name = ' / '.join(str(x) for x in (source, phase, market, period))
            strata[name][d.state].append(labels[d.fixture])
    windows_closed = end is not None and replay.as_of >= end + SETTLEMENT
    reasons = []
    if len(pairs_priced) < MIN_FORWARD_PAIRS:
        reasons.append('priced_matched_pairs_below_minimum')
    if len([d for d in daily_means if d != 0]) < MIN_FORWARD_DAYS:
        reasons.append('informative_kickoff_days_below_minimum')
    if any(not rows or sum(r['status'] == 'settled' and r['priced'] for r in rows) / len(rows) < MIN_COVERAGE
           for rows in paired.values()):
        reasons.append('matched_arm_coverage_below_minimum')
    if states['search_budget']:
        reasons.append('search_incomplete')
    if not windows_closed:
        reasons.append('fixed_window_not_closed')
    reservation_sha = dsl.digest([(d.fixture, d.anchor['message_id'], events.iso(d.at), d.state,
                                   [e['record_sha256'] for e in d.witness]) for d in decisions])
    pairing_sha = dsl.digest([(a.anchor['message_id'], b.anchor['message_id'], events.iso(max(a.at, b.at)))
                              for a, b in pairs])
    return dict(decisions=dict(states), reservation_sha256=reservation_sha, pairing_sha256=pairing_sha,
                arms={name: summary(rows) for name, rows in arms.items()},
                matched=dict(pairs=len(pairs), priced_pairs=len(pairs_priced),
                             day_blocks=len(daily), average_pair_difference=mean(pairs_priced),
                             average_day_difference=mean(daily_means),
                             hit_difference=mean(matched_hit_differences),
                             first_period_difference=first_period, last_period_difference=last_period,
                             arms={name: summary(rows) for name, rows in paired.items()},
                             sign_test_p=sign_p(daily_means) if not reasons else None,
                             insufficiency=reasons, examples=pair_example),
                strata={name: {arm: summary(rows) for arm, rows in sides.items()}
                        for name, sides in sorted(strata.items())},
                counterexamples=dict(losses=losses, winning_controls=winning_controls, missing=missing))
