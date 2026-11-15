"""PR1 cutover: eligible report + silent Telegram status migration, with checks. Offline; never sends.

Usage (integration checkout, collectors paused):
    python scripts/pr1_cutover.py <frozen_main_sha> > audits/pr1-cutover.json

Implements steps 4-6 of audits/pr1-safe-integration.md:
- refuses to run unless data/ and docs/{report.json,bets.csv} are byte-identical to the frozen main commit
  (never restores an older snapshot over newer data);
- report.build(register=False): no registry growth, no CSV, no collectors, LLM or notifications;
- runs the offline migration twice and requires MIGRATED then UNCHANGED with a byte-identical file;
- simulates the next notify cycle without a sender and requires zero status-transition notices;
- requires that only data/report.json, docs/report.json and data/notify_state.json changed.
Any failed check exits with code 1 before anything is committed.
"""
import copy, hashlib, io, json, shutil, socket, subprocess, sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path


def blocked(*a, **k):
    raise AssertionError('network forbidden during cutover')


socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scangoal import notify, report  # noqa: E402
from scangoal.store import data_dir  # noqa: E402

TRACKED = ['docs/report.json', 'docs/bets.csv']
ALLOWED_CHANGES = {'data/report.json', 'docs/report.json', 'data/notify_state.json'}
DELIVERY = ('watch_sent', 'sent_total', 'hello', 'summary_day', 'last_run_utc', 'regime')


def fail(msg):
    print(json.dumps({'ok': False, 'error': msg}, ensure_ascii=False))
    sys.exit(1)


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def snapshot():
    files = sorted(p for p in (ROOT / 'data').iterdir() if p.is_file()) + [ROOT / t for t in TRACKED]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def kind(text):
    if text.startswith(('Hipótese passou a', 'Hipótese voltou', '✅ Tática validada', '⛔ Tática deixou')):
        return 'status_transition'
    if text.startswith('Mudança de comportamento'):
        return 'regime_change'
    if text.startswith(('A seguir:', '🟢 ALERTA')):
        return 'watch'
    if text.startswith('Resumo de'):
        return 'daily_summary'
    return 'other'


def main():
    if len(sys.argv) != 2:
        fail('usage: pr1_cutover.py <frozen_main_sha>')
    frozen = git('rev-parse', '--verify', sys.argv[1] + '^{commit}')
    if data_dir().resolve() != (ROOT / 'data').resolve():
        fail('SCANGOAL_DATA_DIR must point to the checkout data/')
    if git('status', '--porcelain', '--', 'data', 'docs'):
        fail('data/ or docs/ has uncommitted changes')
    try:
        subprocess.check_call(['git', 'merge-base', '--is-ancestor', frozen, 'HEAD'], cwd=ROOT)
    except subprocess.CalledProcessError:
        fail('frozen main commit is not an ancestor of HEAD')
    if subprocess.call(['git', 'diff', '--quiet', frozen, 'HEAD', '--', 'data', *TRACKED], cwd=ROOT):
        fail('data differs from frozen main: refusing to cut over on a different snapshot')
    before = snapshot()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    state_path = data_dir() / 'notify_state.json'
    original = json.loads(state_path.read_text(encoding='utf-8'))   # corrupt state -> exception, nothing written
    if notify.STATUS_MIGRATION in (original.get('migrations') or {}):
        fail('migration marker already present')

    rep = report.build(None, register=False)
    out = data_dir() / 'report.json'
    out.write_text(json.dumps(rep, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    shutil.copyfile(out, ROOT / 'docs' / 'report.json')
    statuses = notify.eligible_statuses(rep)
    if statuses is None:
        fail('report has no valid eligible status_list')

    with redirect_stdout(io.StringIO()) as first_out:
        first = notify.migrate_file(now)
    bytes1 = state_path.read_bytes()
    with redirect_stdout(io.StringIO()) as second_out:
        second = notify.migrate_file(now)
    bytes2 = state_path.read_bytes()
    state = json.loads(bytes1)
    marker = state['migrations'][notify.STATUS_MIGRATION]
    checks = {
        'first_call': first_out.getvalue().strip(),
        'second_call': second_out.getvalue().strip(),
        'second_call_byte_identical': bytes1 == bytes2,
        'statuses_equal_report_status_list': state['statuses'] == statuses,
        'previous_statuses_equal_original': marker['previous_statuses'] == original.get('statuses'),
        'delivery_fields_preserved': all(state.get(k) == original.get(k) for k in DELIVERY),
        'registry_unchanged': None,
    }
    sim = notify.messages(rep, copy.deepcopy(state), now)
    kinds = {}
    for t in sim:
        kinds[kind(t)] = kinds.get(kind(t), 0) + 1
    after = snapshot()
    changed = sorted(k for k in after if before.get(k) != after[k])
    checks['registry_unchanged'] = before['data/hypotheses.json'] == after['data/hypotheses.json']
    checks['only_allowed_files_changed'] = set(changed) <= ALLOWED_CHANGES
    checks['next_cycle_status_transitions'] = kinds.get('status_transition', 0)
    ok = (first and not second and checks['first_call'] == 'NOTIFY_STATUS_MIGRATED'
          and checks['second_call'] == 'NOTIFY_STATUS_UNCHANGED' and all(v is True for k, v in checks.items()
          if k not in ('first_call', 'second_call', 'next_cycle_status_transitions')) and kinds.get('status_transition', 0) == 0)
    old = original.get('statuses') or {}
    b = rep['brain']
    print(json.dumps({
        'ok': ok, 'frozen_main_sha': frozen, 'head_sha': git('rev-parse', 'HEAD'), 'at_utc': now.isoformat(),
        'python': sys.version.split()[0], 'report_generated_at_utc': rep['generated_at_utc'],
        'brain': {'evidence_scope': b['evidence_scope'], 'hypotheses': b['hypotheses'], 'counts': b['counts'],
                  'exploratory_counts': b['exploratory']['counts'], 'eligibility': b['eligibility'],
                  'walk_forward': {k: b['walk_forward'].get(k) for k in ('followed', 'skipped')}},
        'reference': {'previous': len(old), 'baseline': len(statuses),
                      'silenced_transitions': sum(1 for h, s in statuses.items() if old.get(h) != s) + sum(1 for h in old if h not in statuses)},
        'checks': checks, 'next_cycle_simulated_messages': kinds,
        'changed_files': changed, 'sha256_before': before, 'sha256_after': after,
    }, indent=1, ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
