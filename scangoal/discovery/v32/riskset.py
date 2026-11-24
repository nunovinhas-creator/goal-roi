"""Evaluate the entire risk set at one receipt instant, before any reservation."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from math import log

from ... import event_audit, event_intelligence as events, parser
from ...observability import contract_key
from .. import dsl
from ..replay import Decision, Match, stratum
from ..v3.riskset import LIVE_MINUTE_CALIPER, LOG_ODDS_CALIPER, MAX_LIVE_QUOTE_AGE, MAX_QUOTE_AGE, PRE_LEAD_CALIPER
from .language import Sequence
from .tape import DOMAIN, point, states


@dataclass
class Cohort:
    pairs: list = field(default_factory=list)
    cases: list = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    admissible: list = field(default_factory=list)


@dataclass
class View:
    history: list
    quotes: dict


def view_at(tape, fixture, at):
    published_conflicts = {issued for available, issued in tape.issues.get(fixture, ()) if available <= at}
    visible = [row for row in tape.history(fixture, at)
               if row['event_kind'] not in {'revision', 'goal', 'score'} or events.emitted(row) not in published_conflicts]
    return View(*states(visible))


def quote_gate(tape, quote, view, at):
    fixture = quote['fixture_id']
    kickoff = tape.parts[fixture][0]
    if tape.blocked_at(fixture, at):
        return 'identity_ambiguous'
    odds = point(quote)
    if odds is None or odds < parser.MIN_ODDS:
        return 'price_not_explicit_unambiguous_point'
    phase = quote['phase']
    if phase not in ('pre', 'live') or (phase == 'pre' and at >= kickoff):
        return 'phase_unproven_at_decision'
    age = at - events.emitted(quote)
    if age > (MAX_QUOTE_AGE if phase == 'pre' else MAX_LIVE_QUOTE_AGE):
        return 'quote_stale_at_decision'
    if event_audit.timely(quote, at, kickoff):
        return 'decision_window_unproven'
    if phase == 'live':
        score = quote.get('score')
        if score is None or type(quote['minute']) is not int:
            return 'live_state_unproven'
        if any(row['score'] is not None and row['score'] != score and events.emitted(row) >= events.emitted(quote)
               for row in view.history):
            return 'live_state_changed_since_quote'
    return None


def opportunity(tape, sequence, quote, view, at, budget):
    """Retain the quote's price/issue time and move the terminal *decision* to t."""
    fixture = quote['fixture_id']
    kickoff = tape.parts[fixture][0]
    start = at - timedelta(seconds=sequence.rule['within_seconds'])
    prefix = [row for row in view.history if start <= events.emitted(row) < at
              and row['message_id'] != quote['message_id']]

    def covered(left, right):
        return tape.covered(fixture, left, right, at)

    full = covered(start, at)
    alerts = [e for e in prefix if e['event_kind'] in ('alert', 'quote')]
    selection = events.selection_key(quote)
    anchor = dict(quote, emitted_utc=events.iso(at), available_utc=events.iso(at),
                  kickoff_utc=events.iso(kickoff), window_start=start,
                  prior_alerts=len(alerts) if full else None,
                  prior_sources=len({e['source'] for e in alerts}) if full else None,
                  prior_compatible_quotes=sum(e['odds'] is not None and events.selection_key(e) == selection
                                              for e in alerts) if full and selection is not None else None)
    terminal = sequence.terminal(anchor)
    if terminal is not True:
        return Match('indeterminate' if terminal is None else 'ineligible_terminal'), prefix
    return sequence.match(anchor, prefix, covered, budget), prefix


def comparable(tape, case, control, at):
    if contract_key(case) != contract_key(control) or case['fixture_id'] == control['fixture_id']:
        return None
    ca = tape.parts[case['fixture_id']]
    co = tape.parts[control['fixture_id']]
    pa, pb = point(case), point(control)
    difference = abs(log(pa) - log(pb))
    if difference > LOG_ODDS_CALIPER:
        return None
    if case['phase'] == 'pre':
        left, right = ((parts[0] - at).total_seconds() / 60 for parts in (ca, co))
        clock = abs(left - right) / PRE_LEAD_CALIPER
    else:
        if case['score'] != control['score']:
            return None
        left, right = case['minute'], control['minute']
        clock = abs(left - right) / LIVE_MINUTE_CALIPER
    if clock > 1:
        return None
    return dict(case_odds=pa, control_odds=pb, case_clock=left, control_clock=right,
                same_competition=ca[-1] == co[-1], scaled_distance=round(max(clock, difference / LOG_ODDS_CALIPER), 12),
                comparator_quote_age_seconds=(at - events.emitted(control)).total_seconds())


class RiskSet:
    def __init__(self, tape, *, start=None, end=None, discovery_cutoff=None):
        if (start is None) != (end is None) or (start is not None and (start >= end or discovery_cutoff is None
                                                                      or discovery_cutoff >= start)):
            raise ValueError('invalid prospective window')
        self.tape = tape
        self.start, self.end = start, end
        self.old = {fixture for fixture, first in tape.first_seen.items()
                    if discovery_cutoff is not None and first <= discovery_cutoff}
        # Reused across hypotheses. Each view reflects an immutable prefix.
        self.views = {}

    def _view(self, fixture, at):
        key = fixture, at
        if key not in self.views:
            self.views[key] = view_at(self.tape, fixture, at)
        return self.views[key]

    def _eligible_window(self, fixture, quote):
        return self.start is None or (fixture not in self.old and events.emitted(quote) >= self.start
                                      and self.start <= self.tape.parts[fixture][0] < self.end)

    def run(self, rule, budget):
        seq = Sequence(rule)
        cohort = Cohort()
        reserved = set()
        # Keep the set of previously observed fixture/contract identities; a
        # stale/ambiguous quote is filtered at t, never replaced by an older one.
        pool = defaultdict(set)
        for at, batch in sorted(self.tape.batches.items()):
            if self.end is not None and at >= self.end:
                break
            available = {}
            for fixture in sorted(batch):
                view = self._view(fixture, at)
                available[fixture] = view
                for key in view.quotes:
                    pool[key].add(fixture)
            if self.start is not None and at < self.start:
                continue
            if self.start is not None and at - timedelta(seconds=seq.rule['within_seconds']) < self.start:
                continue

            # Everything below is computed against the pre-batch reservation
            # state. Exhausting the budget discards the whole batch.
            current, counts, cache = {}, Counter(), {}

            def evaluate(fixture, quote, view):
                key = fixture, quote['message_id']
                if key not in cache:
                    if not budget.spend():
                        return Match('search_budget'), []
                    cache[key] = opportunity(self.tape, seq, quote, view, at, budget)
                    counts['antecedent_' + cache[key][0].state] += 1
                return cache[key]

            for fixture, view in available.items():
                if fixture in reserved:
                    continue
                for key, quote in sorted(view.quotes.items(), key=lambda item: dsl.encoded(item[0])):
                    if not self._eligible_window(fixture, quote):
                        continue
                    if failure := quote_gate(self.tape, quote, view, at):
                        counts[failure] += 1
                        continue
                    match, prefix = evaluate(fixture, quote, view)
                    if match.state == 'present':
                        current[fixture] = (key, quote, match, prefix)
                        break
            if budget.exhausted:
                cohort.counts['budget_exhausted'] += 1
                break

            edges = defaultdict(list)
            for fixture, (key, quote, _, _) in sorted(current.items()):
                for other in sorted(pool[key] - reserved - current.keys()):
                    if not budget.spend():
                        break
                    view = self._view(other, at)
                    control = view.quotes[key]
                    if not self._eligible_window(other, control) or quote_gate(self.tape, control, view, at):
                        continue
                    meta = comparable(self.tape, quote, control, at)
                    if meta is None:
                        continue
                    match, prefix = evaluate(other, control, view)
                    if match.state != 'absent':
                        continue
                    rank = (not meta['same_competition'], meta['scaled_distance'], meta['comparator_quote_age_seconds'],
                            dsl.digest((DOMAIN, fixture, other, key)))
                    edges[fixture].append((rank, other, control, prefix, meta))
            if budget.exhausted:
                cohort.counts['budget_exhausted'] += 1
                break
            if counts['antecedent_search_budget']:
                # A locally unfinished search could hide a simultaneous case.
                # No claims or reservations are committed at this receipt.
                cohort.counts['search_incomplete'] += 1
                break

            cohort.counts.update(counts)
            reserved.update(current)
            for fixture in sorted(current):
                _, quote, match, prefix = current[fixture]
                ko = self.tape.parts[fixture][0]
                case = Decision(quote, at, ko, 'case', match.witness, stratum(quote, at, ko, prefix))
                cohort.cases.append(case)
                cohort.counts['cases'] += 1
                ordered = sorted(edges[fixture], key=lambda e: e[0])
                cohort.admissible.extend((events.iso(at), fixture, other) for _, other, *_ in ordered)
                for _, other, control, prefix, meta in ordered:
                    if other in reserved:
                        continue
                    ko = self.tape.parts[other][0]
                    decision = Decision(control, at, ko, 'control', (), stratum(control, at, ko, prefix))
                    reserved.add(other)
                    cohort.pairs.append((case, decision, meta))
                    cohort.counts['matched_pairs'] += 1
                    break
        return cohort
