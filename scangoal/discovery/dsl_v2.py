"""Separate DSL namespace for observable state transitions and persistence."""

from . import dsl as v1
from .. import event_intelligence as events

VERSION = 2
MAX_STEPS = v1.MAX_STEPS
MAX_PREDICATES = 8
MAX_RELATIONS = v1.MAX_RELATIONS
Invalid = v1.Invalid


def _spread(event):
    odds = event.get('odds')
    if odds is not None:
        low, high = map(float, odds)
        if low > 0:
            return 10000 * (high - low) / low
    return None


FIELDS = dict(v1.FIELDS, spread_bp=v1.Primitive('number', _spread, mine=True))
# These fields operate on all observed events between the selected endpoints.
# The interval contains only alerts visible at the anchor, including distractors.
PERSISTENCE = {name + '_persists': FIELDS[name].read
               for name in ('score_state', 'direction', 'line', 'phase')}

RELATIONS = {
    **v1.RELATIONS,
    'same_score_state': v1.Primitive('bool', v1.same(FIELDS['score_state'].read), mine=True),
    'goal_difference_delta': v1.Primitive('number', lambda a, b: v1.numeric_delta(
        a, b, FIELDS['goal_difference'].read), mine=True),
    'line_delta': v1.Primitive('number', lambda a, b: v1.numeric_delta(a, b, FIELDS['line'].read), mine=True),
    'observed_between': v1.Primitive('number', None),
    **{name: v1.Primitive('bool', None) for name in PERSISTENCE},
}


def canonical(rule):
    if (not isinstance(rule, dict) or set(rule) != {'version', 'within_seconds', 'steps', 'relations'}
            or type(rule['version']) is not int or rule['version'] != VERSION):
        raise Invalid('invalid_definition_version')
    window, steps = rule['within_seconds'], rule['steps']
    if type(window) is not int or not 1 <= window <= v1.MAX_WINDOW:
        raise Invalid('invalid_observation_window')
    if not isinstance(steps, list) or not 2 <= len(steps) <= MAX_STEPS:
        raise Invalid('invalid_sequence_length')
    # Reuse v1's normalization in bounded groups, then reconcile overlapping
    # constraints in the same primitive across groups.
    steps = [_step(s) for s in steps]
    if any(FIELDS[a['field']].anchor_only for step in steps[:-1] for a in step):
        raise Invalid('prefix_aggregate_outside_anchor')
    edges = v1._atoms(rule['relations'], RELATIONS, links=len(steps))
    if any(not step and not any(i in (a['from'], a['to']) for a in edges) for i, step in enumerate(steps)):
        raise Invalid('unconstrained_step')
    return dict(version=VERSION, within_seconds=window, steps=steps, relations=edges)


def _step(atoms):
    if not isinstance(atoms, list) or len(atoms) > MAX_PREDICATES:
        raise Invalid('predicate_budget_exceeded')
    grouped = {}
    for atom in atoms:
        normalized = v1._atoms([atom], FIELDS)[0]
        grouped.setdefault(normalized['field'], []).append(normalized)
    return [dict(field=name, op=op, value=value) for name, rows in sorted(grouped.items())
            for op, value in v1._conjunction(rows, FIELDS[name])]


def identifier(rule):
    return 'td2:' + v1.digest(canonical(rule))


def relation(name, a, b, candidates=()):
    primitive = RELATIONS[name]
    if primitive.read is not None:
        return primitive.read(a, b)
    start, end = events.emitted(a), events.emitted(b)
    interior = [e for e in candidates if start < events.emitted(e) < end]
    if name == 'observed_between':
        return len(interior)
    getter = PERSISTENCE[name]
    first, last = getter(a), getter(b)
    if first is None or last is None:
        return None
    if first != last:
        return False
    uncertain = False
    for e in interior:
        value = getter(e)
        if value is None:
            uncertain = True
        elif value != first:
            return False
    return None if uncertain else True
