"""Bounded discovery over typed trajectories; outcomes are absent from this input."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
from itertools import combinations
from math import floor, log10
import re
from types import SimpleNamespace

from ... import event_intelligence as events
from .. import dsl, miner, miner_v2, researcher
from ..replay import Budget
from ..replay_v2 import Opportunity
from . import language
from .riskset import quote_gate, view_at

WINDOW = miner_v2.WINDOW
GENERATOR = 'typed-trajectory-projection-v1'


@dataclass(frozen=True)
class Candidate(miner.Candidate):
    @property
    def id(self):
        return language.identifier(self.rule)


class Timeline:
    """The v2 enumerator and v3.2 enriched projections share the same input."""

    def __init__(self, tape):
        self.tape = tape
        self.replay = SimpleNamespace(history=tape.by_fixture)
        self.eligible_anchors = []
        for at, batch in sorted(tape.batches.items()):
            for fixture in sorted(batch):
                view = view_at(tape, fixture, at)
                for key, row in sorted(view.quotes.items(), key=lambda item: dsl.encoded(item[0])):
                    if quote_gate(tape, row, view, at) is None:
                        self.eligible_anchors.append((at, row, tape.parts[fixture][0]))
        self.anchors = [(at, row) for at, row, _ in self.eligible_anchors]
        self.cache = {}

    def prefix(self, raw, at, window):
        view = view_at(self.tape, raw['fixture_id'], at)
        anchor = next((row for row in view.history if row['message_id'] == raw['message_id']), raw)
        start = at - timedelta(seconds=window)
        prefix = [row for row in view.history if start <= events.emitted(row) < at
                  and row['message_id'] != raw['message_id']]
        return dict(anchor, emitted_utc=events.iso(at), available_utc=events.iso(at)), prefix

    def context(self, index, window):
        if (index, window) not in self.cache:
            at, raw, kickoff = self.eligible_anchors[index]
            anchor, prefix = self.prefix(raw, at, window)
            self.cache[index, window] = Opportunity(anchor, at, kickoff, prefix)
        return self.cache[index, window]


def _feature(name, primitive, row):
    value = primitive.read(row)
    if value is None:
        return None
    if primitive.kind != 'number':
        return (name, 'eq', value) if not isinstance(value, str) or len(value) <= 100 else None
    if not -1_000_000_000 <= value <= 1_000_000_000:
        return None
    if value == 0:
        return name, 'eq', 0
    magnitude = 10 ** floor(log10(abs(value)))
    threshold = round(floor(value / magnitude) * magnitude, 9)
    return name, 'ge', threshold


def _keys(path):
    kinds = tuple((('event_kind', 'eq', row['event_kind']),) for row in path)
    yield kinds, ()
    # Overlay any observable primitive on any position. Neither field values
    # nor event orders are prescribed, and a path may combine several kinds.
    enriched = set(language.FIELDS) - set(miner_v2.dsl_v2.FIELDS) - {'event_kind'}
    for position, row in enumerate(path):
        for name in sorted(enriched):
            if (atom := _feature(name, language.FIELDS[name], row)) is not None:
                steps = kinds[:position] + (kinds[position] + (atom,),) + kinds[position + 1:]
                yield steps, ()
    for i, j in combinations(range(len(path)), 2):
        for name, primitive in language.RELATIONS.items():
            if primitive.read is None or not primitive.mine:
                continue
            value = primitive.read(path[i], path[j])
            if value is None:
                continue
            if primitive.kind == 'bool':
                edge = i, j, name, 'eq', value
            else:
                edge = i, j, name, 'gt' if value > 0 else 'lt' if value < 0 else 'eq', 0
            yield kinds, (edge,)


def enrich(timeline, *, emissions, limit=24):
    meter = Budget(emissions)
    motifs = defaultdict(set)
    days = defaultdict(set)
    for index in miner_v2.sample(timeline):
        ctx = timeline.context(index, WINDOW)
        if not ctx.prefix:
            continue
        for path in miner_v2.paths(ctx):
            for signature in _keys(path):
                if not meter.spend():
                    break
                motifs[signature].add(ctx.anchor['fixture_id'])
                days[signature].add(ctx.at.date())
            if meter.exhausted:
                break
        if meter.exhausted:
            break
    ranked = sorted((key for key in motifs if len(motifs[key]) >= 2 and len(days[key]) >= 2),
                    key=lambda key: (-len(motifs[key]), -len(key[0]), dsl.encoded(key)))
    chosen = []
    used = Counter()
    for steps, relations in ranked:
        # Keep alternative shapes in the shortlist instead of letting a
        # common two-event trajectory consume all capacity.
        shape = (tuple(next(atom[2] for atom in step if atom[0] == 'event_kind') for step in steps),
                 tuple(a[0] for step in steps for a in step if a[0] != 'event_kind'),
                 tuple(edge[2] for edge in relations))
        if used[shape] >= 2:
            continue
        rule = dict(version=language.VERSION, within_seconds=WINDOW,
                    steps=[[dict(field=name, op=op, value=value) for name, op, value in step] for step in steps],
                    relations=[dict(field=name, op=op, value=value, **{'from': i, 'to': j})
                               for i, j, name, op, value in relations])
        try:
            rule = language.canonical(rule)
        except dsl.Invalid:
            continue
        used[shape] += 1
        chosen.append(Candidate(rule, 'typed_trajectory', None, None, None))
        if len(chosen) == limit:
            break
    return chosen, meter.used


def prompt(timeline):
    instructions = (
        'Generate novel temporal hypotheses only. Output one JSON object {"proposals":[...]}. Each entry has '
        'rule, alternative (rule or null), mechanism and refutation. A rule has version=32, within_seconds, '
        'steps (2–8 lists of conjunctive predicates) and relations. A predicate has field, op, value; '
        'a relation also has zero-based from and to with from < to. Use eq/ne, and lt/le/gt/ge for numbers. '
        'The terminal is a quote evaluated at decision time. Prefix events may include alerts, revisions, '
        'reported goals and scores. Combine fields and non-adjacent relations freely. Absence needs coverage. '
        'FT, outcomes, actions and profit claims are outside this language. At most five proposals.\n'
    )
    fields = {name: primitive.kind for name, primitive in language.FIELDS.items()}
    relations = {name: primitive.kind for name, primitive in language.RELATIONS.items()}
    data = dict(fields=fields, relations=relations, examples=[])
    sample = ('event_kind', 'source', 'phase', 'point_odds', 'price_kind', 'volume_usd', 'volume_cumulative_usd',
              'biggest_ticket_usd', 'n_bets', 'minute', 'goals', 'score_state', 'period')
    for index in range(8):
        if not timeline.eligible_anchors:
            break
        context = timeline.context((2 * index + 1) * len(timeline.eligible_anchors) // 16, WINDOW)
        if not context.prefix:
            continue
        example = []
        for row in [*context.prefix[-3:], context.anchor]:
            shown = {}
            for field in sample:
                value = language.FIELDS[field].read(row)
                if value is not None and (not isinstance(value, str) or re.fullmatch(r'[\w .\-/]{1,36}', value)):
                    shown[field] = value
            example.append(shown)
        data['examples'].append(example)
        if len(instructions.encode() + dsl.encoded(data)) > researcher.MAX_PROMPT_BYTES:
            data['examples'].pop()
            break
    result = instructions + dsl.encoded(data).decode()
    if len(result.encode()) > researcher.MAX_PROMPT_BYTES:
        raise ValueError('v32_model_prompt_budget_exceeded')
    return result
