"""Budgeted structure-first temporal discovery with integer-only ranking."""

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta
from itertools import islice

from ... import event_intelligence as events
from .. import dsl
from ..experimental_h.observed import ADAPTER, Observed
from ..experimental_h.rules import Meter
from ..v32 import language
from . import features, paths

VERSION = 'balanced-trajectory-portfolio-v1'
WORK = 1_800_000
LIMIT = 24
SHAPES_PER_BAND = 7
BEAM = 8
DEPTH = 5
MIN_FIXTURES = 3
MIN_DAYS = 2
CHANNELS = ('relative', 'structural', 'mixed')


def _indices(bits):
    while bits:
        first = bits & -bits
        yield first.bit_length() - 1
        bits ^= first


def _shares(total, ordered):
    if not ordered:
        return {}
    base, extra = divmod(total, len(ordered))
    return {key: base + (i < extra) for i, key in enumerate(ordered)}


class PortfolioBudget:
    """Charged stage totals sum to an upper bound fixed before observation."""

    def __init__(self, limits):
        self.limits = limits
        self.meters = {key: Meter(value, stage='structure') for key, value in limits.items()}

    def report(self):
        return {key: dict(limit=m.limit, used=m.used, exhausted=m.exhausted, stages=dict(m.stages))
                for key, m in self.meters.items()}


def _define(phase, shape, clauses):
    steps = [[dict(field='event_kind', op='eq', value=kind)] for kind in shape]
    steps[-1].append(dict(field='phase', op='eq', value=phase))
    edges = []
    for term in clauses:
        atom = dict(field=term.field, op=term.op, value=term.value)
        if term.right < 0:
            steps[term.left].append(atom)
        else:
            edges.append(dict(atom, **{'from': term.left, 'to': term.right}))
    return language.canonical(dict(version=language.VERSION, within_seconds=paths.WINDOW, steps=steps, relations=edges))


@dataclass(frozen=True)
class State:
    passed: int
    rejected: int
    clauses: tuple


class Shape:
    def __init__(self, key, rows):
        self.phase, self.kinds = key
        self.rows = rows
        self.all = (1 << len(rows)) - 1
        self.fixtures = tuple(d.fixture for d, _ in rows)
        self.days = tuple(d.at.date() for d, _ in rows)
        self.terminal = tuple(d.key for d, _ in rows)
        self.fixture_index = {fixture: i for i, fixture in enumerate(sorted(set(self.fixtures)))}
        self.fixture_bits = tuple(1 << self.fixture_index[fixture] for fixture in self.fixtures)
        durations = sorted((d.at - events.emitted(path[0])) // timedelta(minutes=1)
                           for d, path in rows)
        self.span = durations[len(durations) // 2]
        self.cache = {}
        self.base = self.support(self.all)

    def support(self, bits):
        if bits not in self.cache:
            indexes = tuple(_indices(bits))
            footprint = sum({self.fixture_bits[i] for i in indexes})
            self.cache[bits] = (footprint.bit_count(),
                                len({self.days[i] for i in indexes}),
                                len({self.terminal[i] for i in indexes}), footprint)
        return self.cache[bits]

    def supported(self, bits, meter):
        if bits not in self.cache and not meter.charge(bits.bit_count()):
            return False
        count, days, _, _ = self.support(bits)
        return count >= MIN_FIXTURES and days >= MIN_DAYS

    def quality(self, state):
        fixtures, days, terminals, footprint = self.support(state.passed)
        known_rejections = (self.support(state.rejected)[3] & ~footprint).bit_count()
        unknown = self.base[0] - self.support(state.passed | state.rejected)[0]
        portable = sum(term.relative for term in state.clauses)
        spanning = sum(max(0, term.right - term.left - 1) for term in state.clauses)
        uniqueness = 1000 * terminals // state.passed.bit_count()
        # Fixed point. Long recurrent paths and observed contradictions both
        # matter. Unknown alternatives are penalized, never given rejection credit.
        return (45 * min(fixtures, 16) + 32 * days + 95 * len(self.kinds) + uniqueness // 10
                + 34 * min(known_rejections, 14) - 14 * unknown + 27 * portable
                + 8 * spanning - 21 * len(state.clauses) - 14 * min(30, self.span))

    def assess(self, state, meter):
        for bits in (state.passed, state.rejected, state.passed | state.rejected):
            if bits not in self.cache and not meter.charge(bits.bit_count()):
                return None
        return self.quality(state)

    def row(self, state, score, meter):
        if not meter.charge(state.passed.bit_count() * len(self.kinds)):
            return None
        rule = _define(self.phase, self.kinds, state.clauses)
        evidence = [(self.terminal[i], tuple(row['message_id'] for row in self.rows[i][1]))
                    for i in _indices(state.passed)]
        footprint = self.support(state.passed)[3]
        contradicted = (i for i in _indices(state.rejected) if not self.fixture_bits[i] & footprint)
        signatures = [self.terminal[i] for i in islice(contradicted, 3)]
        return dict(id=language.identifier(rule), rule=rule, phase=self.phase, shape=self.kinds,
                    channel=('structural' if not state.clauses else 'relative'
                             if all(c.relative for c in state.clauses) else 'mixed'),
                    rank_score=score, support=self.support(state.passed)[0],
                    observed_contrast_fixtures=(self.support(state.rejected)[3] & ~footprint).bit_count(),
                    witness_sha256=dsl.digest(evidence),
                    contrast_examples_sha256=[dsl.digest(item) for item in signatures])


def _beam(shape, columns, meter):
    base = State(shape.all, 0, ())
    frontier = [base]
    kept = []
    meter.stage = 'expansion'
    for _ in range(DEPTH):
        branches = {}
        for previous in frontier:
            slots = {c.slot for c in previous.clauses}
            for column in columns:
                if not meter.spend():
                    break
                if column.clause.slot in slots:
                    continue
                hit = previous.passed & column.passed
                reject = previous.rejected | (column.known ^ column.passed)
                if (hit, reject) == (previous.passed, previous.rejected) or not shape.supported(hit, meter):
                    continue
                terms = tuple(sorted((*previous.clauses, column.clause)))
                if terms in branches:
                    continue
                child = State(hit, reject, terms)
                score = shape.assess(child, meter)
                if score is not None:
                    branches[terms] = (child, score)
            if meter.exhausted:
                break
        # Equal empirical support can have different meanings. Keep separate
        # slots for neutral relations and mixed clauses, and limit congestion
        # by behaviour without declaring those rules logically equivalent.
        candidates = sorted(branches.values(), key=lambda item: (-item[1], tuple(c.order() for c in item[0].clauses)))
        behaviour = Counter()
        types = Counter()
        frontier = []
        for candidate, score in candidates:
            profile = candidate.passed, candidate.rejected
            relative = all(c.relative for c in candidate.clauses)
            if behaviour[profile] >= 2 or types[relative] >= BEAM // 2:
                continue
            behaviour[profile] += 1
            types[relative] += 1
            frontier.append(candidate)
            kept.append((candidate, score))
            if len(frontier) == BEAM:
                break
        if not frontier or meter.exhausted:
            break
    return kept


def _lane_name(phase, band):
    return f'{phase}/{(2, 4, 6)[band]}-{(3, 5, 8)[band]}'


def _rank(row):
    return -row['rank_score'], -len(row['shape']), row['id']


def _shortlist(rows):
    by_phase = defaultdict(lambda: defaultdict(deque))
    for row in sorted(rows, key=_rank):
        key = row['channel'], len(row['shape'])
        by_phase[row['phase']][key].append(row)

    for lanes in by_phase.values():
        for key, lineup in lanes.items():
            shapes = defaultdict(deque)
            for row in lineup:
                shapes[row['shape']].append(row)
            turns = deque(shapes.values())
            ordered = deque()
            while turns:
                candidates = turns.popleft()
                ordered.append(candidates.popleft())
                if candidates:
                    turns.append(candidates)
            lanes[key] = ordered

    def phase_order(queues):
        ordered = [queues[key] for length in (8, 7, 6, 5, 4, 3, 2) for channel in CHANNELS
                   if (key := (channel, length)) in queues]
        while ordered:
            active = []
            for queue in ordered:
                yield queue.popleft()
                if queue:
                    active.append(queue)
            ordered = active

    phases = deque(iter(phase_order(queues)) for _, queues in sorted(by_phase.items()))
    shortlisted, seen = [], set()
    while phases and len(shortlisted) < LIMIT:
        cursor = phases.popleft()
        row = next(cursor, None)
        if row is None:
            continue
        phases.append(cursor)
        if row['id'] not in seen:
            seen.add(row['id'])
            shortlisted.append(row)
    return shortlisted


def discover(tape, *, work=WORK):
    if type(work) is not int or not 0 <= work <= WORK:
        raise ValueError('work must be an integer within the frozen budget')
    observation_meter = Meter(work // 5)
    sampled, anchors, reasons = paths.sample(Observed(tape), observation_meter)
    lanes = defaultdict(list)
    for (phase, kinds), entries in sorted(sampled.items()):
        if len(entries) >= MIN_FIXTURES:
            lanes[phase, paths.band(len(kinds))].append(((phase, kinds), entries))
    sizes = defaultdict(list)
    for phase, band in sorted(lanes):
        sizes[phase].append(band)
    remaining = work - observation_meter.used
    phase_shares = _shares(remaining, sorted(sizes))
    shares = {_lane_name(phase, band): amount for phase, bands in sizes.items()
              for band, amount in _shares(phase_shares[phase], bands).items()}
    pool = []
    value_cache, row_keys = {}, {}
    budget = PortfolioBudget(shares)
    shape_caps = {}
    for (phase, band), samples in sorted(lanes.items()):
        name = _lane_name(phase, band)
        lane = budget.meters[name]
        supported = []
        for key, entries in samples:
            if not lane.charge(len(entries)):
                break
            shape = Shape(key, entries)
            if shape.base[0] >= MIN_FIXTURES and shape.base[1] >= MIN_DAYS:
                supported.append(shape)
        supported.sort(key=lambda shape: (-shape.base[0], -shape.base[1],
                                          -len(shape.kinds), len(shape.rows), shape.kinds))
        shape_caps[name] = dict(recurrent=len(supported), expanded=min(SHAPES_PER_BAND, len(supported)),
                                sampled_shapes=len(samples))
        by_length = defaultdict(deque)
        for shape in supported:
            by_length[len(shape.kinds)].append(shape)
            lane.stage = 'structural_rules'
            base = State(shape.all, 0, ())
            if (row := shape.row(base, shape.quality(base), lane)) is not None:
                pool.append(row)
        for length, queue in by_length.items():
            by_length[length] = deque(sorted(queue, key=lambda s: (-s.quality(State(s.all, 0, ())), s.kinds)))
        priorities = deque(by_length[length] for length in sorted(by_length, reverse=True))
        parents = []
        while priorities and len(parents) < SHAPES_PER_BAND:
            queue = priorities.popleft()
            parents.append(queue.popleft())
            if queue:
                priorities.append(queue)
        costs = [len(shape.rows) * len(shape.kinds) ** 2 for shape in parents]
        for i, shape in enumerate(parents):
            # Phase and length family keep their reservation even when a dense
            # first shape spends its share. Preserve the structural proposal first.
            remaining = lane.limit - lane.used
            floor = remaining // (3 * (len(parents) - i))
            variable = remaining - floor * (len(parents) - i)
            local = Meter(floor + variable * costs[i] // sum(costs[i:]), stage='features')
            columns = features.columns(shape.rows, len(shape.kinds), local,
                                       lambda mask: shape.supported(mask, local), value_cache, row_keys)
            # Reserve a separate output budget inside the shape before refining.
            expansion = Meter(max(0, (local.limit - local.used) * 9 // 10), stage='expansion')
            candidates = _beam(shape, columns, expansion)
            local.stage = 'expansion'
            local.charge(expansion.used)
            local.stage = 'refined_rules'
            for channel in ('relative', 'mixed'):
                eligible = [(state, score) for state, score in candidates
                            if all(c.relative for c in state.clauses) == (channel == 'relative')]
                for state, score in sorted(eligible, key=lambda item: (-item[1], item[0].clauses))[:2]:
                    if (row := shape.row(state, score, local)) is not None:
                        pool.append(row)
            lane.used += local.used
            lane.stages.update(local.stages)
            if local.exhausted or expansion.exhausted:
                reasons.append(f'shape_budget:{name}:{"/".join(shape.kinds)}')
        if lane.exhausted:
            reasons.append(f'family_budget:{name}')
    used = observation_meter.used + sum(m.used for m in budget.meters.values())
    return dict(version=VERSION, adapter=ADAPTER, candidates=_shortlist(pool),
                work=dict(limit=work, used=used, observations=dict(observation_meter.stages),
                          anchors=anchors, families=budget.report(), shapes=shape_caps,
                          approximation=dict(max_shapes_per_family=SHAPES_PER_BAND, beam=BEAM, depth=DEPTH,
                                             tail_issue_batches=paths.TAIL, variants_per_length=paths.PATHS_PER_LENGTH),
                          incomplete=sorted(set(reasons)), search_complete=not reasons),
                confirmatory=False)
