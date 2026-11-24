"""Risk-set comparators defined at the instant of each case decision.

A case is the first observed completion of a sequence in a fixture. At that
instant `t` the comparator is drawn from other fixtures that, using only
receipts available by `t`:

* have exactly the same contract (source, phase, native market, period,
  line and selection) quoted with a point price no older than the age limit;
* have a canonical fixture identity with a single known kickoff;
* are at the same relative clock (minutes to kickoff, or LIVE minute and the
  exact score) and within the price caliper;
* satisfy the terminal step of the rule, i.e. the same entry opportunity;
* show a determinate absence of the antecedent sequence at `t`.

Each fixture is reserved once, as case or comparator. Neither FT nor any
later receipt is consulted. The comparator isolates the antecedent: both arms
have the same entry opportunity, and only the earlier path differs.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
from math import log

from ... import event_intelligence as events, parser
from .. import dsl
from ..replay import Budget, Decision, stratum
from ..replay_v2 import Opportunity, Sequence, Timeline

MAX_QUOTE_AGE = timedelta(minutes=30)
MAX_LIVE_QUOTE_AGE = timedelta(minutes=5)
PRE_LEAD_CALIPER = 45.
LIVE_MINUTE_CALIPER = 5.
LOG_ODDS_CALIPER = .15
TIEBREAK = b'goal-roi-discovery-v3/risk-set/v1\0'


def contract(anchor):
    """Exact contract identity; None if any element is unknown."""
    key = (anchor['source'], anchor['phase'], dsl.FIELDS['native_market'].read(anchor), anchor['period'],
           anchor['line'], dsl.selection(anchor))
    if any(key[i] is None for i in (0, 1, 2, 3, 5)):
        return None
    return key


def priced(anchor):
    odds = dsl.point_odds(anchor)
    return odds if odds is not None and odds >= parser.MIN_ODDS else None


def clock(anchor, kickoff, at):
    """Relative clock at the decision instant `at`."""
    if anchor['phase'] == 'pre':
        return None if kickoff is None else (kickoff - at).total_seconds() / 60
    return anchor.get('minute')


def competition(anchor):
    return anchor.get('league') or None


@dataclass
class Cohort:
    pairs: list = field(default_factory=list)
    cases: list = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)

    @property
    def decisions(self):
        return [d for pair in self.pairs for d in pair[:2]]


class RiskSet:
    """One pass over a window, reusing the v2 point-in-time timeline."""

    def __init__(self, replay, *, start=None, end=None, discovery_cutoff=None,
                 quote_age=MAX_QUOTE_AGE, live_age=MAX_LIVE_QUOTE_AGE, lead=PRE_LEAD_CALIPER,
                 minute=LIVE_MINUTE_CALIPER, log_odds=LOG_ODDS_CALIPER):
        self.replay = replay
        self.start = start
        self.timeline = Timeline(replay, start=start, end=end, discovery_cutoff=discovery_cutoff)
        self.limits = dict(quote_age=quote_age, live_age=live_age, lead=lead, minute=minute, log_odds=log_odds)

    def _context_at(self, raw, at, window):
        """The comparator's own terminal opportunity, observed at the case instant."""
        anchor, prefix = self.replay.prefix(raw, at, window, self.start)
        if self.start is not None:
            prefix = [e for e in prefix if e['receipt'] == 'completed_page']
        key = events.selection_key(anchor)
        anchor = dict(anchor, prior_alerts=len(prefix), prior_sources=len({e['source'] for e in prefix}),
                      prior_compatible_quotes=sum(e['odds'] is not None and events.selection_key(e) == key
                                                  for e in prefix) if key is not None else None)
        return Opportunity(anchor, at, self.replay.kickoff_known(raw['fixture_id'], at), prefix)

    def run(self, rule, budget):
        sequence = Sequence(rule)
        window = sequence.rule['within_seconds']
        limits = self.limits
        cohort = Cohort()
        counts = cohort.counts
        reserved, closed = {}, set()
        recent = defaultdict(dict)  # contract -> fixture -> (at, raw, kickoff)
        for index, (at, raw, kickoff) in enumerate(self.timeline.eligible_anchors):
            if not budget.spend():
                counts['budget_exhausted'] += 1
                break
            fixture = raw['fixture_id']
            if fixture in closed:
                continue
            context = self.timeline.context(index, window)
            key = contract(context.anchor)
            if key is not None and priced(context.anchor) is not None and kickoff is not None:
                recent[key][fixture] = (at, raw, kickoff)
            if fixture in reserved and reserved[fixture] == 'case':
                continue
            if not sequence.eligible(context):
                continue
            result = sequence.match(context, budget)
            if result.state == 'search_budget':
                counts['search_incomplete'] += 1
                closed.add(fixture)
                recent_drop(recent, fixture)
                continue
            if result.state != 'case':
                continue
            if fixture in reserved:
                # The fixture had already served as a comparator. It keeps
                # that reservation; the later exposure is reported.
                counts['controls_later_exposed'] += 1
                closed.add(fixture)
                continue
            reserved[fixture] = 'case'
            counts['cases'] += 1
            case = Decision(context.anchor, at, kickoff, 'case', result.witness,
                            stratum(context.anchor, at, kickoff, context.prefix))
            cohort.cases.append(case)
            odds = priced(context.anchor)
            if key is None:
                counts['case_without_exact_contract'] += 1
                continue
            if odds is None:
                counts['case_without_point_price'] += 1
                continue
            reference = clock(context.anchor, kickoff, at)
            if reference is None:
                counts['case_without_clock'] += 1
                continue
            best, indeterminate, considered = None, 0, 0
            age_limit = limits['quote_age'] if context.anchor['phase'] == 'pre' else limits['live_age']
            pool = recent[key]
            for other in list(pool):
                seen_at, other_raw, other_kickoff = pool[other]
                if events.emitted(other_raw) < at - age_limit:
                    del pool[other]
                    continue
                if other == fixture or other in reserved or other in closed:
                    continue
                if self.replay.kickoff_known(other, at) != other_kickoff:
                    continue
                if not budget.spend():
                    break
                candidate = self._context_at(other_raw, at, window)
                price = priced(candidate.anchor)
                if price is None or abs(log(price) - log(odds)) > limits['log_odds']:
                    continue
                value = clock(candidate.anchor, other_kickoff, at)
                if value is None:
                    continue
                if context.anchor['phase'] == 'pre':
                    gap = abs(value - reference) / limits['lead']
                else:
                    if candidate.anchor.get('score') != context.anchor.get('score') or not candidate.anchor.get('score'):
                        continue
                    gap = abs(value - reference) / limits['minute']
                if gap > 1:
                    continue
                considered += 1
                if not sequence.eligible(candidate):
                    continue
                state = sequence.match(candidate, budget).state
                if state != 'control':
                    indeterminate += state != 'case'
                    continue
                distance = max(gap, abs(log(price) - log(odds)) / limits['log_odds'])
                rank = (competition(candidate.anchor) != competition(context.anchor), round(distance, 12),
                        (at - events.emitted(other_raw)).total_seconds(),
                        hashlib.sha256(TIEBREAK + f"{context.anchor['message_id']}|{other}".encode()).hexdigest())
                if best is None or rank < best[0]:
                    best = rank, candidate, other_kickoff
            if best is None:
                counts['case_without_comparator' if not indeterminate else 'case_comparators_indeterminate'] += 1
                counts['risk_set_considered'] += considered
                continue
            rank, candidate, other_kickoff = best
            control = Decision(candidate.anchor, at, other_kickoff, 'control', (),
                               stratum(candidate.anchor, at, other_kickoff, candidate.prefix))
            reserved[control.fixture] = 'control'
            counts['matched_pairs'] += 1
            counts['risk_set_considered'] += considered
            cohort.pairs.append((case, control, dict(
                same_competition=not rank[0], scaled_distance=rank[1], comparator_quote_age_seconds=rank[2],
                case_clock=reference, control_clock=clock(candidate.anchor, other_kickoff, at),
                case_odds=odds, control_odds=priced(candidate.anchor))))
        return cohort


def recent_drop(recent, fixture):
    for pool in recent.values():
        pool.pop(fixture, None)
