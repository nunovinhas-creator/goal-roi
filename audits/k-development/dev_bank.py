"""Development-only banks of the algorithm author (role C). Never test banks.

Written and frozen before any K variant exists. Four families (two pre-match,
two live) whose near misses include shared-unknown pairs (a masked or
incomparable value next to a reversed value), long trajectories and frequent
decoys. These are the failure types the J post-hoc diagnosis names. The author
knows the hypotheses, so these banks favour K by construction and count only
as development evidence.

The event machinery is a private copy of the frozen PR J bank builder, loaded
into a separate namespace with its own families, start date and RNG salt.
bank_j.py itself is not modified.
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from random import Random

_SOURCE = Path(__file__).resolve().parents[1] / 'selective-abstention/bank_j.py'
_spec = importlib.util.spec_from_file_location('goal_roi_k_dev_bank_engine', _SOURCE)
_engine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_engine)
_price, _late, key, claim = _engine._price, _engine._late, _engine.key, _engine.claim

MINING_SEEDS = (7_314_027, 7_421_903)
VALIDATION_SEEDS = (8_102_511, 8_276_389, 8_390_047)


def _liquidity_fade(event, rng, s, variant):
    p = rng.uniform(1.95, 2.55) * s['price']
    v0 = int(rng.randrange(15_000, 45_000) * s['volume'])
    a, b = int(rng.randrange(8_000, 20_000) * s['volume']), int(rng.randrange(25_000, 60_000) * s['volume'])
    middle = p - .12 if variant == 'fade_middle_moves' else p
    close = p + .14 if variant == 'fade_price_rises' else p - rng.uniform(.18, .32)
    final = v0 + a - int(3_000 * s['volume']) if variant == 'fade_volume_shrinks' else v0 + a + b
    rows = [
        event(-38, volume_cumulative_usd=v0, **_price(p)),
        event(-31, 'alert', source='Polymarket', share_pct=rng.uniform(25, 60)),
        event(-24, volume_cumulative_usd=v0 + a, **_price(middle)),
        event(-17, 'revision', source='ScanGoal'),
        event(-9, 'alert', source='Polymarket' if variant == 'fade_same_alert_source' else 'Betfair'),
        event(0, volume_cumulative_usd=final, period='first_half' if variant == 'fade_terminal_half' else 'regulation',
              **_price(close)),
    ]
    if variant == 'fade_late_alert':
        _late(rows[4], rows[-1])
    extra = [dict(rows[-1], volume_cumulative_usd=v0)] if variant == 'fade_volume_masked' else []
    return rows, extra


def _ticket_climb(event, rng, s, variant):
    p = rng.uniform(2.10, 2.90) * s['price']
    t0 = int(rng.randrange(3_000, 9_000) * s['volume'])
    c0 = rng.randrange(15, 60)
    unit = 'trades' if variant == 'climb_unit_mismatch' else 'bets'
    last_count = c0 + 5 if variant == 'climb_counts_reverse' else c0 + rng.randrange(30, 90)
    close = p + .15 if variant == 'climb_price_rises' else p - rng.uniform(.20, .40)
    rows = [
        event(-44, 'alert', source='Stake', direction='over'),
        event(-36, biggest_ticket_usd=t0, n_bets=c0, count_unit='bets', **_price(p)),
        event(-27, 'alert', source='Betfair', direction='under' if variant == 'climb_direction_flips' else 'over'),
        event(-18, biggest_ticket_usd=t0 * 2, n_bets=c0 + 12, count_unit='bets', **_price(p - .05)),
        event(-10, 'revision', source='Polymarket'),
        event(-4, biggest_ticket_usd=t0 * 3, n_bets=c0 + 20, count_unit='bets', **_price(p - .09)),
        event(0, biggest_ticket_usd=t0 // 2 if variant == 'climb_ticket_drops' else t0 * 4, n_bets=last_count,
              count_unit=unit, **_price(close)),
    ]
    if variant == 'climb_late_revision':
        _late(rows[4], rows[-1])
    return rows, []


def _equaliser_drift(event, rng, s, variant):
    p = rng.uniform(1.85, 2.40) * s['price']
    v = int(rng.randrange(25_000, 70_000) * s['volume'])
    after = [1, 2] if variant == 'drift_away_lead' else [1, 1]
    gain = 0 if variant == 'drift_volume_flat' else int(rng.randrange(20_000, 50_000) * s['volume'])
    close = p - .15 if variant == 'drift_price_shortens' else p + rng.uniform(.25, .45)
    rows = [
        event(-22, score=[1, 0], volume_cumulative_usd=v, **_price(p)),
        event(-17, 'alert', source='Polymarket', score=[1, 0], volume_usd=int(rng.randrange(10_000, 40_000) * s['volume'])),
        event(-14, 'goal', score=after),
        event(-12, 'score', score=[1, 0] if variant == 'drift_score_lags' else after),
        event(-6, score=after, volume_cumulative_usd=v + gain // 2, **_price(p + .1)),
        event(0, score=after, volume_cumulative_usd=v + gain, **_price(close)),
    ]
    if variant == 'drift_late_goal':
        _late(rows[2], rows[-1])
    extra = [dict(rows[-1], volume_cumulative_usd=v // 3)] if variant == 'drift_volume_masked' else []
    return rows, extra


def _venue_pressure(event, rng, s, variant):
    p = rng.uniform(2.20, 2.80) * s['price']
    score = [0, 0]
    first = int(rng.randrange(10_000, 30_000) * s['volume'])
    second = first // 3 if variant == 'pressure_fades' else first * 3
    close = p + .2 if variant == 'pressure_price_lengthens' else p - rng.uniform(.25, .45)
    rows = [
        event(-19, score=score, **_price(p)),
        event(-15, 'alert', source='Betfair', score=score, volume_usd=first),
        event(-11, 'revision', source='Betfair', score=score),
        event(-7, 'alert', source='Betfair' if variant == 'pressure_one_venue' else 'Stake', score=score,
              volume_usd=second),
        event(-3, 'score', score=[0, 1] if variant == 'pressure_goal_scored' else score),
        event(0, score=score, period='first_half' if variant == 'pressure_terminal_half' else 'regulation',
              **_price(close)),
    ]
    if variant == 'pressure_late_second_alert':
        _late(rows[3], rows[-1])
    return rows, []


FAMILIES = (
    ('kdev_pre_liquidity_fade', 'pre', _liquidity_fade,
     ('fade_volume_masked', 'fade_volume_shrinks', 'fade_price_rises', 'fade_same_alert_source', 'fade_late_alert',
      'fade_middle_moves', 'fade_terminal_half')),
    ('kdev_pre_ticket_climb', 'pre', _ticket_climb,
     ('climb_unit_mismatch', 'climb_counts_reverse', 'climb_price_rises', 'climb_direction_flips',
      'climb_ticket_drops', 'climb_late_revision')),
    ('kdev_live_equaliser_drift', 'live', _equaliser_drift,
     ('drift_away_lead', 'drift_volume_flat', 'drift_volume_masked', 'drift_price_shortens', 'drift_score_lags',
      'drift_late_goal')),
    ('kdev_live_venue_pressure', 'live', _venue_pressure,
     ('pressure_fades', 'pressure_price_lengthens', 'pressure_one_venue', 'pressure_goal_scored',
      'pressure_terminal_half', 'pressure_late_second_alert')),
)


def _nuisance(index, seed, fresh):
    rng = Random(f'goal-roi-k-dev-nuisance:{index}:{seed}:{int(fresh)}')
    return dict(distractor=round(rng.uniform(.1, .95), 3), late_revision=round(rng.uniform(0, .5), 3),
                background=rng.randrange(0, 9),
                price=round(rng.uniform(.85, 1.25), 3) if fresh else 1.0,
                volume=round(rng.choice([.5, .8, 1.5, 3.0]), 3) if fresh else 1.0,
                pause=rng.choice([0, 3, 7, 15]) if fresh else rng.choice([0, 2]))


_engine.FAMILIES = FAMILIES
_engine.START = datetime(2063, 5, 11, 15, tzinfo=timezone.utc)
_engine.nuisance = _nuisance
build, transformed, Bank = _engine.build, _engine.transformed, _engine.Bank
