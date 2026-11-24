"""Skeleton for the bank author. Standard library only. Contains no mechanism.

Replace `_family` with the hidden families. Everything random must come from `rng`.
"""

from datetime import datetime, timedelta, timezone
import random

SLOTS = {f'F{i}': ('pre' if i <= 4 else 'live') for i in range(1, 9)}


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _family(slot, rng, emit, variant):
    """Return the ordered events of one fixture; the last one is the terminal quote."""
    raise NotImplementedError('hidden mechanism, written only by the bank author')


def build(slot, role, index, seed):
    if slot not in SLOTS or role not in ('mining', 'validation') or index not in (1, 2, 3):
        raise ValueError('outside_registered_matrix')
    rng = random.Random(seed)
    phase = SLOTS[slot]
    observations, truth, counterexamples, background, coverage = [], [], [], [], []
    origin = datetime(2060, 1, 1, tzinfo=timezone.utc) + timedelta(days=rng.randrange(0, 3650))
    plan = [None] * 10  # targets; extend with >= 6 mechanisms x 3 and RNG-drawn background fixtures
    rng.shuffle(plan)
    next_id = rng.randrange(1, 2 ** 31)
    for variant in plan:
        def emit(offset_minutes, kind='quote', **fields):
            nonlocal next_id
            next_id += 1
            return dict(event_kind=kind, phase=phase, message_id=next_id, **fields)
        _family(slot, rng, emit, variant)  # the author fills observations, truth, counterexamples, coverage
    return dict(observations=observations, cutoff_utc=_iso(origin + timedelta(days=30)), coverage=coverage,
                truth=truth, counterexamples=counterexamples, background=background)
