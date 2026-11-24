"""Unsupervised recurrence search with a bounded, hierarchical contrast beam."""

from collections import Counter, defaultdict
from itertools import combinations, groupby, islice, product
from math import floor, isfinite, log1p, log10, sqrt

from ... import event_intelligence as events
from .. import dsl
from ..v32 import language
from .observed import ADAPTER, Observed
from .rules import FIELDS, RELATIONS, Meter, relation_value

VERSION = 'trajectory-recurrence-v1'
WINDOW = 6 * 3600
WORK = 1_800_000
MAX_ANCHORS = 384
TAIL = 12
PATHS_PER_LENGTH = 8
PER_SHAPE = 2
LIMIT = 24
MIN_FIXTURES = 3
MIN_DAYS = 2
BEAM = 12
MAX_ADDITIONS = 8
GAP_CUTS = (60, 120, 300, 600, 900, 1800, 3600, 7200, 14400)
CENTERS = {'volume_ratio': 1, **dict.fromkeys((
    'odds_delta_bp', 'odds_velocity_bp_hour', 'goal_delta', 'goal_difference_delta', 'line_delta',
    'volume_cumulative_usd_delta', 'biggest_ticket_usd_delta', 'n_bets_delta'), 0)}


def _paths(decision):
    # Simultaneous rows can be alternatives at one position, never successive steps.
    history = decision.within(WINDOW)
    groups = [tuple(rows) for _, rows in groupby(history, key=events.emitted)][-TAIL:]
    for span in range(min(dsl.MAX_STEPS - 1, len(groups)), 0, -1):
        choices = combinations(reversed(groups), span)
        variants = (tuple(reversed(path)) + (decision.quote,) for subset in choices for path in product(*subset))
        yield from islice(variants, PATHS_PER_LENGTH)


def _observations(observed, meter):
    by_fixture = defaultdict(list)
    for decision in observed.decisions(meter):
        by_fixture[decision.fixture].append(decision)
    # A noisy fixture gets one turn before any fixture gets its next turn.
    fixtures = sorted(by_fixture, key=lambda fixture: (by_fixture[fixture][0].at, fixture))
    current = [iter(by_fixture[fixture]) for fixture in fixtures]
    count = 0
    shapes = defaultdict(list)
    meter.stage = 'paths'
    while current and not meter.exhausted and count < MAX_ANCHORS:
        later = []
        for cursor in current:
            decision = next(cursor, None)
            if decision is None:
                continue
            if count >= MAX_ANCHORS or not meter.spend():
                return shapes, count, True
            count += 1
            later.append(cursor)
            for path in _paths(decision):
                if not meter.charge(len(path)):
                    return shapes, count, True
                shape = tuple(row['event_kind'] for row in path)
                shapes[shape].append((decision, path))
        current = later
    return shapes, count, meter.exhausted or count == MAX_ANCHORS


def _numeric_cuts(values):
    values = sorted(set(values))
    if len(values) < 2:
        return ()
    cuts = set()
    for fraction in (.2, .5, .8):
        i = min(len(values) - 1, max(1, floor(len(values) * fraction)))
        low, high = values[i - 1], values[i]
        mid = (low + high) / 2
        magnitude = 10 ** max(-7, floor(log10(high - low)))
        rounded = round(mid / magnitude) * magnitude
        cut = round(rounded if low < rounded <= high else mid, 7)
        if low < cut <= high:
            cuts.add(cut)
    return sorted(cuts)


def _literals(name, primitive, values):
    if primitive.kind != 'number':
        for value in sorted(set(values)):
            if primitive.kind == 'bool' or (0 < len(value) <= 100 and value.isprintable()):
                yield 'eq', value
    elif name in CENTERS:
        center = CENTERS[name]
        # The unit and neutral element distinguish increase, stability and decrease.
        yield from ((op, center) for op in ('lt', 'eq', 'gt'))
        if name not in ('odds_velocity_bp_hour', 'goal_difference_delta'):
            for cut in _numeric_cuts(values):
                yield 'lt', cut
                yield 'ge', cut
    else:
        cuts = GAP_CUTS if name == 'gap_seconds' else _numeric_cuts(values)
        for cut in cuts:
            yield 'lt', cut
            yield 'ge', cut


def _bit_indexes(mask):
    while mask:
        bit = mask & -mask
        yield bit.bit_length() - 1
        mask ^= bit


def _feature_order(clause):
    left, right, name, op, value = clause
    # Reuse portable relations in place of correlated absolute levels. A
    # comparator at a natural zero/identity also travels across price regimes.
    neutral = name in CENTERS and value == CENTERS[name]
    order = 0 if neutral else 1 if right >= 0 and name != 'gap_seconds' else 2 if right < 0 else 3
    return order, left - right if right >= 0 else 0, clause


def _columns(paths, length, meter, support):
    descriptors = [(i, -1, name, p) for i in range(length) for name, p in sorted(FIELDS.items())
                   if p.mine and name != 'event_kind' and not p.anchor_only]
    descriptors += [(i, j, name, p) for i, j in combinations(range(length), 2)
                    for name, p in sorted(RELATIONS.items()) if p.mine and p.read is not None]
    all_rows = (1 << len(paths)) - 1
    by_mask = {}
    redundant = 0
    for i, j, name, primitive in descriptors:
        if not meter.charge(len(paths)):
            return by_mask, redundant
        values = [primitive.read(path[i]) if j < 0 else relation_value(name, path[i], path[j])
                  for _, path in paths]
        available = [(k, value) for k, value in enumerate(values) if value is not None and (primitive.kind != 'number'
                     or (type(value) in (int, float) and isfinite(value) and abs(value) <= 1e9))]
        known = [value for _, value in available]
        known_mask = sum(1 << k for k, _ in available)
        for op, threshold in _literals(name, primitive, known):
            if not meter.charge(len(paths)):
                return by_mask, redundant
            compare = dsl.OPS[op]
            mask = sum(1 << k for k, value in available if compare(value, threshold))
            if mask in (0, all_rows) or not support(mask):
                continue
            clause = (i, j, name, op, threshold)
            existing = by_mask.get(mask)
            if existing is not None:
                redundant += 1
            if existing is None or _feature_order(clause) < _feature_order(existing[0]):
                by_mask[mask] = clause, known_mask
    return by_mask, redundant


def _definition(shape, clauses):
    steps = [[dict(field='event_kind', op='eq', value=kind)] for kind in shape]
    relations = []
    for left, right, name, op, value in clauses:
        atom = dict(field=name, op=op, value=value)
        if right < 0:
            steps[left].append(atom)
        else:
            relations.append(dict(atom, **{'from': left, 'to': right}))
    return language.canonical(dict(version=language.VERSION, within_seconds=WINDOW, steps=steps, relations=relations))


def _shape_beam(shape, paths, meter, stats):
    all_rows = (1 << len(paths)) - 1
    fixtures = tuple(decision.fixture for decision, _ in paths)
    days = tuple(decision.at.date() for decision, _ in paths)
    support_cache = {}

    def support(mask):
        if mask not in support_cache:
            # Charge the distinct witness memberships. No search is declared complete on exhaustion.
            if not meter.charge(mask.bit_count()):
                return 0
            indexes = tuple(_bit_indexes(mask))
            count = len({fixtures[k] for k in indexes})
            support_cache[mask] = count if count >= MIN_FIXTURES and len({days[k] for k in indexes}) >= MIN_DAYS else 0
        return support_cache[mask]

    meter.stage = 'features'
    base_support = support(all_rows)
    if not base_support:
        return []
    by_mask, redundant = _columns(paths, len(shape), meter, support)
    stats['equivalent_features'] += redundant
    if meter.exhausted:
        return []
    terms = tuple(sorted(((mask, clause) for mask, (clause, _) in by_mask.items()),
                         key=lambda value: _feature_order(value[1])))
    freq = {clause: support(mask) / base_support for mask, clause in terms}
    masks = {clause: mask for mask, clause in terms}

    def quality(mask, clauses):
        n = support(mask)
        rarity = sum(-log1p(freq[c] - 1) for c in clauses)
        portable = sum(_feature_order(c)[0] == 0 for c in clauses)
        # Description-length heuristic. Keep repeated full witnesses and prefer
        # informative, independently testable changes when evidence ties.
        return sqrt(n) * (len(shape) + 1.45 * rarity + .38 * portable) - .30 * len(clauses)

    def keep(states):
        return sorted(states.items(), key=lambda x: (-quality(*x), len(x[1]), x[1]))[:BEAM]

    meter.stage = 'beam'
    master = {all_rows: ()}
    frontier = [(all_rows, ())]
    for _ in range(MAX_ADDITIONS):
        next_level = {}
        for parent, clauses in frontier:
            for column, clause in terms:
                if not meter.spend():
                    return []
                child = parent & column
                if child == parent or child in master or not support(child):
                    continue
                attached = tuple(sorted((*clauses, clause)))
                if len(attached) > 7 and any(sum(c[0] == i and c[1] == -1 for c in attached) > 7
                                              for i in range(len(shape))):
                    continue
                prior = next_level.get(child)
                if prior is None or tuple(map(_feature_order, attached)) < tuple(map(_feature_order, prior)):
                    next_level[child] = attached
        if meter.exhausted or not next_level:
            break
        frontier = keep(next_level)
        master.update(frontier)
        stats['beam_survivors'] += len(frontier)
    if meter.exhausted:
        return []

    ranked = sorted(master.items(), key=lambda item: (-quality(*item), len(item[1]), item[1]))
    compound = next((item for item in ranked if len({(c[0], c[1]) for c in item[1]}) > 1), None)
    if compound is not None:
        ranked = [compound, *(item for item in ranked if item != compound)]
    chosen = []
    for mask, clauses in ranked:
        try:
            rule = _definition(shape, clauses)
        except dsl.Invalid:
            continue
        evidence = tuple((paths[k][0].key, tuple(row['message_id'] for row in paths[k][1]))
                         for k in _bit_indexes(mask))
        if not meter.charge(len(evidence)):
            return []
        counterexamples, alternative, difference = [], None, -1
        for clause in clauses:
            left, right, name, op, value = clause
            if (right < 0 and left == len(shape) - 1) or not meter.spend():
                continue
            # Removing one constraint highlights genuinely different witnesses.
            others = tuple(c for c in clauses if c != clause)
            parent = all_rows
            for other in others:
                parent &= masks[other]
            misses = parent & (by_mask[masks[clause]][1] ^ masks[clause])
            if misses and misses.bit_count() > difference:
                alternative = _definition(shape, (*others, (left, right, name, dsl.NEGATE[op], value)))
                counterexamples = [dsl.digest(paths[k][0].key) for k in islice(_bit_indexes(misses), 3)]
                difference = misses.bit_count()
        chosen.append(dict(id=language.identifier(rule), rule=rule, alternative=alternative,
                           contrast_counterexamples=counterexamples, contrast_witness_count=max(0, difference),
                           score=round(quality(mask, clauses), 9), support=support(mask), evidence=evidence))
        if len(chosen) == PER_SHAPE:
            break
    return chosen


def discover(tape, *, work=WORK):
    if type(work) is not int or work < 0:
        raise ValueError('work must be a nonnegative integer')
    meter, stats = Meter(work), Counter()
    shapes, count, anchor_capped = _observations(Observed(tape), meter)
    ranked = sorted(shapes, key=lambda shape: (-len(shape), -len(shapes[shape]), shape))
    pool = []
    for shape in ranked:
        if meter.exhausted:
            break
        pool.extend(_shape_beam(shape, shapes[shape], meter, stats))
    shortlisted, footprints = [], set()
    for row in sorted(pool, key=lambda row: (-row['score'], row['id'])):
        footprint = dsl.digest(sorted(set(row['evidence'])))
        if footprint in footprints:
            stats['equivalent_rules'] += 1
            continue
        footprints.add(footprint)
        shortlisted.append({key: value for key, value in row.items() if key != 'evidence'} | dict(witness_sha256=footprint))
        if len(shortlisted) == LIMIT:
            break
    return dict(version=VERSION, adapter=ADAPTER, candidates=shortlisted,
                work=dict(limit=work, used=meter.used, exhausted=meter.exhausted, stages=dict(meter.stages),
                          anchors=count, anchor_scan_capped=anchor_capped, shapes=len(shapes),
                          tail_issue_batches=TAIL, paths_per_length=PATHS_PER_LENGTH, beam_width=BEAM, **stats),
                confirmatory=False)
