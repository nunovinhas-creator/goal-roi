"""Mutation check for scripts/pr3_cutover.py: disable one guard/check at a time and require a failing test.

Usage (repository root): python3.11 audits/pr3-cutover-evidence/mutation_check.py
Each mutant is written to a temporary copy of the repository (without .git and data/); the real files are untouched.
The unmutated tests must pass first (baseline), otherwise no mutant result means anything.
"""
import os
import shutil
import subprocess
import sys
import tempfile

SRC = open('scripts/pr3_cutover.py', encoding='utf-8').read()
MUTATIONS = {
    'guard: pinned rules equal code rules': ("    if brain.STATUS_RULES != PR3_RULES:\n", "    if False:\n"),
    'guard: PR1 marker required': ("    if PR1_MARKER not in migrations:\n", "    if False and PR1_MARKER not in migrations:\n"),
    'guard: PR2 marker required': ("    if PR2_KEY not in migrations:\n", "    if False and PR2_KEY not in migrations:\n"),
    'guard: PR3 key only if coherent': ("        raise CutoverError('migration marker %s already present but", "        return {'ok': True, 'already_applied': True, 'changed_files': []}\n        raise CutoverError('migration marker %s already present but"),
    'guard: frozen report PR2-format': ("    frozen_report_is_pr2_format(root, frozen)\n", "    pass\n"),
    'guard: ledger at frozen main': ("    if not pr1.git(root, 'cat-file', '-e', '%s:%s' % (frozen, LEDGER)).returncode:\n", "    if False:\n"),
    'guard: status_rules exact': ("    if b.get('status_rules') != PR3_RULES:\n", "    if False:\n"),
    'guard: migration key agreement': ("    if notify.migration_key(rep) != PR3_KEY:\n", "    if False:\n"),
    'guard: operational scope': ("    if (rep.get('operational') or {}).get('scope') != notify.EVIDENCE_SCOPE:\n", "    if False:\n"),
    'guard: certification not activated': ("    if v.get('state') != 'not_activated' or v.get('certified') or v.get('ledger_valid'):\n", "    if False:\n"),
    'guard: no validated status': ("    if any(s in notify.VALID for s in (b.get('status_list') or {}).values()):\n", "    if False:\n"),
    'guard: register=True identical': ("    if statuses_true != statuses:\n", "    if False:\n"),
    'guard: register=True on a copy': ("        with data_env(tmp / 'data'):\n", "        with data_env(root / 'data'):\n"),
    'guard: register=True no ledger': ("    if ledger_created:\n", "    if False:\n"),
    'guard: register=False no write': ("    if contents(root) != original:\n        raise CutoverError('register=False build wrote",
                                       "    if False:\n        raise CutoverError('register=False build wrote"),
    'guard: register=True no leak': ("    if contents(root) != original:\n        raise CutoverError('register=True build wrote",
                                     "    if False:\n        raise CutoverError('register=True build wrote"),
    'transaction: restore on exception': ("        try:\n            restore(root, original)\n        except RestoreFailed as failed:\n            failed.stage, failed.trigger = progress['stage'], exc\n            raise\n", "        pass\n"),
    'transaction: restore on failed check': ("                result['sha256_after'] = restore(root, original)\n                result['restored'] = True\n", "                pass\n"),
    'check: PR1 marker preserved': ("        'pr1_marker_preserved': state['migrations'].get(PR1_MARKER) == before_state['migrations'][PR1_MARKER],\n", "        'pr1_marker_preserved': True,\n"),
    'check: PR2 marker preserved': ("        'pr2_marker_preserved': state['migrations'].get(PR2_KEY) == before_state['migrations'][PR2_KEY],\n", "        'pr2_marker_preserved': True,\n"),
    'check: delivery fields': ("        'delivery_fields_preserved': all(state.get(k) == before_state.get(k) for k in DELIVERY),\n", "        'delivery_fields_preserved': True,\n"),
    'check: no unexpected fields': ("        'no_unexpected_fields': set(state) <= set(before_state) | {'statuses', 'migrations'},\n", "        'no_unexpected_fields': True,\n"),
    'check: zero transitions/validated in both cycles': ("          and all(checks[k] == 0 for k in counted[2:]))", "          and True)"),
    'check: second migration UNCHANGED': ("          and checks['second_call'] == 'NOTIFY_STATUS_UNCHANGED'", "          and True"),
    'check: registry unchanged': ("        'registry_unchanged': after.get('data/hypotheses.json') == original.get('data/hypotheses.json'),\n", "        'registry_unchanged': True,\n"),
    'check: no ledger after': ("        'no_certification_ledger': LEDGER not in after and not (root / LEDGER).exists(),\n", "        'no_certification_ledger': True,\n"),
    'check: only allowed files': ("        'only_allowed_files_changed': set(changed_files) <= WRITES,\n", "        'only_allowed_files_changed': True,\n"),
    'preflight: frozen remote main': ("    if remote != frozen:\n", "    if False:\n"),
    'preflight: fast-forward only': ("    if pr1.git(root, 'merge-base', '--is-ancestor', remote, 'HEAD').returncode:\n", "    if False:\n"),
}
PAIRS = {'checks: registry + only allowed files together': ('check: registry unchanged', 'check: only allowed files'),
         'checks: ledger + only allowed files together': ('check: no ledger after', 'check: only allowed files'),
         'preflight: both checks together': ('preflight: frozen remote main', 'preflight: fast-forward only')}


def run(src, work):
    copy = os.path.join(work, 'repo')
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree('.', copy, ignore=shutil.ignore_patterns('.git', 'data'))
    with open(os.path.join(copy, 'scripts', 'pr3_cutover.py'), 'w', encoding='utf-8') as f:
        f.write(src)
    return subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_pr3_cutover.py'],
                          cwd=copy, capture_output=True, text=True).returncode


def main():
    results, work = {}, tempfile.mkdtemp(prefix='pr3-mutation-')
    try:
        if run(SRC, work):
            print('BASELINE FAILS: unmutated tests must pass before mutants mean anything')
            return 1
        print('baseline (unmutated) passes')
        for name, (old, new) in MUTATIONS.items():
            assert SRC.count(old) == 1, 'mutation anchor not unique: %s' % name
            results[name] = 'caught' if run(SRC.replace(old, new), work) else 'NOT CAUGHT'
            print('%-52s %s' % (name, results[name]))
        for name, members in PAIRS.items():
            src = SRC
            for member in members:
                src = src.replace(*MUTATIONS[member])
            results[name] = 'caught' if run(src, work) else 'NOT CAUGHT'
            print('%-52s %s' % (name, results[name]))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    caught = sum(v == 'caught' for v in results.values())
    print('caught %d of %d mutants' % (caught, len(results)))
    return 0 if caught == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
