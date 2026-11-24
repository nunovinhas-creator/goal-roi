"""Evaluator-only scenarios written after the miner and gates were sealed."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from random import Random

from scangoal import event_intelligence as events
from scangoal.discovery import dsl
from scangoal.discovery.v32.tape import Coverage, fixture_key
from scangoal.match_identity import components
from scangoal.offline_pilot.books import Game
from scangoal.offline_pilot.fixtures import typed_event
from scangoal.observability import contract_key

START = datetime(2051, 4, 9, 16, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)
SPAN = timedelta(hours=6)


@dataclass(frozen=True)
class Bank:
    observations: tuple
    cutoff: datetime
    coverage: tuple
    truth: dict
    counterexamples: dict


def key(fixture, row):
    return fixture, row['available_utc'], dsl.digest(contract_key(row | {'fixture_id': fixture}))


def claim(row, *, terminal=False):
    goal = row['event_kind'] == 'goal'
    # Use independently declared observable evidence, not transport identifiers.
    return (row['event_kind'], row['available_utc'] if terminal else row['emitted_utc'],
            row['available_utc'], None if goal else row.get('source'), None if goal else row.get('period'),
            None if goal else row.get('line'), tuple(row.get('score') or ()), tuple(row.get('odds') or ()))


def _price(number):
    value = f'{number:.2f}'
    return dict(odds=[value, value], price_low=float(value), price_high=float(value))


def _delay(row, terminal):
    row['available_utc'] = events.iso(events.seen(terminal) + 2 * MINUTE)


def _ticket(event, rng, shift, variant):
    p = rng.uniform(1.91, 2.54) + shift
    low = rng.randrange(9500, 30000)
    high = low + rng.randrange(32000, 60000)
    start = high if variant != 'reversed_ticket_change' else low
    end = low if variant != 'reversed_ticket_change' else high
    finish = p if variant != 'price_breaks' else p + .21
    rows = [
        event(-31, 'alert', source='Polymarket', direction='under', selection='Under 2.5'),
        event(-23, biggest_ticket_usd=start, **_price(p)),
        event(-16, 'revision', source='Polymarket'),
        event(-13, 'alert', source='Polymarket' if variant == 'same_alert_source' else 'Betfair'),
        event(-8, biggest_ticket_usd=(start + end) // 2, **_price(p)),
        event(0, biggest_ticket_usd=end, period='first_half' if variant == 'different_terminal_period' else 'regulation',
              **_price(finish)),
    ]
    if variant == 'late_first_alert':
        _delay(rows[0], rows[-1])
    extra = [dict(rows[-1], biggest_ticket_usd=low + high)] if variant == 'simultaneous_ticket_conflict' else []
    return rows, extra


def _range_then_point(event, rng, shift, variant):
    p = rng.uniform(2.63, 3.10) + shift
    x = p - rng.uniform(.21, .43)
    lower, upper = f'{x - .12:.2f}', f'{x + .09:.2f}'
    opening_count = rng.randrange(18, 62)
    former = dict(odds=[lower, upper], price_low=float(lower), price_high=float(upper), price_kind='range')
    if variant == 'intermediate_point':
        former = _price(x)
    b = 'trades' if variant == 'incomparable_count_unit' else 'bets'
    ending_count = opening_count + (rng.randrange(27, 64) if variant != 'counts_fall' else -11)
    close = x - .30 if variant != 'terminal_rebounds' else p + .12
    rows = [
        event(-37, n_bets=opening_count, count_unit='bets', **_price(p)),
        event(-28, 'alert', source='Betfair', share_pct=rng.uniform(28, 65)),
        event(-21, **former),
        event(-15, 'revision', source='ScanGoal'),
        event(-9, n_bets=opening_count + 12, count_unit='bets', **_price(x - .13)),
        event(-2, 'alert', source='Betfair', share_pct=rng.uniform(3, 22)),
        event(0, n_bets=ending_count, count_unit=b, **_price(close)),
    ]
    if variant == 'late_range':
        _delay(rows[2], rows[-1])
    if variant == 'alert_order_swaps':
        rows[1]['share_pct'], rows[5]['share_pct'] = rows[5]['share_pct'], rows[1]['share_pct']
    return rows, []


def _score_confirmation(event, rng, shift, variant):
    p = rng.uniform(1.76, 2.19) + shift
    score = [1, 1]
    winning = [2, 1] if variant != 'away_goal' else [1, 2]
    close = p + (.24 if variant != 'terminal_price_falls' else -.16)
    rows = [
        event(-18, score=score, **_price(p)),
        event(-15, 'alert', source='Betfair', score=score, volume_usd=rng.randrange(5_000, 17_000)),
        event(-12, 'score', score=[2, 0] if variant == 'score_not_level' else score),
        event(-8, 'revision', source='Betfair', score=score),
        event(-6, 'alert', source='Betfair' if variant == 'alerts_same_source' else 'Polymarket',
              score=score, volume_usd=rng.randrange(45_000, 130_000)),
        event(-3, 'goal', score=winning),
        event(0, score=winning, period='first_half' if variant == 'foreign_terminal_period' else 'regulation',
              **_price(close)),
    ]
    if variant == 'late_goal_report':
        _delay(rows[5], rows[-1])
    return rows, []


def _scope_switch(event, rng, shift, variant):
    p = rng.uniform(2.04, 2.58) + shift
    line = {'line': '3.5', 'selection': 'Over 3.5'} if variant != 'same_middle_scope' else {}
    score = [0, 1]
    init = rng.randrange(8000, 21000)
    close = p - .19 if variant != 'opposite_quote_movement' else p + .15
    rows = [
        event(-16, 'alert', source='ScanGoal', score=score),
        event(-11, score=score, volume_cumulative_usd=init, **_price(p)),
        event(-9, 'score', score=[1, 1] if variant == 'marker_changes' else score),
        event(-7, score=score, **(line | _price(p + rng.uniform(.4, .7)))),
        event(-3, 'revision', source='ScanGoal', score=score),
        event(0, score=score, source='Betfair' if variant == 'wrong_terminal_source' else 'Stake',
              volume_cumulative_usd=init + (0 if variant == 'flat_volume' else 25_000), **_price(close)),
    ]
    if variant == 'simultaneous_revision_and_middle':
        rows[4]['emitted_utc'] = rows[3]['emitted_utc']
        rows[4]['available_utc'] = rows[3]['available_utc']
    return rows, []


SCENARIOS = (
    ('pre_ticket_decline', 'pre', _ticket, ('reversed_ticket_change', 'price_breaks', 'same_alert_source',
                                          'different_terminal_period', 'late_first_alert', 'simultaneous_ticket_conflict')),
    ('pre_range_to_point', 'pre', _range_then_point, ('intermediate_point', 'incomparable_count_unit', 'counts_fall',
                                                    'terminal_rebounds', 'late_range', 'alert_order_swaps')),
    ('live_score_confirmation', 'live', _score_confirmation, ('away_goal', 'terminal_price_falls', 'score_not_level',
                                                             'alerts_same_source', 'foreign_terminal_period', 'late_goal_report')),
    ('live_scope_switch', 'live', _scope_switch, ('same_middle_scope', 'marker_changes', 'wrong_terminal_source',
                                               'flat_volume', 'simultaneous_revision_and_middle', 'opposite_quote_movement')),
)


def build(index, seed, *, fresh):
    _, phase, mechanism, alternatives = SCENARIOS[index]
    rng = Random(seed * 47 + index * 133)
    origin = START + timedelta(days=index * 600 + (270 if fresh else 0) + seed % 103)
    observations, proof, truth, counterexamples = [], [], {}, {}
    scenarios = [None] * 10 + list(alternatives) * 3
    rng.shuffle(scenarios)
    for i, variant in enumerate(scenarios):
        at = origin + timedelta(hours=(i // 4) * 26 + i % 4, seconds=rng.randrange(0, 119))
        kickoff = at + MINUTE * (rng.randrange(16, 102) if phase == 'pre' else -rng.randrange(38, 48))
        uid = f'{rng.getrandbits(96):024x}'
        game = Game(uid, events.iso(kickoff), uid + '-home', uid + '-away', 'division-' + str(rng.randrange(1, 7)))
        jitters = {n: rng.randrange(-20, 21) for n in range(-50, 1)}
        pause = rng.choice([0, 2, 7, 12]) if fresh else rng.choice([0, 0, 3])

        def emit(offset, kind='quote', **fields):
            when = at + offset * MINUTE + timedelta(seconds=jitters[offset] if offset else 0)
            available = when + timedelta(seconds=pause if offset == 0 else rng.randrange(0, 6))
            minute = int((when - kickoff) / MINUTE) if phase == 'live' else None
            return typed_event(game, when, kind, **(dict(phase=phase, minute=minute,
                                                       available_utc=events.iso(available)) | fields))

        shift = ((.42 if phase == 'pre' else -.13) + rng.choice([-.05, 0, .08])) if fresh else 0
        path, defects = mechanism(emit, rng, shift, variant)
        distractors = []
        if rng.random() < (.65 if fresh else .38):
            distractors.append(emit(-48 if phase == 'pre' else -23, 'alert', source='ScanGoal', volume_usd=420))
        if rng.random() < (.39 if fresh else .20):
            distractors.append(emit(-1, 'revision', source='Polymarket', period='first_half'))
        observations.extend([*path, *defects, *distractors])
        terminal = path[-1]
        fixture = fixture_key(components(terminal))
        ident = key(fixture, terminal)
        if variant is None:
            truth[ident] = tuple(claim(row, terminal=i == len(path) - 1) for i, row in enumerate(path))
        else:
            counterexamples[ident] = variant
        t = events.seen(terminal)
        if not any(events.seen(row) > t for row in path):
            # Synthetic enumerator knows every receipt in this generated
            # interval. Late paths deliberately receive no such certificate.
            proof.append(Coverage(fixture, t - SPAN, t, t, dsl.digest(['synthetic-complete', uid, seed])))
    rng.shuffle(observations)
    return Bank(tuple(observations), origin + timedelta(days=9), tuple(proof), truth, counterexamples)


def transformed(bank, seed):
    rng = Random(seed)
    rows = [dict(row, message_id=rng.getrandbits(32), journal_line=rng.randrange(1, 999999), ft=[2, 4])
            for row in (*bank.observations, *bank.observations[:29])]
    future = events.iso(bank.cutoff + MINUTE)
    rows.extend(dict(row, emitted_utc=future, available_utc=future, score=[11, 3]) for row in bank.observations[:7])
    rng.shuffle(rows)
    return Bank(tuple(rows), bank.cutoff, bank.coverage, bank.truth, bank.counterexamples)
