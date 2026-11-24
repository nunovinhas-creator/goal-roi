"""Read the existing journal and expose versioned evidence to discovery engines."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TypeAlias

from scangoal import chronology, event_intelligence as journal, match_identity, report, store

from .contracts import contract_key
from .extract import context, exchange_quote, price_bounds, usd_amount
from .models import ContextObservation, Provenance, QuoteObservation, ScoreEvent

Observation: TypeAlias = QuoteObservation | ContextObservation


def _order(row: Provenance) -> tuple:
    return row.available_utc, row.published_utc, row.message_id, row.record_sha256


def _provenance(version: journal.Version) -> dict:
    proof = version.evidence
    return dict(emitted_utc=chronology.parse_utc(proof['emitted_utc']), published_utc=version.published,
                available_utc=version.available, receipt=proof['receipt'], message_id=version.message_id,
                record_sha256=proof['record_sha256'], text_sha256=proof['text_sha256'])


def _fixture(version: journal.Version, bindings: dict[str, match_identity.Binding],
             original: dict | None, event: dict) -> str | None:
    parsed = version.parsed
    if original is not None and (parsed['match_id'] != original['match_id']
                                 or version.evidence['emitted_utc'] != original['emitted_utc']
                                 or event['source'] != original['source']):
        return None
    binding = bindings.get(parsed['match_id'])
    if (binding is None or binding.available > version.available
            or (binding.collision is not None and binding.collision <= version.available)):
        return None
    parts = match_identity.components(parsed)
    kickoff, *identity = parts
    if not all(identity):
        return None
    emitted = chronology.parse_utc(version.evidence['emitted_utc'])
    if kickoff is not None:
        start = end = kickoff
    elif parsed.get('phase') == 'live':
        start, end = emitted - match_identity.LIVE_WINDOW, emitted
    elif (original is not None and original['match_id'] == parsed['match_id']
          and original['fixture_id'] == binding.canonical
          and original['emitted_utc'] == version.evidence['emitted_utc']):
        start, end = binding.start, binding.end
    else:
        return None
    if match_identity.compatible(binding, parts, start, end):
        return binding.canonical
    return None


@dataclass(frozen=True)
class Timeline:
    """Evidence ordered by knowledge time. Quote history survives message edits."""

    as_of_utc: datetime
    quotes: tuple[QuoteObservation, ...]
    versions: tuple[ContextObservation, ...]
    input_exclusions: tuple[tuple[str, int], ...]
    receipt_format_anomalies: int = 0

    def _check_clock(self, at: datetime) -> None:
        if at.utcoffset() is None or at > self.as_of_utc:
            raise ValueError('query clock must be aware and at or before the snapshot cutoff')

    def history(self, fixture_id: str, at: datetime, *, since: datetime | None = None,
                completed_only: bool = False) -> tuple[Observation, ...]:
        """Available evidence in the inclusive receipt-time window for a fixture."""
        self._check_clock(at)
        if since is not None and (since.utcoffset() is None or since > at):
            raise ValueError('since must be aware and at or before the query clock')
        return tuple(sorted((row for rows in (self.quotes, self.versions) for row in rows
                             if row.fixture_id == fixture_id and row.available_utc <= at
                             and (since is None or since <= row.available_utc)
                             and (not completed_only or row.receipt == 'completed_page')), key=_order))

    def context_at(self, fixture_id: str, at: datetime, *,
                   completed_only: bool = False) -> tuple[ContextObservation, ...]:
        """Most recent proven publication per message, suppressing conflicting versions.

        No carry-forward of fields removed by an edit. Use history for evidence
        of what was previously reported, and for quote/event relationships.
        A completed-only view still observes supersession of the older state.
        """
        self._check_clock(at)
        by_message = defaultdict(list)
        for row in self.versions:
            if row.available_utc > at:
                break
            by_message[row.message_id].append(row)
        result = []
        for rows in by_message.values():
            publication = max(row.published_utc for row in rows)
            latest = [row for row in rows if row.published_utc == publication]
            variants = {(row.text_sha256, row.fixture_id, row.contract_key) for row in latest}
            if len(variants) != 1 or latest[0].fixture_id != fixture_id:
                continue
            evidence = next((row for row in latest if not completed_only or row.receipt == 'completed_page'), None)
            if evidence is not None:
                result.append(evidence)
        return tuple(sorted(result, key=_order))


def invalid_completed_receipt(row: dict) -> bool:
    """Reject values that only compare equal to the integer receipt version."""
    value = row.get('receipt_version')
    return value == 1 and type(value) is not int


def read_timeline(data_dir: Path, as_of: datetime) -> Timeline:
    """Reconstruct text observations automatically, without altering the journal."""
    if as_of.utcoffset() is None:
        raise ValueError('as_of must have an explicit timezone')
    path = Path(data_dir) / 'messages.jsonl'
    if not path.is_file():
        raise FileNotFoundError(path)
    original_stat = path.stat()
    versions, exclusions = journal.read_versions(path, as_of)
    # JSON true and 1.0 compare equal to 1 in Python but do not establish
    # a completed-page receipt in the observability schema.
    invalid_receipts = {line for line, row in store.message_rows(path)
                        if invalid_completed_receipt(row)}
    final_stat = path.stat()
    if (original_stat.st_ino, original_stat.st_size, original_stat.st_mtime_ns) != (
            final_stat.st_ino, final_stat.st_size, final_stat.st_mtime_ns):
        raise ValueError('message journal changed during reconstruction; use a stable snapshot')
    anomalies = 0
    if invalid_receipts:
        verified = [v for v in versions if v.evidence['journal_line'] not in invalid_receipts]
        anomalies = len(versions) - len(verified)
        if anomalies:
            exclusions['unknown_receipt_type'] += anomalies
        versions = verified
    originals, withheld, _, bindings, _ = journal.reconstruct(versions, as_of, burst_size=report.BURST)
    exclusions.update(row['reason'] for row in withheld)
    exclusions.update(row['exclusion'] for row in originals if row.get('exclusion'))
    by_message = {row['message_id']: row for row in originals}
    quotes, observations = [], []
    seen = set()
    observed_messages = set()
    for version in versions:
        proof = _provenance(version)
        if proof['record_sha256'] in seen:
            continue
        seen.add(proof['record_sha256'])
        parsed = version.parsed
        if parsed['type'] in journal.NON_SIGNALS:
            if version.message_id in observed_messages:
                observations.append(ContextObservation(
                    **proof, fixture_id=None, contract_key=None, source=None, scores=(),
                    biggest_ticket_usd=None, n_bets=None, count_unit=None, volume_cumulative_usd=None,
                    issues=('unsupported_revision',),
                ))
            continue
        observed_messages.add(version.message_id)
        original = by_message.get(version.message_id)
        is_original = original is not None and original['record_sha256'] == proof['record_sha256']
        event = original if is_original else journal.event_of(version)
        key = contract_key(event)
        fixture = _fixture(version, bindings, original, event)
        exchange = exchange_quote(version.text, event['source']) if event['source'] in ('Polymarket', 'Betfair') else None
        native_selection = exchange[1] if exchange and event['source'] == 'Polymarket' else None
        extracted = context(version.text, event['selection'], native_selection=native_selection)
        alert_score = tuple(event['score']) if event['score'] is not None else None
        scores, issues = extracted.scores, extracted.issues
        if original is not None and event['source'] != original['source']:
            issues = (*issues, 'revision_source_conflict')
        kickoff = journal.kickoff(event)
        if scores and kickoff is not None and kickoff > version.available:
            scores = ()
            issues = (*issues, 'score_timeline_before_kickoff')
        if alert_score is not None:
            scores = (ScoreEvent('alert', alert_score, event['minute']), *scores)
        observations.append(ContextObservation(
            **proof, fixture_id=fixture, contract_key=key, source=event['source'], scores=scores,
            biggest_ticket_usd=extracted.biggest, n_bets=extracted.n_bets, count_unit=extracted.count_unit,
            volume_cumulative_usd=extracted.cumulative, issues=issues,
        ))
        if not is_original:
            continue
        exclusion = event.get('exclusion')
        quote_fixture = fixture if event['fixture_id'] == fixture else None
        if quote_fixture is None and exclusion is None:
            exclusion = 'unproven_fixture_at_receipt'
            exclusions[exclusion] += 1
        kind, low, high = price_bounds(version.text, event['source'], event['odds'])
        volume = usd_amount(exchange[4]) if exchange else None
        if volume is None and event['volume_usd'] is not None:
            volume = Decimal(str(event['volume_usd']))
        quotes.append(QuoteObservation(
            **proof, fixture_id=quote_fixture, contract_key=key, source=event['source'],
            phase=event['phase'], price_kind=kind, price_low=low, price_high=high,
            price_point=low if kind == 'point' else None, volume_alert_usd=volume,
            volume_cumulative_usd=extracted.cumulative, biggest_ticket_usd=extracted.biggest, n_bets=extracted.n_bets,
            kickoff_utc=kickoff, minute=event['minute'], score=alert_score,
            exclusion=exclusion,
        ))
    return Timeline(as_of, tuple(quotes), tuple(observations), tuple(sorted(exclusions.items())), anomalies)
