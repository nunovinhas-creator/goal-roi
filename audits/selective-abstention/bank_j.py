"""PR J evaluator-only banks, written after freeze.json was committed (4d14cc8).

Four new families. None reuses a PR G/H/I mechanism. Nuisance conditions are
drawn from the bank RNG: distractor rates, an unrelated background population,
and price/volume scale shifts in validation banks. Labels never leave here.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from random import Random

from scangoal import event_intelligence as events
from scangoal.discovery import dsl
from scangoal.discovery.v32.tape import Coverage, fixture_key
from scangoal.match_identity import components
from scangoal.observability import contract_key
from scangoal.offline_pilot.books import Game
from scangoal.offline_pilot.fixtures import typed_event

START = datetime(2058, 2, 3, 13, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)
SPAN = timedelta(hours=6)
TARGETS = 10
REPEATS = 3


@dataclass(frozen=True)
class Bank:
    observations: tuple
    cutoff: datetime
    coverage: tuple
    truth: dict
    counterexamples: dict
    background: frozenset
    nuisance: dict


def key(fixture, row):
    return fixture, row['available_utc'], dsl.digest(contract_key(row | {'fixture_id': fixture}))


def claim(row, *, terminal=False):
    goal = row['event_kind'] == 'goal'
    return (row['event_kind'], row['available_utc'] if terminal else row['emitted_utc'],
            row['available_utc'], None if goal else row.get('source'), None if goal else row.get('period'),
            None if goal else row.get('line'), tuple(row.get('score') or ()), tuple(row.get('odds') or ()))


def _price(number):
    value = f'{max(1.05, number):.2f}'
    return dict(odds=[value, value], price_low=float(value), price_high=float(value))


def _late(row, terminal, minutes=2):
    row['available_utc'] = events.iso(events.seen(terminal) + minutes * MINUTE)


def _source_relay(event, rng, s, variant):
    """Two alerts from different venues agree on Over, then the price shortens as volume builds."""
    p = rng.uniform(2.05, 2.60) * s['price']
    v0 = int(rng.randrange(14_000, 40_000) * s['volume'])
    d1, d2 = rng.uniform(.08, .15), rng.uniform(.22, .34)
    second = 'Stake' if variant == 'relay_same_source' else 'Polymarket'
    direction = 'under' if variant == 'relay_opposite_direction' else 'over'
    close = p + .17 if variant == 'relay_price_lengthens' else p - d2
    v2 = v0 + (int(9_000 * s['volume']) if variant == 'relay_volume_stalls' else int(rng.randrange(30_000, 70_000) * s['volume']))
    rows = [
        event(-34, 'alert', source='Stake', direction='over', volume_usd=int(rng.randrange(2_000, 9_000) * s['volume'])),
        event(-27, volume_cumulative_usd=v0, **_price(p)),
        event(-19, 'alert', source=second, direction=direction),
        event(-12, 'revision', source='Betfair'),
        event(-6, volume_cumulative_usd=v0 + int(9_000 * s['volume']), **_price(p - d1)),
        event(0, volume_cumulative_usd=v2, period='first_half' if variant == 'relay_terminal_half' else 'regulation',
              **_price(close)),
    ]
    if variant == 'relay_late_second_alert':
        _late(rows[2], rows[-1])
    extra = [dict(rows[-1], volume_cumulative_usd=v0 // 2)] if variant == 'relay_volume_conflict' else []
    return rows, extra


def _count_surge(event, rng, s, variant):
    """Bet counts climb in two stages while the price holds, then the price drops."""
    p = rng.uniform(1.80, 2.30) * s['price']
    c0 = rng.randrange(20, 90)
    a, b = rng.randrange(15, 40), rng.randrange(40, 110)
    unit = 'trades' if variant == 'surge_unit_changes' else 'bets'
    middle = p - .14 if variant == 'surge_early_move' else p
    close = p + .12 if variant == 'surge_price_rises' else p - rng.uniform(.15, .30)
    final = c0 + a - 9 if variant == 'surge_counts_fall' else c0 + a + b
    rows = [
        event(-40, n_bets=c0, count_unit='bets', **_price(p)),
        event(-30, 'alert', source='ScanGoal' if variant == 'surge_sources_swap' else 'Betfair',
              share_pct=rng.uniform(30, 70)),
        event(-22, n_bets=c0 + a, count_unit='bets', **_price(middle)),
        event(-14, 'revision', source='Polymarket'),
        event(-7, 'alert', source='Betfair' if variant == 'surge_sources_swap' else 'ScanGoal'),
        event(0, n_bets=final, count_unit=unit, **_price(close)),
    ]
    if variant == 'surge_late_revision':
        _late(rows[3], rows[-1])
    return rows, []


def _away_goal_drift(event, rng, s, variant):
    """After an away goal, the Over price lengthens twice while traded volume rises."""
    p = rng.uniform(1.70, 2.20) * s['price']
    v = int(rng.randrange(20_000, 60_000) * s['volume'])
    after = [1, 0] if variant == 'drift_home_goal' else [0, 1]
    reported = [0, 0] if variant == 'drift_score_unconfirmed' else after
    step = rng.uniform(.10, .18)
    close = p - .14 if variant == 'drift_price_shortens' else p + 2 * step + rng.uniform(.02, .08)
    gain = 0 if variant == 'drift_volume_flat' else int(rng.randrange(15_000, 40_000) * s['volume'])
    alert_at = -9 if variant == 'drift_alert_after_goal' else -16
    rows = [
        event(-20, score=[0, 0], volume_cumulative_usd=v, **_price(p)),
        event(alert_at, 'alert', source='Betfair', score=[0, 0] if alert_at < -13 else after,
              volume_usd=int(rng.randrange(20_000, 80_000) * s['volume'])),
        event(-13, 'goal', score=after),
        event(-11, 'score', score=reported),
        event(-6, score=after, volume_cumulative_usd=v + gain // 3, **_price(p + step)),
        event(0, score=after, volume_cumulative_usd=v + gain, **_price(close)),
    ]
    if variant == 'drift_late_goal':
        _late(rows[2], rows[-1])
    return rows, []


def _open_total(event, rng, s, variant):
    """A goal leaves Over 3.5 still open; two venues alert and the Over price shortens."""
    line = dict(line='3.5', selection='Over 3.5')
    p = rng.uniform(2.40, 3.10) * s['price']
    before, after = [1, 1], ([1, 2] if variant != 'total_level_goal_missing' else [1, 1])
    close = p + .20 if variant == 'total_price_lengthens' else p - rng.uniform(.35, .60)
    rows = [
        event(-21, score=before, **line, **_price(p)),
        event(-17, 'alert', source='Stake', score=before, direction='over'),
        event(-12, 'goal', score=after),
        event(-10, 'score', score=after),
        event(-7, 'alert', source='Stake' if variant == 'total_alerts_one_venue' else 'Polymarket', score=after,
              direction='under' if variant == 'total_alert_direction_flips' else 'over',
              volume_usd=int(rng.randrange(30_000, 90_000) * s['volume'])),
        event(0, score=after, **line, period='first_half' if variant == 'total_terminal_half' else 'regulation',
              **_price(close)),
    ]
    if variant == 'total_late_score_report':
        _late(rows[3], rows[-1])
    return rows, []


FAMILIES = (
    ('pre_source_relay', 'pre', _source_relay,
     ('relay_same_source', 'relay_opposite_direction', 'relay_price_lengthens', 'relay_volume_stalls',
      'relay_late_second_alert', 'relay_terminal_half', 'relay_volume_conflict')),
    ('pre_count_surge', 'pre', _count_surge,
     ('surge_unit_changes', 'surge_early_move', 'surge_price_rises', 'surge_counts_fall', 'surge_sources_swap',
      'surge_late_revision')),
    ('live_away_goal_drift', 'live', _away_goal_drift,
     ('drift_home_goal', 'drift_score_unconfirmed', 'drift_price_shortens', 'drift_volume_flat', 'drift_late_goal',
      'drift_alert_after_goal')),
    ('live_open_total', 'live', _open_total,
     ('total_level_goal_missing', 'total_price_lengthens', 'total_alerts_one_venue', 'total_alert_direction_flips',
      'total_terminal_half', 'total_late_score_report')),
)


def _background(event, rng, s):
    """Unrelated fixtures: short, unlabelled trajectories of the same phase."""
    p = rng.uniform(1.6, 3.4) * s['price']
    rows = [event(-rng.randrange(25, 45), 'alert', source=rng.choice(['Stake', 'Betfair', 'ScanGoal']))]
    for offset in sorted(rng.sample(range(-24, 0), rng.randrange(1, 4))):
        rows.append(event(offset, **_price(p + rng.uniform(-.3, .3))))
    rows.append(event(0, **_price(p + rng.uniform(-.3, .3))))
    return rows


def nuisance(index, seed, fresh):
    rng = Random(f'goal-roi-j-nuisance:{index}:{seed}:{int(fresh)}')
    return dict(distractor=round(rng.uniform(.1, .9), 3), late_revision=round(rng.uniform(0, .5), 3),
                background=rng.randrange(0, 9),
                price=round(rng.uniform(.85, 1.25), 3) if fresh else 1.0,
                volume=round(rng.choice([.5, .8, 1.5, 3.0]), 3) if fresh else 1.0,
                pause=rng.choice([0, 3, 7, 15]) if fresh else rng.choice([0, 2]))


def build(index, seed, *, fresh):
    _, phase, mechanism, alternatives = FAMILIES[index]
    s = nuisance(index, seed, fresh)
    rng = Random(seed * 59 + index * 211 + 7)
    origin = START + timedelta(days=index * 500 + (180 if fresh else 0) + seed % 97)
    observations, proof, truth, counterexamples, background = [], [], {}, {}, set()
    plan = [None] * TARGETS + list(alternatives) * REPEATS + ['background'] * s['background']
    rng.shuffle(plan)
    for i, variant in enumerate(plan):
        at = origin + timedelta(hours=(i // 5) * 23 + (i % 5) * 2, seconds=rng.randrange(0, 119))
        kickoff = at + MINUTE * (rng.randrange(20, 110) if phase == 'pre' else -rng.randrange(36, 50))
        uid = f'{rng.getrandbits(96):024x}'
        game = Game(uid, events.iso(kickoff), uid + '-home', uid + '-away', 'zone-' + str(rng.randrange(1, 9)))
        jitters = {n: rng.randrange(-25, 26) for n in range(-50, 1)}

        def emit(offset, kind='quote', **fields):
            when = at + offset * MINUTE + timedelta(seconds=jitters[offset] if offset else 0)
            available = when + timedelta(seconds=s['pause'] if offset == 0 else rng.randrange(0, 6))
            minute = int((when - kickoff) / MINUTE) if phase == 'live' else None
            base = dict(phase=phase, minute=minute, available_utc=events.iso(available))
            if phase == 'live' and 'score' not in fields and kind in ('quote', 'alert', 'revision'):
                base['score'] = None
            return typed_event(game, when, kind, **(base | fields))

        if variant == 'background':
            path = _background(emit, rng, s)
            if phase == 'live':
                path = [dict(row, score=[0, 0]) for row in path]
            defects = []
        else:
            path, defects = mechanism(emit, rng, s, variant)
        distractors = []
        if rng.random() < s['distractor']:
            distractors.append(emit(-46 if phase == 'pre' else -24, 'alert', source='ScanGoal',
                                    volume_usd=int(300 * s['volume'])))
        if rng.random() < s['late_revision']:
            distractors.append(emit(-1, 'revision', source='Stake', period='first_half'))
        if phase == 'live':
            last = path[-1].get('score') or [0, 0]
            distractors = [dict(row, score=last) for row in distractors]
        observations.extend([*path, *defects, *distractors])
        terminal = path[-1]
        fixture = fixture_key(components(terminal))
        ident = key(fixture, terminal)
        if variant is None:
            truth[ident] = tuple(claim(row, terminal=k == len(path) - 1) for k, row in enumerate(path))
        elif variant == 'background':
            background.add(ident)
        else:
            counterexamples[ident] = variant
        t = events.seen(terminal)
        if not any(events.seen(row) > t for row in path):
            proof.append(Coverage(fixture, t - SPAN, t, t, dsl.digest(['synthetic-complete-j', uid, seed])))
    rng.shuffle(observations)
    return Bank(tuple(observations), origin + timedelta(days=12), tuple(proof), truth, counterexamples,
                frozenset(background), s)


def transformed(bank, seed):
    rng = Random(seed)
    rows = [dict(row, message_id=rng.getrandbits(32), journal_line=rng.randrange(1, 999999), ft=[3, 3])
            for row in (*bank.observations, *bank.observations[:23])]
    future = events.iso(bank.cutoff + MINUTE)
    rows.extend(dict(row, emitted_utc=future, available_utc=future, score=[9, 9]) for row in bank.observations[:5])
    rng.shuffle(rows)
    return Bank(tuple(rows), bank.cutoff, bank.coverage, bank.truth, bank.counterexamples, bank.background,
                bank.nuisance)
