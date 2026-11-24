"""Append-only v3 plans: pre-registration with zero confirmatory results.

A plan freezes the hypothesis family, protocol and interpreter digests and
the discovery cutoff. It lives in its own external directory and receipt
domain, separate from the v1/v2 ledger. Records from v1/v2 can be cited as
`source_id` but never carry their status or evidence into a plan.
"""

import fcntl
import os
from datetime import datetime, timezone
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from ... import chronology, event_intelligence as events, scientific_receipts
from .. import dsl, dsl_v2

DOMAIN = 'temporal-risk-set-plan-v3'
MAX_BYTES = 1_000_000
MAX_HYPOTHESES = 12
NAME = re.compile(r'([1-9][0-9]*)-([0-9a-f]{64})\.json')
HEX = re.compile(r'[0-9a-f]{64}')
KEYS = {'schema', 'number', 'previous', 'published_utc', 'discovery_as_of_utc', 'snapshot_sha256',
        'protocol_sha256', 'interpreter_sha256', 'hypotheses', 'confirmatory_results'}
HYPOTHESIS = {'id', 'rule', 'origin', 'mechanism', 'source_id'}


class RegistryError(ValueError):
    pass


def digest(plan):
    return scientific_receipts.digest(DOMAIN, plan)


def _identifier(rule, protocol_sha256=None):
    from .protocol import PROTOCOL_SHA256
    return 'tr3:' + dsl.digest(dict(protocol=protocol_sha256 or PROTOCOL_SHA256, rule=dsl_v2.canonical(rule)))


def validate(plan, number, parent, prior):
    if not isinstance(plan, dict) or set(plan) != KEYS:
        raise RegistryError('invalid_plan')
    published = chronology.parse_utc(plan['published_utc'])
    discovered = chronology.parse_utc(plan['discovery_as_of_utc'])
    if (plan['schema'] != 3 or plan['number'] != number or plan['previous'] != parent or published is None
            or discovered is None or discovered > published or (prior is not None and published < prior)):
        raise RegistryError('invalid_plan_order')
    if any(not isinstance(plan[k], str) or not HEX.fullmatch(plan[k])
           for k in ('snapshot_sha256', 'protocol_sha256', 'interpreter_sha256')):
        raise RegistryError('invalid_plan_digest')
    from .protocol import PROTOCOL_SHA256, SUPERSEDED, V1_PROTOCOL_SHA256, V2_PROTOCOL_SHA256
    if plan['protocol_sha256'] in (V1_PROTOCOL_SHA256, V2_PROTOCOL_SHA256):
        raise RegistryError('defective_protocol_cannot_be_registered_in_v3')
    # Superseded v3 plans remain readable history; evaluate refuses them.
    if plan['protocol_sha256'] != PROTOCOL_SHA256 and plan['protocol_sha256'] not in SUPERSEDED:
        raise RegistryError('unsupported_protocol')
    if plan['confirmatory_results'] != 0:
        raise RegistryError('plan_must_start_with_zero_confirmatory_results')
    rows = plan['hypotheses']
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_HYPOTHESES:
        raise RegistryError('invalid_family_size')
    seen = set()
    for h in rows:
        if not isinstance(h, dict) or set(h) != HYPOTHESIS or h['id'] in seen:
            raise RegistryError('invalid_or_duplicate_hypothesis')
        try:
            canonical = dsl_v2.canonical(h['rule'])
        except dsl.Invalid as exc:
            raise RegistryError('invalid_rule') from exc
        if canonical != h['rule'] or h['id'] != _identifier(canonical, plan['protocol_sha256']):
            raise RegistryError('invalid_hypothesis_fingerprint')
        seen.add(h['id'])
    return published


def read(directory):
    directory = Path(directory)
    if not directory.exists():
        return []
    entries = []
    for path in directory.iterdir():
        if path.name.startswith('.unpublished-'):
            continue
        match = NAME.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_file():
            raise RegistryError('unexpected_registry_entry')
        entries.append((int(match[1]), match[2], path))
    plans, parent, prior = [], None, None
    for number, (_, claimed, path) in enumerate(sorted(entries), 1):
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            raise RegistryError('plan_too_large')
        content = dsl.strict_json(raw)
        if raw != dsl.encoded(content) + b'\n' or digest(content) != claimed or path.name != f'{number}-{claimed}.json':
            raise RegistryError('plan_digest_mismatch')
        prior = validate(content, number, parent, prior)
        parent = claimed
        plans.append(content)
    return plans


def visible(plans, as_of):
    return [p for p in plans if chronology.parse_utc(p['published_utc']) <= as_of]


def append(directory, *, discovery_as_of, snapshot_sha256, protocol_sha256, interpreter_sha256, hypotheses, now=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        existing = read(directory)
        registered = {h['id'] for p in existing for h in p['hypotheses']}
        from .protocol import PROTOCOL_SHA256
        if protocol_sha256 != PROTOCOL_SHA256:
            raise RegistryError('only_the_current_v3_protocol_can_be_registered')
        fresh = [h for h in hypotheses if h['id'] not in registered]
        if not fresh:
            return None
        now = now or datetime.now(timezone.utc)
        parent = digest(existing[-1]) if existing else None
        plan = dict(schema=3, number=len(existing) + 1, previous=parent, published_utc=events.iso(now),
                    discovery_as_of_utc=events.iso(discovery_as_of), snapshot_sha256=snapshot_sha256,
                    protocol_sha256=protocol_sha256, interpreter_sha256=interpreter_sha256,
                    hypotheses=fresh, confirmatory_results=0)
        prior = chronology.parse_utc(existing[-1]['published_utc']) if existing else None
        validate(plan, plan['number'], parent, prior)
        payload = dsl.encoded(plan) + b'\n'
        fingerprint = digest(plan)
        with NamedTemporaryFile(dir=directory, prefix='.unpublished-') as temp:
            temp.write(payload)
            temp.flush()
            os.fsync(temp.fileno())
            os.link(temp.name, directory / f"{plan['number']}-{fingerprint}.json")
        os.fsync(fd)
        return dict(plan=plan['number'], sha256=fingerprint, hypotheses=len(fresh), published_utc=plan['published_utc'])
    finally:
        os.close(fd)


def attest(plan, receipts, as_of, *, verify=scientific_receipts.verify):
    recorded = chronology.parse_utc(plan['published_utc'])
    if recorded is None or recorded > as_of:
        return None
    proof = verify(receipts, DOMAIN, plan, as_of=as_of)
    if proof is None or proof.digest != digest(plan) or not recorded <= proof.at <= as_of:
        return None
    return proof
