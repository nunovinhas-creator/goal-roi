"""Three separate questions about the same matched pairs.

1. Prediction: does the antecedent add information beyond the announced
   price? Per decision, residual = won - 1/odds on decisive outcomes.
2. Announced-price performance: net return at the announced price after the
   frozen haircut and fee, paired against the comparator, plus the case
   arm's absolute return.
3. Executable profitability: not measurable offline. There is no fill,
   limit, rejection or executable price; the layer reports only a slippage
   stress and break-even, and its status is always `not_demonstrated`.
"""

from math import fsum

HAIRCUT = .02
FEE = .02
SLIPPAGE = (0., .02, .05, .10)


def net(profit, haircut=HAIRCUT, fee=FEE):
    return profit - haircut * max(0., profit) - fee


def values(label, odds):
    """Per-decision quantities with their missing-FT bounds.

    `label` is Replay.label(decision). Prices are the announced ones.
    Pushes (payoff 0) are not decisive for prediction, but return 0 profit.
    """
    payoff = label['payoff']
    settled = label['status'] == 'settled' and payoff is not None
    implied = 1 / odds
    win_profit, loss_profit = odds - 1, -1.
    if settled:
        profit = payoff * (odds - 1) if payoff > 0 else payoff
        decisive = payoff != 0
        won = 1. if payoff > 0 else 0.
        return dict(settled=True, decisive=decisive, push=payoff == 0,
                    prediction=(won - implied,) * 3 if decisive else None,
                    announced=(net(profit),) * 3, profit=profit, odds=odds, payoff=payoff)
    # Unknown FT: observed is missing, bounds are loss and full win.
    return dict(settled=False, decisive=None, push=None,
                prediction=(None, -implied, 1 - implied),
                announced=(None, net(loss_profit), net(win_profit)), profit=None, odds=odds, payoff=None)


def pair_difference(case, control, layer):
    """Observed, lower and upper bounds of case minus control."""
    a, b = case[layer], control[layer]
    if a is None or b is None:
        return None
    observed = a[0] - b[0] if a[0] is not None and b[0] is not None else None
    return dict(observed=observed, lower=a[1] - b[2], upper=a[2] - b[1])


def slippage_net(row, slip):
    if row['profit'] is None:
        return None
    odds = 1 + (row['odds'] - 1) * (1 - slip)
    profit = row['payoff'] * (odds - 1) if row['payoff'] > 0 else row['payoff']
    return net(profit)


def executable(rows):
    """Stress the case arm only; never a claim of executable profit."""
    complete = [r for r in rows if r['profit'] is not None]
    stress = {}
    for slip in SLIPPAGE:
        nets = [slippage_net(r, slip) for r in complete]
        stress[f'{slip:.2f}'] = fsum(nets) / len(nets) if nets else None

    def mean_at(slip):
        return fsum(slippage_net(r, slip) for r in complete) / len(complete)

    break_even = None
    if complete:
        if mean_at(0.) <= 0:
            break_even = 0.
        elif mean_at(1.) > 0:
            break_even = 1.
        else:
            lo, hi = 0., 1.
            for _ in range(60):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if mean_at(mid) > 0 else (lo, mid)
            break_even = lo
    return dict(status='not_demonstrated', reason='no_execution_evidence_offline',
                price_basis='announced_only', case_mean_net_by_price_degradation=stress,
                break_even_price_degradation=break_even,
                note='Degradação aplicada ao lucro bruto (odd-1). Não mede limites, rejeições nem a odd executável.')
