"""Verify independently sealed plans and receipts in the offline trust domain."""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import timedelta
import fcntl
import hashlib
from math import isfinite
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from types import MappingProxyType

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from scangoal import chronology, event_intelligence as events, match_identity
from scangoal.discovery import dsl
from scangoal.discovery.v32.tape import Coverage, STREAMS, Tape, fixture_key
from scangoal.observability import contract_key

DOMAIN = 'goal-roi/pilot-offline-fixture-v1'
POLL = 'sampled_clob'
COMPLETE = 'complete_synthetic_feed'
FEED = {**dict.fromkeys(STREAMS, 'synthetic-event-writer'), 'book': 'mock-polymarket'}
STATUSES = {'received', 'empty', 'timeout', 'rate_limited', 'offline', 'suspended',
            'identity_conflict', 'incomplete', 'disk_error'}
MAX_LATENCY = timedelta(minutes=2)
MAX_CLOCK_SKEW = timedelta(seconds=2)
TYPED_ODDS = re.compile(r'[0-9]{1,10}(?:\.[0-9]{1,8})?\Z')


class Invalid(ValueError):
    """Evidence is missing, inconsistent or outside the declared protocol."""


def sha(value):
    return hashlib.sha256(dsl.encoded(value)).hexdigest()


def stamp(value):
    out = chronology.parse_utc(value)
    if out is None or events.iso(out) != value:
        raise Invalid('invalid_utc_stamp')
    return out


def sealed(data, key, kind):
    return dict(content=data, signature=key.sign(dsl.encoded((DOMAIN, kind, data))).hex())


def opened(envelope, key, kind):
    if not isinstance(envelope, dict) or set(envelope) != {'content', 'signature'}:
        raise Invalid('invalid_seal_envelope')
    try:
        signature = bytes.fromhex(envelope['signature'])
        if len(signature) != 64:
            raise ValueError
        key.verify(signature, dsl.encoded((DOMAIN, kind, envelope['content'])))
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise Invalid('invalid_signature') from exc
    return envelope['content']


@dataclass(frozen=True)
class Slot:
    fixture: str
    stream: str
    mode: str
    start: str
    end: str
    target: str
    inventory: str | None = None

    @property
    def id(self):
        return sha(asdict(self))

    def validate(self):
        left, right = stamp(self.start), stamp(self.end)
        for identity in (self.fixture, self.target, self.inventory if self.mode == COMPLETE else self.target):
            if (not isinstance(identity, str) or len(identity) != 64
                    or any(c not in '0123456789abcdef' for c in identity)):
                raise Invalid('invalid_scope_hash')
        if self.mode == POLL and self.stream == 'book' and left == right and self.inventory is None:
            return
        if (self.mode == COMPLETE and self.stream in STREAMS and left < right
                and self.target == sha((COMPLETE, self.fixture, self.stream))):
            return
        raise Invalid('invalid_slot_scope')


def make_plan(slots, *, sealed_at, catalog_sha256, authority):
    ordered = sorted(slots, key=lambda s: s.id)
    content = dict(schema=DOMAIN, sealed_at=events.iso(sealed_at), catalog_sha256=catalog_sha256,
                   slots=[asdict(slot) for slot in ordered])
    return sealed(content, authority, 'plan')


def load_plan(envelope, authority):
    content = opened(envelope, authority, 'plan')
    if not isinstance(content, dict) or set(content) != {'schema', 'sealed_at', 'catalog_sha256', 'slots'}:
        raise Invalid('invalid_plan_document')
    if content['schema'] != DOMAIN or not isinstance(content['slots'], list) or not content['slots']:
        raise Invalid('wrong_plan_domain_or_empty')
    catalog = content['catalog_sha256']
    if not isinstance(catalog, str) or len(catalog) != 64 or any(c not in '0123456789abcdef' for c in catalog):
        raise Invalid('invalid_catalog_hash')
    plan_time = stamp(content['sealed_at'])
    slots = {}
    for row in content['slots']:
        try:
            slot = Slot(**row)
            slot.validate()
        except TypeError as exc:
            raise Invalid('invalid_slot_shape') from exc
        if slot.id in slots or plan_time > stamp(slot.start):
            raise Invalid('duplicate_or_postdated_slot_plan')
        slots[slot.id] = slot
    scopes = {(slot.fixture, slot.stream, slot.start, slot.end, slot.target) for slot in slots.values()}
    if len(scopes) != len(slots) or list(slots) != sorted(slots):
        raise Invalid('duplicate_semantic_slot_or_noncanonical_plan')
    return sha(envelope), slots


def receipt(slot, *, requested, available, source_clock, status, payload):
    """Normalized offline capture before the distinct capture seal is applied."""
    return dict(slot=slot.id, fixture=slot.fixture, stream=slot.stream, origin=FEED[slot.stream],
                status=status, requested_utc=None if requested is None else events.iso(requested),
                available_utc=events.iso(available), source_utc=events.iso(source_clock),
                body_sha256=sha(payload), payload=payload)


def validate_receipt(row, slot):
    fields = {'slot', 'fixture', 'stream', 'origin', 'status', 'requested_utc', 'available_utc',
              'source_utc', 'body_sha256', 'payload'}
    if not isinstance(row, dict) or set(row) != fields:
        raise Invalid('invalid_receipt_shape')
    if (row['slot'], row['fixture'], row['stream'], row['origin']) != (
            slot.id, slot.fixture, slot.stream, FEED[slot.stream]):
        raise Invalid('receipt_scope_mismatch')
    if not isinstance(row['status'], str) or row['status'] not in STATUSES or sha(row['payload']) != row['body_sha256']:
        raise Invalid('invalid_status_or_body_hash')
    available = stamp(row['available_utc'])
    edge = stamp(slot.end)
    if row['requested_utc'] is None:
        if row['status'] not in {'offline', 'disk_error'} or row['payload'] is not None or row['source_utc'] is not None:
            raise Invalid('missing_request_clock')
        if available < edge:
            raise Invalid('premature_failure')
        return
    request = stamp(row['requested_utc'])
    if not edge <= request <= available:
        raise Invalid('request_or_receipt_outside_clock')
    if row['status'] not in {'received', 'empty'}:
        if row['source_utc'] is not None or row['payload'] is not None:
            raise Invalid('failed_slot_has_evidence')
        return
    observed = stamp(row['source_utc'])
    if not edge - MAX_CLOCK_SKEW <= observed <= available or available - request > MAX_LATENCY:
        raise Invalid('unproven_latency_or_source_clock')
    if slot.mode == COMPLETE and observed != edge:
        raise Invalid('no_signed_end_watermark')


class Journal:
    """One fsynced canonical file per entry, with a checked append-only chain."""

    def __init__(self, path, plan_sha, public_key, *, witness=None):
        self.path = Path(path)
        self.plan_sha = plan_sha
        self.public_key = public_key
        self.witness = witness

    def read(self):
        result, parent, previous = [], self.plan_sha, None
        if not self.path.exists():
            names = []
        else:
            names = sorted(p for p in self.path.iterdir() if not p.name.startswith('.unpublished-'))
        for index, path in enumerate(names, 1):
            if path.name != f'{index:08d}.json' or path.is_symlink() or not path.is_file():
                raise Invalid('journal_filename_or_sequence')
            raw = path.read_bytes()
            envelope = dsl.strict_json(raw)
            if raw != dsl.encoded(envelope) + b'\n':
                raise Invalid('noncanonical_journal')
            content = opened(envelope, self.public_key, 'receipt')
            if (not isinstance(content, dict) or set(content) != {'sequence', 'previous', 'plan', 'receipt'}
                    or type(content['sequence']) is not int or content['sequence'] != index
                    or content['previous'] != parent or content['plan'] != self.plan_sha
                    or not isinstance(content['receipt'], dict)):
                raise Invalid('journal_chain_mismatch')
            clock = stamp(content['receipt'].get('available_utc'))
            if previous is not None and clock < previous:
                raise Invalid('nonmonotone_capture_log')
            parent = sha(envelope)
            previous = clock
            result.append((parent, content['receipt']))
        if self.witness is not None and self.witness not in {head for head, _ in result}:
            raise Invalid('witnessed_checkpoint_missing')
        return result

    def append_many(self, rows, capture):
        """Batch writes retain durable prefix after interruption, with idempotent restart."""
        self.path.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            entries = self.read()
            seen = {sha(row) for _, row in entries}
            last = stamp(entries[-1][1]['available_utc']) if entries else None
            parent = entries[-1][0] if entries else self.plan_sha
            index = len(entries)
            for row in rows:
                signature = sha(row)
                if signature in seen:
                    continue
                clock = stamp(row['available_utc'])
                if last is not None and clock < last:
                    raise Invalid('backdated_capture_append')
                envelope = sealed(dict(sequence=index + 1, plan=self.plan_sha, previous=parent, receipt=row),
                                  capture, 'receipt')
                index += 1
                self._publish(fd, index, envelope)
                parent, last = sha(envelope), clock
                seen.add(signature)
            return parent
        finally:
            os.close(fd)

    def _publish(self, fd, index, envelope):
        with NamedTemporaryFile(dir=self.path, prefix='.unpublished-') as scratch:
            scratch.write(dsl.encoded(envelope) + b'\n')
            scratch.flush()
            os.fsync(scratch.fileno())
            os.link(scratch.name, self.path / f'{index:08d}.json')
        os.fsync(fd)


@dataclass
class Evidence:
    rows: tuple
    coverage: tuple
    slots: dict
    counts: dict


def _continuous(slot, row, proof):
    payload = row['payload']
    if not isinstance(payload, list) or sha(payload) != slot.inventory:
        raise Invalid('independent_inventory_mismatch')
    if row['status'] != ('received' if payload else 'empty'):
        raise Invalid('inventory_status_mismatch')
    left, right, known = stamp(slot.start), stamp(slot.end), stamp(row['available_utc'])
    identities = set()
    for item in payload:
        if not isinstance(item, dict) or item.get('event_kind') != slot.stream or item.get('receipt') != 'completed_page':
            raise Invalid('wrong_typed_stream')
        if not isinstance(item.get('match_id'), str) or not item['match_id']:
            raise Invalid('missing_typed_match_identity')
        for field in ('volume_usd', 'volume_cumulative_usd', 'biggest_ticket_usd', 'n_bets',
                      'share_pct', 'price_low', 'price_high', 'minute'):
            value = item.get(field)
            if value is not None and (type(value) not in (int, float) or not 0 <= value <= 1_000_000_000
                                      or not isfinite(value)):
                raise Invalid('invalid_typed_number')
        odds = item.get('odds')
        if odds is not None and (not isinstance(odds, list) or len(odds) != 2
                                 or any(not isinstance(x, str) or not TYPED_ODDS.fullmatch(x) for x in odds)):
            raise Invalid('invalid_typed_odds')
        score = item.get('score')
        if score is not None and (not isinstance(score, list) or len(score) != 2
                                  or any(type(x) is not int or x < 0 for x in score)):
            raise Invalid('invalid_typed_score')
        if item.get('count_unit') not in (None, 'bets', 'trades'):
            raise Invalid('invalid_typed_count_unit')
        parts = match_identity.components(item)
        if not all(parts) or fixture_key(parts) != slot.fixture:
            raise Invalid('wrong_event_fixture')
        emitted, seen = stamp(item.get('emitted_utc')), stamp(item.get('available_utc'))
        if not left <= emitted <= right or not emitted <= seen <= right:
            raise Invalid('event_outside_sealed_watermark')
        fingerprint = sha(item)
        if fingerprint in identities:
            raise Invalid('duplicate_event_in_inventory')
        identities.add(fingerprint)
    # The projector cannot assume it saw a row before receiving the seal.
    observed = [dict(item, available_utc=row['available_utc']) for item in payload]
    return Coverage(slot.fixture, left, right, known, proof, frozenset({slot.stream})), observed


def _project(slots, entries, cutoff):
    """A sample of a book never becomes continuous typed coverage."""
    grouped = defaultdict(list)
    for proof, row in entries:
        if stamp(row['available_utc']) <= cutoff:
            if row.get('slot') not in slots:
                raise Invalid('receipt_not_in_signed_plan')
            grouped[row['slot']].append((proof, row))
    observations, spans, states, counts = {}, [], {}, Counter()
    for slot_id, slot in slots.items():
        records = grouped[slot_id]
        if not records:
            status = 'pending' if stamp(slot.end) > cutoff else 'lost_receipt'
        elif len({sha(row) for _, row in records}) != 1:
            status = 'conflicting_receipts'
        else:
            proof, row = records[0]
            try:
                validate_receipt(row, slot)
                status = row['status']
                if status in {'received', 'empty'} and slot.mode == COMPLETE:
                    span, payload = _continuous(slot, row, proof)
                    spans.append(span)
                    for item in payload:
                        observations[sha(item)] = item
            except Invalid as exc:
                status = str(exc)
        chosen = records[0][1] if len(records) == 1 else None
        states[slot_id] = dict(status=status, attempted=any(r['requested_utc'] is not None for _, r in records
                                                          if 'requested_utc' in r), receipt=chosen)
        counts[status] += 1
    return Evidence(tuple(observations[k] for k in sorted(observations)),
                    tuple(sorted(spans, key=lambda s: (s.fixture, s.start, s.end, sorted(s.streams)))), states, dict(counts))


class VerifiedReplay:
    """Project each decision from the checked receipt prefix for that instant."""

    def __init__(self, plan, authority, journal, capture, *, witness):
        if authority.public_bytes(Encoding.Raw, PublicFormat.Raw) == capture.public_bytes(Encoding.Raw, PublicFormat.Raw):
            raise Invalid('plan_and_capture_authorities_must_be_distinct')
        self.plan_sha, slots = load_plan(plan, authority)
        self.slots = MappingProxyType(slots)
        self._entries = tuple(Journal(journal, self.plan_sha, capture, witness=witness).read())
        self.catalog_sha256 = plan['content']['catalog_sha256']
        self.head = self._entries[-1][0] if self._entries else self.plan_sha

    @property
    def entries(self):
        return deepcopy(self._entries)

    def at(self, cutoff):
        if cutoff.utcoffset() is None:
            raise Invalid('timezone_required')
        return _project(self.slots, self.entries, cutoff)

    def tape_at(self, cutoff, *, fixtures=None):
        evidence = self.at(cutoff)
        rows = [row for row in evidence.rows if fixtures is None or row['match_id'] in fixtures]
        spans = evidence.coverage
        if fixtures is not None:
            keys = {fixture_key(match_identity.components(row)) for row in rows}
            spans = [span for span in spans if span.fixture in keys]
        return Tape(conservative_quotes(rows), cutoff, spans)


def conservative_quotes(rows):
    """Withhold conflicting scalar state even when both books quote the same price."""
    groups, other = defaultdict(list), []
    for row in rows:
        if row['event_kind'] == 'quote' and (key := contract_key(dict(row, fixture_id=None))) is not None:
            fixture = fixture_key(match_identity.components(row))
            groups[fixture, key, row['emitted_utc']].append(row)
        else:
            other.append(row)
    fields = ('volume_usd', 'volume_cumulative_usd', 'biggest_ticket_usd', 'n_bets', 'count_unit', 'share_pct')
    for cluster in groups.values():
        masked = {field: None for field in fields if len({row.get(field) for row in cluster}) > 1}
        other.extend(dict(row, **masked) for row in cluster)
        if masked:
            # Closing evidence can show a complete inventory and still show
            # unknown facts. Protect absence on the affected interval too.
            latest = max(cluster, key=lambda row: (stamp(row['available_utc']), sha(row)))
            other.append(dict(latest, event_kind='gap'))
    return other
