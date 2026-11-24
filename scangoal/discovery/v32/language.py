"""Typed, outcome-blind v3.2 rules over the journal and its enrichments."""

from ... import event_intelligence as events
from .. import dsl, dsl_v2
from ..replay import Budget, MAX_SEARCH_PER_ANCHOR, Match, conjunction

VERSION = 32
Invalid = dsl.Invalid
FIELDS = {
    **dsl_v2.FIELDS,
    **{name: dsl.Primitive('text', dsl.field(name), mine=True)
       for name in ('event_kind', 'price_kind', 'count_unit')},
    **{name: dsl.Primitive('number', dsl.field(name), mine=True) for name in
       ('price_low', 'price_high', 'biggest_ticket_usd', 'volume_cumulative_usd', 'n_bets', 'stoppage')},
}
RELATIONS = {
    **dsl_v2.RELATIONS,
    'odds_velocity_bp_hour': dsl.Primitive('number', lambda a, b: _price_velocity(a, b), mine=True),
    **{name + '_delta': dsl.Primitive('number', lambda a, b, field=name: _scoped_delta(a, b, field), mine=True)
       for name in ('volume_cumulative_usd', 'biggest_ticket_usd', 'n_bets')},
}


def _price_velocity(a, b):
    from ... import chronology
    left = chronology.parse_utc(a.get('quote_issued_utc')) or events.emitted(a)
    right = chronology.parse_utc(b.get('quote_issued_utc')) or events.emitted(b)
    elapsed = (right - left).total_seconds()
    delta = dsl.price_ratio(a, b)
    return delta * 3600 / elapsed if delta is not None and elapsed > 0 else None


def _scoped_delta(a, b, field):
    scope = events.selection_key(a)
    if scope is None or scope != events.selection_key(b):
        return None
    if field == 'n_bets' and (a.get('count_unit') is None or a.get('count_unit') != b.get('count_unit')):
        return None
    return dsl.numeric_delta(a, b, FIELDS[field].read)


def canonical(rule):
    if (not isinstance(rule, dict) or set(rule) != {'version', 'within_seconds', 'steps', 'relations'}
            or type(rule['version']) is not int or rule['version'] != VERSION):
        raise Invalid('invalid_definition_version')
    seconds, raw = rule['within_seconds'], rule['steps']
    if type(seconds) is not int or not 1 <= seconds <= dsl.MAX_WINDOW:
        raise Invalid('invalid_observation_window')
    if not isinstance(raw, list) or not 2 <= len(raw) <= dsl.MAX_STEPS:
        raise Invalid('invalid_sequence_length')
    steps = []
    for index, step in enumerate(raw):
        if not isinstance(step, list) or len(step) > dsl_v2.MAX_PREDICATES:
            raise Invalid('predicate_budget_exceeded')
        grouped = {}
        for atom in step:
            normalized, = dsl._atoms([atom], FIELDS)
            name = normalized['field']
            if index < len(raw) - 1 and FIELDS[name].anchor_only:
                raise Invalid('prefix_aggregate_outside_anchor')
            grouped.setdefault(name, []).append(normalized)
        steps.append([dict(field=name, op=op, value=value) for name, atoms in sorted(grouped.items())
                      for op, value in dsl._conjunction(atoms, FIELDS[name])])
    links = dsl._atoms(rule['relations'], RELATIONS, links=len(raw))
    if any(not step and not any(index in (link['from'], link['to']) for link in links)
           for index, step in enumerate(steps)):
        raise Invalid('unconstrained_step')
    return dict(version=VERSION, within_seconds=seconds, steps=steps, relations=links)


def lift(rule):
    """Reinterpret a legacy proposal as a new definition and new clock."""
    return canonical(dict(dsl_v2.canonical(rule), version=VERSION))


def identifier(rule):
    return 'td32:' + dsl.digest(canonical(rule))


class Sequence:
    def __init__(self, rule):
        self.rule = canonical(rule)
        self.steps = self.rule['steps']
        self.links = tuple(tuple(edge for edge in self.rule['relations'] if edge['from'] == step)
                           for step in range(len(self.steps) - 1))

    def terminal(self, anchor):
        return conjunction(self.steps[-1], FIELDS, anchor)

    def match(self, anchor, prefix, covered, budget):
        """A negative search needs a complete window and no unresolved path."""
        path = [None] * (len(self.steps) - 1) + [anchor]
        local = Budget(MAX_SEARCH_PER_ANCHOR)
        uncertain = False

        def walk(step, end, unknown):
            nonlocal uncertain
            if step < 0:
                if unknown:
                    uncertain = True
                    return ()
                return tuple(path)
            right = events.emitted(path[step + 1])
            for index in range(end - 1, step - 1, -1):
                if not local.spend() or not budget.spend():
                    return ()
                event = prefix[index]
                if events.emitted(event) >= right:
                    continue
                state = conjunction(self.steps[step], FIELDS, event)
                if state is False:
                    continue
                unresolved = unknown or state is None
                for edge in self.links[step]:
                    other = path[edge['to']]
                    read = RELATIONS[edge['field']].read
                    if read is None:
                        # An unseen extra event can falsify a count or a
                        # persistence assertion about the interior.
                        if not covered(events.emitted(event), events.emitted(other)):
                            value = None
                        else:
                            if not all(local.spend() and budget.spend() for _ in prefix):
                                return ()
                            value = dsl_v2.relation(edge['field'], event, other, prefix)
                    else:
                        value = read(event, other)
                    state = dsl.predicate(edge, value)
                    if state is False:
                        break
                    unresolved |= state is None
                else:
                    path[step] = event
                    witness = walk(step - 1, index, unresolved)
                    if witness:
                        return witness
                    if local.exhausted or budget.exhausted:
                        return ()
            return ()

        witness = walk(len(self.steps) - 2, len(prefix), False)
        if witness:
            return Match('present', witness)
        if local.exhausted or budget.exhausted:
            return Match('search_budget')
        if uncertain or not covered(anchor['window_start'], events.emitted(anchor)):
            return Match('indeterminate')
        return Match('absent')
