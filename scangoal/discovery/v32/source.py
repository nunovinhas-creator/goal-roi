"""Project saved journal versions into v3.2 inputs; settlement stays separate."""

from collections import defaultdict
from dataclasses import replace
from decimal import Decimal
import hashlib
from itertools import groupby
from pathlib import Path

from ... import event_audit, event_intelligence as events, match_identity, store
from ...observability.extract import context, exchange_quote, price_bounds, usd_amount
from ...observability.timeline import invalid_completed_receipt
from ..replay import Replay
from .tape import Tape


def _amount(value):
    return float(value) if value is not None and value <= Decimal('1000000000') else None


def _context_fields(extracted):
    return dict(volume_cumulative_usd=_amount(extracted.cumulative), biggest_ticket_usd=_amount(extracted.biggest),
                count_unit=extracted.count_unit, n_bets=extracted.n_bets)


def _body(version, row):
    exchange = exchange_quote(version.text, row['source']) if row['source'] in ('Polymarket', 'Betfair') else None
    native = exchange[1] if exchange and row['source'] == 'Polymarket' else None
    return context(version.text, row['selection'], native_selection=native), exchange


def _goals(row, extracted, kickoff):
    if kickoff is not None and events.emitted(row) < kickoff:
        return
    for score in extracted.scores:
        if score.kind == 'goal':
            yield dict(row, event_kind='goal', score=score.score, minute=score.minute, stoppage=score.stoppage,
                       odds=None, price_kind=None, price_low=None, price_high=None, volume_usd=None,
                       biggest_ticket_usd=None, volume_cumulative_usd=None, n_bets=None, count_unit=None)


def _rows(versions):
    groups = defaultdict(list)
    for version in versions:
        groups[version.message_id].append(version)
    for group in groups.values():
        first = group[0]
        initial = first.parsed
        if initial['type'] in events.NON_SIGNALS:
            continue
        candidates = [v for v in group if (v.available, v.published) == (first.available, first.published)]
        alternatives = {(v.evidence['text_sha256'], tuple(v.parsed['explicit_match_ids'])) for v in candidates}
        if len(alternatives) > 1:
            # Conflicting versions of one message can qualify as an identity
            # barrier but can never create a positive witness.
            for v in candidates:
                if v.parsed['type'] not in events.NON_SIGNALS:
                    yield dict(events.event_of(v), event_kind='alert', identity_ambiguous=True)
            continue
        original = events.event_of(first)
        if first.evidence['edit_utc'] is not None or initial.get('ft') is not None:
            # Record the completeness defect without creating an alert.
            yield dict(original, event_kind='gap')
            continue
        fields, exchange = _body(first, original)
        kind, low, high = price_bounds(first.text, original['source'], original['odds'])
        volume = usd_amount(exchange[4]) if exchange else None
        if volume is not None:
            original['volume_usd'] = _amount(volume)
        # Retain the price form, so an explicit equal-bound range is never a point.
        odds = [str(value.normalize()) for value in (low, high)] if low is not None else None
        usable_odds = (high is not None and high <= Decimal('1000000000')
                       and (kind == 'point' or low != high))
        yield dict(original, **_context_fields(fields), event_kind='quote' if low is not None or original['market'] else 'alert',
                   odds=odds if usable_odds else None, price_kind=kind, price_low=_amount(low), price_high=_amount(high))

        known_parts = match_identity.components(original)
        yield from _goals(original, fields, known_parts[0])
        prior_revisions = {}
        for _, simultaneous in groupby(group, key=lambda v: (v.available, v.published)):
            revisions = [v for v in simultaneous if v.evidence['edit_utc'] is not None]
            if not revisions:
                continue
            signatures = {(v.evidence['text_sha256'], tuple(v.parsed['explicit_match_ids'])) for v in revisions}
            version = revisions[0]
            gap = dict(original, event_kind='gap', emitted_utc=events.iso(version.published),
                       available_utc=events.iso(version.available), receipt=version.evidence['receipt'])
            earlier = prior_revisions.setdefault(version.published, signatures)
            if len(signatures) != 1 or signatures != earlier or version.parsed['type'] in events.NON_SIGNALS:
                yield gap
                continue
            row = events.event_of(version)
            parts = match_identity.components(row)
            if (row['source'] != original['source'] or row['match_id'] != original['match_id']
                    or row['emitted_utc'] != original['emitted_utc']
                    or any(old is not None and new is not None and old != new for old, new in zip(known_parts, parts))):
                yield gap
                continue
            parsed, _ = _body(version, row)
            lifted = dict(row, **_context_fields(parsed), emitted_utc=events.iso(version.published),
                          event_kind='revision', odds=None, phase=None, score=None, minute=None, volume_usd=None)
            # Historical goal times cannot move evidence before publication.
            # Full-time scores are never projected into the tape.
            yield lifted
            if any(issue not in ('partial_goal_timeline',) for issue in parsed.issues):
                yield gap
            kickoff = known_parts[0] or parts[0]
            yield from _goals(lifted, parsed, kickoff)


def journal(directory, as_of, *, historical=False):
    path = Path(directory) / 'messages.jsonl'
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    versions, _ = events.read_versions(path, as_of)
    invalid = {line for line, row in store.message_rows(path) if invalid_completed_receipt(row)}
    tape = Tape(_rows(v for v in versions if v.evidence['journal_line'] not in invalid),
                as_of, historical=historical)
    if before != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError('message journal changed during replay')
    tape.journal_sha256 = before
    tape.rejected_receipts = frozenset(v.evidence['record_sha256'] for v in versions
                                       if v.evidence['journal_line'] in invalid)
    return tape


class Outcomes:
    """Settlement is available only after reservation, via the frozen kernel."""

    def __init__(self, directory, tape):
        self.tape = tape
        self.directory = directory
        self.archive = None

    def label(self, decision):
        if self.archive is None:
            audit = event_audit.build(self.directory, self.tape.as_of, quote_only=True)
            if audit['input_sha256']['messages.jsonl'] != self.tape.journal_sha256:
                raise ValueError('observation and outcome archives have different journal snapshots')
            if self.tape.rejected_receipts:
                audit['fixture_results'] = {
                    fixture: [row for row in rows
                              if (row.get('journal') or {}).get('record_sha256') not in self.tape.rejected_receipts]
                    for fixture, rows in audit['fixture_results'].items()
                }
            self.archive = Replay(audit)
            self.records = {row['record_sha256']: row for row in self.archive.by_id.values() if row.get('fixture_id')}
        proofs = self.tape.proofs.get((decision.fixture, decision.anchor['message_id']), {})
        rows = [self.records[proof] for proof, at in proofs.items() if at <= decision.at and proof in self.records]
        # If the old settlement archive cannot resolve the exact fixture, its
        # label contributes only the frozen missing-FT bounds.
        fixtures = {row['fixture_id'] for row in rows}
        if len(fixtures) != 1:
            return dict(status='missing_ft', payoff=None)
        row = dict(decision.anchor, fixture_id=next(iter(fixtures)), message_id=min(row['message_id'] for row in rows))
        return self.archive.label(replace(decision, anchor=row))
