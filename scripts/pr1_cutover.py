"""PR1 cutover: eligible report + silent Telegram status migration, with checks. Offline; never sends.

Usage (integration checkout = PR branch with the frozen main merged in; collectors paused):

    python scripts/pr1_cutover.py <frozen_main_sha> > audits/pr1-cutover.json

Automates steps 4-6 of audits/pr1-safe-integration.md. See audits/pr1-cutover-runbook.md for the full procedure.

Guards (checked before anything is written):
- data/ and docs/ have no uncommitted or untracked changes;
- the frozen main commit is an ancestor of HEAD and data/, docs/report.json and docs/bets.csv are byte-identical
  to it, so an older snapshot can never be cut over on top of newer production data;
- notify_state.json exists, is valid JSON and has no migration marker yet (the baseline is set exactly once);
- the freshly built report carries a valid eligible status_list.

Work: report.build(register=False) (no registry growth, no CSV, no collectors, LLM or notifications), copy to
docs/report.json, offline migration twice (must print MIGRATED then UNCHANGED, file byte-identical), and a
simulation of the next notify cycle without any sender (must contain zero status-transition notices).

Post-checks: only data/report.json, docs/report.json and data/notify_state.json changed; registry unchanged; delivery
history preserved. If any check fails, or any exception happens after the guards, every file under data/ and the two
docs/ files is restored byte for byte and the exit code is 1. Network access raises an error for the whole run.
The script never reads credentials, never calls the Telegram sender, never commits and never pushes.
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
    pass


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
    """Put every tracked file back exactly as it was before the cutover; remove files the cutover created."""
    for rel in set(contents(root)) - set(original):
        (root / rel).unlink()
    for rel, data in original.items():
        path = root / rel
        if not path.is_file() or path.read_bytes() != data:
            path.write_bytes(data)
    if digests(contents(root)) != digests(original):
        raise CutoverError('restore failed: inspect data/ manually before any further action')


def kind(text):
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


def cutover(root=ROOT, frozen='HEAD', now=None, build=None):
    """Run the cutover in `root`. Returns the audit record; raises CutoverError when a guard refuses to start."""
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

    with data_dir_of(root), offline():
        try:
            rep = build(None, register=False)
            statuses = notify.eligible_statuses(rep)
            if statuses is None:
                raise CutoverError('built report has no valid eligible status_list')
            out = root / 'data' / 'report.json'
            out.write_text(json.dumps(rep, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
            shutil.copyfile(out, root / 'docs' / 'report.json')
            state_path = root / 'data' / 'notify_state.json'
            with redirect_stdout(io.StringIO()) as first_out:
                first = notify.migrate_file(now)
            bytes1 = state_path.read_bytes()
            with redirect_stdout(io.StringIO()) as second_out:
                second = notify.migrate_file(now)
            bytes2 = state_path.read_bytes()
            state = json.loads(bytes1)
            marker = state['migrations'][notify.STATUS_MIGRATION]
            simulated = notify.messages(rep, copy.deepcopy(state), now)
        except BaseException:
            restore(root, original)
            raise

    after = contents(root)
    changed = sorted(k for k in set(after) | set(original) if after.get(k) != original.get(k))
    kinds = {}
    for text in simulated:
        kinds[kind(text)] = kinds.get(kind(text), 0) + 1
    checks = {
        'first_call': first_out.getvalue().strip(),
        'second_call': second_out.getvalue().strip(),
        'second_call_byte_identical': bytes1 == bytes2,
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
    if not ok:
        restore(root, original)
    old = before_state.get('statuses') or {}
    brain = rep.get('brain') or {}
    wf = brain.get('walk_forward') or {}
    return {
        'ok': ok, 'restored': not ok, 'frozen_main_sha': frozen, 'head_sha': head, 'at_utc': now.isoformat(),
        'python': sys.version.split()[0], 'report_generated_at_utc': rep.get('generated_at_utc'),
        'brain': {'evidence_scope': brain.get('evidence_scope'), 'hypotheses': brain.get('hypotheses'), 'counts': brain.get('counts'),
                  'exploratory_counts': (brain.get('exploratory') or {}).get('counts'), 'eligibility': brain.get('eligibility'),
                  'walk_forward': {k: wf.get(k) for k in ('followed', 'skipped')}},
        'reference': {'previous': len(old), 'baseline': len(statuses),
                      'silenced_transitions': sum(1 for h, s in statuses.items() if old.get(h) != s) + sum(1 for h in old if h not in statuses)},
        'checks': checks, 'next_cycle_simulated_messages': kinds,
        'changed_files': changed, 'sha256_before': digests(original), 'sha256_after': digests(after if ok else contents(root)),
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(json.dumps({'ok': False, 'error': 'usage: pr1_cutover.py <frozen_main_sha>'}))
        return 2
    try:
        result = cutover(ROOT, argv[0])
    except CutoverError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:   # files were already restored by cutover(); never print environment values
        print(json.dumps({'ok': False, 'error': type(exc).__name__}))
        return 1
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
