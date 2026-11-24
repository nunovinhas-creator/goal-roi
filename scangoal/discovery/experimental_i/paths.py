"""Path sampling, stable across message order and independent of outcomes."""

from collections import defaultdict, deque
from itertools import combinations, groupby
from math import comb

from ... import event_intelligence as events
from .. import dsl

WINDOW = 21600
TAIL = 12
PATHS_PER_LENGTH = 8
MAX_ANCHORS = 384


def band(length):
    return (length - 2) // 2 if length < 6 else 2


def _unrank(size, take, rank):
    selected = []
    for index in range(size):
        remaining = take - len(selected)
        if not remaining:
            break
        block = comb(size - index - 1, remaining - 1)
        if rank < block:
            selected.append(index)
        else:
            rank -= block
    return tuple(selected)


def _choices(size, take):
    """Cover both boundaries and deletions before filling gaps by rank."""
    count = comb(size, take)
    recent = min(dsl.MAX_STEPS, size)
    priority = [tuple(range(start, start + take)) for start in range(size - take, max(-1, size - take - 3), -1)]
    if recent <= take + 2:
        priority.extend(combinations(range(size - recent, size), take))
    priority.append(tuple(range(take)))
    for i in range(min(count, PATHS_PER_LENGTH)):
        rank = i * (count - 1) // max(1, min(count, PATHS_PER_LENGTH) - 1)
        priority.append(_unrank(size, take, rank))
    return tuple(dict.fromkeys(priority))


def sampled_paths(decision):
    history = decision.within(WINDOW)
    groups = tuple(tuple(rows) for _, rows in groupby(history, key=events.emitted))[-TAIL:]
    for take in range(1, min(dsl.MAX_STEPS - 1, len(groups)) + 1):
        choices = _choices(len(groups), take)
        # Give different issue selections a turn before receipt variants.
        actual = set()
        for variant in range(PATHS_PER_LENGTH):
            for indexes in choices:
                q = variant
                prefix = []
                for k in indexes:
                    q, choice = divmod(q, len(groups[k]))
                    prefix.append(groups[k][choice])
                evidence = tuple(event['message_id'] for event in prefix)
                if evidence not in actual:
                    actual.add(evidence)
                    yield (*prefix, decision.quote)
                if len(actual) == PATHS_PER_LENGTH:
                    break
            if len(actual) == PATHS_PER_LENGTH or all(len(group) == 1 for group in groups):
                break


def _interleave(items):
    queues = deque(deque(group) for _, group in sorted(items.items()))
    while queues:
        row = queues.popleft()
        yield row.popleft()
        if row:
            queues.append(row)


def sample(observed, meter):
    phases = defaultdict(lambda: defaultdict(list))
    for decision in observed.decisions(meter):
        phases[decision.quote['phase']][decision.fixture].append(decision)
    if meter.exhausted:
        return {}, 0, ['observation_budget']
    # Equal turns by phase, then by fixture in each phase.
    cursors = deque(iter(_interleave(by_fixture)) for _, by_fixture in sorted(phases.items()))
    shapes = defaultdict(list)
    count = 0
    meter.stage = 'paths'
    while cursors:
        cursor = cursors.popleft()
        decision = next(cursor, None)
        if decision is None:
            continue
        if count == MAX_ANCHORS:
            return shapes, count, ['anchor_limit']
        if not meter.charge(len(decision.history) + 1):
            return shapes, count, ['path_budget']
        count += 1
        cursors.append(cursor)
        for witness in sampled_paths(decision):
            if not meter.charge(len(witness)):
                return shapes, count, ['path_budget']
            shape = decision.quote['phase'], tuple(row['event_kind'] for row in witness)
            shapes[shape].append((decision, witness))
    return shapes, count, []
