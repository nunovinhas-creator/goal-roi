"""Reproducible rehearsal of the PR3 cutover and of its failure modes against a given main commit. Offline for the
production data: never sends, never pushes, never touches this checkout's data/ or docs/ (verified by hash).

Usage (clean checkout of the PR3 branch, after `git fetch origin main`):

    python scripts/rehearse_pr3_cutover.py origin/main > audits/pr3-cutover-rehearsal.json

1. Temporary detached worktree of HEAD, <main> merged in with a throwaway identity (any conflict aborts). No ref moves.
2. `scripts/pr3_cutover.py <main>` runs there in a separate interpreter (network blocked by the script).
3. Telegram: two simulated cycles after the migration (cutover report, then an independently rebuilt register=True
   report): zero transitions and zero validated messages; counterfactual without the PR3 migration; PR1/PR2 markers,
   counters and delivery history intact; legitimate later changes notified exactly once.
4. Failure modes, each in throwaway copies: interrupted execution (restore + re-run + idempotent re-run), concurrent
   collector commit (push preflight NO-GO), stale PR2 writer rebased onto the cutover (conflict or overwrite, measured),
   rollback by `git revert` (files back to <main>), data integrity (only the three allowed files change), linear history.
5. Certification preparation (no ledger in data/): the 30-min publication window and the collector's rebase on a
   synthetic ledger in a temporary repository; full vs shallow checkout (history_proof, size and time).
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
import time
import types
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('pr3_cutover', ROOT / 'scripts' / 'pr3_cutover.py')
cut = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cut)
from scangoal import certification, notify, report  # noqa: E402

IDENTITY = ('-c', 'user.name=rehearsal', '-c', 'user.email=rehearsal@invalid', '-c', 'commit.gpgsign=false')
SAFE_ENV = {k: v for k, v in os.environ.items()
            if not k.startswith(('TG_', 'HIGHLIGHTLY', 'BSD_', 'CLOUDFLARE', 'MASTER_BRAIN')) and k != 'SCANGOAL_DATA_DIR'}
SYNTHETIC = 'Ensaio | hipótese sintética promovida'


def git(*args, cwd=ROOT, check=True, env=None):
    return subprocess.run(['git', *IDENTITY, *args], cwd=cwd, capture_output=True, text=True, check=check, env=env)


def shown(ref, path, cwd=ROOT):
    return git('show', f'{ref}:{path}', cwd=cwd).stdout


def kinds(messages):
    return dict(Counter(cut.kind(m) for m in messages))


def transitions(messages):
    return sum(1 for m in messages if cut.kind(m) == 'status_transition')


def notifier_at(ref, cwd=ROOT):
    mod = types.ModuleType('scangoal.notify_at_ref')
    mod.__package__ = 'scangoal'
    exec(compile(shown(ref, 'scangoal/notify.py', cwd), 'notify_at_ref.py', 'exec'), mod.__dict__)
    return mod


def telegram_rehearsal(wt, main, now):
    out = {}
    prod_state = json.loads(shown(main, 'data/notify_state.json'))
    prod_report = json.loads(shown(main, 'data/report.json'))
    new_report = json.loads((wt / 'data' / 'report.json').read_text(encoding='utf-8'))
    migrated = json.loads((wt / 'data' / 'notify_state.json').read_text(encoding='utf-8'))
    old, new = prod_state.get('statuses') or {}, new_report['brain']['status_list']
    out['reclassification'] = {'removed': sorted(h for h in old if h not in new), 'added': {h: s for h, s in sorted(new.items()) if h not in old},
                               'changed': {h: [old[h], s] for h, s in sorted(new.items()) if h in old and old[h] != s},
                               'unchanged': sum(1 for h, s in new.items() if old.get(h) == s)}
    dump = lambda o: json.dumps(o, sort_keys=True, ensure_ascii=False)
    out['pr1_marker_byte_identical'] = dump(migrated['migrations'][cut.PR1_MARKER]) == dump(prod_state['migrations'][cut.PR1_MARKER])
    out['pr2_marker_byte_identical'] = dump(migrated['migrations'][cut.PR2_KEY]) == dump(prod_state['migrations'][cut.PR2_KEY])
    out['delivery_history_preserved'] = all(migrated.get(k) == prod_state.get(k) for k in cut.DELIVERY)
    out['delivery_counters'] = {'sent_total': migrated.get('sent_total'), 'watch_sent': len(migrated.get('watch_sent') or [])}
    with cut.offline():
        copy_dir = Path(tempfile.mkdtemp(prefix='pr3-rehearsal-register-')) / 'data'
        shutil.copytree(wt / 'data', copy_dir)
        with cut.data_env(copy_dir):
            rep_true = report.build(None, register=True)
        ledger_created = (copy_dir / certification.FILE).exists()
        shutil.rmtree(copy_dir.parent, ignore_errors=True)
        st = copy.deepcopy(migrated)
        cycle1 = notify.messages(new_report, st, now)
        cycle2 = notify.messages(rep_true, st, now)
        counterfactual = copy.deepcopy(prod_state)
        counterfactual.setdefault('migrations', {})[cut.PR3_KEY] = {'counterfactual_no_migration': True}
        without = notify.messages(new_report, counterfactual, now)
        state = copy.deepcopy(migrated)
        by_old = notifier_at(main).messages(prod_report, state, now)
        overwritten = state.get('statuses') != new
        by_new = notify.messages(new_report, state, now)
        later = copy.deepcopy(new_report)
        dropped = next((h for h, s in sorted(new.items()) if s == 'promissora'), None)
        if dropped:
            later['brain']['status_list'].pop(dropped)
        later['brain']['status_list'][SYNTHETIC] = 'promissora'
        st2 = copy.deepcopy(migrated)
        first_legit, repeat_legit = notify.messages(later, st2, now), notify.messages(later, st2, now)
    out['cycle1'], out['cycle2'] = kinds(cycle1), kinds(cycle2)
    out['validated_messages'] = {'cycle1': cut.validated(cycle1), 'cycle2': cut.validated(cycle2)}
    out['register_true'] = {'status_list_identical_to_cutover': rep_true['brain']['status_list'] == new,
                            'validation_state': rep_true['brain']['validation']['state'], 'ledger_created': ledger_created}
    out['counterfactual_without_pr3_migration'] = kinds(without)
    out['old_code_risk'] = {'old_code_run_after_cutover': kinds(by_old), 'pr3_reference_overwritten': overwritten,
                            'next_new_code_run': kinds(by_new)}
    out['legitimate_changes'] = {'dropped': dropped, 'added': SYNTHETIC, 'expected': 2 if dropped else 1,
                                 'first_call': transitions(first_legit), 'repeat_call': transitions(repeat_legit)}
    return out


def failure_rehearsals(wt, main, now):
    """Failure modes on throwaway copies of the integration worktree (git history included)."""
    out = {}
    work = Path(tempfile.mkdtemp(prefix='pr3-failures-'))
    try:
        repo = work / 'repo'
        git('clone', '-q', '--no-hardlinks', str(wt), str(repo))
        git('checkout', '-q', git('rev-parse', 'HEAD', cwd=wt).stdout.strip(), cwd=repo)
        data_before = cut.digests(cut.contents(repo))
        # Interrupted execution: an injected failure after the report and the migration were written.
        with patch.object(cut.notify, 'messages', side_effect=RuntimeError('injected interruption')):
            try:
                cut.cutover(repo, main, now)
                aborted = None
            except cut.CutoverAborted as exc:
                aborted = exc.stage
        out['interrupted'] = {'aborted_at_stage': aborted, 'restored_byte_identical': cut.digests(cut.contents(repo)) == data_before}
        first = cut.cutover(repo, main, now)
        out['interrupted']['rerun_ok'] = first['ok']
        git('add', '-A', cwd=repo)
        git('commit', '-q', '-m', 'data: PR3 cutover (rehearsal)', cwd=repo)
        cut_commit = git('rev-parse', 'HEAD', cwd=repo).stdout.strip()
        again = cut.cutover(repo, cut_commit, now)
        out['interrupted']['idempotent_rerun'] = {'ok': again['ok'], 'already_applied': again.get('already_applied'), 'changed_files': again['changed_files']}
        # Data integrity: only the three allowed files differ from main; every other data/docs file byte-identical.
        integration = git('rev-parse', cut_commit + '^', cwd=repo).stdout.strip()
        by_cutover = git('diff', '--name-only', integration, cut_commit, cwd=repo).stdout.split()
        data_vs_main = git('diff', '--name-only', main, cut_commit, '--', 'data', 'docs/report.json', 'docs/bets.csv', cwd=repo).stdout.split()
        out['data_integrity'] = {'changed_by_cutover_commit': sorted(by_cutover), 'data_changed_vs_main': sorted(data_vs_main),
                                 'only_allowed': set(by_cutover) <= set(cut.WRITES) and set(data_vs_main) <= set(cut.WRITES),
                                 'messages_identical': shown(main, 'data/messages.jsonl', repo) == shown(cut_commit, 'data/messages.jsonl', repo),
                                 'code_changed_vs_main': sorted(set(git('diff', '--name-only', main, integration, cwd=repo).stdout.split()))}
        # Linear history and push preflight against a remote main that did not move.
        git('branch', '-f', 'remote-main', main, cwd=repo)
        out['push_preflight_frozen'] = cut.push_preflight(repo, 'remote-main', main)
        # Concurrent collector commit on main after the freeze: preflight must say NO-GO.
        git('checkout', '-q', '-b', 'collector', main, cwd=repo)
        with open(repo / 'data' / 'messages.jsonl', 'a', encoding='utf-8') as f:
            f.write('{"message_id": 99999999, "rehearsal": true}\n')
        git('commit', '-q', '-am', 'data: collect (concurrent, rehearsal)', cwd=repo)
        concurrent = git('rev-parse', 'HEAD', cwd=repo).stdout.strip()
        git('checkout', '-q', cut_commit, cwd=repo)
        out['push_preflight_after_concurrent_commit'] = cut.push_preflight(repo, concurrent, main)
        # A stale PR2 writer (started before the freeze) commits a report/state based on main and rebases onto the cutover.
        git('checkout', '-q', '-b', 'stale', main, cwd=repo)
        state = json.loads((repo / 'data' / 'notify_state.json').read_text(encoding='utf-8'))
        state['last_run_utc'] = now.isoformat()
        (repo / 'data' / 'notify_state.json').write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
        rep = json.loads((repo / 'data' / 'report.json').read_text(encoding='utf-8'))
        rep['generated_at_utc'] = now.isoformat()
        (repo / 'data' / 'report.json').write_text(json.dumps(rep, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
        git('commit', '-q', '-am', 'data: collect (stale PR2 writer, rehearsal)', cwd=repo)
        rebase = git('rebase', cut_commit, cwd=repo, check=False)
        conflicted = sorted(git('diff', '--name-only', '--diff-filter=U', cwd=repo).stdout.split())
        git('rebase', '--abort', cwd=repo, check=False)
        out['stale_writer_rebase'] = {'rebase_exit_code': rebase.returncode, 'conflicted_files': conflicted,
                                      'outcome': 'conflict: the stale run cannot push (fails safe)' if rebase.returncode else 'applied: overwrite risk'}
        # Rollback: revert the cutover commit; the three files return to main, everything else stays.
        git('checkout', '-q', cut_commit, cwd=repo)
        git('revert', '--no-edit', cut_commit, cwd=repo)
        reverted = git('rev-parse', 'HEAD', cwd=repo).stdout.strip()
        out['rollback'] = {'files_equal_main': all(shown(main, f, repo) == shown(reverted, f, repo) for f in sorted(cut.WRITES)),
                           'pr3_marker_removed': cut.PR3_KEY not in json.loads(shown(reverted, 'data/notify_state.json', repo))['migrations'],
                           'pr2_marker_present': cut.PR2_KEY in json.loads(shown(reverted, 'data/notify_state.json', repo))['migrations'],
                           'fast_forward_of_cutover': git('merge-base', '--is-ancestor', cut_commit, reverted, cwd=repo, check=False).returncode == 0}
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


def publication_window_rehearsal():
    """Certification preparation on a synthetic ledger in a temporary repository (never in data/): commit delays and the
    collector's `git pull --rebase` (which rewrites the committer date) against the 30-min window of history_proof."""
    act = datetime(2026, 11, 20, tzinfo=timezone.utc)
    reg = {'x': {'family': 'Jogo aberto', 'conditions': [['Minuto', '31 a 60']], 'born_utc': '2026-11-01T00:00:00+00:00', 'source': 'gerador'}}
    out = {}

    def repo_with(commit_delay_min, rebase_delay_min=None):
        tmp = Path(tempfile.mkdtemp(prefix='pr3-window-'))
        env = lambda t: dict(os.environ, GIT_AUTHOR_DATE=t.isoformat(), GIT_COMMITTER_DATE=t.isoformat())
        try:
            (tmp / 'data').mkdir()
            git('init', '-q', cwd=tmp)
            git('commit', '-q', '--allow-empty', '-m', 'base', cwd=tmp, env=env(act - timedelta(hours=1)))
            led = certification.activate({}, act)
            certification.save(tmp / 'data' / certification.FILE, led)
            git('add', '-A', cwd=tmp)
            git('commit', '-q', '-m', 'activation', cwd=tmp, env=env(act + timedelta(minutes=commit_delay_min)))
            if rebase_delay_min is not None:   # an upstream commit arrives; the local activation commit is rebased onto it
                git('checkout', '-q', '-b', 'upstream', 'HEAD~1', cwd=tmp)
                git('commit', '-q', '--allow-empty', '-m', 'upstream', cwd=tmp, env=env(act + timedelta(minutes=1)))
                git('checkout', '-q', '-', cwd=tmp)
                git('rebase', '-q', 'upstream', cwd=tmp, env=env(act + timedelta(minutes=rebase_delay_min)))
            return certification.history_proof(tmp / 'data' / certification.FILE, led)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    for delay in (5, 29, 31):
        out['commit_after_%d_min' % delay] = repo_with(delay)
    for delay in (12, 40):
        out['rebased_at_%d_min' % delay] = repo_with(5, delay)
    return out


def checkout_rehearsal(main):
    """fetch-depth: 0 versus the current shallow checkout (measured on fresh read-only clones, as actions/checkout does;
    the workflow is NOT changed). history_proof refuses a shallow clone and works on a full one."""
    out = {}
    tmp = Path(tempfile.mkdtemp(prefix='pr3-checkout-'))
    source = git('remote', 'get-url', 'origin', check=False).stdout.strip() or 'file://%s' % ROOT
    try:
        for label, args in (('shallow_depth_1', ['--depth', '1']), ('full_fetch_depth_0', [])):
            dest = tmp / label
            t0 = time.perf_counter()
            done = git('clone', '-q', '--no-local', '--single-branch', '--branch', 'main', *args, source, str(dest), check=False)
            if done.returncode:
                shutil.rmtree(dest, ignore_errors=True)
                source = 'file://%s' % ROOT
                git('clone', '-q', '--no-local', *args, source, str(dest))
            elapsed = time.perf_counter() - t0
            size = sum(p.stat().st_size for p in (dest / '.git').rglob('*') if p.is_file())
            led = certification.activate({}, datetime(2026, 11, 20, tzinfo=timezone.utc))
            out[label] = {'source': 'github' if source.startswith('https://') else 'local', 'seconds': round(elapsed, 2),
                          'git_dir_mib': round(size / 2 ** 20, 2), 'shallow': git('rev-parse', '--is-shallow-repository', cwd=dest).stdout.strip(),
                          'commits_visible': int(git('rev-list', '--count', 'HEAD', cwd=dest).stdout.strip()),
                          'history_proof_without_ledger': certification.history_proof(dest / 'data' / certification.FILE, led)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def criteria(out):
    tg, fail, window = out.get('telegram') or {}, out.get('failures') or {}, out.get('publication_window') or {}
    checkout = out.get('checkout') or {}
    return {
        'cutover_ok': (out.get('cutover') or {}).get('ok') is True,
        'certification_not_activated': (out.get('cutover') or {}).get('brain', {}).get('validation_state') == 'not_activated',
        'cycle1_no_transitions_no_validated': tg.get('cycle1', {}).get('status_transition', 0) == 0 and tg.get('validated_messages', {}).get('cycle1') == 0,
        'cycle2_no_transitions_no_validated': tg.get('cycle2', {}).get('status_transition', 0) == 0 and tg.get('validated_messages', {}).get('cycle2') == 0,
        'register_true_identical_and_no_ledger': tg.get('register_true', {}).get('status_list_identical_to_cutover') is True
                                                 and tg.get('register_true', {}).get('ledger_created') is False,
        'pr1_and_pr2_markers_byte_identical': tg.get('pr1_marker_byte_identical') is True and tg.get('pr2_marker_byte_identical') is True,
        'delivery_history_preserved': tg.get('delivery_history_preserved') is True,
        'legitimate_changes_notified_once': (tg.get('legitimate_changes') or {}).get('first_call') == (tg.get('legitimate_changes') or {}).get('expected')
                                            and (tg.get('legitimate_changes') or {}).get('repeat_call') == 0,
        'interrupted_run_restored_and_rerun': fail.get('interrupted', {}).get('restored_byte_identical') is True and fail.get('interrupted', {}).get('rerun_ok') is True,
        'idempotent_rerun': fail.get('interrupted', {}).get('idempotent_rerun', {}).get('already_applied') is True,
        'data_integrity': fail.get('data_integrity', {}).get('only_allowed') is True and fail.get('data_integrity', {}).get('messages_identical') is True,
        'push_preflight_go_when_frozen': fail.get('push_preflight_frozen') == [],
        'push_preflight_no_go_after_concurrent_commit': bool(fail.get('push_preflight_after_concurrent_commit')),
        'rollback_restores_main_files': fail.get('rollback', {}).get('files_equal_main') is True,
        'window_5_ok_31_refused': window.get('commit_after_5_min') == [] and bool(window.get('commit_after_31_min')),
        'rebase_inside_window_ok_outside_refused': window.get('rebased_at_12_min') == [] and bool(window.get('rebased_at_40_min')),
        'full_checkout_needed': checkout.get('shallow_depth_1', {}).get('shallow') == 'true' and checkout.get('full_fetch_depth_0', {}).get('shallow') == 'false',
    }


def rehearse(main_ref):
    main = git('rev-parse', '--verify', main_ref + '^{commit}').stdout.strip()
    head = git('rev-parse', 'HEAD').stdout.strip()
    before = cut.digests(cut.contents(ROOT))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    out = {'main_sha': main, 'pr_head_sha': head, 'at_utc': now.isoformat(), 'python': sys.version.split()[0],
           'status_rules': cut.PR3_RULES, 'migration_key': cut.PR3_KEY}
    tmp = Path(tempfile.mkdtemp(prefix='pr3-rehearsal-'))
    wt = tmp / 'worktree'
    try:
        git('worktree', 'add', '--detach', str(wt), head)
        merged = git('merge', '--no-ff', '--no-edit', '-m', 'rehearsal: merge main', main, cwd=wt, check=False)
        if merged.returncode:
            out.update(ok=False, error='merge with main failed (conflict): integration needs review')
            return out
        out['merge_conflicts'] = 0
        out['integration_diff_vs_main'] = sorted(git('diff', '--name-only', main, 'HEAD', cwd=wt).stdout.split())
        proc = subprocess.run([sys.executable, '-I', str(wt / 'scripts' / 'pr3_cutover.py'), main], cwd=wt,
                              capture_output=True, text=True, env=SAFE_ENV)
        out['cutover_exit_code'] = proc.returncode
        out['cutover'] = json.loads(proc.stdout)
        if not out['cutover'].get('ok'):
            out['ok'] = False
            return out
        out['telegram'] = telegram_rehearsal(wt, main, now)
        git('checkout', '-q', '--', 'data', 'docs', cwd=wt)            # failures start again from the integration commit
        out['failures'] = failure_rehearsals(wt, main, now)
        out['publication_window'] = publication_window_rehearsal()
        out['checkout'] = checkout_rehearsal(main)
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
        print(json.dumps({'ok': False, 'error': 'usage: rehearse_pr3_cutover.py <main_ref>'}))
        return 2
    result = rehearse(argv[0])
    print(json.dumps(result, indent=1, ensure_ascii=False, default=str))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
