"""PR2 cutover: report under the new status rules + silent Telegram re-baseline under its own key. Offline; never sends.

Usage (integration checkout = PR2 branch with the frozen main merged in; collectors paused):

    python scripts/pr2_cutover.py <frozen_main_sha> > audits/pr2-cutover.json

See audits/pr2-cutover-runbook.md. Reuses, unmodified, the audited primitives of scripts/pr1_cutover.py (snapshot guards,
byte-for-byte restore verified by SHA-256, CutoverAborted/RestoreFailed, network block, data dir pinning).

Guards (before anything is written):
- PR1 guards: clean data/ and docs/; frozen main is an ancestor of HEAD; data/ and docs/{report,bets} byte-identical to it;
- notify_state.json valid, WITH the PR1 marker 'eligible_statuses_v1' (PR1 integrated) and WITHOUT the PR2 key
  'status_rules:independent_games_hysteresis_v1' (the new reference is created exactly once);
- the report committed at the frozen main is PR1-format (eligible scope, no status_rules): no PR2-code writer ran yet;
- the built report (register=False) has brain.evidence_scope and operational.scope eligible, brain.status_rules equal to
  this code's STATUS_RULES and a valid status_list;
- a second report built with register=True ON A TEMPORARY COPY of data/ (the production report step registers) passes the
  same checks and has an IDENTICAL status_list; otherwise the first production cycle would announce statuses that are not
  new evidence. The checkout must be byte-identical after EACH build (neither build may write into it).

Work: write data/report.json + docs/report.json, offline migration twice (MIGRATED, then UNCHANGED, file byte-identical),
and simulate the next notify cycle with BOTH reports, without any sender: zero status transitions required.

Post-checks: only data/report.json, docs/report.json and data/notify_state.json changed; registry unchanged; PR1 marker
byte-identical; migration keys = previous keys + the PR2 key; delivery history (watch_sent, sent_total, hello,
summary_day, last_run_utc, regime, operational_regime) and every other field preserved; no new top-level field.

Exit codes as in pr1_cutover.py: 0 done; 1 refused or aborted with restore verified; 2 usage; 3 RESTORE FAILED (stop;
`git restore --source=HEAD -- data docs` recovers the clean, committed checkout).
"""
import copy
import importlib.util
import io
import json
import os
import shutil
import subprocess
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
from scangoal import brain, notify, report  # noqa: E402

# Audited primitives, re-exported so callers catch the same classes.
CutoverError, CutoverAborted, RestoreFailed = pr1.CutoverError, pr1.CutoverAborted, pr1.RestoreFailed
contents, digests, restore, kind, offline, data_dir_of, guards = (pr1.contents, pr1.digests, pr1.restore, pr1.kind,
                                                                  pr1.offline, pr1.data_dir_of, pr1.guards)

PR1_MARKER = notify.STATUS_MIGRATION
# Pinned (PR 3): this script performed the PR 2 cutover only. It must never accept reports produced under later rules.
PR2_RULES = 'independent_games_hysteresis_v1'
PR2_KEY = 'status_rules:%s' % PR2_RULES
WRITES = pr1.WRITES
DELIVERY = pr1.DELIVERY


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


def check_report(rep, label):
    statuses = notify.eligible_statuses(rep)
    if statuses is None:
        raise CutoverError('%s has no valid eligible status_list' % label)
    if (rep.get('operational') or {}).get('scope') != notify.EVIDENCE_SCOPE:
        raise CutoverError('%s has no eligible operational block (operational.scope)' % label)
    if (rep.get('brain') or {}).get('status_rules') != PR2_RULES:
        raise CutoverError('%s is not produced under status rules %s' % (label, PR2_RULES))
    if notify.migration_key(rep) != PR2_KEY:
        raise CutoverError('%s maps to migration key %s, expected %s' % (label, notify.migration_key(rep), PR2_KEY))
    return statuses


def frozen_report_is_pr1_format(root, frozen):
    """The report committed at the frozen main must come from PR1 code: eligible scope and no status_rules."""
    res = pr1.git(root, 'show', '%s:data/report.json' % frozen)
    if res.returncode:
        raise CutoverError('frozen main has no data/report.json')
    try:
        rep = json.loads(res.stdout)
    except ValueError:
        raise CutoverError('frozen main data/report.json is not valid JSON') from None
    b = rep.get('brain') or {}
    if b.get('evidence_scope') != notify.EVIDENCE_SCOPE:
        raise CutoverError('frozen main report is not eligible-scope: integrate PR1 first')
    if b.get('status_rules'):
        raise CutoverError('frozen main report already carries status_rules=%s: a PR2-code writer has run' % b['status_rules'])


def build_register_true_on_copy(root, build, original):
    """report.build(register=True) on a temporary copy of data/: the registry growth never touches the checkout."""
    tmp = Path(tempfile.mkdtemp(prefix='pr2-cutover-register-'))
    try:
        shutil.copytree(root / 'data', tmp / 'data')
        with data_env(tmp / 'data'):
            rep = build(None, register=True)
        grown = len(json.loads((tmp / 'data' / 'hypotheses.json').read_text(encoding='utf-8')))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if contents(root) != original:
        raise CutoverError('register=True build wrote into the checkout')
    return rep, grown


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
        only_false = sorted(set(statuses) - set(statuses_true))
        only_true = sorted(set(statuses_true) - set(statuses))
        changed = sorted(h for h in set(statuses) & set(statuses_true) if statuses[h] != statuses_true[h])
        raise CutoverError('register=True status_list differs from register=False (only False %d, only True %d, changed %d): '
                           'the first production report would announce statuses that are not new evidence'
                           % (len(only_false), len(only_true), len(changed)))

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
    marker = state['migrations'][PR2_KEY]

    progress['stage'] = 'simulate_next_cycle'
    simulated = notify.messages(rep, copy.deepcopy(state), now)
    simulated_true = notify.messages(rep_true, copy.deepcopy(state), now)

    progress['stage'] = 'post_checks'
    after = contents(root)
    changed_files = sorted(k for k in set(after) | set(original) if after.get(k) != original.get(k))

    def kinds(messages):
        out = {}
        for text in messages:
            out[kind(text)] = out.get(kind(text), 0) + 1
        return out

    k_false, k_true = kinds(simulated), kinds(simulated_true)
    before_keys = sorted((before_state.get('migrations') or {}))
    checks = {
        'first_call': first_out.getvalue().strip(),
        'second_call': second_out.getvalue().strip(),
        'second_call_byte_identical': bytes1 == bytes2,
        'pr1_marker_preserved': state['migrations'].get(PR1_MARKER) == before_state['migrations'][PR1_MARKER],
        'migration_keys_previous_plus_pr2': sorted(state['migrations']) == sorted(before_keys + [PR2_KEY]),
        'register_true_status_list_identical': statuses_true == statuses,
        'statuses_equal_report_status_list': state.get('statuses') == statuses,
        'previous_statuses_equal_original': marker.get('previous_statuses') == before_state.get('statuses'),
        'baseline_statuses_equal_report_status_list': marker.get('baseline_statuses') == statuses,
        'delivery_fields_preserved': all(state.get(k) == before_state.get(k) for k in DELIVERY),
        'other_fields_preserved': all(state.get(k) == v for k, v in before_state.items() if k not in ('statuses', 'migrations')),
        'no_unexpected_fields': set(state) <= set(before_state) | {'statuses', 'migrations'},
        'registry_unchanged': after.get('data/hypotheses.json') == original.get('data/hypotheses.json'),
        'docs_report_equals_data_report': after.get('docs/report.json') == after.get('data/report.json'),
        'only_allowed_files_changed': set(changed_files) <= WRITES,
        'next_cycle_status_transitions': k_false.get('status_transition', 0),
        'next_cycle_status_transitions_register_true': k_true.get('status_transition', 0),
    }
    counted = ('first_call', 'second_call', 'next_cycle_status_transitions', 'next_cycle_status_transitions_register_true')
    ok = (first is True and second is False and checks['first_call'] == 'NOTIFY_STATUS_MIGRATED'
          and checks['second_call'] == 'NOTIFY_STATUS_UNCHANGED'
          and all(v is True for k, v in checks.items() if k not in counted)
          and checks['next_cycle_status_transitions'] == 0 and checks['next_cycle_status_transitions_register_true'] == 0)

    progress['stage'] = 'build_result'
    old = before_state.get('statuses') or {}
    b = rep.get('brain') or {}
    wf = b.get('walk_forward') or {}
    return {
        'ok': ok, 'restored': False, 'frozen_main_sha': frozen, 'head_sha': head, 'at_utc': now.isoformat(),
        'python': sys.version.split()[0], 'report_generated_at_utc': rep.get('generated_at_utc'),
        'migration_key': PR2_KEY, 'status_rules': PR2_RULES,
        'brain': {'evidence_scope': b.get('evidence_scope'), 'operational_scope': (rep.get('operational') or {}).get('scope'),
                  'status_rules': b.get('status_rules'), 'hypotheses': b.get('hypotheses'), 'tested': b.get('tested'), 'counts': b.get('counts'),
                  'exploratory_counts': (b.get('exploratory') or {}).get('counts'), 'eligibility': b.get('eligibility'),
                  'walk_forward': {k: wf.get(k) for k in ('followed', 'skipped')}},
        'register_true': {'registry_size_after_on_copy': registry_after_register, 'status_list_identical': statuses_true == statuses},
        'reference': {'previous': len(old), 'baseline': len(statuses),
                      'silenced_transitions': sum(1 for h, s in statuses.items() if old.get(h) != s) + sum(1 for h in old if h not in statuses)},
        'checks': checks, 'next_cycle_simulated_messages': {'register_false': k_false, 'register_true': k_true},
        'changed_files': changed_files, 'sha256_before': digests(original), 'sha256_after': digests(after),
    }


def cutover(root=ROOT, frozen='HEAD', now=None, build=None):
    """Run the PR2 cutover in `root` as one transaction (same contract as pr1_cutover.cutover)."""
    root = Path(root).resolve()
    build = build or report.build
    frozen = guards(root, frozen)
    frozen_report_is_pr1_format(root, frozen)
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
    if PR1_MARKER not in migrations:
        raise CutoverError('PR1 migration marker %s missing: PR1 must be integrated first' % PR1_MARKER)
    if PR2_KEY in migrations:
        raise CutoverError('migration marker %s already present: the PR2 reference can only be set once' % PR2_KEY)
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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(json.dumps({'ok': False, 'error': 'usage: pr2_cutover.py <frozen_main_sha>'}))
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
