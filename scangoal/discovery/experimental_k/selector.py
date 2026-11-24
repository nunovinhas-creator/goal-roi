"""Experimental K: the J selector with three independently switchable changes.

K1  shapes are also enumerated from the sampled trajectories, not only from the
    generator shortlist (the shortlist is kept and nothing is removed);
K2  consensus is measured over fixtures where the predicate value is observable;
    unknown values never count as counterexamples and stay indeterminate in matching;
K3  replication re-selects each calendar half without the nested half-stability check.

With all switches off this reproduces J exactly (tested). J, I and H code is
imported, never modified. No label, outcome or evaluator is reachable from here.
"""

from dataclasses import dataclass, replace
from fractions import Fraction

from ..experimental_h.observed import Observed
from ..experimental_h.rules import Meter, relation_value
from ..experimental_i import miner as miner_i, paths
from ..experimental_j import refine as j_refine, selector as j_selector
from ..experimental_j.observe import Index
from ..v32 import language

VERSION = 'k-switchable-selector-v1'


@dataclass(frozen=True)
class Config:
    k1: bool = False
    k2: bool = False
    k3: bool = False
    consensus: Fraction = j_refine.CONSENSUS
    regular: Fraction = j_selector.REGULAR
    min_known: Fraction = Fraction(1, 2)
    max_enumerated_shapes: int = 48

    @property
    def name(self):
        parts = [k for k in ('k1', 'k2', 'k3') if getattr(self, k)]
        return 'J' if not parts else 'K' if len(parts) == 3 else 'J+' + '+'.join(p.upper() for p in parts)


VARIANTS = {c.name: c for c in (Config(), Config(k1=True), Config(k2=True), Config(k3=True),
                                 Config(k1=True, k2=True, k3=True))}


def consensus(rule, firing, index, config):
    length = len(rule['steps'])
    present = {}
    for d, witness in firing.witnesses.items():
        present.setdefault(index.fixture[d], []).append(witness)
    n = len(present)
    if not n:
        return [], {}, n
    holds, known = {}, {}
    for i, j, name, p in j_refine._literals(length):
        for fixture, witnesses in present.items():
            seen = set()
            observable = False
            for path in witnesses:
                value = p.read(path[i]) if j < 0 else relation_value(name, path[i], path[j])
                if value is None or (p.kind == 'number' and type(value) not in (int, float)):
                    continue
                observable = True
                seen.update(j_refine._ops(name, p.kind, value))
            if observable:
                known.setdefault((i, j, name), set()).add(fixture)
            for op, literal in seen:
                holds.setdefault((i, j, name, op, literal), set()).add(fixture)
    existing = {(a['field'], i, -1) for i, step in enumerate(rule['steps']) for a in step}
    existing |= {(e['field'], e['from'], e['to']) for e in rule['relations']}
    groups = {}
    for literal in sorted(holds, key=j_refine._preference):
        i, j, name, op, value = literal
        count = len(holds[literal])
        if (name, i, j) in existing:
            continue
        if config.k2:
            base = known[i, j, name]
            if (Fraction(len(base), n) < config.min_known or count == len(base)
                    or Fraction(count, len(base)) < config.consensus):
                continue
            excluded = frozenset(base) - holds[literal]
        else:
            if count == n or Fraction(count, n) < config.consensus:
                continue
            excluded = frozenset(present) - holds[literal]
        groups.setdefault(excluded, []).append(literal)
    chosen = [members[0] for members in groups.values()]
    return chosen, {members[0]: tuple(members) for members in groups.values()}, n


def refine(proposal, index, config):
    rule, trail, informative = proposal['rule'], [], set()
    for _ in range(j_refine.ROUNDS):
        firing = index.fire(language.identifier(rule), rule)
        if not firing.complete:
            return None, set()
        literals, equivalents, _ = consensus(rule, firing, index, config)
        if not literals:
            break
        extended, added = j_refine.extend(rule, literals)
        if extended is None or not added:
            break
        rule = extended
        informative |= j_refine.informative_steps(len(rule['steps']),
                                                  {k: v for k, v in equivalents.items() if k in added})
        trail.append([list(map(str, x)) for x in added])
    if not trail:
        return None, set()
    return dict(id=language.identifier(rule), rule=rule, phase=proposal['phase'], shape=tuple(proposal['shape']),
                channel='consensus', parent=proposal['id'], consensus_rounds=trail), informative


def _regime(shape, phase, index, config):
    while True:
        base = j_selector.structural(phase, shape)
        refined, informative = refine(base, index, config)
        if refined is None:
            return base, shape
        first = min(informative)
        if first == 0 or len(shape) - first < 2:
            return refined, shape
        shape = shape[first:]


def enumerated_shapes(tape, phase, config):
    """K1: shapes of the sampled trajectories, by distinct-fixture support. Label-free."""
    sampled, _, _ = paths.sample(Observed(tape), Meter(miner_i.WORK // 5))
    support = {}
    for (p, kinds), entries in sampled.items():
        if p == phase and len(kinds) >= 2:
            support[kinds] = len({decision.fixture for decision, _ in entries})
    ordered = sorted((k for k, s in support.items() if s >= j_selector.MIN_FIXTURES),
                     key=lambda k: (-support[k], -len(k), k))
    return ordered[:config.max_enumerated_shapes]


def select(shapes, index, phase, config, *, replicate=True, nested_stability=True):
    audit = dict(version=VERSION, variant=config.name, phase=phase, shapes=[], checks={}, abstain_reason=None,
                 alternatives=[])
    if not index.complete:
        audit['abstain_reason'] = 'decision_index_incomplete'
        return None, audit
    population = j_selector._population(index, phase)
    rows = []
    for shape in shapes:
        m = j_selector._measure(j_selector.structural(phase, shape), index)
        support = len(m['fixtures'])
        reference = j_selector._reference(shape, phase, index, population) if support >= j_selector.MIN_FIXTURES else 0
        regular = (m['complete'] and support >= j_selector.MIN_FIXTURES and len(m['days']) >= j_selector.MIN_DAYS
                   and reference > 0 and Fraction(support, reference) >= config.regular)
        rows.append(dict(shape=list(shape), length=len(shape), support=support, reference=reference, regular=regular))
    rows.sort(key=lambda r: (-r['length'], -r['support'], r['shape']))
    audit['shapes'] = rows
    regular = [r for r in rows if r['regular']]
    if not regular:
        audit['abstain_reason'] = 'no_regular_trajectory'
        return None, audit
    longest = regular[0]['length']
    finalists, seen = [], set()
    for row in (r for r in regular if r['length'] == longest):
        refined, _ = _regime(tuple(row['shape']), phase, index, config)
        if refined['id'] not in seen:
            seen.add(refined['id'])
            finalists.append((refined, j_selector._measure(refined, index)))
    finalists.sort(key=lambda x: (-len(x[0]['shape']), -len(x[1]['fixtures']), x[0]['id']))
    choice, m = finalists[0]
    n = len(m['fixtures'])
    early, late, early_all, late_all = j_selector._halves(index, m['fixtures'])
    checks = dict(
        complete=m['complete'], support=n, support_ok=n >= j_selector.MIN_FIXTURES,
        days=len(m['days']), days_ok=len(m['days']) >= j_selector.MIN_DAYS, detections=m['detections'],
        unique_terminal=n > 0 and Fraction(m['detections'], n) <= j_selector.MAX_TERMINALS_PER_FIXTURE,
        halves=[early, late, early_all, late_all],
        stable=(early_all > 0 and late_all > 0 and early >= j_selector.HALF_SHARE * n
                and late >= j_selector.HALF_SHARE * n),
        predicates=sum(len(s) for s in choice['rule']['steps']) + len(choice['rule']['relations']))
    if not nested_stability:
        checks['stable'] = True
    for other, om in finalists[1:]:
        overlap = len(m['fixtures'] & om['fixtures'])
        distinct = (len(om['fixtures']) >= j_selector.AMBIGUITY * n
                    and overlap < j_selector.AMBIGUITY * len(om['fixtures']))
        audit['alternatives'].append(dict(id=other['id'], shape=list(other['shape']), support=len(om['fixtures']),
                                          overlap=overlap, competing=distinct))
    checks['unambiguous'] = not any(a['competing'] for a in audit['alternatives'])
    if replicate:
        replicas = []
        for half in index.calendar_halves():
            again, sub = select(shapes, index.subset(half), phase, config, replicate=False,
                                nested_stability=not config.k3)
            replicas.append(dict(shape=list(again['shape']) if again else None, abstain_reason=sub['abstain_reason']))
        checks['replicas'] = replicas
        checks['replicated'] = all(r['shape'] == list(choice['shape']) for r in replicas)
    else:
        checks['replicated'] = True
    audit['checks'] = checks
    for name in ('complete', 'support_ok', 'days_ok', 'unique_terminal', 'stable', 'unambiguous', 'replicated'):
        if not checks[name]:
            audit['abstain_reason'] = name
            return None, audit
    audit['choice_id'] = choice['id']
    return choice, audit


def investigate(tape, *, phase, config, index=None, mined=None, work=miner_i.WORK):
    index = index or Index(Observed(tape))
    mined = mined or miner_i.discover(tape, work=work)
    pool = sorted({tuple(p['shape']) for p in mined['candidates'] if p['phase'] == phase})
    shapes = sorted(set(pool) | set(enumerated_shapes(tape, phase, config))) if config.k1 else pool
    choice, audit = select(shapes, index, phase, config)
    audit['enumerated_shapes'] = len(shapes) - len(pool)
    added = [choice] if choice is not None and choice['channel'] == 'consensus' else []
    return dict(version=VERSION, variant=config.name, proposals=mined['candidates'] + added,
                mining_work=mined['work'], choice=choice, abstained=choice is None, audit=audit, confirmatory=False)


def with_thresholds(config, consensus, regular):
    return replace(config, consensus=consensus, regular=regular)
