"""Outcome-blind motif mining with projected prefixes and a diverse shortlist."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations, zip_longest
from math import log, log1p, sqrt

from .. import event_intelligence as events
from . import dsl, dsl_v2, miner
from .replay import Budget

WINDOW = miner.DISCOVERY_WINDOW
MAX_ANCHORS = 4096
MAX_EMISSIONS = 236_000
MAX_SIGNATURES = 32_000
MAX_CANDIDATES = 54
MAX_REPLAY_WORK = 2_000_000
MIN_SUPPORT = 4
MIN_DAYS = 2
MAX_WITNESSES = 18
# Projections over complementary representations of a whole path. These
# symbols never constrain the values or the ordering of the events.
CATEGORICAL = ('signal_type', 'source', 'direction', 'phase', 'market_key', 'period', 'score_state')
JOINT_VIEWS = tuple(combinations(('source', 'direction', 'phase', 'score_state', 'line'), 2))
TRAJECTORIES = ('odds_delta_bp', 'goal_difference_delta', 'line_delta', 'volume_ratio', 'same_score_state', 'same_direction')
NUMERIC = ('point_odds', 'volume_usd', 'minute', 'share_pct', 'goals')


def atom(name, value, op='eq'):
    return name, op, value


def link(a, b, name, value, op='eq'):
    return a, b, name, op, value


def rule(key):
    steps, relations = key
    return dict(version=dsl_v2.VERSION, within_seconds=WINDOW,
                steps=[[miner.atom(name, value, op) for name, op, value in step] for step in steps],
                relations=[miner.edge(name, value, op, a, b) for a, b, name, op, value in relations])


@dataclass(frozen=True)
class Candidate(miner.Candidate):
    @property
    def id(self):
        return dsl_v2.identifier(self.rule)


@dataclass
class Motif:
    key: tuple
    family: tuple
    fixtures: set = field(default_factory=set)
    days: set = field(default_factory=set)
    populations: set = field(default_factory=set)
    examples: list = field(default_factory=list)

    @property
    def length(self):
        return len(self.key[0])

    def observe(self, path, day, population):
        fixture = path[-1]['fixture_id']
        if fixture not in self.fixtures and len(self.examples) < MAX_WITNESSES:
            self.examples.append(path)
        self.fixtures.add(fixture)
        self.days.add(day)
        self.populations.add(population)


def population(e):
    return e['source'], e['phase'], e['period']


def sample(timeline):
    """Round-robin across phases/sources/periods and across weeks of the snapshot."""
    groups = defaultdict(list)
    for i, (t, row, _) in enumerate(timeline.eligible_anchors):
        groups[(*population(row), t.date().toordinal() // 7)].append(i)
    iterators = []
    for _, items in sorted(groups.items(), key=lambda item: (len(item[1]), str(item[0]))):
        # Midpoint ordering keeps early prefixes from consuming a small budget.
        def spread(indices):
            queue = [(0, len(indices))]
            while queue:
                new = []
                for lo, hi in queue:
                    middle = (lo + hi) // 2
                    yield indices[middle]
                    if lo < middle:
                        new.append((lo, middle))
                    if middle + 1 < hi:
                        new.append((middle + 1, hi))
                queue = new
        iterators.append(spread(items))
    for group in zip_longest(*iterators):
        yield from (i for i in group if i is not None)


def paths(context):
    """Extend real prefixes up to eight steps in nested market views."""
    anchor = context.anchor
    scopes = (lambda _: True,
              lambda e: e['market_key'] == anchor['market_key'] and e['period'] == anchor['period']
                        and e['line'] == anchor['line'],
              lambda e: e['source'] == anchor['source'] and e['selection'] == anchor['selection']
                        and e['market'] == anchor['market'] and e['period'] == anchor['period'])
    orders = []
    for view in scopes:
        chain = [anchor]
        newest = events.emitted(anchor)
        for event in reversed(context.prefix):
            sent = events.emitted(event)
            if sent < newest and view(event):
                chain.append(event)
                newest = sent
                if len(chain) == dsl_v2.MAX_STEPS:
                    break
        orders.append(chain)
    seen = set()
    for length in range(2, dsl_v2.MAX_STEPS + 1):
        for column in orders:
            if len(column) < length:
                continue
            path = tuple(reversed(column[:length]))
            key = tuple(e['message_id'] for e in path)
            if key not in seen:
                seen.add(key)
                yield path


def curve(value, center):
    if type(value) is bool:
        return 'eq', value
    return ('lt' if value < center else 'gt' if value > center else 'eq'), center


def projections(path, fields, marginals):
    for names in (*((name,) for name in CATEGORICAL), *JOINT_VIEWS):
        steps = tuple(tuple(atom(name, fields(e, name)) for name in names) for e in path)
        if all(a[-1] is not None for step in steps for a in step):
            yield ('event',) + names, (steps, ())
    # Repeated quantitative values may form a state sequence. Require
    # marginal recurrence rather than enumerating arbitrary decimal cutoffs.
    for name in NUMERIC:
        values = [fields(e, name) for e in path]
        if all(v is not None and marginals[name, v] >= MIN_SUPPORT for v in values):
            yield ('level', name), (tuple((atom(name, v),) for v in values), ())
    for name in TRAJECTORIES:
        values = [dsl_v2.relation(name, a, b) for a, b in zip(path, path[1:])]
        if any(v is None for v in values):
            continue
        edges = tuple(link(i, i + 1, name, value, op) for i, (op, value) in
                      enumerate(curve(v, 1 if name == 'volume_ratio' else 0) for v in values))
        # A relative price path is scoped by its primitive. Include the
        # available signal roles as additional structural evidence.
        steps = tuple((atom('signal_type', e['signal_type']),) for e in path)
        yield ('path', name), (steps, edges)


def quantiles(timeline, fields):
    values = defaultdict(list)
    marginals = Counter()
    observations = 0
    for history in timeline.replay.history.values():
        for e in history:
            observations += 1
            for name in (*CATEGORICAL, 'line'):
                value = fields(e, name)
                if value is not None:
                    marginals[name, value] += 1
            for name in NUMERIC:
                value = fields(e, name)
                if value is not None:
                    values[name].append(value)
                    marginals[name, value] += 1
    cuts = {}
    for name, numbers in values.items():
        numbers.sort()
        if len(numbers) >= MIN_SUPPORT:
            cuts[name] = tuple(sorted(set(round(numbers[int(len(numbers) * p)], 4)
                                          for p in (.25, .50, .75, .90))))
    return cuts, marginals, observations


def refinements(motif, thresholds, fields):
    """Expand a supported motif with observed predicates and long-range edges."""
    counts, witnesses = Counter(), defaultdict(list)
    steps, edges = motif.key
    for path in motif.examples:
        additions = []
        for index, event in enumerate(path):
            present = {a[0] for a in steps[index]}
            for name in (*CATEGORICAL, 'line', *NUMERIC):
                if name in present or (v := fields(event, name)) is None:
                    continue
                if name in thresholds:
                    bounds = thresholds[name]
                    low = next((x for x in reversed(bounds) if x <= v), None)
                    high = next((x for x in bounds if v < x), None)
                    if low is not None:
                        additions.append(('event', index, *atom(name, low, 'ge')))
                    if high is not None:
                        additions.append(('event', index, *atom(name, high, 'lt')))
                elif name not in NUMERIC:
                    additions.append(('event', index, *atom(name, v)))
        for left, right in combinations(range(len(path)), 2):
            a, b = path[left], path[right]
            for name in ('same_source', 'same_direction', 'same_line', 'same_score_state'):
                value = dsl_v2.relation(name, a, b)
                if value is not None:
                    additions.append(('edge', *link(left, right, name, value)))
            gap = (events.emitted(b) - events.emitted(a)).total_seconds()
            for threshold in (300, 900, 1800, 3600, 5400, 7200, 10800):
                additions.append(('edge', *link(left, right, 'gap_seconds', threshold,
                                                 'le' if gap <= threshold else 'gt')))
        for addition in dict.fromkeys(additions):
            counts[addition] += 1
            witnesses[addition].append(path)

    pool = defaultdict(list)
    for change, count in counts.items():
        if count < MIN_SUPPORT or count < .6 * len(motif.examples):
            continue
        key = change[0], change[2] if change[0] == 'event' else change[3]
        # Use one representative per kind, preferring terminal or endpoint
        # tests when support ties. Generate both interval and state probes.
        order = (-count, -change[1] if change[0] == 'event' else change[1] - change[2], change)
        pool[key].append((order, change))
    for _, options in sorted(pool.items(), key=lambda item: item[0]):
        _, change = min(options)
        if change[0] == 'event':
            _, position, *value = change
            modified = list(steps)
            modified[position] = tuple(sorted((*modified[position], tuple(value))))
            key = tuple(modified), edges
        else:
            edge = change[1:]
            if any(a[:3] == edge[:3] for a in edges):
                continue
            key = steps, tuple(sorted((*edges, edge)))
        yield key, ('refined', change[2] if change[0] == 'event' else change[3]), witnesses[change]


def quality(motif, marginals, observations, denominator, population_counts):
    steps, edges = motif.key
    n = len(motif.fixtures)
    # Approximate background by marginal symbol frequencies. This is a
    # structural ranking heuristic; it is not a p-value or return estimate.
    chance = 1.
    for step in steps:
        for name, op, value in step:
            chance *= ((marginals[name, value] + 1) / (observations + 2)) if op == 'eq' else .4
    chance *= .4 ** len(edges)
    surprise = log1p(n / (1 + denominator * chance))
    niche = 1 / sqrt(min((population_counts[p] for p in motif.populations), default=1))
    complexity = sum(map(len, steps)) + len(edges)
    return surprise * sqrt(n / (n + 8)) + .22 * log1p(n) + .12 * log1p(len(motif.days)) + niche - .025 * complexity


def similar(a, b):
    common = len(a & b)
    return common > 0 and common / len(a | b) >= .80


def mine(timeline, known=(), *, emissions=MAX_EMISSIONS, limit=MAX_CANDIDATES):
    meter = Budget(emissions)
    cache = {}

    def fields(e, name):
        key = e['message_id'], name, tuple(e['odds'] or ()) if name == 'point_odds' else None
        if key not in cache:
            cache[key] = dsl_v2.FIELDS[name].read(e)
        return cache[key]

    cuts, marginals, observations = quantiles(timeline, fields)
    candidates = {}
    pop = Counter()
    scanned = 0
    capped = False
    primary = Budget(int(.85 * emissions))

    def record(key, family, path, day, pop):
        nonlocal capped
        if key not in candidates:
            if len(candidates) >= MAX_SIGNATURES:
                capped = True
                return
            candidates[key] = Motif(key, family)
        candidates[key].observe(path, day, pop)

    for index in sample(timeline):
        if scanned >= MAX_ANCHORS:
            break
        context = timeline.context(index, WINDOW)
        scanned += 1
        group = population(context.anchor)
        pop[group] += 1
        for path in paths(context):
            for family, key in projections(path, fields, marginals):
                if not primary.spend():
                    break
                record(key, family, path, context.at.date(), group)
            if primary.exhausted:
                break
        if primary.exhausted:
            break
    meter.used = primary.used

    def supported(m):
        return len(m.fixtures) >= MIN_SUPPORT and len(m.days) >= MIN_DAYS

    def ranked(m):
        return -quality(m, marginals, observations, scanned, pop), -len(m.fixtures), m.key

    parents = defaultdict(list)
    for m in candidates.values():
        if supported(m):
            parents[m.length, m.family].append(m)
    # Expand a bounded, structurally varied group. The second pass changes
    # direction from greedy popularity to search within supported contexts.
    shortlist = []
    for key, values in sorted(parents.items()):
        shortlist.extend(sorted(values, key=ranked)[:2])
    expansions = 0
    for m in shortlist:
        if meter.exhausted:
            break
        # Charge the bounded set of endpoint/edge observations before
        # refinement, rather than only the few signatures it retains.
        charge = len(m.examples) * m.length * (m.length + 3)
        if meter.used + charge > meter.limit:
            meter.exhausted = True
            break
        meter.used += charge
        for key, family, witnesses in refinements(m, cuts, fields):
            if not meter.spend():
                break
            expansions += 1
            for path in witnesses:
                e = path[-1]
                record(key, family, path, events.seen(e).date(), population(e))

    grouped = defaultdict(list)
    for m in candidates.values():
        if supported(m):
            grouped[m.length].append(m)
    for values in grouped.values():
        values.sort(key=ranked)

    selected, footprints, ids = [], [], set(known)
    uses = Counter()
    while grouped and len(selected) < limit:
        for length in sorted(tuple(grouped)):
            values = [m for m in grouped[length] if all(not similar(m.fixtures, old) for old in footprints)]
            if not values:
                del grouped[length]
                continue
            winner = min(values[:96], key=lambda m: (ranked(m)[0] + .40 * uses[m.family],
                         -len(m.fixtures), m.key))
            values.remove(winner)
            grouped[length] = values
            definition = dsl_v2.canonical(rule(winner.key))
            ident = dsl_v2.identifier(definition)
            if ident in ids:
                continue
            ids.add(ident)
            uses[winner.family] += 1
            footprints.append(winner.fixtures)
            alternative = contrast(definition)
            selected.append(Candidate(definition, 'deterministic',
                'Sequência recorrente na amostra temporal, selecionada por suporte estrutural e diversidade.',
                'Verificar a dependência do antecedente através de uma variante e comparar antes de observar o desfecho.',
                alternative))
            if len(selected) == limit:
                break
    return selected, dict(anchors_available=len(timeline.eligible_anchors), anchors_scanned=scanned,
                          signature_limit_reached=capped, pattern_observations=primary.used,
                          projection_truncated=primary.exhausted, generator_operations=meter.used,
                          refinements=expansions, generator_truncated=meter.exhausted,
                          unique_projected_rules=len(candidates), screened_proposals=len(selected))


def contrast(definition):
    variant = dsl.strict_json(dsl.encoded(definition))
    atoms = variant['relations'] or next(step for step in variant['steps'][:-1] if step)
    for a in atoms:
        original = a['op']
        a['op'] = dsl.NEGATE[original]
        try:
            return dsl_v2.canonical(variant)
        except dsl.Invalid:
            a['op'] = original
    return None
