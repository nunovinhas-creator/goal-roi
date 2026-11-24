"""Append-only, content-addressed offline publication journal."""

import fcntl
import os
from pathlib import Path
import re
from datetime import datetime, timezone
from tempfile import NamedTemporaryFile

from .. import chronology, event_intelligence as events, scientific_receipts
from . import dsl, dsl_v2

DOMAIN = 'temporal-discovery-publication-v1'
MAX_BYTES = 1_000_000
MAX_RECORDS = 64
NAME = re.compile(r'([1-9][0-9]*)-([0-9a-f]{64})\.json')
HEX = re.compile(r'[0-9a-f]{64}')
STATES = {'experimental', 'insufficient', 'rejected'}


class LedgerError(ValueError):
    pass


def digest(content):
    return scientific_receipts.digest(DOMAIN, content)


def _validate(batch, number, parent, prior):
    keys = {'schema', 'number', 'previous', 'published_utc', 'discovery_as_of_utc',
            'input_sha256', 'snapshot_sha256', 'records', 'investigation'}
    if not isinstance(batch, dict) or set(batch) != keys:
        raise LedgerError('invalid_batch')
    published = chronology.parse_utc(batch['published_utc'])
    discovered = chronology.parse_utc(batch['discovery_as_of_utc'])
    if (type(batch['schema']) is not int or batch['schema'] != 1 or type(batch['number']) is not int
            or batch['number'] != number or batch['previous'] != parent or published is None
            or discovered is None or discovered > published or (prior is not None and published < prior)):
        raise LedgerError('invalid_publication_order')
    manifest = batch['input_sha256']
    if (not isinstance(manifest, dict) or 'messages.jsonl' not in manifest
            or any(not isinstance(sha, str) or not HEX.fullmatch(sha) for sha in manifest.values())):
        raise LedgerError('invalid_discovery_manifest')
    if (not isinstance(batch['snapshot_sha256'], str) or not HEX.fullmatch(batch['snapshot_sha256'])
            or not isinstance(batch['investigation'], dict)):
        raise LedgerError('invalid_discovery_snapshot')
    records = batch['records']
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_RECORDS:
        raise LedgerError('invalid_record_count')
    identifiers = set()
    for record in records:
        if (not isinstance(record, dict) or not isinstance(record.get('id'), str)
                or not isinstance(record.get('status'), str) or record['status'] not in STATES
                or record['id'] in identifiers or not isinstance(record.get('reason'), str)):
            raise LedgerError('invalid_or_duplicate_record')
        identifiers.add(record['id'])
        if 'rule' in record:
            definition = record['rule']
            language = dsl_v2 if isinstance(definition, dict) and definition.get('version') == 2 else dsl
            rule = language.canonical(definition)
            if (definition != rule or record['id'] != language.identifier(rule)
                    or type(record.get('track_prospectively')) is not bool
                    or record['status'] == 'rejected' and record['track_prospectively']):
                raise LedgerError('invalid_definition_fingerprint')
        elif (record['status'] != 'rejected' or not record['id'].startswith('rejected:')
              or not HEX.fullmatch(record['id'].removeprefix('rejected:'))):
            raise LedgerError('missing_definition')
    return published


def read(directory, *, expected_head=None):
    directory = Path(directory)
    if not directory.exists():
        if expected_head is not None:
            raise LedgerError('missing_expected_head')
        return []
    entries = []
    for path in directory.iterdir():
        if path.name.startswith('.unpublished-'):
            continue
        match = NAME.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_file():
            raise LedgerError('unexpected_ledger_entry')
        entries.append((int(match[1]), match[2], path))
    batches, parent, prior = [], None, None
    for number, (_, claimed, path) in enumerate(sorted(entries), 1):
        with path.open('rb') as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise LedgerError('ledger_batch_too_large')
        content = dsl.strict_json(raw)
        if raw != dsl.encoded(content) + b'\n' or digest(content) != claimed:
            raise LedgerError('ledger_digest_mismatch')
        if path.name != f'{number}-{claimed}.json':
            raise LedgerError('ledger_sequence_gap')
        prior = _validate(content, number, parent, prior)
        parent = claimed
        batches.append(content)
    if expected_head is not None and (not batches or expected_head not in {digest(b) for b in batches}):
        raise LedgerError('missing_expected_head')
    return batches


def visible(batches, as_of):
    return [b for b in batches if chronology.parse_utc(b['published_utc']) <= as_of]


def known(batches, discovery_as_of, snapshot_sha256):
    """Retain decisions and rejection memory as of the applicable snapshot."""
    result = set()
    for batch in batches:
        if chronology.parse_utc(batch['published_utc']) > discovery_as_of:
            continue
        for record in batch['records']:
            if (record['status'] != 'insufficient' or record.get('track_prospectively')
                    or batch['snapshot_sha256'] == snapshot_sha256):
                result.add(record['id'])
    return result


def append(directory, *, discovery_as_of, manifest, snapshot_sha256, records, investigation, now=None):
    """Atomically append one immutable batch, under a directory file lock."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        existing = read(directory)
        retained = set()
        for b in existing:
            same_cut = b['snapshot_sha256'] == snapshot_sha256
            retained.update(r['id'] for r in b['records']
                            if r['status'] != 'insufficient' or r.get('track_prospectively') or same_cut)
        fresh = []
        for record in records:
            if record['id'] not in retained:
                fresh.append(record)
                retained.add(record['id'])
        if not fresh:
            return None
        now = datetime.now(timezone.utc) if now is None else now
        parent = digest(existing[-1]) if existing else None
        content = dict(schema=1, number=len(existing) + 1, previous=parent, published_utc=events.iso(now),
                       discovery_as_of_utc=events.iso(discovery_as_of), input_sha256=manifest,
                       snapshot_sha256=snapshot_sha256, records=fresh, investigation=investigation)
        prior = chronology.parse_utc(existing[-1]['published_utc']) if existing else None
        _validate(content, len(existing) + 1, parent, prior)
        payload = dsl.encoded(content) + b'\n'
        if len(payload) > MAX_BYTES:
            raise LedgerError('ledger_batch_too_large')
        fingerprint = digest(content)
        with NamedTemporaryFile(dir=directory, prefix='.unpublished-') as temp:
            temp.write(payload)
            temp.flush()
            os.fsync(temp.fileno())
            os.link(temp.name, directory / f"{content['number']}-{fingerprint}.json")
        os.fsync(fd)
        return dict(batch=content['number'], sha256=fingerprint, records=len(fresh),
                    published_utc=content['published_utc'])
    finally:
        os.close(fd)


def attest(batch, receipts, as_of, *, verify=scientific_receipts.verify):
    recorded = chronology.parse_utc(batch['published_utc'])
    if recorded is None or recorded > as_of:
        return None
    proof = verify(receipts, DOMAIN, batch, as_of=as_of)
    if proof is None or proof.digest != digest(batch) or not recorded <= proof.at <= as_of:
        return None
    return proof
