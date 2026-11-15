"""Reproducible rehearsal of the PR2 cutover against a given main commit. Offline; never sends, never pushes.

Usage (clean checkout of the PR2 branch, after `git fetch origin main`):

    python scripts/rehearse_pr2_cutover.py origin/main > audits/pr2-cutover-rehearsal.json

1. Temporary detached worktree of HEAD, <main> merged in with a throwaway identity (any conflict aborts). No ref moves.
2. `scripts/pr2_cutover.py <main>` runs there in a separate interpreter (network blocked by the script).
3. With network blocked and no sender, on the rehearsed result:
   - first cycle with the cutover report and with an independently rebuilt register=True report: zero transitions;
   - counterfactual: the PR2 report against the production state WITHOUT the PR2 migration (retroactive transitions);
   - old-code risk: the notifier currently on <main> (PR1 code) fed with the production PR1-format report after the
     cutover, then the PR2 notifier on the resulting state;
   - legitimate later changes: one hypothesis returning to observation plus one new promotion, and the confirmation of
     every promotion currently pending, must each be notified exactly once.
4. Removes the worktree and verifies this checkout's data/ and docs/ files are byte-identical before and after.
Exit code 0 only if every entry of `criteria` holds.
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
spec = importlib.util.spec_from_file_location('pr2_cutover', ROOT / 'scripts' / 'pr2_cutover.py')
cut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cut)
from scangoal import brain, notify, report  # noqa: E402

IDENTITY = ('-c', 'user.name=rehearsal', '-c', 'user.email=rehearsal@invalid', '-c', 'commit.gpgsign=false')
SAFE_ENV = {k: v for k, v in os.environ.items()
            if not k.startswith(('TG_', 'HIGHLIGHTLY', 'BSD_', 'CLOUDFLARE', 'MASTER_BRAIN')) and k != 'SCANGOAL_DATA_DIR'}
SYNTHETIC = 'Ensaio | hipótese sintética promovida'


def git(*args, cwd=ROOT, check=True):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=check)


def shown(ref, path):
    return git('show', f'{ref}:{path}').stdout


def kinds(messages):
    return dict(Counter(cut.kind(m) for m in messages))


def transitions(messages):
    return sum(1 for m in messages if cut.kind(m) == 'status_transition')


def main_notifier(main):
    mod = types.ModuleType('scangoal.notify_at_main')
    mod.__package__ = 'scangoal'
    exec(compile(shown(main, 'scangoal/notify.py'), 'notify_at_main.py', 'exec'), mod.__dict__)
    return mod


def criteria(out):
    """Every rehearsal criterion, required explicitly."""
    legit = out.get('legitimate_changes') or {}
    risk = out.get('old_code_risk') or {}
    return {
        'cutover_ok': (out.get('cutover') or {}).get('ok') is True,
        'first_cycle_no_transitions': out.get('first_cycle_same_report', {}).get('status_transition', 0) == 0,
        'first_cycle_register_true_no_transitions': out.get('first_cycle_register_true', {}).get('messages', {}).get('status_transition', 0) == 0,
        'register_true_status_list_identical': out.get('first_cycle_register_true', {}).get('status_list_identical_to_cutover') is True,
        'pr1_marker_byte_identical': out.get('pr1_marker_byte_identical') is True,
        'delivery_history_preserved': out.get('delivery_history_preserved') is True,
        'legitimate_change_notified_once': legit.get('first_call') == legit.get('expected') and legit.get('repeat_call') == 0,
        'pending_confirmations_notified_once': legit.get('pending_confirmed_first') == legit.get('pending_count') and legit.get('pending_confirmed_repeat') == 0,
        'old_code_risk_measured': isinstance(risk.get('old_code_run_after_cutover'), dict),
    }


def rehearse(main_ref):
    main = git('rev-parse', '--verify', main_ref + '^{commit}').stdout.strip()
    head = git('rev-parse', 'HEAD').stdout.strip()
    before = cut.digests(cut.contents(ROOT))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    out = {'main_sha': main, 'pr_head_sha': head, 'at_utc': now.isoformat(), 'python': sys.version.split()[0],
           'status_rules': cut.PR2_RULES, 'migration_key': cut.PR2_KEY}
    tmp = Path(tempfile.mkdtemp(prefix='pr2-rehearsal-'))
    wt = tmp / 'worktree'
    try:
        git('worktree', 'add', '--detach', str(wt), head)
        merged = git(*IDENTITY, 'merge', '--no-ff', '--no-edit', '-m', 'rehearsal: merge main', main, cwd=wt, check=False)
        if merged.returncode:
            out.update(ok=False, error='merge with main failed (conflict): integration needs review')
            return out
        out['merge_conflicts'] = 0
        out['integration_diff_vs_main'] = sorted(git('diff', '--name-only', main, 'HEAD', cwd=wt).stdout.split())
        proc = subprocess.run([sys.executable, '-I', str(wt / 'scripts' / 'pr2_cutover.py'), main], cwd=wt,
                              capture_output=True, text=True, env=SAFE_ENV)
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
            'new_in_baseline': {h: s for h, s in sorted(new.items()) if h not in old},
            'changed': {h: [old[h], s] for h, s in sorted(new.items()) if h in old and old[h] != s},
            'unchanged': sum(1 for h, s in new.items() if old.get(h) == s),
        }
        dump = lambda o: json.dumps(o, sort_keys=True, ensure_ascii=False)
        out['pr1_marker_byte_identical'] = dump(migrated['migrations'][cut.PR1_MARKER]) == dump(prod_state['migrations'][cut.PR1_MARKER])
        out['delivery_history_preserved'] = all(migrated.get(k) == prod_state.get(k) for k in cut.DELIVERY)
        out['delivery_counters'] = {'sent_total': migrated.get('sent_total'), 'watch_sent': len(migrated.get('watch_sent') or [])}

        with cut.offline():
            first = notify.messages(new_report, copy.deepcopy(migrated), now)
            copy_dir = tmp / 'register_true'
            shutil.copytree(wt / 'data', copy_dir)
            with cut.data_env(copy_dir):
                rep_true = report.build(None, register=True)
            first_true = notify.messages(rep_true, copy.deepcopy(migrated), now)

            counterfactual = copy.deepcopy(prod_state)
            counterfactual.setdefault('migrations', {})[cut.PR2_KEY] = {'counterfactual_no_migration': True}
            without = notify.messages(new_report, counterfactual, now)

            state = copy.deepcopy(migrated)
            by_old = main_notifier(main).messages(prod_report, state, now)
            overwritten = state.get('statuses') != new
            by_new = notify.messages(new_report, state, now)
            risk = {'main_notifier_has_rules_key': 'migration_key' in shown(main, 'scangoal/notify.py'),
                    'old_code_run_after_cutover': kinds(by_old), 'pr2_reference_overwritten': overwritten,
                    'next_new_code_run': kinds(by_new), 'pr2_marker_still_present_so_no_rebaseline': cut.PR2_KEY in state['migrations']}

            # Legitimate later changes after the cutover (synthetic edits of the PR2 status_list).
            later = copy.deepcopy(new_report)
            dropped = next((h for h, s in sorted(new.items()) if s == 'promissora'), None)
            if dropped:
                later['brain']['status_list'].pop(dropped)
            later['brain']['status_list'][SYNTHETIC] = 'promissora'
            st = copy.deepcopy(migrated)
            first_legit = notify.messages(later, st, now)
            repeat_legit = notify.messages(later, st, now)
            pending = sorted(r['id'] for r in new_report['brain']['top'] if r.get('pending_status') == 'promissora' and r['id'] not in new)
            confirmed = copy.deepcopy(new_report)
            confirmed['brain']['status_list'].update({h: 'promissora' for h in pending})
            st2 = copy.deepcopy(migrated)
            first_pending = notify.messages(confirmed, st2, now)
            repeat_pending = notify.messages(confirmed, st2, now)

        out['first_cycle_same_report'] = kinds(first)
        out['first_cycle_register_true'] = {'messages': kinds(first_true), 'status_list_identical_to_cutover': rep_true['brain']['status_list'] == new,
                                            'registry_after_register_true_on_copy': rep_true['brain']['hypotheses']}
        out['counterfactual_without_pr2_migration'] = kinds(without)
        out['old_code_risk'] = risk
        out['legitimate_changes'] = {'dropped': dropped, 'added': SYNTHETIC, 'expected': 2 if dropped else 1,
                                     'first_call': transitions(first_legit), 'repeat_call': transitions(repeat_legit), 'texts': first_legit,
                                     'pending_promotions': pending, 'pending_count': len(pending),
                                     'pending_confirmed_first': transitions(first_pending), 'pending_confirmed_repeat': transitions(repeat_pending)}
        return out
    finally:
        git('worktree', 'remove', '--force', str(wt), check=False)
        git('worktree', 'prune', check=False)
        shutil.rmtree(tmp, ignore_errors=True)
        out['this_checkout_unchanged'] = cut.digests(cut.contents(ROOT)) == before
        if 'cutover' in out and out['cutover'].get('ok'):
            out['criteria'] = dict(criteria(out), this_checkout_unchanged=out['this_checkout_unchanged'])
            out['ok'] = all(out['criteria'].values())
        else:
            out['ok'] = False


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(json.dumps({'ok': False, 'error': 'usage: rehearse_pr2_cutover.py <main_ref>'}))
        return 2
    result = rehearse(argv[0])
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
