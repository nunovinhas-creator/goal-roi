"""Independent, evaluator-only synthetic worlds. Labels never leave this boundary."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from random import Random

from scangoal import event_intelligence as events
from scangoal.discovery import dsl
from scangoal.discovery.v32.tape import fixture_key
from scangoal.match_identity import components
from scangoal.observability import contract_key
from scangoal.offline_pilot import books, fixtures

MINUTE = timedelta(minutes=1)
EPOCH = datetime(2043, 6, 12, 15, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Bank:
    observations: tuple
    cutoff: datetime
    truth: dict
    counterexamples: dict


def claim(row):
    goal = row['event_kind'] == 'goal'
    return (row['event_kind'], row['emitted_utc'], None if goal else row.get('source'),
            None if goal else row.get('period'), tuple(row.get('score') or ()), tuple(row.get('odds') or ()))


def key(fixture, quote):
    return fixture, quote['available_utc'], dsl.digest(contract_key(quote | {'fixture_id': fixture}))


def _price(value):
    price = f'{value:.2f}'
    return dict(odds=[price, price], price_low=float(price), price_high=float(price))


def _pre_reversal(emit, rng, shift, negative):
    start = rng.uniform(1.78, 2.06) + shift
    up = start + rng.uniform(.21, .37)
    down = up - rng.uniform(.24, .45)
    base = rng.randrange(12_000, 26_000)
    final = base + rng.randrange(36_000, 67_000)
    if negative == 'terminal_rise':
        down = up + rng.uniform(.14, .32)
    if negative == 'initial_fall':
        up, down = start - .20, start - .29
    if negative == 'flat_cumulative_volume':
        final = base - 900
    position = 'first_half' if negative == 'wrong_opening_period' else 'regulation'
    rows = [
        emit(-17, volume_cumulative_usd=base, period=position, **_price(start)),
        emit(-13, 'alert', source='Betfair', volume_usd=rng.randrange(4_000, 12_000)),
        emit(-10, volume_cumulative_usd=base + 2_000, **_price(up)),
        emit(-7, 'revision', source='Betfair', volume_cumulative_usd=base + 8_000),
        emit(-3, 'alert', source='Polymarket', direction='under', selection='Under 2.5',
             volume_usd=rng.randrange(18_000, 41_000)),
        emit(0, volume_cumulative_usd=final, **_price(down)),
    ]
    noise = []
    if negative == 'late_revision':
        rows[3]['available_utc'] = events.iso(events.emitted(rows[-1]) + 2 * MINUTE)
    if negative == 'volume_receipt_conflict':
        noise.append(dict(rows[-1], volume_cumulative_usd=base))
    return rows, noise


def _equaliser(emit, rng, shift, negative):
    a = rng.uniform(2.12, 2.41) + shift
    b = a - rng.uniform(.25, .40)
    c = b + rng.uniform(.18, .36)
    second_period = 'first_half' if negative == 'middle_period_switch' else 'regulation'
    if negative == 'continued_price_drop':
        c = b - .15
    if negative == 'weak_alert':
        volume = rng.randrange(1_000, 4_000)
    else:
        volume = rng.randrange(41_000, 92_000)
    change = [2, 0] if negative == 'contradictory_marker' else [1, 1]
    alert_time = -16 if negative == 'alert_before_first_goal' else -10
    rows = [
        emit(-19, score=[0, 0], **_price(a)),
        emit(-15, 'goal', score=[1, 0]),
        emit(-12, 'score', score=[1, 0]),
        emit(alert_time, 'alert', source='Polymarket', score=[1, 0], volume_usd=volume),
        emit(-7, score=[1, 0], period=second_period, **_price(b)),
        emit(-5, 'goal', score=[1, 1]),
        emit(-2, 'score', score=change),
        emit(0, score=[1, 1], **_price(c)),
    ]
    if negative == 'late_equaliser':
        rows[5]['available_utc'] = events.iso(events.emitted(rows[-1]) + 3 * MINUTE)
    return rows, []


def _market_pressure(emit, rng, shift, negative):
    start = rng.uniform(1.66, 1.94) + shift
    end = start + rng.uniform(.20, .36)
    first = rng.randrange(26_000, 77_000)
    last = int(first * rng.uniform(.12, .55))
    if negative == 'pressure_increases':
        last = first * 2
    if negative == 'falling_quotation':
        end = start - .17
    period = 'first_half' if negative == 'wrong_alert_period' else 'regulation'
    quote_line = {'line': '3.5', 'selection': 'Over 3.5'} if negative == 'opening_line_changes' else {}
    crossing = [0, 1] if negative == 'marker_changes_lead' else [1, 0]
    score, provider = [1, 0], 'Betfair'
    rows = [
        emit(-14, 'alert', source=provider, period=period, volume_usd=first, score=score),
        emit(-11, 'revision', source=provider, volume_cumulative_usd=rng.randrange(20_000, 110_000), score=score),
        emit(-9, score=score, **quote_line, **_price(start)),
        emit(-5, 'alert', source=provider, volume_usd=last, score=score),
        emit(-3, 'score', score=crossing),
        emit(0, score=score, **_price(end)),
    ]
    if negative == 'late_pressure_revision':
        rows[1]['available_utc'] = events.iso(events.emitted(rows[-1]) + 2 * MINUTE)
    return rows, []


WORLDS = (
    ('pre_market_reversal', _pre_reversal,
     ('terminal_rise', 'initial_fall', 'flat_cumulative_volume', 'wrong_opening_period',
      'late_revision', 'volume_receipt_conflict')),
    ('live_equaliser_reversal', _equaliser,
     ('middle_period_switch', 'continued_price_drop', 'weak_alert', 'contradictory_marker',
      'alert_before_first_goal', 'late_equaliser')),
    ('live_pressure_and_price', _market_pressure,
     ('pressure_increases', 'falling_quotation', 'wrong_alert_period', 'opening_line_changes',
      'marker_changes_lead', 'late_pressure_revision')),
)


def build(index, seed, *, holdout):
    """Generate dates, labels and irrelevant changes separately from the miner."""
    _, mechanism, alternatives = WORLDS[index]
    rng = Random(seed * 17 + index)
    days = EPOCH + timedelta(days=(240 if holdout else 0) + (seed % 59) + index * 370)
    scenarios = [None] * 9 + list(alternatives) * 3
    rng.shuffle(scenarios)
    observations, truth, counterexamples = [], {}, {}
    for i, scenario in enumerate(scenarios):
        at = days + timedelta(days=i // 5, minutes=28 * (i % 5))
        ko = at + (rng.randrange(25, 60) if index == 0 else -rng.randrange(35, 44)) * MINUTE
        code = f'{rng.getrandbits(72):018x}'
        game = books.Game(code, events.iso(ko), code + '-home', code + '-away', 'league-' + str(rng.randrange(4)))
        gaps = {offset: rng.randrange(-11, 12) for offset in range(-22, 0)}

        def emit(offset, kind='quote', **fields):
            when = at + timedelta(minutes=offset, seconds=gaps.get(offset, 0))
            phase = dict(phase='pre') if index == 0 else dict(phase='live', minute=int((when - ko) / MINUTE))
            return fixtures.typed_event(game, when, kind, **(phase | fields))

        moved_regime = ((.17 if index != 1 else -.15) if holdout else 0)
        path, defects = mechanism(emit, rng, moved_regime, scenario)
        # Short decoys affect some prefixes, without standing in for any required step.
        noise = [emit(-20, 'alert', source='ScanGoal', volume_usd=rng.randrange(10, 500))]
        if rng.randrange(4) == 0:
            noise.append(emit(-1, 'revision', source='ScanGoal', period='first_half'))
        observations.extend([*noise, *path, *defects])
        terminal = path[-1]
        fixture = fixture_key(components(terminal))
        if scenario is None:
            truth[key(fixture, terminal)] = tuple(claim(row) for row in path)
        else:
            counterexamples[fixture] = scenario
    rng.shuffle(observations)
    return Bank(tuple(observations), days + timedelta(days=7), truth, counterexamples)


def irrelevant(bank, seed):
    rng = Random(seed)
    rows = [dict(row, message_id=rng.getrandbits(32), journal_line=rng.randrange(1, 999999), ft=[7, 3])
            for row in (*bank.observations, *bank.observations[:31])]
    beyond = events.iso(bank.cutoff + MINUTE)
    rows.extend(dict(row, emitted_utc=beyond, available_utc=beyond, score=[8, 5])
                for row in bank.observations[:9])
    rng.shuffle(rows)
    return Bank(tuple(rows), bank.cutoff, bank.truth, bank.counterexamples)
