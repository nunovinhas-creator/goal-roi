"""Mutation check for scripts/pr2_cutover.py: disable one guard/check at a time and require a failing test.

Usage (repository root): python3.11 audits/pr2-cutover-evidence/mutation_check.py
Each mutant is written to a temporary copy of the repository (without .git and data/); the real files are untouched.
"""
import os
import shutil
import subprocess
import sys
import tempfile

SRC = open('scripts/pr2_cutover.py', encoding='utf-8').read()
MUTATIONS = {
    'guard: PR1 marker required': ("    if PR1_MARKER not in migrations:\n", "    if False and PR1_MARKER not in migrations:\n"),
    'guard: PR2 key refused if present': ("    if PR2_KEY in migrations:\n", "    if False and PR2_KEY in migrations:\n"),
    'guard: frozen report PR1-format': ("    frozen_report_is_pr1_format(root, frozen)\n", "    pass\n"),
    'guard: status_rules exact': ("    if (rep.get('brain') or {}).get('status_rules') != brain.STATUS_RULES:\n", "    if False:\n"),
    'guard: migration key agreement': ("    if notify.migration_key(rep) != PR2_KEY:\n", "    if False:\n"),
    'guard: operational scope': ("    if (rep.get('operational') or {}).get('scope') != notify.EVIDENCE_SCOPE:\n        raise CutoverError('%s has no eligible",
                                 "    if False:\n        raise CutoverError('%s has no eligible"),
    'guard: register=True identical': ("    if statuses_true != statuses:\n        only_false", "    if False:\n        only_false"),
    'guard: register=True on a copy': ("        with data_env(tmp / 'data'):\n", "        with data_env(root / 'data'):\n"),
    'guard: register=False no write': ("    if contents(root) != original:\n        raise CutoverError('register=False build wrote",
                                       "    if False:\n        raise CutoverError('register=False build wrote"),
    'guard: register=True no leak': ("    if contents(root) != original:\n        raise CutoverError('register=True build wrote",
                                     "    if False:\n        raise CutoverError('register=True build wrote"),
    'transaction: restore on exception': ("        try:\n            restore(root, original)\n        except RestoreFailed as failed:\n            failed.stage, failed.trigger = progress['stage'], exc\n            raise\n", "        pass\n"),
    'transaction: restore on failed check': ("                result['sha256_after'] = restore(root, original)\n                result['restored'] = True\n", "                pass\n"),
    'check: PR1 marker preserved': ("        'pr1_marker_preserved': state['migrations'].get(PR1_MARKER) == before_state['migrations'][PR1_MARKER],\n",
                                    "        'pr1_marker_preserved': True,\n"),
    'check: delivery fields': ("        'delivery_fields_preserved': all(state.get(k) == before_state.get(k) for k in DELIVERY),\n",
                               "        'delivery_fields_preserved': True,\n"),
    'check: no unexpected fields': ("        'no_unexpected_fields': set(state) <= set(before_state) | {'statuses', 'migrations'},\n",
                                    "        'no_unexpected_fields': True,\n"),
    'check: next cycle (register=False)': ("          and checks['next_cycle_status_transitions'] == 0 and", "          and True and"),
    'check: next cycle (register=True)': ("and checks['next_cycle_status_transitions_register_true'] == 0)", "and True)"),
    'check: second migration UNCHANGED': ("and checks['second_call'] == 'NOTIFY_STATUS_UNCHANGED'", "and True"),
    'check: registry unchanged': ("        'registry_unchanged': after.get('data/hypotheses.json') == original.get('data/hypotheses.json'),\n",
                                  "        'registry_unchanged': True,\n"),
    'check: only allowed files': ("        'only_allowed_files_changed': set(changed_files) <= WRITES,\n", "        'only_allowed_files_changed': True,\n"),
}
# registry_unchanged overlaps only_allowed_files_changed (hypotheses.json is not an allowed write); the tests assert the exact
# set of failing checks, so each is killed on its own. The pair is also mutated together below.
EQUIVALENT = {}
PAIRS = {'checks: registry + only allowed files together': ('check: registry unchanged', 'check: only allowed files')}


def run_tests(copy):
    return subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_pr2_cutover.py'],
                          cwd=copy, capture_output=True, text=True)


def main():
    results, work = {}, tempfile.mkdtemp(prefix='pr2-mutation-')
    try:
        baseline = os.path.join(work, 'baseline')
        shutil.copytree('.', baseline, ignore=shutil.ignore_patterns('.git', 'data'))
        if run_tests(baseline).returncode:
            print('BASELINE FAILS: unmutated tests must pass before mutants mean anything')
            return 1
        print('baseline (unmutated) passes')
        for name, (old, new) in MUTATIONS.items():
            assert SRC.count(old) == 1, 'mutation anchor not unique: %s' % name
            copy = os.path.join(work, 'repo')
            shutil.rmtree(copy, ignore_errors=True)
            shutil.copytree('.', copy, ignore=shutil.ignore_patterns('.git', 'data'))
            with open(os.path.join(copy, 'scripts', 'pr2_cutover.py'), 'w', encoding='utf-8') as f:
                f.write(SRC.replace(old, new))
            r = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_pr2_cutover.py'],
                               cwd=copy, capture_output=True, text=True)
            results[name] = 'caught' if r.returncode else 'NOT CAUGHT'
            print('%-40s %s' % (name, results[name]))
        for name, members in PAIRS.items():
            src = SRC
            for member in members:
                old, new = MUTATIONS[member]
                src = src.replace(old, new)
            copy = os.path.join(work, 'repo')
            shutil.rmtree(copy, ignore_errors=True)
            shutil.copytree('.', copy, ignore=shutil.ignore_patterns('.git', 'data'))
            with open(os.path.join(copy, 'scripts', 'pr2_cutover.py'), 'w', encoding='utf-8') as f:
                f.write(src)
            r = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_pr2_cutover.py'],
                               cwd=copy, capture_output=True, text=True)
            results[name] = 'caught' if r.returncode else 'NOT CAUGHT'
            print('%-40s %s' % (name, results[name]))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    required = {k: v for k, v in results.items() if k not in EQUIVALENT}
    caught = sum(v == 'caught' for v in required.values())
    print('caught %d of %d mutants' % (caught, len(required)))
    return 0 if caught == len(required) else 1


if __name__ == '__main__':
    sys.exit(main())
