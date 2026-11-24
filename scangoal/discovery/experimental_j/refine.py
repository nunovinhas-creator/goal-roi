"""Consensus refinement: an added generator, never a filter on the miner's proposals.

A proposal fires on a set of fixtures. Inside that set, a scale-free predicate
that holds for most fixtures but fails for an observed minority describes a
recurrent regularity with observable counterexamples. Adding all such
predicates yields the dominant regime of that trajectory. Predicates that hold
everywhere are not added: nothing observed shows that they are necessary.
Absolute levels (prices, volumes, minutes, gaps) are excluded by construction,
so the refined rule is invariant to shifts of scale in those quantities.
"""

from fractions import Fraction

from .. import dsl
from ..experimental_h.rules import FIELDS, RELATIONS, relation_value
from ..experimental_i.features import CENTERS
from ..v32 import language

CONSENSUS = Fraction(4, 5)
ROUNDS = 2
STEP_PREDICATES = 8
RELATION_LIMIT = 16
SKIP_FIELDS = frozenset({'event_kind', 'phase'})


def _literals(length):
    out = []
    for i in range(length):
        for name, p in sorted(FIELDS.items()):
            if p.mine and not p.anchor_only and p.kind == 'text' and name not in SKIP_FIELDS:
                out.append((i, -1, name, p))
    for j in range(length):
        for i in range(j):
            for name, p in sorted(RELATIONS.items()):
                if p.mine and p.read is not None and (p.kind == 'bool' or name in CENTERS):
                    out.append((i, j, name, p))
    return out


def _ops(name, kind, value):
    if kind == 'bool':
        return (('eq', value),)
    if kind == 'text':
        return (('eq', value),) if isinstance(value, str) and 0 < len(value) <= 100 and value.isprintable() else ()
    center = CENTERS[name]
    return tuple((op, center) for op in ('lt', 'eq', 'gt') if dsl.OPS[op](value, center))


def _preference(literal):
    (i, j, name, op, value) = literal
    # Relative movement first, then semantic booleans, then categorical text.
    # Longer spans summarise the whole trajectory and are tried first.
    kind = 0 if name in CENTERS else 1 if j >= 0 else 2
    return kind, -(j - i) if j >= 0 else 0, i, j, name, op, str(value)


def consensus(rule, firing, index):
    """Return label-free consensus literals for the fixtures a rule already fires on."""
    length = len(rule['steps'])
    present = {}
    for d, witness in firing.witnesses.items():
        present.setdefault(index.fixture[d], []).append(witness)
    n = len(present)
    if not n:
        return [], {}, n
    holds = {}
    for i, j, name, p in _literals(length):
        for fixture, witnesses in present.items():
            seen = set()
            for path in witnesses:
                left = path[i]
                value = p.read(left) if j < 0 else relation_value(name, left, path[j])
                if value is None or (p.kind == 'number' and type(value) not in (int, float)):
                    continue
                for op, literal in _ops(name, p.kind, value):
                    seen.add((op, literal))
            for op, literal in seen:
                holds.setdefault((i, j, name, op, literal), set()).add(fixture)
    existing = {(a['field'], i, -1) for i, step in enumerate(rule['steps']) for a in step}
    existing |= {(e['field'], e['from'], e['to']) for e in rule['relations']}
    groups = {}
    for literal in sorted(holds, key=_preference):
        i, j, name, op, value = literal
        count = len(holds[literal])
        if (name, i, j) in existing or count == n or Fraction(count, n) < CONSENSUS:
            continue
        groups.setdefault(frozenset(present) - holds[literal], []).append(literal)
    # One representative per observed contrast; the others are recorded as
    # semantically different expressions of the same contrast.
    chosen = [members[0] for members in groups.values()]
    equivalents = {members[0]: tuple(members) for members in groups.values()}
    return chosen, equivalents, n


def extend(rule, literals):
    steps = [list(step) for step in rule['steps']]
    relations = list(rule['relations'])
    added = []
    for i, j, name, op, value in literals:
        atom = dict(field=name, op=op, value=value)
        if j < 0:
            if len(steps[i]) >= STEP_PREDICATES:
                continue
            steps[i].append(atom)
        else:
            if len(relations) >= RELATION_LIMIT:
                continue
            relations.append(dict(atom, **{'from': i, 'to': j}))
        added.append((i, j, name, op, value))
    try:
        return language.canonical(dict(rule, steps=steps, relations=relations)), added
    except dsl.Invalid:
        return None, []


def informative_steps(length, equivalents):
    """Steps used by at least one expression of an observed contrast, plus the terminal."""
    used = {length - 1}
    for members in equivalents.values():
        for i, j, *_ in members:
            used |= {i} if j < 0 else {i, j}
    return used


def refine(proposal, index):
    """Iterate consensus a fixed number of rounds. Returns (refined proposal or None, informative steps)."""
    rule, trail, informative = proposal['rule'], [], set()
    for _ in range(ROUNDS):
        identity = language.identifier(rule)
        firing = index.fire(identity, rule)
        if not firing.complete:
            return None, set()
        literals, equivalents, _ = consensus(rule, firing, index)
        if not literals:
            break
        extended, added = extend(rule, literals)
        if extended is None or not added:
            break
        rule = extended
        informative |= informative_steps(len(rule['steps']), {k: v for k, v in equivalents.items() if k in added})
        trail.append([list(map(str, x)) for x in added])
    if not trail:
        return None, set()
    return dict(id=language.identifier(rule), rule=rule, phase=proposal['phase'], shape=tuple(proposal['shape']),
                channel='consensus', parent=proposal['id'], consensus_rounds=trail), informative
