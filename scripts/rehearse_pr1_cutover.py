"""Reproducible rehearsal of the PR1 cutover against a given main commit. Offline; never sends, never pushes.

Usage (clean checkout of the PR branch, after `git fetch origin main`):

    python scripts/rehearse_pr1_cutover.py origin/main > audits/pr1-cutover-rehearsal.json

1. Creates a temporary detached worktree of HEAD and merges <main> into it with a throwaway identity, exactly like the
   integration step. Any conflict aborts the rehearsal. No branch or ref is created or moved.
2. Runs `scripts/pr1_cutover.py <main>` there in a separate interpreter (the script blocks the network itself).
3. Measures, with network blocked and no sender:
   - first cycle: the notifier on the migrated state with the cutover report, and with a report rebuilt with
     register=True (what the first production report step does);
   - counterfactual: the eligible report against the production state WITHOUT the migration;
   - old-code risk: the notifier currently on <main> fed with the current production report after the migration,
     followed by the new notifier on the resulting state (an old run writing after the cutover).
4. Removes the worktree and verifies that this checkout's data/ and docs/ files are byte-identical before and after.
Exit code 0 only if every entry of `criteria` holds: the cutover passed every check; both first-cycle variants have
zero status transitions; the status_list rebuilt with register=True is identical to the cutover baseline (otherwise the
first production report could announce statuses that are not new evidence); that report keeps
operational.scope = eligible_bets_only; and this checkout is unchanged.
"""
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('pr1_cutover', ROOT / 'scripts' / 'pr1_cutover.py')
cut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cut)
from scangoal import notify, report  # noqa: E402

IDENTITY = ('-c', 'user.name=rehearsal', '-c', 'user.email=rehearsal@invalid', '-c', 'commit.gpgsign=false')
SAFE_ENV = {k: v for k, v in os.environ.items()
            if not k.startswith(('TG_', 'HIGHLIGHTLY', 'BSD_', 'CLOUDFLARE', 'MASTER_BRAIN')) and k != 'SCANGOAL_DATA_DIR'}


def git(*args, cwd=ROOT, check=True):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=check)


def shown(ref, path):
    return git('show', f'{ref}:{path}').stdout


def kinds(messages):
    return dict(Counter(cut.kind(m) for m in messages))


def old_notifier(main):
    mod = types.ModuleType('scangoal.notify_at_main')
    mod.__package__ = 'scangoal'
    exec(compile(shown(main, 'scangoal/notify.py'), 'notify_at_main.py', 'exec'), mod.__dict__)
    return mod


def rehearse(main_ref):
    main = git('rev-parse', '--verify', main_ref + '^{commit}').stdout.strip()
    head = git('rev-parse', 'HEAD').stdout.strip()
    before = cut.digests(cut.contents(ROOT))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    out = {'main_sha': main, 'pr_head_sha': head, 'at_utc': now.isoformat(), 'python': sys.version.split()[0]}
    tmp = Path(tempfile.mkdtemp(prefix='pr1-rehearsal-'))
    wt = tmp / 'worktree'
    try:
        git('worktree', 'add', '--detach', str(wt), head)
        merged = git(*IDENTITY, 'merge', '--no-ff', '--no-edit', '-m', 'rehearsal: merge main', main, cwd=wt, check=False)
        if merged.returncode:
            out.update(ok=False, error='merge with main failed (conflict): integration needs review')
            return out
        out['merge_conflicts'] = 0
        out['integration_diff_vs_main'] = sorted(git('diff', '--name-only', main, 'HEAD', cwd=wt).stdout.split())
        proc = subprocess.run([sys.executable, '-I', str(wt / 'scripts' / 'pr1_cutover.py'), main],
                              cwd=wt, capture_output=True, text=True, env=SAFE_ENV)
        out['cutover_exit_code'] = proc.returncode
        out['cutover'] = json.loads(proc.stdout)
        if not out['cutover'].get('ok'):
            out['ok'] = False
            return out

        prod_state = json.loads(shown(main, 'data/notify_state.json'))
        prod_report = json.loads(shown(main, 'data/report.json'))
        new_report = json.loads((wt / 'data' / 'report.json').read_text(encoding='utf-8'))
        migrated = json.loads((wt / 'data' / 'notify_state.json').read_text(encoding='utf-8'))
        old, new = prod_state.get('statuses') or {}, new_report['brain']['status_list']
        out['reclassification'] = {
            'promissora_to_observacao': sorted(h for h, s in old.items() if s == 'promissora' and h not in new),
            'abandonada_to_observacao': sorted(h for h, s in old.items() if s == 'abandonada' and h not in new),
            'validated_to_observacao': sorted(h for h, s in old.items() if s in notify.VALID and h not in new),
            'new_in_baseline': {h: s for h, s in sorted(new.items()) if h not in old},
            'changed': {h: [old[h], s] for h, s in sorted(new.items()) if h in old and old[h] != s},
            'unchanged': sum(1 for h, s in new.items() if old.get(h) == s),
        }

        with cut.offline():
            first = notify.messages(new_report, copy.deepcopy(migrated), now)
            copy_dir = tmp / 'register_true'
            shutil.copytree(wt / 'data', copy_dir)
            previous_dir = os.environ.get('SCANGOAL_DATA_DIR')
            os.environ['SCANGOAL_DATA_DIR'] = str(copy_dir)
            try:
                reg_before = len(json.loads((copy_dir / 'hypotheses.json').read_text(encoding='utf-8')))
                rep_true = report.build(None, register=True)
                reg_after = len(json.loads((copy_dir / 'hypotheses.json').read_text(encoding='utf-8')))
            finally:
                if previous_dir is None:
                    os.environ.pop('SCANGOAL_DATA_DIR', None)
                else:
                    os.environ['SCANGOAL_DATA_DIR'] = previous_dir
            first_true = notify.messages(rep_true, copy.deepcopy(migrated), now)

            counterfactual = copy.deepcopy(prod_state)
            counterfactual['migrations'] = {notify.STATUS_MIGRATION: {'counterfactual_no_migration': True}}
            without = notify.messages(new_report, counterfactual, now)

            main_has_migration = 'STATUS_MIGRATION' in shown(main, 'scangoal/notify.py')
            risk = None
            if not main_has_migration:
                state = copy.deepcopy(migrated)
                by_old = old_notifier(main).messages(prod_report, state, now)
                overwritten = state.get('statuses') != new
                by_new = notify.messages(new_report, state, now)
                risk = {'old_code_run_after_cutover': kinds(by_old), 'baseline_overwritten_by_old_run': overwritten,
                        'next_new_code_run': kinds(by_new),
                        'marker_still_present_so_no_remigration': notify.STATUS_MIGRATION in (state.get('migrations') or {})}

        identical = rep_true['brain']['status_list'] == new
        out['first_cycle_same_report'] = kinds(first)
        out['first_cycle_register_true'] = {'messages': kinds(first_true), 'registry_before': reg_before, 'registry_after': reg_after,
                                            'status_list_identical_to_cutover': identical,
                                            'operational_scope': (rep_true.get('operational') or {}).get('scope'),
                                            'status_list_differences': None if identical else {
                                                'only_in_register_true': sorted(set(rep_true['brain']['status_list']) - set(new)),
                                                'only_in_cutover': sorted(set(new) - set(rep_true['brain']['status_list'])),
                                                'changed': sorted(h for h in set(new) & set(rep_true['brain']['status_list'])
                                                                  if new[h] != rep_true['brain']['status_list'][h])}}
        out['counterfactual_without_migration'] = kinds(without)
        out['main_notifier_already_has_migration'] = main_has_migration
        out['old_code_risk'] = risk
        out['criteria'] = criteria(out['cutover'], kinds(first), kinds(first_true), identical,
                                   out['first_cycle_register_true']['operational_scope'])
        out['ok'] = all(out['criteria'].values())
        return out
    finally:
        git('worktree', 'remove', '--force', str(wt), check=False)
        git('worktree', 'prune', check=False)
        shutil.rmtree(tmp, ignore_errors=True)
        out['this_checkout_unchanged'] = cut.digests(cut.contents(ROOT)) == before
        if 'criteria' in out:
            out['criteria']['this_checkout_unchanged'] = out['this_checkout_unchanged']
        out['ok'] = bool(out.get('ok')) and out['this_checkout_unchanged']


def criteria(cutover_result, first_kinds, first_true_kinds, status_list_identical, register_true_scope):
    """Every rehearsal criterion, required explicitly (the JSON shows which one failed)."""
    return {
        'cutover_ok': cutover_result.get('ok') is True,
        'first_cycle_same_report_no_transitions': first_kinds.get('status_transition', 0) == 0,
        'first_cycle_register_true_no_transitions': first_true_kinds.get('status_transition', 0) == 0,
        'status_list_identical_register_true_vs_cutover': status_list_identical is True,
        'register_true_operational_scope_eligible': register_true_scope == notify.EVIDENCE_SCOPE,
    }


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(json.dumps({'ok': False, 'error': 'usage: rehearse_pr1_cutover.py <main_ref>'}))
        return 2
    result = rehearse(argv[0])
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
