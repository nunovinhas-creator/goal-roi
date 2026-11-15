"""Demonstrate the P1 defect of #5909958197 on a given pr1_cutover.py: exception in post-checks -> partial writes?

Usage (repository root; synthetic fixtures from tests/test_pr1_cutover.py, temporary git repos, no real data):
    git show 426489e:scripts/pr1_cutover.py > /tmp/pr1_cutover_426489e.py
    python3.11 -I audits/pr1-cutover-evidence/p1-rollback-demo.py /tmp/pr1_cutover_426489e.py .   # 3 files left changed
    python3.11 -I audits/pr1-cutover-evidence/p1-rollback-demo.py scripts/pr1_cutover.py .        # none
"""
import importlib.util, json, subprocess, sys, tempfile
from pathlib import Path
from datetime import datetime
script, repo_root = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(repo_root)); sys.path.insert(0, str(repo_root / 'tests'))
import test_pr1_cutover as T      # reuse the synthetic fixtures only
spec = importlib.util.spec_from_file_location('cut_under_test', script); cut = importlib.util.module_from_spec(spec); spec.loader.exec_module(cut)
for stage_label, name, n in (('post_checks: after = contents()', 'contents', 2), ('result: digests()', 'digests', 1)):
    tc = T.Cutover('test_cutover_33_to_8_silent_idempotent_and_preserving_history'); tc.setUp()
    try:
        original = cut.contents(tc.root)
        real, calls = getattr(cut, name), {'n': 0}
        def wrapper(*a, **k):
            calls['n'] += 1
            if calls['n'] == n: raise OSError('injected')
            return real(*a, **k)
        setattr(cut, name, wrapper)
        try:
            cut.cutover(tc.root, tc.main, T.NOW, T.build_ok); outcome = 'no exception'
        except Exception as exc:
            outcome = type(exc).__name__
        setattr(cut, name, real)
        now = cut.contents(tc.root)
        changed = sorted(k for k in set(now) | set(original) if now.get(k) != original.get(k))
        print(json.dumps({'script': script.name, 'injected_at': stage_label, 'outcome': outcome, 'files_left_changed': changed}))
    finally:
        tc.tearDown()
