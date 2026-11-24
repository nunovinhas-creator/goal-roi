"""As-observed fixture timelines and outcome-independent reservation."""

from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta

from .. import chronology, event_audit, event_intelligence as events, opportunity_results, parser
from . import dsl

MAX_SEARCH_PER_ANCHOR = 4096
SETTLEMENT = timedelta(days=opportunity_results.SETTLEMENT_WINDOW_DAYS)
PAIR_LAG = timedelta(minutes=chronology.DECISION_DELAY_MAX_MIN)


@dataclass
class Budget:
    limit: int
    used: int = 0
    exhausted: bool = False

    def spend(self):
        if self.used >= self.limit:
            self.exhausted = True
            return False
        self.used += 1
        return True


@dataclass(frozen=True)
class Match:
    state: str
    witness: tuple = ()


def conjunction(atoms, catalog, *values):
    unknown = False
    for atom in atoms:
        result = dsl.predicate(atom, catalog[atom['field']].read(*values))
        if result is False:
            return False
        unknown |= result is None
    return None if unknown else True


class Sequence:
    def __init__(self, rule):
        self.rule = dsl.canonical(rule)
        self.steps = self.rule['steps']
        self.links = defaultdict(list)
        for atom in self.rule['relations']:
            self.links[atom['from']].append(atom)

    def anchor_state(self, anchor):
        return conjunction(self.steps[-1], dsl.FIELDS, anchor)

    def match(self, anchor, prefix, budget):
        """Three-valued existence of a distinct, strictly emitted sequence.

        The caller has already removed events received after the decision.
        Unknown inputs and truncated searches never become controls.
        """
        path = [None] * len(self.steps)
        path[-1] = anchor
        local = Budget(MAX_SEARCH_PER_ANCHOR)
        anchor_seen = events.seen(anchor)
        earliest = events.emitted(anchor) - timedelta(seconds=self.rule['within_seconds'])
        uncertain = False

        def search(step, end, unknown):
            nonlocal uncertain
            if step < 0:
                if not unknown:
                    return tuple(path)
                uncertain = True
                return None
            for i in range(end - 1, step - 1, -1):
                if not local.spend() or not budget.spend():
                    return None
                e = prefix[i]
                if (events.emitted(e) >= events.emitted(path[step + 1]) or events.emitted(e) < earliest
                        or events.seen(e) > anchor_seen or e['fixture_id'] != anchor['fixture_id']
                        or any(e['message_id'] == other['message_id'] for other in path[step + 1:])):
                    continue
                state = conjunction(self.steps[step], dsl.FIELDS, e)
                if state is False:
                    continue
                edge_missing = False
                for atom in self.links[step]:
                    state_edge = conjunction([atom], dsl.RELATIONS, e, path[atom['to']])
                    if state_edge is False:
                        break
                    edge_missing |= state_edge is None
                else:
                    path[step] = e
                    witness = search(step - 1, i, unknown or state is None or edge_missing)
                    if witness is not None:
                        return witness
                    if local.exhausted or budget.exhausted:
                        return None
            return None

        witness = search(len(path) - 2, len(prefix), False)
        if witness is not None:
            return Match('case', witness)
        if local.exhausted or budget.exhausted:
            return Match('search_budget')
        return Match('unknown' if uncertain else 'control')


def band(value, limits):
    return None if value is None else next((i for i, upper in enumerate(limits) if value < upper), len(limits))


@dataclass(frozen=True)
class Decision:
    anchor: dict
    at: object
    kickoff: object
    state: str
    witness: tuple
    stratum: tuple

    @property
    def fixture(self):
        return self.anchor['fixture_id']

    def describe(self):
        keys = ('message_id', 'match_id', 'journal_line', 'record_sha256', 'text_sha256',
                'emitted_utc', 'available_utc', 'source', 'phase', 'market', 'period', 'selection')
        return dict(fixture_id=self.fixture, decision_utc=events.iso(self.at), kickoff_utc=events.iso(self.kickoff),
                    state=self.state, events=[{k: e.get(k) for k in keys} for e in (self.witness or (self.anchor,))])


def stratum(anchor, at, kickoff, prefix):
    phase = anchor['phase']
    if phase == 'live':
        context = (band(anchor['minute'], (16, 31, 46, 61, 76)), tuple(anchor.get('score') or ()))
    else:
        lead = (kickoff - at).total_seconds() / 60 if kickoff is not None else None
        context = band(lead, (30, 120, 360))
    odds = anchor['odds']
    # Control coverage follows the opportunity observable at the time.
    return ((kickoff or at).date().isoformat(), anchor['source'], phase,
            dsl.FIELDS['native_market'].read(anchor), anchor['period'], anchor['line'], dsl.selection(anchor),
            context, band(float(odds[0]), (1.5, 2, 2.5, 3.5)) if odds else None,
            bool(odds and odds[0] == odds[1]), band(len(prefix), (1, 4, 8)))


def comparable(a, b):
    # Distinct fixtures are asserted by reservation. Match on known contract,
    # phase and price band, with common support within the same kickoff day.
    return a.stratum == b.stratum and a.fixture != b.fixture


class Replay:
    def __init__(self, audit):
        self.audit = audit
        self.as_of = chronology.parse_utc(audit['as_of_utc'])
        self.by_id = {e['message_id']: e for e in audit['events']}
        self.history = defaultdict(list)
        self.exclusions = defaultdict(int)
        self._labels = {}
        self._prior_fixtures = {}
        for e in audit['events']:
            if e['fixture_id'] and not e.get('exclusion') and events.seen(e) <= self.as_of:
                self.history[e['fixture_id']].append(e)
        for rows in self.history.values():
            rows.sort(key=lambda e: (events.emitted(e), events.seen(e), e['message_id']))
        self.times = {fixture: [events.emitted(e) for e in rows] for fixture, rows in self.history.items()}
        self.first_seen = {fixture: min(map(events.seen, rows)) for fixture, rows in self.history.items()}
        self.anchors = []
        for row in audit['episodes']:
            if row['kind'] != 'quote':
                continue
            if row['decision_utc'] is None:
                for reason in row['exclusion_reasons']:
                    self.exclusions[reason] += 1
                continue
            e = self.by_id[row['message_ids'][0]]
            at = chronology.parse_utc(row['decision_utc'])
            if at == events.seen(e) and at <= self.as_of:
                self.anchors.append((at, e))
        self.anchors.sort(key=lambda item: (item[0], events.emitted(item[1]), item[1]['message_id']))
        self.snapshot_sha256 = dsl.digest(dict(
            cutoff=audit['as_of_utc'],
            event_proofs=[(e['message_id'], e['record_sha256'], e['fixture_id'], e.get('exclusion'))
                          for e in audit['events']],
            decisions=[(events.iso(t), e['message_id']) for t, e in self.anchors],
            results=audit.get('fixture_results', {}),
        ))
        self.anchor_times = [t for t, _ in self.anchors]
        self.kickoffs = {
            fixture: [(events.seen(e), ko) for e in rows if (ko := events.kickoff(e)) is not None]
            for fixture, rows in self.history.items()
        }
        self.results = {
            fixture: [opportunity_results.Observation(
                r['source'], r['record_id'], 'regulation', opportunity_results.score_pair(r['score']),
                chronology.parse_utc(r['observed_utc']), chronology.parse_utc(r['available_utc']),
                chronology.parse_utc(r['published_utc']), chronology.parse_utc(r['kickoff_utc']),
                fixture_id=r['fixture_id'], link=r['link']) for r in rows]
            for fixture, rows in audit.get('fixture_results', {}).items()
        }

    def prior_fixtures(self, clock):
        if clock not in self._prior_fixtures:
            if len(self._prior_fixtures) >= 32:
                self._prior_fixtures.clear()
            self._prior_fixtures[clock] = frozenset(fixture for fixture, read in self.first_seen.items() if read <= clock)
        return self._prior_fixtures[clock]

    def kickoff_known(self, fixture, clock):
        stamps = {ko for at, ko in self.kickoffs[fixture] if at <= clock}
        return next(iter(stamps)) if len(stamps) == 1 else None

    def prefix(self, anchor, at, window, start=None):
        fixture = anchor['fixture_id']
        times, rows = self.times[fixture], self.history[fixture]
        sent = events.emitted(anchor)
        earliest = sent - timedelta(seconds=window)
        if start is not None:
            earliest = max(start, earliest)
        lo, hi = bisect_left(times, earliest), bisect_left(times, sent)
        visible = [e for e in rows[lo:hi] if events.seen(e) <= at]
        # Same contract + different odds in one emission second is ambiguous,
        # regardless of the order in which its Telegram pages arrived.
        ties = [e for e in rows[hi:bisect_right(times, sent)] if events.seen(e) <= at]
        prices = defaultdict(set)
        for e in (*visible, *ties):
            key = events.selection_key(e)
            if key and e['odds'] is not None:
                prices[events.emitted(e), key].add(tuple(e['odds']))

        def unambiguous(e):
            key = events.selection_key(e)
            return dict(e, odds=None) if len(prices[events.emitted(e), key]) > 1 else e

        return unambiguous(anchor), [unambiguous(e) for e in visible]

    def decide(self, rule, budget, *, start=None, end=None, discovery_cutoff=None):
        sequence = Sequence(rule)
        reserved = {}
        waiting = defaultdict(lambda: {'case': deque(), 'control': deque()})
        pairs = []
        scope_cutoff = discovery_cutoff if discovery_cutoff is not None else start
        old = self.prior_fixtures(scope_cutoff) if scope_cutoff is not None else set()
        begin = bisect_left(self.anchor_times, start) if start is not None else 0
        for at, event in self.anchors[begin:]:
            if end is not None and at >= end:
                break
            if not budget.spend():
                break
            fixture = event['fixture_id']
            if fixture in reserved or fixture in old:
                continue
            kickoff = self.kickoff_known(fixture, at)
            if start is not None and (events.emitted(event) < start or kickoff is None
                                      or not start <= kickoff < end or event['receipt'] != 'completed_page'):
                continue
            anchor, prefix = self.prefix(event, at, sequence.rule['within_seconds'], start)
            if start is not None:
                prefix = [e for e in prefix if e['receipt'] == 'completed_page']
            selection_key = events.selection_key(anchor)
            anchor = dict(anchor, prior_alerts=len(prefix), prior_sources=len({e['source'] for e in prefix}),
                          prior_compatible_quotes=sum(
                              e['odds'] is not None and events.selection_key(e) == selection_key
                              for e in prefix) if selection_key else None)
            if sequence.anchor_state(anchor) is not True:
                continue
            # Missing counterpart scope is not evidence of an absent pattern.
            result = sequence.match(anchor, prefix, budget)
            key = stratum(anchor, at, kickoff, prefix)
            d = Decision(anchor, at, kickoff, result.state, result.witness, key)
            reserved[fixture] = d
            if result.state not in ('case', 'control') or key[3] is None or key[4] is None or key[6] is None:
                continue
            opposite = 'control' if result.state == 'case' else 'case'
            pool = waiting[key]
            while pool[opposite] and at - pool[opposite][0].at >= PAIR_LAG:
                pool[opposite].popleft()
            if pool[opposite]:
                other = pool[opposite].pop()
                if comparable(d, other):
                    pairs.append((d, other) if result.state == 'case' else (other, d))
            else:
                pool[result.state].append(d)
        return list(reserved.values()), pairs

    def label(self, decision):
        d = decision
        cache_key = (d.anchor['message_id'], tuple(d.anchor['odds'] or ()), d.kickoff)
        if cache_key in self._labels:
            return self._labels[cache_key]
        deadline = d.at + SETTLEMENT
        clock = min(self.as_of, deadline)
        evidence = self.results.get(d.fixture, [])
        kickoff = d.kickoff or event_audit.fixture_kickoff(self.history[d.fixture], evidence, clock)
        resolved = opportunity_results.resolve(evidence, kickoff=kickoff, as_of=clock)
        w = event_audit.payoff(d.anchor, resolved.score)
        odds = dsl.point_odds(d.anchor)
        eligible_price = odds is not None and odds >= parser.MIN_ODDS
        profit = (w * (odds - 1) if w > 0 else w) if w is not None and eligible_price else None
        if resolved.conflict:
            status = 'conflicting_ft'
        elif resolved.score is None:
            status = 'pending_ft' if self.as_of < deadline else 'missing_ft'
        elif w is None:
            status = 'unsupported_settlement'
        else:
            status = 'settled'
        result = dict(status=status, ft=resolved.score, payoff=w, profit=profit,
                      priced=eligible_price, odds=odds if eligible_price else None)
        self._labels[cache_key] = result
        return result
