"""External immutable v3.2 plan registry with a distinct witness domain."""

import fcntl
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from ... import chronology, event_intelligence as events, scientific_receipts
from .. import dsl
from ..v3.registry import HEX, MAX_BYTES, NAME, RegistryError
from . import language, protocol

DOMAIN = 'temporal-risk-set-plan-v32'
KEYS = {'schema', 'number', 'previous', 'published_utc', 'discovery_as_of_utc',
        'snapshot_sha256', 'protocol_sha256', 'interpreter_sha256', 'hypotheses', 'confirmatory_results'}


def digest(plan):
    return scientific_receipts.digest(DOMAIN, plan)


def hypothesis_id(rule):
    return 'tr32:' + dsl.digest((protocol.PROTOCOL_SHA256, language.canonical(rule)))


def validate(plan, number, previous, prior):
    if not isinstance(plan, dict) or set(plan) != KEYS:
        raise RegistryError('invalid_v32_plan')
    if plan['protocol_sha256'] != protocol.PROTOCOL_SHA256:
        raise RegistryError('v32_registry_requires_current_protocol')
    published = chronology.parse_utc(plan['published_utc'])
    cutoff = chronology.parse_utc(plan['discovery_as_of_utc'])
    if (type(plan['schema']) is not int or plan['schema'] != 32
            or type(plan['number']) is not int or plan['number'] != number or plan['previous'] != previous
            or published is None or cutoff is None or cutoff > published or (prior is not None and published < prior)
            or type(plan['confirmatory_results']) is not int or plan['confirmatory_results'] != 0):
        raise RegistryError('invalid_v32_plan_order_or_state')
    for key in ('snapshot_sha256', 'interpreter_sha256'):
        if not isinstance(plan[key], str) or not HEX.fullmatch(plan[key]):
            raise RegistryError('invalid_v32_plan_digest')
    rows = plan['hypotheses']
    if not isinstance(rows, list) or not 1 <= len(rows) <= protocol.MAX_FAMILY:
        raise RegistryError('invalid_v32_family_size')
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'id', 'rule', 'origin'}
                or not isinstance(row['id'], str) or row['id'] in seen or not isinstance(row['origin'], str)):
            raise RegistryError('invalid_or_duplicate_v32_hypothesis')
        if row['rule'] != language.canonical(row['rule']) or row['id'] != hypothesis_id(row['rule']):
            raise RegistryError('invalid_v32_hypothesis_fingerprint')
        seen.add(row['id'])
    return published


def read(directory, *, expected_head=None):
    directory = Path(directory)
    plans, previous, prior = [], None, None
    if directory.exists():
        files = []
        for path in directory.iterdir():
            if path.name.startswith('.unpublished-'):
                continue
            match = NAME.fullmatch(path.name)
            if match is None or path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
                raise RegistryError('unexpected_v32_registry_entry')
            files.append((int(match[1]), match[2], path))
        for number, (named_number, claimed, path) in enumerate(sorted(files), 1):
            raw = path.read_bytes()
            plan = dsl.strict_json(raw)
            if (number != named_number or path.name != f'{number}-{claimed}.json'
                    or raw != dsl.encoded(plan) + b'\n' or digest(plan) != claimed):
                raise RegistryError('v32_plan_digest_mismatch')
            prior = validate(plan, number, previous, prior)
            previous = claimed
            plans.append(plan)
    if expected_head is not None and not any(digest(plan) == expected_head for plan in plans):
        raise RegistryError('expected_v32_checkpoint_missing')
    return plans


def append(directory, *, cutoff, snapshot, interpreter, hypotheses, now=None, expected_head=None):
    repository = Path(__file__).resolve().parents[3]
    directory = Path(directory).resolve()
    if directory.is_relative_to(repository) or repository.is_relative_to(directory):
        raise RegistryError('use_a_separate_v32_registry_directory')
    if not hypotheses:
        if expected_head is not None:
            read(directory, expected_head=expected_head)
        return None
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        plans = read(directory, expected_head=expected_head)
        registered = {row['id'] for plan in plans for row in plan['hypotheses']}
        fresh = [row for row in hypotheses if row['id'] not in registered]
        if not fresh:
            return None
        previous = digest(plans[-1]) if plans else None
        prior = chronology.parse_utc(plans[-1]['published_utc']) if plans else None
        now = datetime.now(timezone.utc) if now is None else now
        plan = dict(schema=32, number=len(plans) + 1, previous=previous, published_utc=events.iso(now),
                    discovery_as_of_utc=events.iso(cutoff), snapshot_sha256=snapshot,
                    protocol_sha256=protocol.PROTOCOL_SHA256, interpreter_sha256=interpreter,
                    hypotheses=fresh, confirmatory_results=0)
        validate(plan, plan['number'], previous, prior)
        fingerprint = digest(plan)
        with NamedTemporaryFile(dir=directory, prefix='.unpublished-') as temp:
            temp.write(dsl.encoded(plan) + b'\n')
            temp.flush()
            os.fsync(temp.fileno())
            os.link(temp.name, directory / f"{plan['number']}-{fingerprint}.json")
        os.fsync(fd)
        return dict(plan=plan['number'], sha256=fingerprint, hypotheses=len(fresh), published_utc=plan['published_utc'])
    finally:
        os.close(fd)


def attest(plan, receipts, as_of, *, verify=scientific_receipts.verify):
    published = chronology.parse_utc(plan['published_utc'])
    if published is None or published > as_of:
        return None
    proof = verify(receipts, DOMAIN, plan, as_of=as_of)
    if proof is None or proof.digest != digest(plan) or not published <= proof.at <= as_of:
        return None
    return proof
