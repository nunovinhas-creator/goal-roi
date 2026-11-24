"""Cluster-level inference for matched case-control differences.

The estimand is the mean paired difference. Tied pairs (difference exactly
zero) stay in the numerator and in the denominator; they are never discarded,
which removes the v2 defect where a sign test conditioned on non-zero days.
Dependence is handled by flipping whole clusters (kickoff week, competition),
never single pairs, and the claim must survive every estimable clustering.
Missing FT is handled by testing the worst-case lower bound.
"""

from collections import defaultdict
from dataclasses import dataclass
from math import exp, fsum, lgamma, log, sqrt
import random

EXACT_CLUSTERS = 16
TOLERANCE = 1e-12


# ---------------------------------------------------------------- t-distribution

def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1., a - 1.
    c, d = 1., 1. - qab * x / qap
    d = 1. / (d if abs(d) > 1e-300 else 1e-300)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1. + aa * d
        d = 1. / (d if abs(d) > 1e-300 else 1e-300)
        c = 1. + aa / c if abs(1. + aa / c) > 1e-300 else 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1. + aa * d
        d = 1. / (d if abs(d) > 1e-300 else 1e-300)
        c = 1. + aa / c if abs(1. + aa / c) > 1e-300 else 1e-300
        delta = d * c
        h *= delta
        if abs(delta - 1.) < 3e-15:
            break
    return h


def _betai(a, b, x):
    if x <= 0:
        return 0.
    if x >= 1:
        return 1.
    front = exp(lgamma(a + b) - lgamma(a) - lgamma(b) + a * log(x) + b * log(1 - x))
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1. - front * _betacf(b, a, 1 - x) / b


def t_sf(t, df):
    """Upper tail P(T > t) of Student's t."""
    tail = .5 * _betai(df / 2., .5, df / (df + t * t))
    return tail if t > 0 else 1. - tail


def t_quantile(q, df):
    lo, hi = -1e3, 1e3
    for _ in range(200):
        mid = (lo + hi) / 2
        if 1 - t_sf(mid, df) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ------------------------------------------------------------------ cluster tests

def cluster_sums(values, labels):
    """Sum of the per-pair statistic within each cluster, in a stable order."""
    sums, sizes = defaultdict(float), defaultdict(int)
    for value, label in zip(values, labels):
        sums[label] += value
        sizes[label] += 1
    order = sorted(sums, key=repr)
    return [sums[k] for k in order], [sizes[k] for k in order]


def _tables(sums):
    tables = []
    for start in range(0, len(sums), 8):
        chunk = sums[start:start + 8]
        table = [0.] * (1 << len(chunk))
        for mask in range(1, len(table)):
            low = mask & -mask
            bit = low.bit_length() - 1
            table[mask] = table[mask ^ low] + chunk[bit]
        tables.append((len(chunk), table))
    return tables


def sign_flip_p(sums, *, draws=20_000, seed=0):
    """One-sided randomization p for H0: cluster sums are symmetric about zero.

    The observed statistic is sum(S_c). A flipped statistic is sum(e_c S_c)
    with e_c in {-1, +1}. Masks pick the clusters that keep their sign, so
    T(mask) = 2 * sum(S_c kept) - total. Exact up to 16 clusters, otherwise
    Monte Carlo with the (1 + hits) / (1 + draws) correction and a fixed seed.
    """
    if not sums:
        return None
    total = fsum(sums)
    tables = _tables(sums)
    threshold = total - TOLERANCE

    if len(sums) <= EXACT_CLUSTERS:
        partial = [0.]
        for width, table in tables:
            partial = [p + t for p in partial for t in table]
        hits = sum(2 * kept - total >= threshold for kept in partial)
        return hits / len(partial)
    rng = random.Random(seed)
    hits = 0
    for _ in range(draws):
        kept = 0.
        for width, table in tables:
            kept += table[rng.getrandbits(width)]
        hits += 2 * kept - total >= threshold
    return (1 + hits) / (1 + draws)


def cluster_t(values, labels, *, level=.95):
    """CR1 cluster-robust mean, standard error, one-sided p and two-sided CI."""
    sums, sizes = cluster_sums(values, labels)
    n, clusters = len(values), len(sums)
    if n == 0 or clusters < 2:
        return dict(estimate=fsum(values) / n if n else None, se=None, df=None, p=None, interval=[None, None])
    estimate = fsum(values) / n
    residual = fsum((s - k * estimate) ** 2 for s, k in zip(sums, sizes))
    se = sqrt(clusters / (clusters - 1) * residual) / n
    df = clusters - 1
    if se == 0:
        p = 0. if estimate > 0 else 1.
        return dict(estimate=estimate, se=0., df=df, p=p, interval=[estimate, estimate])
    q = t_quantile(1 - (1 - level) / 2, df)
    return dict(estimate=estimate, se=se, df=df, p=t_sf(estimate / se, df),
                interval=[estimate - q * se, estimate + q * se])


# ---------------------------------------------------------------- bias correction

def linear_fit(rows, ys):
    """OLS with intercept via normal equations; regressors without variation are dropped."""
    n = len(rows)
    if not n:
        return None
    width = len(rows[0])
    keep = [j for j in range(width) if max(r[j] for r in rows) - min(r[j] for r in rows) > 1e-12]
    if n <= len(keep) + 2:
        keep = []
    design = [[1.] + [r[j] for j in keep] for r in rows]
    k = len(design[0])
    a = [[fsum(d[i] * d[j] for d in design) for j in range(k)] + [fsum(d[i] * y for d, y in zip(design, ys))]
         for i in range(k)]
    for col in range(k):
        pivot = max(range(col, k), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(k):
            if r != col:
                f = a[r][col] / a[col][col]
                a[r] = [x - f * y for x, y in zip(a[r], a[col])]
    beta = [a[i][k] / a[i][i] for i in range(k)]
    slopes = [0.] * width
    for j, b in zip(keep, beta[1:]):
        slopes[j] = b
    return slopes


# -------------------------------------------------------------------- multiplicity

def holm(pvalues):
    adjusted = [None] * len(pvalues)
    present = sorted((p, i) for i, p in enumerate(pvalues) if p is not None)
    running, m = 0., len(pvalues)
    for rank, (p, index) in enumerate(present):
        running = max(running, min(1., (m - rank) * p))
        adjusted[index] = running
    return adjusted


def benjamini_hochberg(pvalues):
    present = sorted((p, i) for i, p in enumerate(pvalues) if p is not None)
    m, adjusted, running = len(pvalues), [None] * len(pvalues), 1.
    for rank in range(len(present), 0, -1):
        p, index = present[rank - 1]
        running = min(running, p * m / rank)
        adjusted[index] = running
    return adjusted


# --------------------------------------------------------------------- the layer

@dataclass(frozen=True)
class Settings:
    clusterings: tuple = ('kickoff_week', 'competition')
    reported: tuple = ('kickoff_day', 'source')
    robustness: tuple = ('source', 'competition')
    min_pairs: int = 60
    min_clusters: int = 8
    min_coverage: float = .90
    draws: int = 20_000
    seed: int = 0


def assess(pairs, settings=Settings()):
    """Test one layer on matched pairs.

    Each pair is a mapping with `lower`, `upper` and `observed` differences
    (observed is None when an FT is missing) and cluster labels under
    `clusters`. The test statistic uses `lower`, the conservative bound.
    """
    lower = [p['lower'] for p in pairs]
    observed = [p['observed'] for p in pairs if p['observed'] is not None]
    n = len(pairs)
    result = dict(pairs=n, complete_pairs=len(observed),
                  tied_pairs=sum(v == 0 for v in observed),
                  mean_observed=fsum(observed) / len(observed) if observed else None,
                  mean_bounds=[fsum(lower) / n if n else None,
                               fsum(p['upper'] for p in pairs) / n if n else None])
    reasons = []
    if n < settings.min_pairs:
        reasons.append('matched_pairs_below_minimum')
    if n and len(observed) / n < settings.min_coverage:
        reasons.append('ft_coverage_below_minimum')
    tests = {}
    for name in (*settings.clusterings, *settings.reported):
        labels = [p['clusters'][name] for p in pairs]
        sums, _ = cluster_sums(lower, labels)
        estimable = len(sums) >= settings.min_clusters
        tests[name] = dict(clusters=len(sums), estimable=estimable,
                           sign_flip_p=sign_flip_p(sums, draws=settings.draws, seed=settings.seed) if sums else None,
                           cluster_t=cluster_t(lower, labels))
    required = [tests[name] for name in settings.clusterings]
    if not required[0]['estimable']:
        reasons.append('primary_clusters_below_minimum')
    usable = [t['sign_flip_p'] for t in required if t['estimable'] and t['sign_flip_p'] is not None]
    # A claim must survive every estimable pre-registered dependence structure.
    p = max(usable) if usable and not reasons else None
    robustness = {}
    for name in settings.robustness:
        groups = defaultdict(list)
        for pair in pairs:
            groups[pair['clusters'][name]].append(pair['lower'])
        if len(groups) < 2:
            robustness[name] = dict(groups=len(groups), scope='single_' + name, minimum_leave_one_out=None)
            continue
        rest = []
        for value in groups:
            remaining = [v for key, rows in groups.items() if key != value for v in rows]
            if remaining:
                rest.append(fsum(remaining) / len(remaining))
        robustness[name] = dict(groups=len(groups), scope='multiple',
                                minimum_leave_one_out=min(rest) if rest else None,
                                by_group={str(k): dict(pairs=len(v), mean_lower=fsum(v) / len(v))
                                          for k, v in sorted(groups.items(), key=lambda kv: repr(kv[0]))})
    robust = all(r['minimum_leave_one_out'] is None or r['minimum_leave_one_out'] > 0 for r in robustness.values())
    # Interval: widest over estimable required clusterings, from the lower
    # bound's lower limit to the upper bound's upper limit (missing FT).
    upper = [p['upper'] for p in pairs]
    lows, highs = [], []
    for index, name in enumerate(settings.clusterings):
        if index and not tests[name]['estimable']:
            continue
        labels = [p['clusters'][name] for p in pairs]
        lows.append(cluster_t(lower, labels)['interval'][0])
        highs.append(cluster_t(upper, labels)['interval'][1])
    interval = ([min(lows), max(highs)] if lows and None not in lows and None not in highs else [None, None])
    result.update(tests=tests, p=p, insufficiency=reasons, robustness=robustness, robust_leave_one_out=robust,
                  interval_95=interval)
    return result
