"""Typed, versioned temporal predicates over already observed events."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
import operator

from .. import event_intelligence as events, match_identity, parser

VERSION = 1
MAX_STEPS = 8
MAX_WINDOW = 7 * 86400
MAX_PREDICATES = 5
MAX_RELATIONS = 16
OPS = {'eq': operator.eq, 'ne': operator.ne, 'gt': operator.gt,
       'ge': operator.ge, 'lt': operator.lt, 'le': operator.le}
NEGATE = {'eq': 'ne', 'ne': 'eq', 'gt': 'le', 'ge': 'lt', 'lt': 'ge', 'le': 'gt'}


class Invalid(ValueError):
    """A proposal has no meaning in this DSL version."""


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def strict_json(raw):
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise Invalid('duplicate_json_key')
        return result

    def reject(_):
        raise Invalid('invalid_json_number')

    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=reject)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Invalid('invalid_json') from exc


@dataclass(frozen=True)
class Primitive:
    kind: str
    read: object
    mine: bool = False
    anchor_only: bool = False


def field(name):
    return lambda event: event.get(name)


def point_odds(event):
    prices = event.get('odds')
    if prices and prices[0] == prices[1]:
        return float(prices[0])
    return None


def selection(event):
    key = events.selection_key(event)
    return key[2:] if key else None


def score(event, fn):
    value = event.get('score')
    return fn(value) if value is not None else None


def price_ratio(a, b):
    left, right = a.get('odds'), b.get('odds')
    if (left and right and left[0] == left[1] and right[0] == right[1]
            and (key := events.selection_key(a)) is not None and key == events.selection_key(b)):
        return float((Decimal(right[0]) / Decimal(left[0]) - 1) * 10000)
    return None


def price_velocity(a, b):
    move = price_ratio(a, b)
    gap = (events.emitted(b) - events.emitted(a)).total_seconds()
    return move * 3600 / gap if move is not None and gap > 0 else None


def same(fn):
    def compare(a, b):
        left, right = fn(a), fn(b)
        return left == right if left is not None and right is not None else None
    return compare


def numeric_delta(a, b, fn):
    left, right = fn(a), fn(b)
    return right - left if left is not None and right is not None else None


def volume_ratio(a, b):
    left, right = a.get('volume_usd'), b.get('volume_usd')
    if a['source'] == b['source'] and left and right is not None:
        return right / left
    return None


# Add a typed, point-in-time getter here and bump VERSION for a semantic change.
# Only these getters can access event data. FT/results and arbitrary field paths
# are absent from the interpreter and cannot be added by a proposal.
FIELDS = {
    **{name: Primitive('text', field(name), mine=True)
       for name in ('source', 'signal_type', 'direction', 'phase', 'market_key', 'period', 'signal_kind')},
    'native_market': Primitive('text', lambda e: match_identity.normalized(e.get('market'))),
    'competition_kind': Primitive('text', lambda e: parser.competition_kind(e.get('league'), e.get('teams') or ())),
    'line': Primitive('number', lambda e: float(e['line']) if e.get('line') is not None else None),
    'point_odds': Primitive('number', point_odds, mine=True),
    'volume_usd': Primitive('number', field('volume_usd'), mine=True),
    'share_pct': Primitive('number', field('share_pct'), mine=True),
    'minute': Primitive('number', field('minute'), mine=True),
    'goals': Primitive('number', lambda e: score(e, sum), mine=True),
    'goal_difference': Primitive('number', lambda e: score(e, lambda s: s[0] - s[1])),
    'score_state': Primitive('text', lambda e: score(e, lambda s: 'draw' if s[0] == s[1]
                                                   else 'home_leading' if s[0] > s[1] else 'away_leading'), mine=True),
    'prior_alerts': Primitive('number', field('prior_alerts'), anchor_only=True),
    'prior_sources': Primitive('number', field('prior_sources'), anchor_only=True),
    'prior_compatible_quotes': Primitive('number', field('prior_compatible_quotes'), anchor_only=True),
    'lead_seconds': Primitive('number', lambda e: (events.kickoff(e) - events.seen(e)).total_seconds()
                              if events.kickoff(e) is not None else None),
}

RELATIONS = {
    **{f'same_{name}': Primitive('bool', same(FIELDS[name].read), mine=True)
       for name in ('source', 'phase', 'direction', 'period', 'native_market', 'line')},
    'same_selection': Primitive('bool', same(selection), mine=True),
    'gap_seconds': Primitive('number', lambda a, b: (events.emitted(b) - events.emitted(a)).total_seconds(), mine=True),
    'receipt_gap_seconds': Primitive('number', lambda a, b: (events.seen(b) - events.seen(a)).total_seconds()),
    'odds_delta_bp': Primitive('number', price_ratio, mine=True),
    'odds_velocity_bp_hour': Primitive('number', price_velocity, mine=True),
    'volume_ratio': Primitive('number', volume_ratio, mine=True),
    'goal_delta': Primitive('number', lambda a, b: numeric_delta(a, b, FIELDS['goals'].read), mine=True),
}


def _literal(value, kind):
    if kind == 'bool' and type(value) is bool:
        return value
    if kind == 'number' and type(value) in (int, float) and abs(value) <= 1_000_000_000 and math.isfinite(value):
        return int(value) if value == int(value) else value
    if kind == 'text' and isinstance(value, str) and 0 < len(value) <= 100 and value.isprintable():
        return value
    raise Invalid('invalid_literal')


def _conjunction(atoms, primitive):
    """Canonicalize reordering, repeated predicates and equivalent intervals."""
    unique = {(a['op'], a['value']) for a in atoms}
    if primitive.kind == 'bool':
        possibilities = [x for x in (False, True) if all(OPS[o](x, v) for o, v in unique)]
        if len(possibilities) != 1:
            raise Invalid('unsatisfiable_or_vacuous_predicate')
        return [('eq', possibilities[0])]

    equals = [v for o, v in unique if o == 'eq']
    if equals:
        if len(equals) != 1 or not all(OPS[o](equals[0], v) for o, v in unique):
            raise Invalid('contradictory_predicates')
        return [('eq', equals[0])]
    lower = max(((v, o == 'gt', o) for o, v in unique if o in ('ge', 'gt')), default=None)
    upper = min(((v, o == 'le', o) for o, v in unique if o in ('le', 'lt')), default=None)
    if lower and ('ne', lower[0]) in unique:
        lower = lower[0], True, 'gt'
    if upper and ('ne', upper[0]) in unique:
        upper = upper[0], False, 'lt'
    if lower and upper and lower[0] >= upper[0]:
        if lower[0] != upper[0] or lower[1] or not upper[1] or ('ne', lower[0]) in unique:
            raise Invalid('contradictory_predicates')
        return [('eq', lower[0])]
    bounds = [(b[2], b[0]) for b in (lower, upper) if b]
    return sorted([*bounds, *(('ne', v) for o, v in unique if o == 'ne'
                              and all(OPS[op](v, x) for op, x in bounds))])


def _atoms(rows, catalog, *, links=None):
    limit = MAX_PREDICATES if links is None else MAX_RELATIONS
    if not isinstance(rows, list) or len(rows) > limit:
        raise Invalid('predicate_budget_exceeded')
    fields = {'field', 'op', 'value'} if links is None else {'from', 'to', 'field', 'op', 'value'}
    grouped = defaultdict(list)
    for atom in rows:
        if not isinstance(atom, dict) or set(atom) != fields:
            raise Invalid('invalid_predicate')
        name, op = atom['field'], atom['op']
        if not isinstance(name, str) or name not in catalog or not isinstance(op, str) or op not in OPS:
            raise Invalid('unknown_primitive_or_operator')
        kind = catalog[name].kind
        if kind != 'number' and op not in ('eq', 'ne'):
            raise Invalid('incompatible_operator')
        prefix = ()
        if links is not None:
            left, right = atom['from'], atom['to']
            if type(left) is not int or type(right) is not int or not 0 <= left < right < links:
                raise Invalid('invalid_or_cyclic_relation')
            prefix = left, right
        grouped[(*prefix, name)].append(dict(op=op, value=_literal(atom['value'], kind)))
    result = []
    for (*prefix, name), values in sorted(grouped.items()):
        for op, value in _conjunction(values, catalog[name]):
            atom = dict(field=name, op=op, value=value)
            if prefix:
                atom.update({'from': prefix[0], 'to': prefix[1]})
            result.append(atom)
    return result


def canonical(rule):
    if (not isinstance(rule, dict) or set(rule) != {'version', 'within_seconds', 'steps', 'relations'}
            or type(rule['version']) is not int or rule['version'] != VERSION):
        raise Invalid('invalid_definition_version')
    window, steps = rule['within_seconds'], rule['steps']
    if type(window) is not int or not 1 <= window <= MAX_WINDOW:
        raise Invalid('invalid_observation_window')
    if not isinstance(steps, list) or not 2 <= len(steps) <= MAX_STEPS:
        raise Invalid('invalid_sequence_length')
    steps = [_atoms(step, FIELDS) for step in steps]
    if any(FIELDS[a['field']].anchor_only for step in steps[:-1] for a in step):
        raise Invalid('prefix_aggregate_outside_anchor')
    relations = _atoms(rule['relations'], RELATIONS, links=len(steps))
    if any(not step and not any(i in (a['from'], a['to']) for a in relations) for i, step in enumerate(steps)):
        raise Invalid('unconstrained_step')
    return dict(version=VERSION, within_seconds=window, steps=steps, relations=relations)


def identifier(rule):
    return 'td1:' + digest(canonical(rule))


def predicate(atom, value):
    if value is None:
        return None
    return OPS[atom['op']](value, atom['value'])
