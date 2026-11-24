"""First-completion replay with prospective, fixture-disjoint control allocation."""

from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
import hashlib

from .. import event_intelligence as events
from . import dsl, dsl_v2
from .replay import Budget, Decision, MAX_SEARCH_PER_ANCHOR, Match, conjunction, stratum

ALLOCATION = b'goal-roi-discovery-v2/allocation/v1\0'
CONTROL_MODULUS = 3


def arm(fixture):
    digest = hashlib.sha256(ALLOCATION + fixture.encode()).digest()
    return 'control' if int.from_bytes(digest[:8], 'big') % CONTROL_MODULUS == 0 else 'case'


@dataclass
class Opportunity:
    anchor: dict
    at: object
    kickoff: object
    prefix: list
    values: dict = field(default_factory=dict)


@dataclass
class Cohort:
    decisions: list
    eligible: dict
    withheld: dict


class Sequence:
    def __init__(self, rule):
        self.rule = dsl_v2.canonical(rule)
        self.steps = self.rule['steps']
        self.links = defaultdict(list)
        for edge in self.rule['relations']:
            self.links[edge['from']].append(edge)

    def eligible(self, context):
        # Between candidates with the same window, cache the known anchor
        # projection. It never includes results or later source observations.
        for atom in self.steps[-1]:
            name = atom['field']
            if name not in context.values:
                context.values[name] = dsl_v2.FIELDS[name].read(context.anchor)
            if dsl.predicate(atom, context.values[name]) is not True:
                return False
        return True

    def match(self, context, budget):
        prefix, anchor = context.prefix, context.anchor
        length = len(self.steps)
        if len(prefix) < length - 1:
            return Match('control')
        local = Budget(MAX_SEARCH_PER_ANCHOR)
        path = [None] * length
        path[-1] = anchor
        uncertain = False
        position_cache = {}

        def spend():
            return local.spend() and budget.spend()

        def walk(step, end, unknown):
            nonlocal uncertain
            if step < 0:
                if not unknown:
                    return tuple(path)
                uncertain = True
                return None
            right = events.emitted(path[step + 1])
            for index in range(end - 1, step - 1, -1):
                if not spend():
                    return None
                event = prefix[index]
                if (events.emitted(event) >= right
                        or any(event['message_id'] == other['message_id'] for other in path[step + 1:])):
                    continue
                key = step, index
                if key not in position_cache:
                    position_cache[key] = conjunction(self.steps[step], dsl_v2.FIELDS, event)
                state = position_cache[key]
                if state is False:
                    continue
                missing = False
                for edge in self.links[step]:
                    name = edge['field']
                    interior = prefix if dsl_v2.RELATIONS[name].read is None else ()
                    # A persistence query traverses a bounded observed interval.
                    if interior and not all(spend() for _ in interior):
                        return None
                    result = dsl.predicate(edge, dsl_v2.relation(name, event, path[edge['to']], interior))
                    if result is False:
                        break
                    missing |= result is None
                else:
                    path[step] = event
                    result = walk(step - 1, index, unknown or state is None or missing)
                    if result is not None or local.exhausted or budget.exhausted:
                        return result
            return None

        result = walk(length - 2, len(prefix), False)
        if result is not None:
            return Match('case', result)
        if local.exhausted or budget.exhausted:
            return Match('search_budget')
        return Match('unknown' if uncertain else 'control')


class Timeline:
    """Cache the point-in-time prefix per anchor; share it across rules."""

    def __init__(self, replay, *, start=None, end=None, discovery_cutoff=None):
        if (start is None) != (end is None):
            raise ValueError('supply both window limits')
        self.replay = replay
        old = replay.prior_fixtures(discovery_cutoff or start) if start is not None else ()
        begin = bisect_left(replay.anchor_times, start) if start is not None else 0
        self.start = start
        self.eligible_anchors = []
        self.cache = {}
        for at, anchor in replay.anchors[begin:]:
            if end is not None and at >= end:
                break
            fixture = anchor['fixture_id']
            kickoff = replay.kickoff_known(fixture, at)
            if start is not None and (fixture in old or kickoff is None or not start <= kickoff < end
                    or events.emitted(anchor) < start or anchor['receipt'] != 'completed_page'):
                continue
            self.eligible_anchors.append((at, anchor, kickoff))

    def context(self, index, window):
        # Normal mining and evaluation share one six-hour window. Evict the
        # auxiliary cache when the bounded research request uses many windows.
        if window not in self.cache:
            if len(self.cache) >= 4:
                self.cache.clear()
            self.cache[window] = {}
        bucket = self.cache[window]
        if index not in bucket:
            at, raw, kickoff = self.eligible_anchors[index]
            anchor, prefix = self.replay.prefix(raw, at, window, self.start)
            if self.start is not None:
                prefix = [e for e in prefix if e['receipt'] == 'completed_page']
            key = events.selection_key(anchor)
            anchor = dict(anchor, prior_alerts=len(prefix), prior_sources=len({e['source'] for e in prefix}),
                          prior_compatible_quotes=sum(e['odds'] is not None and events.selection_key(e) == key
                                                      for e in prefix) if key is not None else None)
            bucket[index] = Opportunity(anchor, at, kickoff, prefix)
        return bucket[index]

    def run(self, rule, budget, *, structural=False):
        """Reserve at the first admissible completion or at the assigned control check.

        The structural screen counts only positive first completions. It makes
        no claim about a control group and produces no prospective evidence.
        """
        sequence = Sequence(rule)
        window = sequence.rule['within_seconds']
        decisions, closed = [], set()
        eligible, withheld = defaultdict(set), Counter()
        for index, (_, row, _) in enumerate(self.eligible_anchors):
            if not budget.spend():
                break
            fixture = row['fixture_id']
            if fixture in closed:
                continue
            context = self.context(index, window)
            if not sequence.eligible(context):
                continue
            group = 'case' if structural else arm(fixture)
            eligible[group].add(fixture)
            result = sequence.match(context, budget)
            if result.state == 'search_budget':
                withheld['search_incomplete'] += 1
                closed.add(fixture)
                continue
            if result.state == 'unknown':
                continue
            if group == 'control' and result.state == 'case':
                withheld['exposure_at_control_check'] += 1
                closed.add(fixture)
                continue
            if result.state != group:
                continue
            closed.add(fixture)
            decisions.append(Decision(context.anchor, context.at, context.kickoff, group, result.witness,
                                      stratum(context.anchor, context.at, context.kickoff, context.prefix)))
        if not structural:
            ended = {d.fixture for d in decisions}
            withheld['no_case_completion'] = len(eligible['case'] - ended - closed)
            withheld['indeterminate_control'] = len(eligible['control'] - closed)
        return Cohort(decisions, {name: len(rows) for name, rows in eligible.items()}, dict(withheld))
