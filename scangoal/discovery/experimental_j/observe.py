"""Label-free match records of a proposed rule over one completed observation tape."""

from collections import defaultdict
from dataclasses import dataclass

from ..experimental_h.rules import Meter, Rule

MATCH_WORK = 300_000


@dataclass(frozen=True)
class Firing:
    """What a rule does on the investigated tape. It contains no outcome or label."""

    rule_id: str
    states: dict          # decision index -> present/absent/indeterminate/ineligible_terminal/search_budget
    witnesses: dict       # decision index -> tuple of rows (present only)
    complete: bool

    @property
    def present(self):
        return tuple(sorted(self.witnesses))


class Index:
    """Decisions of a tape, frozen once so that each rule sees the same receipts."""

    def __init__(self, source, *, work=MATCH_WORK, decisions=None, complete=True):
        self.source = source
        if decisions is None:
            meter = Meter(work)
            decisions = tuple(source.decisions(meter))
            complete = not meter.exhausted
        self.decisions = tuple(decisions)
        self.complete = complete
        self.fixture = tuple(d.fixture for d in self.decisions)
        self.day = tuple(d.at.date() for d in self.decisions)
        self.by_fixture = defaultdict(list)
        for i, d in enumerate(self.decisions):
            self.by_fixture[d.fixture].append(i)
        self.cache = {}

    def subset(self, fixtures):
        """The same tape, with decisions restricted to some fixtures (for replication checks)."""
        return Index(self.source, decisions=[d for d in self.decisions if d.fixture in fixtures],
                     complete=self.complete)

    def calendar_halves(self):
        """Early and late fixtures by first decision time. Uses no outcome."""
        first = {f: min(self.decisions[i].at for i in rows) for f, rows in self.by_fixture.items()}
        ordered = sorted(first, key=lambda f: (first[f], f))
        middle = len(ordered) // 2
        return frozenset(ordered[:middle]), frozenset(ordered[middle:])

    def fire(self, rule_id, definition, *, work=MATCH_WORK):
        if rule_id in self.cache:
            return self.cache[rule_id]
        rule = Rule(definition)
        meter = Meter(work, stage='matching')
        states, witnesses = {}, {}
        for i, decision in enumerate(self.decisions):
            answer = rule.match(self.source, decision, meter)
            states[i] = answer.state
            if answer.state == 'present':
                witnesses[i] = answer.witness
            if meter.exhausted:
                break
        result = Firing(rule_id, states, witnesses, complete=not meter.exhausted and self.complete)
        self.cache[rule_id] = result
        return result

    def fixtures(self, indexes):
        return frozenset(self.fixture[i] for i in indexes)

    def days(self, indexes):
        return frozenset(self.day[i] for i in indexes)
