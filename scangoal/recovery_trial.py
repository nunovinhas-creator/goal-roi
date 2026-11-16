"""Manual trial preparation and OFFLINE simulation; live execution is fail-closed.

A prepared, network-disabled adapter is in recovery_trial_transport. Supplying an integer balance or a
collector-idle assertion cannot establish an account-wide exclusive lease.
"""
import argparse
import copy
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from . import recovery as engine
from .recovery_audit import snapshot

ENGINE_SHA = '247deee40e5e6ebdb107808b66adb96622074238'
TRIAL_ID = 'goal-roi-bsd-20261113-15-v1'
DAYS = frozenset(('2026-11-13', '2026-11-14', '2026-11-15'))
MATCH_IDS = frozenset((
    'vasteras-sk-vs-brommapojkarna-20261113',
    'shooting-stars-nigeria-vs-bendel-insurance-20261113',
    'lumezzane-vs-trento-calcio-1921-20261114',
    'fukushima-vs-sagamihara-20261114',
    'athletic-bilbao-b-vs-racing-ferrol-20261114',
    'villa-dalmine-vs-deportivo-camioneros-20261114',
    'horsham-vs-torquay-united-20261114',
    'farnham-vs-ebbsfleet-united-20261114',
    'fc-guidonia-montecelio-1937-vs-as-ostia-mare-20261115',
    'cittadella-vs-us-folgore-caratese-asd-20261115',
))
CAP = 5
UTC = timezone.utc


class TrialBlocked(RuntimeError):
    pass


def manifest(root):
    """Hash every original byte, rejecting symlinks (also directory symlinks)."""
    out = {}
    for p in sorted(Path(root).rglob('*')):
        if p.is_symlink():
            raise TrialBlocked('symlink_input')
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def verify_engine(root):
    """All original Python modules must equal the audited Git objects, byte for byte."""
    names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', ENGINE_SHA, 'scangoal'], cwd=root, text=True).splitlines()
    if not names:
        raise TrialBlocked('audited_code_unavailable')
    for name in names:
        p = Path(root) / name
        original = subprocess.check_output(['git', 'show', ENGINE_SHA + ':' + name], cwd=root)
        if p.is_symlink() or not p.is_file() or p.read_bytes() != original:
            raise TrialBlocked('audited_code_changed')


def isolated(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if root == output or root in output.parents or output in root.parents:
        raise TrialBlocked('staging_must_be_outside_checkout')
    return output


def blockers(*, key_present, balance=None, reserve=100, quota_verified=False,
             collector_active=None, exclusion_verified=False, durable_verified=False):
    reasons = []
    if not key_present:
        reasons.append('missing_bsd_key')
    if type(reserve) is not int or reserve < 100:
        reasons.append('invalid_reserve')
    if not quota_verified:
        reasons.append('quota_not_verified')
    if type(balance) is not int or type(reserve) is not int or balance < reserve + CAP:
        reasons.append('insufficient_or_unknown_balance')
    if collector_active is not False or not exclusion_verified:
        reasons.append('account_wide_exclusion_not_proven')
    if not durable_verified:
        reasons.append('cross_run_budget_not_proven')
    # Deliberate non-configurable blocker. No input/boolean can authorize network.
    reasons.append('live_transport_not_enabled_pending_review')
    return reasons


def select_targets(bets, now):
    selected = engine.targets_from([b for b in bets if b.get('match_id') in MATCH_IDS], now)
    # Never trim required adjacent days: that would falsely certify uniqueness.
    return {mid: t for mid, t in selected.items() if t['days'] and set(t['days']) <= DAYS}


def prepare(root, output, now, *, snapshot_checkout=None, snapshot_sha=None):
    root = Path(root).resolve()
    output = isolated(root, output)
    verify_engine(root)
    source=root
    if (snapshot_checkout is None)!=(snapshot_sha is None):
        raise TrialBlocked('snapshot_checkout_and_sha_required_together')
    if snapshot_checkout is not None:
        source=Path(snapshot_checkout).resolve()
        isolated(source,output)
        if (len(snapshot_sha)!=40 or any(c not in '0123456789abcdef' for c in snapshot_sha)
                or subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()!=snapshot_sha):
            raise TrialBlocked('snapshot_revision_mismatch')
        # Source checkout contributes data ONLY, never executable code.
        if subprocess.run(['git','diff','--quiet',snapshot_sha,'--','data'],cwd=source).returncode:
            raise TrialBlocked('dirty_snapshot_data')
        if subprocess.check_output(['git','ls-files','--others','--exclude-standard','--','data'],cwd=source).strip():
            raise TrialBlocked('untracked_snapshot_data')
    before = manifest(source / 'data')
    output.mkdir(parents=True, exist_ok=True)
    # Existing state must be resumed, never recopied/reinitialized silently.
    with (output / 'trial.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        meta = output / 'inputs.json'
        if meta.exists():
            saved = json.loads(meta.read_text())
            if saved['trial_id'] != TRIAL_ID or saved['original_sha256'] != before or saved.get('source_revision') != snapshot_sha:
                raise TrialBlocked('snapshot_changed_use_original_snapshot')
            if manifest(output / 'snapshot') != before:
                raise TrialBlocked('snapshot_tampered')
            return saved
        if any(p.name != 'trial.lock' for p in output.iterdir()):
            raise TrialBlocked('nonempty_unidentified_staging')
        shutil.copytree(source / 'data', output / 'snapshot')
        with patch.dict(os.environ, {'SCANGOAL_DATA_DIR': str(output / 'snapshot')}):
            bets, _, existing = snapshot()
            aliases = engine.load_aliases()
        targets = select_targets(bets, now)
        value = {'trial_id': TRIAL_ID, 'engine_sha': ENGINE_SHA, 'source_revision':snapshot_sha,
                 'snapshot_utc': engine.stamp(now), 'original_sha256': before,
                 'targets': targets, 'existing': {k: v for k, v in existing.items() if k in MATCH_IDS},
                 'aliases': aliases, 'excluded_ids': sorted(MATCH_IDS - targets.keys())}
        engine.write_json(meta, value)
        if manifest(source / 'data') != before or manifest(output / 'snapshot') != before:
            raise TrialBlocked('input_integrity_failure')
        return value


class FixtureTransport:
    """Data-only simulated transport; never calls a supplied callable or a network API."""
    live = False

    def __init__(self, pages):
        self.pages = copy.deepcopy(pages)
        self.calls = []

    def __call__(self, source, day, offset, limit):
        self.calls.append((source, day, offset, limit))
        value = self.pages[(day, offset)]
        if isinstance(value, int):
            raise RuntimeError('HTTP ' + str(value))
        return copy.deepcopy(value)


def simulate(output, targets, transport, now, *, aliases=None, existing=None,
             key_present=True, balance=105, quota_verified=True,
             collector_active=False, exclusion_verified=True):
    """Exercise the audited engine offline. Never a live execution entry point.

    Ledger persists across UTC days; fixture bytes cannot be promoted to real
    evidence. A new Actions runner is NOT a durable ledger and is blocked by CLI.
    """
    if type(transport) is not FixtureTransport:
        raise TrialBlocked('fixture_transport_required')
    if any(mid not in MATCH_IDS or not t['days'] or not set(t['days']) <= DAYS for mid, t in targets.items()):
        raise TrialBlocked('target_outside_authorization')
    reasons = blockers(key_present=key_present, balance=balance, quota_verified=quota_verified,
                       collector_active=collector_active, exclusion_verified=exclusion_verified,
                       durable_verified=True)
    reasons.remove('live_transport_not_enabled_pending_review')
    if reasons:
        raise TrialBlocked(','.join(reasons))
    output = isolated(Path(__file__).resolve().parents[1], output)
    # Restrict test evidence to an explicitly labelled namespace.
    if output.name != 'fixture-trial':
        raise TrialBlocked('fixture_namespace_required')
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'state.json'
    binding = engine.digest({'targets': targets, 'aliases': aliases or {}, 'existing': existing or {}})
    with (output / 'state.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(path.read_text()) if path.exists() else engine.initial_state()
        if state.get('trial_id', TRIAL_ID) != TRIAL_ID or state.get('binding', binding) != binding:
            raise TrialBlocked('state_binding_changed')
        if state.get('evidence_kind', 'synthetic_fixture') != 'synthetic_fixture':
            raise TrialBlocked('evidence_namespace_conflict')
        state.update(trial_id=TRIAL_ID, engine_sha=ENGINE_SHA, binding=binding,
                     evidence_kind='synthetic_fixture')
        def save(s):
            engine.write_json(path, s)
        # Attempts are the lifetime debit, never the daily bucket alone.
        while len(state['attempts']) < CAP and not state.get('halted'):
            count = len(state['attempts'])
            def fetch(source, day, offset, limit):
                if source != 'bsd' or day not in DAYS or limit != 200:
                    raise TrialBlocked('request_outside_authorization')
                params = {'date_from': day, 'date_to': day, 'status': 'finished', 'limit': limit, 'offset': offset}
                payload = transport(source, day, offset, limit)
                raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
                # Canonical fixture bytes, explicitly NOT original HTTP wire bytes.
                response = {'evidence_kind': 'synthetic_fixture', 'params': params,
                            'observed_utc': engine.stamp(now), 'sha256': hashlib.sha256(raw).hexdigest(),
                            'payload': payload}
                engine.write_json(output / ('response-%02d.json' % count), response)
                return payload
            state = engine.run_batch(state, targets, fetch, now, aliases=aliases, existing=existing,
                                     max_requests=1, daily={'hl': 0, 'bsd': CAP},
                                     available={'hl': 0, 'bsd': balance}, reserve={'hl': 0, 'bsd': 100},
                                     max_passes=1, save=save)
            if len(state['attempts']) == count:
                break
            if state['attempts'][-1]['outcome'] not in ('complete', 'incomplete'):
                state['halted'] = True
            save(state)
        save(state)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--snapshot-checkout', type=Path)
    parser.add_argument('--snapshot-sha')
    # There is deliberately no --live flag or unsafe override.
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = isolated(root, args.output)
    now = datetime.now(UTC)
    verify_engine(root)
    protected = {name: manifest(root / name) for name in ('data', 'docs', 'scangoal', '.github')}
    inputs = prepare(root, output, now, snapshot_checkout=args.snapshot_checkout, snapshot_sha=args.snapshot_sha)
    # Preflight has no secret access. Availability is checked only at execution.
    reasons = blockers(key_present=True)
    reasons.insert(0, 'bsd_key_check_deferred_to_authorized_execution')
    report = {'trial_id': TRIAL_ID, 'engine_sha': ENGINE_SHA, 'mode': 'preflight_only',
              'as_of_utc': engine.stamp(now), 'blockers': reasons,
              'selected_ids': sorted(inputs['targets']), 'excluded_ids': inputs['excluded_ids'],
              'bsd_requests': 0, 'hl_requests': 0, 'production_integrations': 0,
              'originals_unchanged': protected == {name: manifest(root / name) for name in protected}}
    engine.write_json(output / 'preflight.json', report)
    print(json.dumps(report, sort_keys=True))
    return 2  # Failure is intentional: never advertise this as ready for live.


if __name__ == '__main__':
    raise SystemExit(main())
