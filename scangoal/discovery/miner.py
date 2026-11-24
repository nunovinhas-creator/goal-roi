"""Bounded projected subsequence mining and falsification proposals."""

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations, islice, zip_longest

from .. import event_intelligence as events
from . import dsl
from .replay import Budget

DISCOVERY_WINDOW = 6 * 3600
MINE_STEPS = 5
MAX_EMISSIONS = 60_000
MAX_ANCHORS = 256
MAX_HYPOTHESES = 36
MIN_SCREEN_GAMES = 5
MAX_REPLAY_WORK = 350_000


@dataclass(frozen=True)
class Candidate:
    rule: dict
    origin: str
    mechanism: str
    refutation: str
    alternative: dict | None = None

    @property
    def id(self):
        return dsl.identifier(self.rule)


def atom(name, value, op='eq'):
    return dict(field=name, op=op, value=value)


def edge(name, value, op, left, right):
    return {'from': left, 'to': right, **atom(name, value, op)}


def definition(steps, relations=()):
    # Comparable opportunities must have enough time-series observations to
    # express the pattern even when they do not exhibit it.
    anchor = [*steps[-1], atom('prior_alerts', len(steps) - 1, 'ge')]
    return dict(version=dsl.VERSION, within_seconds=DISCOVERY_WINDOW,
                steps=[*steps[:-1], anchor], relations=list(relations))


def contrast(rule):
    """Change an antecedent or a relation while retaining the anchor population."""
    edited = dsl.strict_json(dsl.encoded(rule))
    clauses = edited['relations'] or next((step for step in edited['steps'][:-1] if step), [])
    for clause in clauses:
        op = clause['op']
        clause['op'] = dsl.NEGATE[op]
        try:
            return dsl.canonical(edited)
        except dsl.Invalid:
            clause['op'] = op
    return None


def chains(history, anchor):
    """Projected search considers both consecutive and intervening observations."""
    prior = []
    last = events.emitted(anchor)
    for e in reversed(history):
        if events.emitted(e) < last:
            prior.append(e)
            last = events.emitted(e)
        if len(prior) == MINE_STEPS:
            break
    for length in range(1, min(len(prior), MINE_STEPS - 1) + 1):
        for indexes in islice(combinations(range(len(prior)), length), 2):
            yield [*(prior[i] for i in reversed(indexes)), anchor]


def samples(replay):
    """Learn candidate numeric cutoffs from the discovery sample only."""
    values = defaultdict(list)
    fields = {name: p for name, p in dsl.FIELDS.items() if p.mine and p.kind == 'number'}
    for _, row in replay.anchors:
        for name, primitive in fields.items():
            number = primitive.read(row)
            if number is not None and -1e9 <= number <= 1e9:
                values[name].append(number)
    percentiles = {}
    for name, rows in values.items():
        if len(rows) >= MIN_SCREEN_GAMES:
            ordered = sorted(rows)
            percentiles[name] = sorted({round(ordered[len(rows) * q // 4], 4) for q in (1, 2, 3)})
    return percentiles


def projections(chain, thresholds):
    """Generate whole sequences across fields, outcomes of relations and interactions."""
    for name, p in dsl.FIELDS.items():
        if not p.mine:
            continue
        values = [p.read(e) for e in chain]
        if any(v is None for v in values):
            continue
        if p.kind == 'text':
            yield name, definition([[atom(name, v)] for v in values])
        elif name in thresholds:
            for cut in thresholds[name]:
                yield name, definition([[atom(name, cut, 'ge' if v >= cut else 'lt')] for v in values])

    steps = [[atom('signal_type', e['signal_type'])] for e in chain]
    for name, p in dsl.RELATIONS.items():
        if not p.mine:
            continue
        values = [p.read(a, b) for a, b in zip(chain, chain[1:])]
        if any(v is None for v in values):
            continue
        if p.kind == 'bool':
            relations = [edge(name, v, 'eq', i, i + 1) for i, v in enumerate(values)]
        elif name == 'gap_seconds':
            relations = [edge(name, 1800, 'le' if v <= 1800 else 'gt', i, i + 1) for i, v in enumerate(values)]
        else:
            center = 1 if name == 'volume_ratio' else 0
            relations = [edge(name, center, 'eq' if v == center else 'lt' if v < center else 'gt', i, i + 1)
                         for i, v in enumerate(values)]
        yield name, definition(steps, relations)
        if len(chain) > 2:
            # Cross the trajectory with a non-adjacent contextual relation.
            same = dsl.RELATIONS['same_source'].read(chain[0], chain[-1])
            yield name + '+origin', definition(steps, [*relations, edge('same_source', same, 'eq', 0, len(chain) - 1)])

    # Contrast a change in a measured relation with an empirically high
    # anchor feature. The axes are typed and may change across DSL versions.
    a, b = chain[-2:]
    for metric in ('odds_delta_bp', 'odds_velocity_bp_hour', 'volume_ratio', 'goal_delta'):
        change = dsl.RELATIONS[metric].read(a, b)
        if change is None:
            continue
        center = 1 if metric == 'volume_ratio' else 0
        for name, cuts in thresholds.items():
            v = dsl.FIELDS[name].read(b)
            if v is not None and v >= cuts[-1]:
                tail = [*steps[-1], atom(name, cuts[-1], 'ge')]
                change_op = 'eq' if change == center else 'gt' if change > center else 'lt'
                yield metric + '+' + name, definition([*steps[:-1], tail],
                    [edge(metric, center, change_op, len(chain) - 2, len(chain) - 1)])


def mine(replay, known=(), *, emissions=MAX_EMISSIONS, limit=MAX_HYPOTHESES):
    threshold = samples(replay)
    found = {}
    budget = Budget(emissions)
    scanned = 0
    n = min(MAX_ANCHORS, len(replay.anchors))
    sampled = [replay.anchors[(2 * i + 1) * len(replay.anchors) // (2 * n)] for i in range(n)]
    for at, event in sampled:
        anchor, prefix = replay.prefix(event, at, DISCOVERY_WINDOW)
        scanned += 1
        for chain in chains(prefix, anchor):
            for family, rule in projections(chain, threshold):
                if not budget.spend():
                    break
                key = dsl.encoded(rule)
                if key not in found:
                    found[key] = (family, set())
                found[key][1].add(event['fixture_id'])
            if budget.exhausted:
                break
        if budget.exhausted:
            break

    pools = defaultdict(list)
    known = set(known)
    for key, (family, fixtures) in found.items():
        if len(fixtures) < MIN_SCREEN_GAMES:
            continue
        rule = dsl.canonical(dsl.strict_json(key))
        ident = dsl.identifier(rule)
        if ident not in known:
            pools[(len(rule['steps']), family)].append((-len(fixtures), ident, rule))
    # Round-robin by length and projection so a populous dimension cannot
    # consume every slot. Support is a screen, never a measure of return.
    by_length = defaultdict(list)
    for (length, _), pool in sorted(pools.items()):
        by_length[length].append(iter(sorted(pool, key=lambda item: item[:2])))
    ranked = [iter(record for group in zip_longest(*by_length[length]) for record in group if record is not None)
              for length in sorted(by_length)]
    proposals = []
    seen = set(known)
    for group in zip_longest(*ranked):
        for record in group:
            if record is None:
                continue
            _, ident, rule = record
            if ident in seen:
                continue
            seen.add(ident)
            proposals.append(Candidate(rule, 'deterministic',
                'Sequência observada em jogos distintos. Investigar se muda o desfecho da âncora comparável.',
                'Procurar perdas no grupo exposto, vitórias nos controlos e inversão do efeito por fonte ou dia.',
                contrast(rule)))
            if len(proposals) == limit:
                break
        if len(proposals) == limit:
            break
    return proposals, dict(pattern_observations=budget.used, generator_truncated=budget.exhausted,
                           unique_projected_rules=len(found), anchors_scanned=scanned,
                           anchors_available=len(replay.anchors), numeric_thresholds=threshold,
                           screened_proposals=len(proposals))
