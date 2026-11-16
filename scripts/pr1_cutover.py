"""PR1 cutover: eligible report + silent Telegram status migration, with checks. Offline; never sends.

Usage (integration checkout = PR branch with the frozen main merged in; collectors paused):

    python scripts/pr1_cutover.py <frozen_main_sha> > audits/pr1-cutover.json

Automates steps 4-6 of audits/pr1-safe-integration.md. See audits/pr1-cutover-runbook.md for the full procedure.

Guards (checked before anything is written):
- data/ and docs/ have no uncommitted or untracked changes;
- the frozen main commit is an ancestor of HEAD and data/, docs/report.json and docs/bets.csv are byte-identical
  to it, so an older snapshot can never be cut over on top of newer production data;
- notify_state.json exists, is valid JSON and has no migration marker yet (the baseline is set exactly once);
- the freshly built report has brain.evidence_scope and operational.scope equal to eligible_bets_only and a valid
  eligible status_list.

Work: report.build(register=False) (no registry growth, no CSV, no collectors, LLM or notifications), copy to
docs/report.json, offline migration twice (must print MIGRATED then UNCHANGED, file byte-identical), and a
simulation of the next notify cycle without any sender (must contain zero status-transition notices).

Post-checks: only data/report.json, docs/report.json and data/notify_state.json changed; registry unchanged; delivery
history preserved.

Transaction: everything after the guards, including the post-checks and the construction of the audit record, runs
under one rollback. A failed check or any exception restores every file under data/ and the two docs/ files byte for
byte (files the cutover created are removed) and confirms the restore by SHA-256. Exit codes:
  0  cutover done, every check passed;
  1  refused by a guard, or a check/exception aborted it and the restore was verified (diagnostic shows the stage);
  2  usage error;
  3  RESTORE FAILED: the checkout may hold partial changes; stop and inspect manually (git restore can recover it,
     because the cutover only runs on a clean, committed checkout).
Network access raises an error for the whole run. The script never reads credentials, never calls the Telegram
sender, never commits and never pushes.
"""
import copy
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scangoal import notify, report  # noqa: E402

DOCS = ('docs/report.json', 'docs/bets.csv')
WRITES = frozenset({'data/report.json', 'docs/report.json', 'data/notify_state.json'})
DELIVERY = ('watch_sent', 'sent_total', 'hello', 'summary_day', 'last_run_utc', 'regime', 'operational_regime')
TRANSITION_PREFIXES = ('Hipótese passou a', 'Hipótese voltou', '✅ Tática validada', '⛔ Tática deixou')


class CutoverError(RuntimeError):
    """Refused or aborted; the checkout is byte-identical to its state before the run."""


class CutoverAborted(CutoverError):
    """An error after the guards; every file was restored and the restore was verified by hash."""

    def __init__(self, stage, cause):
        self.stage, self.cause = stage, cause
        detail = str(cause) if isinstance(cause, CutoverError) else '%s: %s' % (type(cause).__name__, cause)
        super().__init__('%s (during %s; all files restored and verified)' % (detail, stage))


class RestoreFailed(RuntimeError):
    """Not a CutoverError on purpose: the checkout may hold partial changes and needs manual inspection."""
    stage = None      # stage that was running when the restore was needed
    trigger = None    # exception that required the restore (None when a check failed normally)


@contextmanager
def offline():
    def blocked(*args, **kwargs):
        raise CutoverError('network access attempted during cutover')

    saved = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    socket.socket.connect, socket.socket.connect_ex, socket.create_connection = blocked, blocked, blocked
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = saved


@contextmanager
def data_dir_of(root):
    want = (root / 'data').resolve()
    previous = os.environ.get('SCANGOAL_DATA_DIR')
    if previous is not None and Path(previous).resolve() != want:
        raise CutoverError('SCANGOAL_DATA_DIR points outside the checkout data/')
    os.environ['SCANGOAL_DATA_DIR'] = str(want)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop('SCANGOAL_DATA_DIR', None)
        else:
            os.environ['SCANGOAL_DATA_DIR'] = previous


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)


def contents(root):
    """Bytes of every file under data/ plus the two published docs files, keyed by repo-relative path."""
    paths = [p for p in sorted((root / 'data').rglob('*')) if p.is_file()] + [root / d for d in DOCS if (root / d).is_file()]
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in paths}


def digests(files):
    return {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())}


def restore(root, original):
    """Put every tracked file back exactly as it was; remove files the cutover created; verify by hash."""
    expected = digests(original)
    try:
        for rel in set(contents(root)) - set(original):
            (root / rel).unlink()
        for rel, data in original.items():
            path = root / rel
            if not path.is_file() or path.read_bytes() != data:
                path.write_bytes(data)
        restored = digests(contents(root))
    except BaseException as exc:
        raise RestoreFailed('restore raised %s: inspect data/ and docs/ manually before any further action'
                            % type(exc).__name__) from exc
    if restored != expected:
        raise RestoreFailed('restored files differ from the originals: inspect data/ and docs/ manually')
    return restored


def kind(text):
    lines = text.split('\n')
    while len(lines) > 1 and lines[0].startswith('Estado científico:'):   # PR 5: o estado científico vem no topo
        lines.pop(0)
    text = '\n'.join(lines)
    if text.startswith(TRANSITION_PREFIXES):
        return 'status_transition'
    if text.startswith('Mudança de comportamento'):
        return 'regime_change'
    if text.startswith(('A seguir:', '🟢 ALERTA')):
        return 'watch'
    if text.startswith('Resumo de'):
        return 'daily_summary'
    if text.startswith('Avisos do goal-roi ligados'):
        return 'hello'
    return 'other'


def guards(root, frozen):
    res = git(root, 'rev-parse', '--verify', '--quiet', frozen + '^{commit}')
    if res.returncode:
        raise CutoverError('unknown frozen main commit')
    frozen = res.stdout.strip()
    if git(root, 'status', '--porcelain', '--untracked-files=all', '--', 'data', 'docs').stdout.strip():
        raise CutoverError('data/ or docs/ has uncommitted changes')
    if git(root, 'merge-base', '--is-ancestor', frozen, 'HEAD').returncode:
        raise CutoverError('frozen main commit is not an ancestor of HEAD')
    if git(root, 'diff', '--quiet', frozen, 'HEAD', '--', 'data', *DOCS).returncode:
        raise CutoverError('data differs from frozen main: refusing to cut over on a different snapshot')
    return frozen


def execute(root, frozen, head, original, before_state, now, build, progress):
    """Everything that writes or inspects written files. Runs entirely under cutover()'s rollback."""
    progress['stage'] = 'build_report'
    rep = build(None, register=False)
    progress['stage'] = 'validate_report'
    statuses = notify.eligible_statuses(rep)
    if statuses is None:
        raise CutoverError('built report has no valid eligible status_list')
    if (rep.get('operational') or {}).get('scope') != notify.EVIDENCE_SCOPE:
        raise CutoverError('built report has no eligible operational block (operational.scope)')

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
    marker = state['migrations'][notify.STATUS_MIGRATION]

    progress['stage'] = 'simulate_next_cycle'
    simulated = notify.messages(rep, copy.deepcopy(state), now)

    progress['stage'] = 'post_checks'
    after = contents(root)
    changed = sorted(k for k in set(after) | set(original) if after.get(k) != original.get(k))
    kinds = {}
    for text in simulated:
        kinds[kind(text)] = kinds.get(kind(text), 0) + 1
    checks = {
        'first_call': first_out.getvalue().strip(),
        'second_call': second_out.getvalue().strip(),
        'second_call_byte_identical': bytes1 == bytes2,
        'operational_scope_eligible': (rep.get('operational') or {}).get('scope') == notify.EVIDENCE_SCOPE,
        'statuses_equal_report_status_list': state.get('statuses') == statuses,
        'previous_statuses_equal_original': marker.get('previous_statuses') == before_state.get('statuses'),
        'baseline_statuses_equal_report_status_list': marker.get('baseline_statuses') == statuses,
        'delivery_fields_preserved': all(state.get(k) == before_state.get(k) for k in DELIVERY),
        'other_fields_preserved': all(state.get(k) == v for k, v in before_state.items() if k not in ('statuses', 'migrations')),
        'registry_unchanged': after.get('data/hypotheses.json') == original.get('data/hypotheses.json'),
        'docs_report_equals_data_report': after.get('docs/report.json') == after.get('data/report.json'),
        'only_allowed_files_changed': set(changed) <= WRITES,
        'next_cycle_status_transitions': kinds.get('status_transition', 0),
    }
    ok = (first is True and second is False and checks['first_call'] == 'NOTIFY_STATUS_MIGRATED'
          and checks['second_call'] == 'NOTIFY_STATUS_UNCHANGED'
          and all(v is True for k, v in checks.items() if k not in ('first_call', 'second_call', 'next_cycle_status_transitions'))
          and checks['next_cycle_status_transitions'] == 0)

    progress['stage'] = 'build_result'
    old = before_state.get('statuses') or {}
    brain = rep.get('brain') or {}
    wf = brain.get('walk_forward') or {}
    return {
        'ok': ok, 'restored': False, 'frozen_main_sha': frozen, 'head_sha': head, 'at_utc': now.isoformat(),
        'python': sys.version.split()[0], 'report_generated_at_utc': rep.get('generated_at_utc'),
        'brain': {'evidence_scope': brain.get('evidence_scope'), 'operational_scope': (rep.get('operational') or {}).get('scope'),
                  'hypotheses': brain.get('hypotheses'), 'counts': brain.get('counts'),
                  'exploratory_counts': (brain.get('exploratory') or {}).get('counts'), 'eligibility': brain.get('eligibility'),
                  'walk_forward': {k: wf.get(k) for k in ('followed', 'skipped')}},
        'reference': {'previous': len(old), 'baseline': len(statuses),
                      'silenced_transitions': sum(1 for h, s in statuses.items() if old.get(h) != s) + sum(1 for h in old if h not in statuses)},
        'checks': checks, 'next_cycle_simulated_messages': kinds,
        'changed_files': changed, 'sha256_before': digests(original), 'sha256_after': digests(after),
    }


def cutover(root=ROOT, frozen='HEAD', now=None, build=None):
    """Run the cutover in `root` as one transaction.

    Returns the audit record (ok True, or ok False with the files restored and verified). Raises CutoverError when a
    guard refuses (nothing written), CutoverAborted when an exception aborts it after the guards (files restored and
    verified) and RestoreFailed when the restore itself cannot be confirmed.
    """
    root = Path(root).resolve()
    build = build or report.build
    frozen = guards(root, frozen)
    head = git(root, 'rev-parse', 'HEAD').stdout.strip()
    original = contents(root)
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    try:
        before_state = json.loads(original['data/notify_state.json'])
    except (KeyError, ValueError):
        raise CutoverError('data/notify_state.json missing or invalid: refusing to create an empty history') from None
    if not isinstance(before_state, dict):
        raise CutoverError('data/notify_state.json is not an object')
    if notify.STATUS_MIGRATION in (before_state.get('migrations') or {}):
        raise CutoverError('migration marker already present: the baseline can only be set once')
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
            raise                          # KeyboardInterrupt/SystemExit: restored, then propagate unchanged
        raise CutoverAborted(progress['stage'], exc) from exc


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(json.dumps({'ok': False, 'error': 'usage: pr1_cutover.py <frozen_main_sha>'}))
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
