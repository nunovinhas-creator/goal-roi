"""Typed predicates, semantic identities, and deterministic numeric thresholds."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from itertools import combinations
from math import isfinite

from .. import dsl
from ..experimental_h.rules import FIELDS, RELATIONS, relation_value

CENTERS = {'volume_ratio': 1, **dict.fromkeys((
    'odds_delta_bp', 'odds_velocity_bp_hour', 'goal_delta', 'goal_difference_delta', 'line_delta',
    'volume_cumulative_usd_delta', 'biggest_ticket_usd_delta', 'n_bets_delta'), 0)}
GAP_CUTS = (60, 120, 300, 600, 900, 1800, 3600, 7200, 14400)


@dataclass(frozen=True, order=True)
class Clause:
    left: int
    right: int
    field: str
    op: str
    value: object

    @property
    def slot(self):
        return self.left, self.right, self.field

    @property
    def relative(self):
        return self.field in CENTERS and self.value == CENTERS[self.field]

    def order(self):
        return (0 if self.relative else 1 if self.right >= 0 else 2,
                self.left - self.right if self.right >= 0 else 0, self)


@dataclass(frozen=True)
class Column:
    clause: Clause
    passed: int
    known: int


def _cuts(values):
    ordered = sorted(set(Decimal(str(value)) for value in values))
    with localcontext() as ctx:
        ctx.prec = 40
        for p in (1, 2, 3):
            index = len(ordered) * p // 4
            if index == 0:
                continue
            low, high = ordered[index - 1:index + 1]
            spread = high - low
            quantum = Decimal(1).scaleb(max(-7, spread.adjusted()))
            mid = (low + high) / 2
            rounded = mid.quantize(quantum, rounding=ROUND_HALF_EVEN)
            cut = rounded if low < rounded <= high else mid.quantize(Decimal('0.0000001'))
            if low < cut <= high:
                yield int(cut) if cut == cut.to_integral_value() else float(cut)


def _literals(name, primitive, values):
    if not values:
        return ()
    if primitive.kind == 'bool':
        return tuple(('eq', v) for v in sorted(set(values)))
    if primitive.kind == 'text':
        return tuple(('eq', v) for v in sorted(set(values)) if 0 < len(v) <= 100 and v.isprintable())
    if name in CENTERS:
        center = CENTERS[name]
        return tuple((op, center) for op in ('lt', 'eq', 'gt'))
    cuts = GAP_CUTS if name == 'gap_seconds' else sorted(set(_cuts(values)))
    return tuple((op, cut) for cut in cuts for op in ('lt', 'ge'))


def descriptors(length):
    fields = [(i, -1, name, p) for i in range(length) for name, p in sorted(FIELDS.items())
              if p.mine and not p.anchor_only and name not in {'event_kind', 'phase'}]
    edges = [(i, j, name, p) for i, j in combinations(range(length), 2)
             for name, p in sorted(RELATIONS.items()) if p.mine and p.read is not None]
    # Begin with portable long-range price/volume/score transitions, then
    # progressively expand to adjacent transitions, types and numeric levels.
    return sorted([*fields, *edges],
                  key=lambda d: (0 if d[2] in CENTERS else 1 if d[1] >= 0 and d[3].kind == 'bool'
                                 else 2 if d[1] < 0 else 3, d[0] - d[1], d[0], d[1], d[2]))


def _available(value, kind):
    return value is not None and (kind != 'number' or
                                  type(value) in (int, float) and isfinite(value) and abs(value) <= 1_000_000_000)


def columns(witnesses, length, meter, support, value_cache, row_keys):
    out = []
    all_rows = (1 << len(witnesses)) - 1
    for i, j, name, primitive in descriptors(length):
        if not meter.charge(len(witnesses)):
            break
        available = []
        for k, (_, path) in enumerate(witnesses):
            left, right = path[i], path[j] if j >= 0 else None
            # Cache keys include scalar state to distinguish an old receipt
            # before and after a later conflict masks its fields.
            for row in (left, right):
                if id(row) not in row_keys:
                    row_keys[id(row)] = dsl.digest(row)
            key = (name, row_keys[id(left)], row_keys[id(right)])
            if key not in value_cache:
                if not meter.spend():
                    return out
                value_cache[key] = primitive.read(left) if right is None else relation_value(name, left, right)
            value = value_cache[key]
            if _available(value, primitive.kind):
                available.append((k, value))
        known = sum(1 << k for k, _ in available)
        for op, value in _literals(name, primitive, [raw for _, raw in available]):
            if not meter.charge(len(available)):
                return out
            compare = dsl.OPS[op]
            passed = sum(1 << k for k, raw in available if compare(raw, value))
            if passed not in (0, all_rows) and support(passed):
                out.append(Column(Clause(i, j, name, op, value), passed, known))
    return out
