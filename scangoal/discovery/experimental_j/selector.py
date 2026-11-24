"""Label-free autonomous selection with explicit abstention.

Selection never sees outcomes, labels or evaluator banks. It uses only the
investigated tape and the proposals already generated for it:

1. Regular trajectory. A shape is regular when its pure structural rule fires
   on at least REGULAR of the fixtures of its best-supported sub-shape
   with one step removed (an approximately closed sequential pattern). The longest
   regular shape is preferred: shorter shapes omit a step that recurs, longer
   ones add a step that most fixtures do not show.
2. Dominant regime. The structural rule of that shape is refined by
   consensus (see refine.py): necessary, scale-free conditions with observed
   counterexamples only.
3. Refutation checks, each a reason to abstain: insufficient support or days,
   more than one terminal per fixture, incomplete matching, instability between
   halves of the investigated calendar, a competing regular shape of the same
   length with a comparable and different fixture set.
"""

from collections import defaultdict
from fractions import Fraction

from ..experimental_i import miner as miner_i
from ..v32 import language
from . import refine

VERSION = 'regular-shape-consensus-selector-v1'
REGULAR = Fraction(3, 4)
MIN_FIXTURES = 5
MIN_DAYS = 2
MAX_TERMINALS_PER_FIXTURE = Fraction(11, 10)
HALF_SHARE = Fraction(1, 4)
AMBIGUITY = Fraction(1, 2)


def _subsequence(short, long):
    it = iter(long)
    return len(short) < len(long) and all(kind in it for kind in short)


def structural(phase, shape):
    rule = miner_i._define(phase, tuple(shape), ())
    return dict(id=language.identifier(rule), rule=rule, phase=phase, shape=tuple(shape), channel='structural')


def _measure(proposal, index):
    firing = index.fire(proposal['id'], proposal['rule'])
    present = firing.present
    fixtures = index.fixtures(present)
    return dict(firing=firing, fixtures=fixtures, days=index.days(present), detections=len(present),
                complete=firing.complete)


def _halves(index, fixtures):
    early, late = index.calendar_halves()
    return len(fixtures & early), len(fixtures & late), len(early), len(late)


def _population(index, phase):
    return frozenset(index.fixture[i] for i, d in enumerate(index.decisions) if d.quote.get('phase') == phase)


def _reference(shape, phase, index, population):
    """Best support among shapes with one non-terminal step removed (the terminal anchors for length 2)."""
    if len(shape) == 2:
        return len(population)
    supports = []
    for drop in range(len(shape) - 1):
        sub = shape[:drop] + shape[drop + 1:]
        supports.append(len(_measure(structural(phase, sub), index)['fixtures']))
    return max(supports)


def _regime(shape, phase, index):
    """Refine a shape and trim leading steps that no observed contrast needs."""
    while True:
        base = structural(phase, shape)
        refined, informative = refine.refine(base, index)
        if refined is None:
            return base, shape, 0
        first = min(informative)
        if first == 0 or len(shape) - first < 2:
            return refined, shape, 0
        shape = shape[first:]


def select(proposals, index, phase, *, replicate=True):
    """Return (choice or None, audit). Audit lists every check and the reason to abstain."""
    audit = dict(version=VERSION, phase=phase, shapes=[], checks={}, abstain_reason=None, alternatives=[])
    if not index.complete:
        audit['abstain_reason'] = 'decision_index_incomplete'
        return None, audit
    population = _population(index, phase)
    shapes = sorted({tuple(p['shape']) for p in proposals if p['phase'] == phase})
    rows = []
    for shape in shapes:
        m = _measure(structural(phase, shape), index)
        support = len(m['fixtures'])
        reference = _reference(shape, phase, index, population) if support >= MIN_FIXTURES else 0
        regular = (m['complete'] and support >= MIN_FIXTURES and len(m['days']) >= MIN_DAYS
                   and reference > 0 and Fraction(support, reference) >= REGULAR)
        rows.append(dict(shape=list(shape), length=len(shape), support=support, reference=reference,
                         regular=regular))
    rows.sort(key=lambda r: (-r['length'], -r['support'], r['shape']))
    audit['shapes'] = rows
    audit['population'] = len(population)
    regular = [r for r in rows if r['regular']]
    if not regular:
        audit['abstain_reason'] = 'no_regular_trajectory'
        return None, audit
    longest = regular[0]['length']
    finalists, seen = [], set()
    for row in (r for r in regular if r['length'] == longest):
        refined, shape, _ = _regime(tuple(row['shape']), phase, index)
        if refined['id'] in seen:
            continue
        seen.add(refined['id'])
        finalists.append((refined, _measure(refined, index)))
    finalists.sort(key=lambda x: (-len(x[0]['shape']), -len(x[1]['fixtures']), x[0]['id']))
    choice, m = finalists[0]
    n = len(m['fixtures'])
    early, late, early_all, late_all = _halves(index, m['fixtures'])
    checks = dict(
        complete=m['complete'],
        support=n, support_ok=n >= MIN_FIXTURES,
        days=len(m['days']), days_ok=len(m['days']) >= MIN_DAYS,
        detections=m['detections'],
        unique_terminal=n > 0 and Fraction(m['detections'], n) <= MAX_TERMINALS_PER_FIXTURE,
        halves=[early, late, early_all, late_all],
        stable=(early_all > 0 and late_all > 0 and early >= HALF_SHARE * n and late >= HALF_SHARE * n),
        predicates=sum(len(s) for s in choice['rule']['steps']) + len(choice['rule']['relations']),
    )
    for other, om in finalists[1:]:
        overlap = len(m['fixtures'] & om['fixtures'])
        distinct = len(om['fixtures']) >= AMBIGUITY * n and overlap < AMBIGUITY * len(om['fixtures'])
        audit['alternatives'].append(dict(id=other['id'], shape=list(other['shape']), support=len(om['fixtures']),
                                          overlap=overlap, competing=distinct))
    checks['unambiguous'] = not any(a['competing'] for a in audit['alternatives'])
    if replicate:
        # Independent re-selection on each calendar half must reach the same trajectory.
        replicas = []
        for half in index.calendar_halves():
            again, sub = select(proposals, index.subset(half), phase, replicate=False)
            replicas.append(dict(shape=list(again['shape']) if again else None,
                                 abstain_reason=sub['abstain_reason']))
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


def investigate(tape, *, phase, index=None, work=miner_i.WORK):
    """Generation (frozen I miner) + consensus proposals, then autonomous selection."""
    from ..experimental_h.observed import Observed
    from .observe import Index
    index = index or Index(Observed(tape))
    mined = miner_i.discover(tape, work=work)
    proposals = mined['candidates']
    choice, audit = select(proposals, index, phase)
    added = [choice] if choice is not None and choice['channel'] == 'consensus' else []
    return dict(version=VERSION, generator=mined['version'], proposals=proposals + added,
                mining_work=mined['work'], choice=choice, abstained=choice is None, audit=audit,
                confirmatory=False)
