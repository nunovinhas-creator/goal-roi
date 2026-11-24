"""Typed v3.2 syntax interpreted in the experimental quote-receipt environment."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta

from ... import chronology, event_intelligence as events
from ...observability import contract_key
from .. import dsl, dsl_v2
from ..replay import Match, conjunction
from ..v32 import language
from ..v32.tape import point

FIELDS = dict(language.FIELDS, point_odds=dsl.Primitive('number', point, mine=True))
RELATIONS = language.RELATIONS
PRICE_MOTION = frozenset({'odds_delta_bp', 'odds_velocity_bp_hour'})
CONTRACT_VOLUME = frozenset({'volume_ratio', 'volume_cumulative_usd_delta', 'biggest_ticket_usd_delta', 'n_bets_delta'})


@dataclass
class Meter:
    limit: int
    used: int = 0
    exhausted: bool = False
    stages: Counter = field(default_factory=Counter)
    stage: str = 'observations'

    def charge(self, units):
        if self.exhausted or units > self.limit - self.used:
            self.exhausted = True
            return False
        self.used += units
        self.stages[self.stage] += units
        return True

    def spend(self):
        return self.charge(1)


def relation_value(name, left, right):
    if name in PRICE_MOTION | CONTRACT_VOLUME:
        contract = contract_key(left)
        if contract is None or contract != contract_key(right) or left['fixture_id'] != right['fixture_id']:
            return None
    if name in PRICE_MOTION:
        if any(row.get('price_kind') != 'point' for row in (left, right)):
            return None
        issued_left, issued_right = (chronology.parse_utc(row.get('quote_issued_utc')) for row in (left, right))
        if issued_left is None or issued_right is None or issued_left >= issued_right:
            return None
    return RELATIONS[name].read(left, right)


class Rule:
    def __init__(self, definition):
        self.definition = language.canonical(definition)
        self.steps = self.definition['steps']
        self.links = tuple(tuple(edge for edge in self.definition['relations'] if edge['from'] == step)
                           for step in range(len(self.steps) - 1))

    def match(self, observed, decision, meter):
        start = decision.at - timedelta(seconds=self.definition['within_seconds'])
        if not meter.charge(len(decision.history)):
            return Match('search_budget')
        prefix = decision.within(self.definition['within_seconds'])
        full = observed.covered(decision, start, decision.at)
        anchor = decision.quote
        if any(FIELDS[a['field']].anchor_only for a in self.steps[-1]):
            alerts = [row for row in prefix if row['event_kind'] in ('quote', 'alert')]
            scope = events.selection_key(anchor)
            anchor = dict(anchor, prior_alerts=len(alerts) if full else None,
                          prior_sources=len({row['source'] for row in alerts}) if full else None,
                          prior_compatible_quotes=sum(row['odds'] is not None and events.selection_key(row) == scope
                                                      for row in alerts) if full and scope is not None else None)
        if not meter.charge(1 + len(self.steps[-1])):
            return Match('search_budget')
        terminal = conjunction(self.steps[-1], FIELDS, anchor)
        if terminal is not True:
            return Match('ineligible_terminal' if terminal is False else 'indeterminate')
        path = [None] * (len(self.steps) - 1) + [anchor]
        uncertain = False

        def walk(step, end, unresolved):
            nonlocal uncertain
            if step < 0:
                uncertain |= unresolved
                return () if unresolved else tuple(path)
            right = events.emitted(path[step + 1])
            for index in range(end - 1, step - 1, -1):
                if not meter.charge(1 + len(self.steps[step]) + len(self.links[step])):
                    break
                row = prefix[index]
                if events.emitted(row) >= right:
                    continue
                unary = conjunction(self.steps[step], FIELDS, row)
                if unary is False:
                    continue
                unknown = unresolved or unary is None
                for edge in self.links[step]:
                    other = path[edge['to']]
                    name = edge['field']
                    if RELATIONS[name].read is not None:
                        value = relation_value(name, row, other)
                    elif observed.covered(decision, events.emitted(row), events.emitted(other)):
                        if not meter.charge(len(prefix)):
                            return ()
                        value = dsl_v2.relation(name, row, other, prefix)
                    else:
                        value = None
                    verdict = dsl.predicate(edge, value)
                    if verdict is False:
                        break
                    unknown |= verdict is None
                else:
                    path[step] = row
                    found = walk(step - 1, index, unknown)
                    if found or meter.exhausted:
                        return found
            return ()

        witness = walk(len(self.steps) - 2, len(prefix), False)
        if witness:
            return Match('present', witness)
        if meter.exhausted:
            return Match('search_budget')
        return Match('indeterminate' if uncertain or not full else 'absent')
