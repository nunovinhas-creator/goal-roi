"""Observation-only tape indexed by receipt time and semantic fixture identity."""

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import groupby
from math import isfinite

from ... import event_intelligence as events, match_identity
from ...observability import contract_key
from .. import dsl

STREAMS = frozenset({'quote', 'alert', 'revision', 'goal', 'score'})
FIELDS = (
    'source', 'signal_type', 'signal_kind', 'kickoff_utc', 'league', 'teams', 'phase', 'market',
    'market_key', 'period', 'selection', 'direction', 'line', 'odds', 'price_kind', 'price_low', 'price_high',
    'volume_usd', 'share_pct', 'minute', 'score', 'event_kind', 'stoppage',
    'n_bets', 'count_unit', 'biggest_ticket_usd', 'volume_cumulative_usd',
)
DOMAIN = 'goal-roi/temporal-risk-set-v3.2'


def fixture_key(parts):
    kickoff, home, away, league = parts
    return dsl.digest((events.iso(kickoff), home, away, league))


@dataclass(frozen=True)
class Coverage:
    """Proof of complete enumeration of the typed streams in [start, end].

    Trusted adapters supply this only for a sealed, verified archive.
    The saved Telegram journal cannot supply this proof.
    """

    fixture: str
    start: datetime
    end: datetime
    available: datetime
    proof_sha256: str
    streams: frozenset[str] = STREAMS

    def __post_init__(self):
        if (not all(stamp.utcoffset() is not None for stamp in (self.start, self.end, self.available))
                or not self.start <= self.end <= self.available or not self.streams or not self.streams <= STREAMS
                or len(self.proof_sha256) != 64 or any(c not in '0123456789abcdef' for c in self.proof_sha256)):
            raise ValueError('invalid coverage evidence')


def _normal(row):
    values = {name: row.get(name) for name in FIELDS}
    values['teams'] = [match_identity.normalized(team) for team in row.get('teams') or ()]
    values['league'] = match_identity.normalized(row.get('league'))
    values['kickoff_utc'] = events.iso(events.kickoff(values))
    values['score'] = list(values['score']) if values['score'] is not None else None
    return values


class Tape:
    """Replay stable input to any cutoff, without settlement evidence."""

    def __init__(self, rows, as_of, coverage=(), *, historical=False):
        if as_of.utcoffset() is None:
            raise ValueError('as_of must be timezone-aware')
        self.as_of = as_of
        self.coverage = defaultdict(set)
        for span in coverage:
            if span.available <= as_of:
                self.coverage[span.fixture].add(span)
        self.blocked = {}
        self.parts = {}
        self.aliases = {}
        self.first_seen = {}
        self.by_fixture = defaultdict(list)
        self.times = {}
        self.batches = {}
        self.proofs = {}
        self.lookup_rows = {}
        self.issues = defaultdict(set)
        claims = defaultdict(set)
        partials = defaultdict(set)
        tainted = set()
        observed = set()
        visible = sorted(((at, row) for row in rows
                          if (at := events.seen(row)) is not None and at <= as_of
                          and (historical or row['receipt'] == 'completed_page')
                          and (sent := events.emitted(row)) is not None and sent <= at),
                         key=lambda item: item[0])
        for at, group in groupby(visible, key=lambda item: item[0]):
            batch = sorted((row for _, row in group), key=lambda row: row['receipt'] != 'completed_page')
            aliases = set()
            for row in batch:
                alias = row.get('match_id')
                if not alias or row.get('event_kind') not in {'quote', 'alert'}:
                    continue
                parts = match_identity.components(row)
                if row.get('identity_ambiguous'):
                    tainted.add(alias)
                if not all(parts[1:]) or parts[1] == parts[2]:
                    continue
                aliases.add(alias)
                partials[alias].add(parts[1:])
                if parts[0] is not None:
                    claims[alias].add(parts)
            for alias in sorted(aliases):
                if len(partials[alias]) > 1 or len(claims[alias]) > 1 or alias in tainted:
                    for parts in claims[alias]:
                        self.blocked.setdefault(fixture_key(parts), at)
                    self.aliases.pop(alias, None)
                elif len(claims[alias]) == 1:
                    parts, = claims[alias]
                    fixture = fixture_key(parts)
                    self.parts[fixture] = parts
                    self.aliases[alias] = fixture, at if alias not in self.aliases else self.aliases[alias][1]

            touched = set()
            for row in batch:
                alias = row.get('match_id')
                if alias not in self.aliases or row.get('identity_ambiguous'):
                    continue
                fixture = self.aliases[alias][0]
                if fixture in self.blocked:
                    continue
                parts = match_identity.components(row)
                if not all(parts[1:]) or any(new is not None and new != old for new, old in zip(parts, self.parts[fixture])):
                    continue
                if row.get('event_kind') == 'gap':
                    self.issues[fixture].add((at, events.emitted(row)))
                    touched.add(fixture)
                    continue
                if row.get('event_kind') not in STREAMS:
                    continue
                normalized = _normal(row)
                normalized['kickoff_utc'] = events.iso(self.parts[fixture][0])
                issue = events.emitted(row)
                # A repeated goal report is one observed assertion, with the
                # first available evidence. It cannot generate a second goal.
                if row['event_kind'] == 'goal':
                    identity = (fixture, 'goal', normalized['minute'], normalized['stoppage'], normalized['score'])
                    score = {name: normalized[name] for name in
                             ('event_kind', 'kickoff_utc', 'league', 'teams', 'score', 'minute', 'stoppage')}
                    normalized = dict.fromkeys(FIELDS) | score
                else:
                    identity = (fixture, events.iso(issue), normalized)
                fingerprint = dsl.digest(identity)
                if row.get('record_sha256'):
                    self.proofs.setdefault((fixture, fingerprint), {}).setdefault(row['record_sha256'], at)
                    self.lookup_rows[(row['record_sha256'], row['event_kind'])] = fixture, fingerprint
                if fingerprint in observed:
                    continue
                observed.add(fingerprint)
                normalized.update(fixture_id=fixture, emitted_utc=events.iso(issue), available_utc=events.iso(at),
                                  quote_issued_utc=events.iso(issue), receipt=row['receipt'],
                                  message_id=fingerprint, record_sha256=fingerprint)
                self.by_fixture[fixture].append(normalized)
                self.first_seen.setdefault(fixture, at)
                touched.add(fixture)
            if touched:
                self.batches[at] = touched

        for fixture, spans in self.coverage.items():
            for span in spans:
                self.batches.setdefault(span.available, set()).add(fixture)
        for fixture, history in self.by_fixture.items():
            history.sort(key=lambda row: (events.seen(row), row['message_id']))
            self.times[fixture] = [events.seen(row) for row in history]
        self.snapshot_sha256 = dsl.digest(dict(
            cutoff=events.iso(as_of),
            events=sorted((row['message_id'], row['available_utc'], row['receipt'])
                          for history in self.by_fixture.values() for row in history),
            blocked=sorted((fixture, events.iso(at)) for fixture, at in self.blocked.items()),
            coverage=sorted((span.fixture, events.iso(span.start), events.iso(span.end),
                             events.iso(span.available), span.proof_sha256, sorted(span.streams))
                            for spans in self.coverage.values() for span in spans),
            gaps=sorted((fixture, events.iso(available), events.iso(issued))
                        for fixture, issues in self.issues.items() for available, issued in issues),
        ))

    def blocked_at(self, fixture, at):
        return fixture in self.blocked and self.blocked[fixture] <= at

    def history(self, fixture, at):
        size = bisect_right(self.times.get(fixture, ()), at)
        return self.by_fixture.get(fixture, ())[:size]

    def covered(self, fixture, start, end, at):
        if self.blocked_at(fixture, at) or any(available <= at and start <= issued <= end
                                               for available, issued in self.issues.get(fixture, ())):
            return False
        for stream in STREAMS:
            right = start
            for span in sorted(self.coverage.get(fixture, ()), key=lambda p: (p.start, p.end)):
                if span.available <= at and stream in span.streams and span.start <= right <= span.end:
                    right = max(right, span.end)
                    if right >= end:
                        break
            else:
                return False
        return True


def point(row):
    odds = row.get('odds')
    if row.get('price_kind') == 'point' and odds is not None and odds[0] == odds[1]:
        decimal = float(odds[0])
        if isfinite(decimal):
            return decimal
    return None


def states(history):
    """Return a prefix and latest quote per native contract, with conflicting evidence masked."""
    prices = defaultdict(set)
    latest = {}
    for row in history:
        if row['event_kind'] != 'quote' or (key := contract_key(row)) is None:
            continue
        stamp = events.emitted(row)
        prices[key, stamp].add((row['price_kind'], tuple(row['odds'] or ()), row['price_low'],
                                row['price_high'], row['minute'], tuple(row['score'] or ())))
    prefix = []
    for row in history:
        if row['event_kind'] == 'quote' and (key := contract_key(row)) is not None:
            stamp = events.emitted(row)
            if len(prices[key, stamp]) > 1:
                row = dict(row, odds=None, price_kind='indeterminate', price_low=None, price_high=None)
            prior = latest.get(key)
            if prior is None or (stamp, row['message_id']) > (events.emitted(prior), prior['message_id']):
                latest[key] = row
        prefix.append(row)
    prefix.sort(key=lambda row: (events.emitted(row), row['message_id']))
    return prefix, latest
