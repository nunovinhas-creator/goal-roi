"""Reproducible PR 5 test fixtures from git history (run at the repository root; read-only on the checkout).

- tests/fixtures/pr5/state_{pr1,pr2,pr3}.json: data/notify_state.json at the first commit carrying each migration marker;
- tests/fixtures/pr5/state_production.json: data/notify_state.json at the frozen main commit;
- tests/fixtures/pr5/report_production.json: the report the PR 5 code builds from the data of that same commit, at the
  instant the production report of that commit was generated (trimmed to the fields the notifier reads).

Usage: python3.11 audits/pr5-evidence/make_fixtures.py <frozen_main_sha>
"""
import json, os, shutil, subprocess, sys, tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'tests' / 'fixtures' / 'pr5'
MARKERS = {'pr1': 'eligible_statuses_v1', 'pr2': 'status_rules:independent_games_hysteresis_v1',
           'pr3': 'status_rules:chronological_eprocess_v3'}
DATA = ('messages.jsonl', 'hypotheses.json', 'results_hl.json', 'results_bsd.json', 'details_hl.json', 'llm_state.json',
        'llm_notes.json', 'state.json', 'team_aliases.json', 'manual_results.json', 'certification_ledger.json')


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT), *args], check=True, capture_output=True).stdout


def first_with(marker):
    revs = git('log', '--reverse', '--format=%H', '-S"%s"' % marker, '--', 'data/notify_state.json').decode().split()
    return revs[0]


def trim(rep):
    b = rep['brain']
    keep = ('evidence_scope', 'status_rules', 'eligibility', 'hypotheses', 'counts', 'validation', 'status_list', 'watch',
            'presentation', 'llm_today')
    return {'generated_at_utc': rep['generated_at_utc'], 'settled_bets': rep['settled_bets'],
            'audit': {k: rep['audit'].get(k) for k in ('hl_results', 'bsd_results')},
            'daily': rep['daily'][-3:], 'timeline': rep['timeline'][-1:], 'operational': {k: rep['operational'].get(k) for k in ('scope', 'regime')},
            'brain': {k: b.get(k) for k in keep}}


def main(frozen):
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {'frozen_main': frozen}
    for name, marker in MARKERS.items():
        rev = first_with(marker)
        (OUT / ('state_%s.json' % name)).write_bytes(git('show', '%s:data/notify_state.json' % rev))
        meta[name] = rev
    (OUT / 'state_production.json').write_bytes(git('show', '%s:data/notify_state.json' % frozen))
    old_report = json.loads(git('show', '%s:data/report.json' % frozen))
    with tempfile.TemporaryDirectory() as d:
        for f in DATA:
            try:
                (Path(d) / f).write_bytes(git('show', '%s:data/%s' % (frozen, f)))
            except subprocess.CalledProcessError:
                pass
        code = ('import json;from datetime import datetime;from scangoal import report;'
                'r=report.build(None,register=False,now=datetime.fromisoformat(%r));'
                'json.dump(r,open(%r,"w"),ensure_ascii=False)') % (old_report['generated_at_utc'], d + '/r.json')
        subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=dict(os.environ, SCANGOAL_DATA_DIR=d), check=True)
        rep = json.loads(Path(d, 'r.json').read_text(encoding='utf-8'))
    for k in ('status_list', 'watch', 'validation'):
        a = old_report['brain'][k]
        b = rep['brain'][k] if k != 'watch' else [{x: y for x, y in w.items() if x != 'match_id'} for w in rep['brain'][k]]
        assert a == b, 'science differs from production on %s' % k
    (OUT / 'report_production.json').write_text(json.dumps(trim(rep), ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    meta['report_generated_at_utc'] = rep['generated_at_utc']
    (OUT / 'SOURCE.json').write_text(json.dumps(meta, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(meta, indent=1))


if __name__ == '__main__':
    main(sys.argv[1])
