"""PR3 cutover: report under the PR3 status rules + silent Telegram re-baseline under its own key. Offline; never sends.

INTEGRATION ONLY. The cutover never creates data/certification_ledger.json: scientific validation stays OFF (state
not_activated) after the merge. Activating it is a separate, later, explicitly authorised step
(audits/pr3-certification-activation-plan.md).

Usage (integration checkout = PR3 branch with the frozen main merged in; collectors paused and verified idle):

    python scripts/pr3_cutover.py <frozen_main_sha> > audits/pr3-cutover.json

See audits/pr3-cutover-runbook.md. Reuses, unmodified, the audited primitives of scripts/pr1_cutover.py (snapshot guards,
byte-for-byte restore verified by SHA-256, CutoverAborted/RestoreFailed, network block, data dir pinning).

Guards (before anything is written):
- PR1 guards: clean data/ and docs/; frozen main is an ancestor of HEAD; data/ and docs/{report,bets} byte-identical to it;
- notify_state.json valid, WITH the PR1 marker 'eligible_statuses_v1' and the PR2 key
  'status_rules:independent_games_hysteresis_v1', and WITHOUT the PR3 key 'status_rules:chronological_eprocess_v3';
- the report committed at the frozen main is PR2-format (status_rules = independent_games_hysteresis_v1);
- no certification ledger exists (neither in the checkout nor at the frozen main);
- this code's brain.STATUS_RULES is exactly the pinned PR3 rules;
- the built report (register=False) has eligible scopes, the PR3 rules, a valid status_list and validation.state
  not_activated with no certified tactic;
- a second report built with register=True ON A TEMPORARY COPY of data/ passes the same checks, has an IDENTICAL
  status_list and creates no ledger. The checkout must be byte-identical after EACH build.

Idempotent: if the PR3 key is already present, the committed report is PR3-format and its status_list equals the stored
reference and the PR3 baseline, the run returns {"ok": true, "already_applied": true} and writes nothing. Any other state
with the PR3 key is refused.

Work: write data/report.json + docs/report.json, offline migration twice (MIGRATED, then UNCHANGED, file byte-identical),
and simulate the next TWO notify cycles in sequence (cutover report, then the register=True report), without any sender:
zero status transitions and zero validated messages/alerts required in both.

Post-checks: only data/report.json, docs/report.json and data/notify_state.json changed; registry unchanged; no ledger;
PR1 and PR2 markers byte-identical; migration keys = previous keys + the PR3 key; delivery history (watch_sent, sent_total,
hello, summary_day, last_run_utc, regime, operational_regime) and every other field preserved; no new top-level field.

Exit codes as in pr1_cutover.py: 0 done (or already applied); 1 refused or aborted with restore verified; 2 usage;
3 RESTORE FAILED (stop; `git restore --source=HEAD -- data docs` recovers the clean, committed checkout).
"""
import copy
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location('pr1_cutover', Path(__file__).with_name('pr1_cutover.py'))
pr1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr1)
from scangoal import brain, certification, notify, report  # noqa: E402

CutoverError, CutoverAborted, RestoreFailed = pr1.CutoverError, pr1.CutoverAborted, pr1.RestoreFailed
contents, digests, restore, kind, offline, data_dir_of, guards = (pr1.contents, pr1.digests, pr1.restore, pr1.kind,
                                                                  pr1.offline, pr1.data_dir_of, pr1.guards)

PR1_MARKER = notify.STATUS_MIGRATION
PR2_KEY = 'status_rules:independent_games_hysteresis_v1'
PR2_RULES = 'independent_games_hysteresis_v1'
PR3_RULES = 'chronological_eprocess_v3'          # pinned: this script performs the PR 3 cutover only
PR3_KEY = 'status_rules:%s' % PR3_RULES
LEDGER = 'data/%s' % certification.FILE
WRITES = pr1.WRITES
DELIVERY = pr1.DELIVERY
VALIDATED_PREFIXES = ('🟢 ALERTA', '✅ Tática validada')


@contextmanager
def data_env(path):
    previous = os.environ.get('SCANGOAL_DATA_DIR')
    os.environ['SCANGOAL_DATA_DIR'] = str(path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('SCANGOAL_DATA_DIR', None)
        else:
            os.environ['SCANGOAL_DATA_DIR'] = previous


def validated(messages):
    return sum(1 for m in messages if m.startswith(VALIDATED_PREFIXES))


def check_report(rep, label):
    statuses = notify.eligible_statuses(rep)
    if statuses is None:
        raise CutoverError('%s has no valid eligible status_list' % label)
    if (rep.get('operational') or {}).get('scope') != notify.EVIDENCE_SCOPE:
        raise CutoverError('%s has no eligible operational block (operational.scope)' % label)
    b = rep.get('brain') or {}
    if b.get('status_rules') != PR3_RULES:
        raise CutoverError('%s is not produced under status rules %s' % (label, PR3_RULES))
    if notify.migration_key(rep) != PR3_KEY:
        raise CutoverError('%s maps to migration key %s, expected %s' % (label, notify.migration_key(rep), PR3_KEY))
    v = b.get('validation') or {}
    if v.get('state') != 'not_activated' or v.get('certified') or v.get('ledger_valid'):
        raise CutoverError('%s has certification state %s: the PR3 integration must not activate certification' % (label, v.get('state')))
    if any(s in notify.VALID for s in (b.get('status_list') or {}).values()):
        raise CutoverError('%s announces a validated status without certification' % label)
    return statuses


def frozen_report_is_pr2_format(root, frozen):
    """The report committed at the frozen main must come from PR2 code (no PR3-code writer ran yet)."""
    res = pr1.git(root, 'show', '%s:data/report.json' % frozen)
    if res.returncode:
        raise CutoverError('frozen main has no data/report.json')
    try:
        rep = json.loads(res.stdout)
    except ValueError:
        raise CutoverError('frozen main data/report.json is not valid JSON') from None
    rules = (rep.get('brain') or {}).get('status_rules')
    if rules == PR3_RULES:
        raise CutoverError('frozen main report already carries status_rules=%s: a PR3-code writer has run' % rules)
    if rules != PR2_RULES:
        raise CutoverError('frozen main report has status_rules=%s: integrate PR2 first' % rules)
    if not pr1.git(root, 'cat-file', '-e', '%s:%s' % (frozen, LEDGER)).returncode:
        raise CutoverError('a certification ledger exists at the frozen main: activation is not part of this cutover')


def build_register_true_on_copy(root, build, original):
    """report.build(register=True) on a temporary copy of data/: the registry growth never touches the checkout."""
    tmp = Path(tempfile.mkdtemp(prefix='pr3-cutover-register-'))
    try:
        shutil.copytree(root / 'data', tmp / 'data')
        with data_env(tmp / 'data'):
            rep = build(None, register=True)
        grown = len(json.loads((tmp / 'data' / 'hypotheses.json').read_text(encoding='utf-8')))
        ledger_created = (tmp / LEDGER).exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if contents(root) != original:
        raise CutoverError('register=True build wrote into the checkout')
    if ledger_created:
        raise CutoverError('register=True build created a certification ledger')
    return rep, grown


def already_applied(root, state):
    """Idempotency: the PR3 reference is set and coherent with the committed PR3 report -> nothing to do."""
    try:
        rep = json.loads((root / 'data' / 'report.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    b = rep.get('brain') or {}
    marker = (state.get('migrations') or {}).get(PR3_KEY) or {}
    return (b.get('status_rules') == PR3_RULES and isinstance(b.get('status_list'), dict)
            and state.get('statuses') == b['status_list'] == marker.get('baseline_statuses')
            and not (root / LEDGER).exists())


def execute(root, frozen, head, original, before_state, now, build, progress):
    """Everything that writes or inspects written files. Runs entirely under cutover()'s rollback."""
    progress['stage'] = 'build_report'
    rep = build(None, register=False)
    if contents(root) != original:
        raise CutoverError('register=False build wrote into the checkout')
    progress['stage'] = 'validate_report'
    statuses = check_report(rep, 'register=False report')

    progress['stage'] = 'build_register_true_copy'
    rep_true, registry_after_register = build_register_true_on_copy(root, build, original)
    progress['stage'] = 'validate_register_true'
    statuses_true = check_report(rep_true, 'register=True report')
    if statuses_true != statuses:
        changed = sorted(h for h in set(statuses) | set(statuses_true) if statuses.get(h) != statuses_true.get(h))
        raise CutoverError('register=True status_list differs from register=False (%d hypotheses): the first production report '
                           'would announce statuses that are not new evidence' % len(changed))

    progress['stage'] = 'write_report'
    out = root / 'data' / 'report.json'
    out.write_text(json.dumps(rep, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    shutil.copyfile(out, root / 'docs' / 'report.json')

    progress['stage'] = 'migrate'
    state_path = root / 'data' / 'notify_state.json'
    with redirect_stdout(io.StringIO()) as first_out:
        first = notify.migrate_file(now)
    bytes1 = state_path.read_bytes()
    progress['stage'] = 'migrate_again'
    with redirect_stdout(io.StringIO()) as second_out:
        second = notify.migrate_file(now)
    bytes2 = state_path.read_bytes()

    progress['stage'] = 'read_migrated_state'
    state = json.loads(bytes1)
    marker = state['migrations'][PR3_KEY]

    progress['stage'] = 'simulate_next_cycles'
    sim_state = copy.deepcopy(state)
    cycle1 = notify.messages(rep, sim_state, now)
    cycle2 = notify.messages(rep_true, sim_state, now)

    progress['stage'] = 'post_checks'
    after = contents(root)
    changed_files = sorted(k for k in set(after) | set(original) if after.get(k) != original.get(k))

    def kinds(messages):
        out = {}
        for text in messages:
            out[kind(text)] = out.get(kind(text), 0) + 1
        return out

    k1, k2 = kinds(cycle1), kinds(cycle2)
    before_keys = sorted((before_state.get('migrations') or {}))
    checks = {
        'first_call': first_out.getvalue().strip(),
        'second_call': second_out.getvalue().strip(),
        'second_call_byte_identical': bytes1 == bytes2,
        'pr1_marker_preserved': state['migrations'].get(PR1_MARKER) == before_state['migrations'][PR1_MARKER],
        'pr2_marker_preserved': state['migrations'].get(PR2_KEY) == before_state['migrations'][PR2_KEY],
        'migration_keys_previous_plus_pr3': sorted(state['migrations']) == sorted(before_keys + [PR3_KEY]),
        'register_true_status_list_identical': statuses_true == statuses,
        'statuses_equal_report_status_list': state.get('statuses') == statuses,
        'previous_statuses_equal_original': marker.get('previous_statuses') == before_state.get('statuses'),
        'baseline_statuses_equal_report_status_list': marker.get('baseline_statuses') == statuses,
        'delivery_fields_preserved': all(state.get(k) == before_state.get(k) for k in DELIVERY),
        'other_fields_preserved': all(state.get(k) == v for k, v in before_state.items() if k not in ('statuses', 'migrations')),
        'no_unexpected_fields': set(state) <= set(before_state) | {'statuses', 'migrations'},
        'registry_unchanged': after.get('data/hypotheses.json') == original.get('data/hypotheses.json'),
        'no_certification_ledger': LEDGER not in after and not (root / LEDGER).exists(),
        'certification_not_activated': (rep['brain'].get('validation') or {}).get('state') == 'not_activated',
        'docs_report_equals_data_report': after.get('docs/report.json') == after.get('data/report.json'),
        'only_allowed_files_changed': set(changed_files) <= WRITES,
        'cycle1_status_transitions': k1.get('status_transition', 0),
        'cycle2_status_transitions': k2.get('status_transition', 0),
        'cycle1_validated_messages': validated(cycle1),
        'cycle2_validated_messages': validated(cycle2),
    }
    counted = ('first_call', 'second_call', 'cycle1_status_transitions', 'cycle2_status_transitions',
               'cycle1_validated_messages', 'cycle2_validated_messages')
    ok = (first is True and second is False and checks['first_call'] == 'NOTIFY_STATUS_MIGRATED'
          and checks['second_call'] == 'NOTIFY_STATUS_UNCHANGED'
          and all(v is True for k, v in checks.items() if k not in counted)
          and all(checks[k] == 0 for k in counted[2:]))

    progress['stage'] = 'build_result'
    old = before_state.get('statuses') or {}
    b = rep.get('brain') or {}
    wf = b.get('walk_forward') or {}
    return {
        'ok': ok, 'restored': False, 'already_applied': False, 'frozen_main_sha': frozen, 'head_sha': head, 'at_utc': now.isoformat(),
        'python': sys.version.split()[0], 'report_generated_at_utc': rep.get('generated_at_utc'),
        'migration_key': PR3_KEY, 'status_rules': PR3_RULES,
        'brain': {'evidence_scope': b.get('evidence_scope'), 'operational_scope': (rep.get('operational') or {}).get('scope'),
                  'status_rules': b.get('status_rules'), 'hypotheses': b.get('hypotheses'), 'tested': b.get('tested'), 'counts': b.get('counts'),
                  'exploratory_counts': (b.get('exploratory') or {}).get('counts'), 'eligibility': b.get('eligibility'),
                  'validation_state': (b.get('validation') or {}).get('state'), 'walk_forward': {k: wf.get(k) for k in ('followed', 'skipped', 'not_reconstructible')}},
        'register_true': {'registry_size_after_on_copy': registry_after_register, 'status_list_identical': statuses_true == statuses},
        'reference': {'previous': len(old), 'baseline': len(statuses),
                      'silenced_transitions': sum(1 for h, s in statuses.items() if old.get(h) != s) + sum(1 for h in old if h not in statuses)},
        'checks': checks, 'simulated_cycles': {'cycle1': k1, 'cycle2': k2},
        'changed_files': changed_files, 'sha256_before': digests(original), 'sha256_after': digests(after),
    }


def cutover(root=ROOT, frozen='HEAD', now=None, build=None):
    """Run the PR3 cutover in `root` as one transaction (same contract as pr1_cutover.cutover)."""
    root = Path(root).resolve()
    build = build or report.build
    if brain.STATUS_RULES != PR3_RULES:
        raise CutoverError('this code runs status rules %s, the script is pinned to %s' % (brain.STATUS_RULES, PR3_RULES))
    frozen = guards(root, frozen)
    head = pr1.git(root, 'rev-parse', 'HEAD').stdout.strip()
    original = contents(root)
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    try:
        before_state = json.loads(original['data/notify_state.json'])
    except (KeyError, ValueError):
        raise CutoverError('data/notify_state.json missing or invalid: refusing to create an empty history') from None
    if not isinstance(before_state, dict):
        raise CutoverError('data/notify_state.json is not an object')
    migrations = before_state.get('migrations') or {}
    if PR3_KEY in migrations:
        if already_applied(root, before_state):
            return {'ok': True, 'already_applied': True, 'restored': False, 'frozen_main_sha': frozen, 'head_sha': head,
                    'migration_key': PR3_KEY, 'changed_files': [], 'sha256_before': digests(original)}
        raise CutoverError('migration marker %s already present but the committed state is not a coherent PR3 cutover' % PR3_KEY)
    if PR1_MARKER not in migrations:
        raise CutoverError('PR1 migration marker %s missing: PR1 must be integrated first' % PR1_MARKER)
    if PR2_KEY not in migrations:
        raise CutoverError('PR2 migration marker %s missing: PR2 must be integrated first' % PR2_KEY)
    frozen_report_is_pr2_format(root, frozen)
    if (root / LEDGER).exists():
        raise CutoverError('a certification ledger exists in the checkout: activation is not part of this cutover')
    env_dir = os.environ.get('SCANGOAL_DATA_DIR')
    if env_dir is not None and Path(env_dir).resolve() != (root / 'data').resolve():
        raise CutoverError('SCANGOAL_DATA_DIR points outside the checkout data/')

    progress = {'stage': 'start'}
    try:
        with data_dir_of(root), offline():
            result = execute(root, frozen, head, original, before_state, now, build, progress)
            if not result['ok']:
                progress['stage'] = 'restore_after_failed_checks'
                result['sha256_after'] = restore(root, original)
                result['restored'] = True
        return result
    except RestoreFailed as failed:
        failed.stage = progress['stage']
        raise
    except BaseException as exc:
        try:
            restore(root, original)
        except RestoreFailed as failed:
            failed.stage, failed.trigger = progress['stage'], exc
            raise
        if not isinstance(exc, Exception):
            raise
        raise CutoverAborted(progress['stage'], exc) from exc


def push_preflight(root, remote_main, frozen):
    """Before pushing the cutover: the remote main must still be the frozen commit (no collector commit in between) and the
    pushed commit must be a fast-forward of it (linear history, no force-push). -> list of problems (empty = GO)."""
    root = Path(root)
    problems = []
    remote = pr1.git(root, 'rev-parse', '--verify', '--quiet', remote_main + '^{commit}').stdout.strip()
    if not remote:
        return ['remote main %s unknown: fetch first' % remote_main]
    if remote != frozen:
        problems.append('remote main moved from the frozen commit %s to %s: NO-GO (a writer ran)' % (frozen[:12], remote[:12]))
    if pr1.git(root, 'merge-base', '--is-ancestor', remote, 'HEAD').returncode:
        problems.append('HEAD is not a fast-forward of the remote main: a push would need force (forbidden)')
    if pr1.git(root, 'status', '--porcelain', '--untracked-files=all').stdout.strip():
        problems.append('uncommitted changes in the integration checkout')
    return problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(json.dumps({'ok': False, 'error': 'usage: pr3_cutover.py <frozen_main_sha>'}))
        return 2
    try:
        result = cutover(ROOT, argv[0])
    except RestoreFailed as exc:
        print(json.dumps({'ok': False, 'restore_failed': True, 'stage': exc.stage, 'error': str(exc),
                          'trigger': type(exc.trigger).__name__ if exc.trigger else None,
                          'restore_error': type(exc.__cause__).__name__ if exc.__cause__ else None}, ensure_ascii=False))
        return 3
    except CutoverAborted as exc:
        print(json.dumps({'ok': False, 'restored': True, 'restore_verified': True, 'stage': exc.stage,
                          'cause': type(exc.cause).__name__, 'error': str(exc)}, ensure_ascii=False))
        return 1
    except CutoverError as exc:
        print(json.dumps({'ok': False, 'restored': False, 'error': str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:   # only reachable before the transaction starts, i.e. before anything is written
        print(json.dumps({'ok': False, 'restored': False, 'error': 'unexpected %s before any write' % type(exc).__name__}))
        return 1
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
