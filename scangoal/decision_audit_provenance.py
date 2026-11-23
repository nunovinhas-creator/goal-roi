"""Immutable input identifiers for the offline decision audit."""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import subprocess

from . import chronology, opportunity_results, store


REGISTRY_PATH = 'data/hypotheses.json'


def _canonical_sha256(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def signal_versions(path, as_of):
    """Identify the first visible journal row per message, like report.load.

    The record fingerprint covers the whole JSON object, including buttons and
    timestamps, with sorted keys and compact UTF-8 JSON. The journal's own SHA
    covers text only. Both hashes let a reader verify the original without
    including its payload in the audit.
    """
    found = {}
    for line, row in store.message_rows(path):
        collected = opportunity_results.utc(row.get('collected_at_utc'))
        emitted = opportunity_results.utc(row.get('date_utc'))
        edit = row.get('edit_date_utc')
        edited = opportunity_results.utc(edit)
        published = edited if edit else emitted
        if collected is None or published is None or max(collected, published) > as_of:
            continue
        message_id = row['message_id']
        if message_id in found:
            continue
        text_sha = hashlib.sha256(row['text'].encode('utf-8')).hexdigest()
        stored_sha = row.get('sha256')
        found[message_id] = {
            'message_id': message_id,
            'journal_line': line,
            'record_sha256': _canonical_sha256(row),
            'text_sha256': text_sha,
            'text_sha256_matches_journal': text_sha == stored_sha if stored_sha is not None else None,
            'emitted_utc': chronology.iso(emitted, timespec='auto'),
            'edit_utc': chronology.iso(edited, timespec='auto'),
            'first_read_utc': chronology.iso(collected, timespec='auto'),
            'published_utc': chronology.iso(published, timespec='auto'),
            'receipt_quality': 'completed_page' if row.get('receipt_version') == 1 else 'legacy_collection_stamp',
        }
    return found


@dataclass(frozen=True)
class Registration:
    declared: datetime | None = None
    proven_at: datetime | None = None
    effective: datetime | None = None
    proof: dict | None = None
    flags: tuple[str, ...] = ()
    entry: dict | None = None


def _git_bytes(root, *args, data=None):
    return subprocess.run(['git', '--no-optional-locks', '--no-replace-objects', '-C', str(root), *args],
                          input=data, capture_output=True, check=True).stdout


def _git(root, *args):
    return _git_bytes(root, *args).decode().strip()


def _visible_prefix(root, ref, as_of):
    pinned = _git(root, 'rev-parse', '--verify', '--end-of-options', f'{ref}^{{commit}}')
    visible = None
    previous = None
    for line in _git(root, 'log', '--first-parent', '--reverse', '--format=%H %cI', pinned).splitlines():
        commit, stamp = line.split()
        when = chronology.parse_utc(stamp)
        if when is None or when > as_of or (previous is not None and when < previous):
            break
        visible, previous = commit, when
    return visible


def _registry_versions(root, commits):
    # Batch reads cap memory without starting a process for every snapshot.
    for start in range(0, len(commits), 16):
        batch = commits[start:start + 16]
        queries = ''.join(f'{sha}:{REGISTRY_PATH}\n' for sha, _ in batch)
        response = _git_bytes(root, 'cat-file', '--batch', data=queries.encode())
        offset = 0
        for sha, stamp in batch:
            end = response.index(b'\n', offset)
            header = response[offset:end].split()
            offset = end + 1
            if header[-1] == b'missing':
                yield sha, stamp, None, {}
                continue
            oid, object_type, length = header
            if object_type != b'blob':
                raise ValueError('Registry object is not a blob')
            end = offset + int(length)
            yield sha, stamp, oid.decode(), json.loads(response[offset:end])
            offset = end + 1


def registration(root, hypothesis_id, as_of, *, ref='HEAD'):
    """Find the exact active registry entry in a visible first-parent prefix.

    The pinned Git ancestry proves content and ordering. The committer clock
    is repository-local and never an independent server/receipt attestation.
    Stop before a future or clock-regressing commit and ignore its descendants.
    Pin a known ref to reproduce a past audit without admitting new history.
    """
    try:
        if _git(root, 'rev-parse', '--is-shallow-repository') != 'false':
            return Registration(flags=('registration_history_unavailable',))
        visible = _visible_prefix(root, ref, as_of)
        if visible is None:
            return Registration(flags=('registration_not_observed_by_cutoff',))
        entries = _git(root, 'log', '--first-parent', '--reverse', '--format=%H %cI', visible, '--', REGISTRY_PATH)
        commits = [line.split() for line in entries.splitlines()]
        prior, latest = None, None
        changed = False
        for sha, stamp, oid, registry in _registry_versions(root, commits):
            if not isinstance(registry, dict):
                return Registration(flags=('registration_history_unavailable',))
            entry = registry.get(hypothesis_id)
            if hypothesis_id in registry and not isinstance(entry, dict):
                return Registration(flags=('registration_history_unavailable',))
            if entry != prior:
                changed = latest is not None
                latest = (sha, stamp, oid)
                prior = entry
        if prior is None:
            flag = 'registration_removed' if changed else 'registration_not_observed_by_cutoff'
            return Registration(flags=(flag,))
        sha, stamp, oid = latest
        when = chronology.parse_utc(stamp)
        proof = {'kind': 'git_first_parent_commit', 'commit': sha, 'path': REGISTRY_PATH,
                 'blob_oid': oid,
                 'entry_sha256': _canonical_sha256(prior), 'commit_utc': chronology.iso(when)}
    except (OSError, ValueError, subprocess.CalledProcessError):
        return Registration(flags=('registration_history_unavailable',))

    declared = chronology.born(prior)
    flags = ['registration_definition_changed'] if changed else []
    if declared is None:
        return Registration(None, when, None, proof, (*flags, 'invalid_registration_timestamp'), prior)
    if declared < when:
        flags.append('registration_claim_precedes_git')
    if declared > as_of:
        flags.append('registration_declared_after_cutoff')
    return Registration(declared, when, max(declared, when), proof, tuple(flags), prior)
