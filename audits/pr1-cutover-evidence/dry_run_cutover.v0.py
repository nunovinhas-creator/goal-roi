"""Dry run of the PR1 cutover on a COPY of current production data. Offline; never sends.

Usage: python3.11 dry_run_cutover.py <merged_checkout> <workdir>
- Network is blocked for the whole run.
- Production files in <merged_checkout>/data are hashed before/after and must not change.
"""
import copy, hashlib, importlib, json, os, shutil, socket, subprocess, sys, types
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

repo, work = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()


def blocked(*a, **k):
    raise AssertionError('network forbidden')


for name in ('connect', 'connect_ex'):
    setattr(socket.socket, name, blocked)
socket.create_connection = blocked

sys.path.insert(0, str(repo))
os.chdir(repo)


def hashes(d):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(d).iterdir()) if p.is_file()}


prod_before = hashes(repo / 'data')
if work.exists():
    shutil.rmtree(work)
A, B = work / 'A_register_false', work / 'B_register_true'
for d in (A, B):
    shutil.copytree(repo / 'data', d)

from scangoal import notify, report, brain  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)
out = {'now_utc': NOW.isoformat(), 'main_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()}


def categorize(msgs):
    c = Counter()
    for m in msgs:
        if m.startswith('Avisos do goal-roi ligados'):
            c['hello'] += 1
        elif m.startswith('🟢 ALERTA'):
            c['alert_validated'] += 1
        elif m.startswith('A seguir:'):
            c['watch_hint'] += 1
        elif m.startswith(('✅ Tática validada', '⛔ Tática deixou')):
            c['validated_transition'] += 1
        elif m.startswith('Hipótese passou a') or m.startswith('Hipótese voltou'):
            c['status_transition'] += 1
        elif m.startswith('Mudança de comportamento'):
            c['regime_change'] += 1
        elif m.startswith('Resumo de'):
            c['daily_summary'] += 1
        else:
            c['other'] += 1
    return dict(c)


def build_into(d, register):
    os.environ['SCANGOAL_DATA_DIR'] = str(d)
    reg_before = len(json.loads((d / 'hypotheses.json').read_text(encoding='utf-8')))
    rep = report.build(None, register=register)
    (d / 'report.json').write_text(json.dumps(rep, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    reg_after = len(json.loads((d / 'hypotheses.json').read_text(encoding='utf-8')))
    b = rep['brain']
    return rep, {'evidence_scope': b.get('evidence_scope'), 'operational_scope': rep['operational']['scope'],
                 'hypotheses': b['hypotheses'], 'counts': b['counts'], 'exploratory_counts': b['exploratory']['counts'],
                 'eligibility': b['eligibility'], 'status_list_counts': dict(Counter(b['status_list'].values())),
                 'registry_before': reg_before, 'registry_after': reg_after, 'watch_items': len(b['watch']),
                 'walk_forward': {k: b['walk_forward'].get(k) for k in ('followed', 'skipped')}}


# --- Variant A: documented procedure (register=False), then migrate twice.
repA, out['A_report'] = build_into(A, False)
state_orig = json.loads((A / 'notify_state.json').read_text(encoding='utf-8'))
os.environ['SCANGOAL_DATA_DIR'] = str(A)
m1 = notify.migrate_file(NOW)
bytes1 = (A / 'notify_state.json').read_bytes()
m2 = notify.migrate_file(NOW)
bytes2 = (A / 'notify_state.json').read_bytes()
stA = json.loads(bytes1)
keep = ('watch_sent', 'sent_total', 'hello', 'summary_day', 'last_run_utc', 'regime')
out['A_migration'] = {
    'first_call_changed': m1, 'second_call_changed': m2, 'second_call_byte_identical': bytes1 == bytes2,
    'statuses_equal_report_status_list': stA['statuses'] == repA['brain']['status_list'],
    'previous_statuses_equal_original': stA['migrations'][notify.STATUS_MIGRATION]['previous_statuses'] == state_orig['statuses'],
    'delivery_fields_preserved': {k: stA.get(k) == state_orig.get(k) for k in keep},
    'operational_regime_present_after_migration': 'operational_regime' in stA,
}
old, new = state_orig['statuses'], repA['brain']['status_list']
out['A_reclassification'] = {
    'old_promissora_now_observacao': sorted(h for h, s in old.items() if s == 'promissora' and h not in new),
    'old_abandonada_now_observacao': sorted(h for h, s in old.items() if s == 'abandonada' and h not in new),
    'new_entries_not_in_old': {h: s for h, s in new.items() if h not in old},
    'changed_status': {h: [old[h], s] for h, s in new.items() if h in old and old[h] != s},
    'unchanged': sum(1 for h, s in new.items() if old.get(h) == s),
}

# First normal cycle against migrated state, same report (what run() would send right after cutover).
first = notify.messages(repA, copy.deepcopy(stA), NOW)
out['A_first_cycle_same_report'] = {'count': len(first), 'by_kind': categorize(first), 'texts': first}

# Counterfactual: same new report, NO migration (what the approved-before-dad9279 notifier would do).
st_nomig = copy.deepcopy(state_orig)
st_nomig['migrations'] = {notify.STATUS_MIGRATION: {'counterfactual': True}}   # blocks auto-migration
cf = notify.messages(repA, st_nomig, NOW)
out['counterfactual_no_migration'] = {'count': len(cf), 'by_kind': categorize(cf)}

# Risk scenario: OLD code (current main notify) runs on an old-format report AFTER migration.
old_src = subprocess.check_output(['git', 'show', 'origin/main:scangoal/notify.py'], text=True)
mod = types.ModuleType('scangoal.notify_old')
mod.__package__ = 'scangoal'
exec(compile(old_src, 'notify_old.py', 'exec'), mod.__dict__)
old_report = json.loads((repo / 'data' / 'report.json').read_text(encoding='utf-8'))   # production report (old code)
risk = mod.messages(old_report, copy.deepcopy(stA), NOW)
out['risk_old_code_after_migration'] = {'count': len(risk), 'by_kind': categorize(risk)}

# --- Variant B: register=True (what the first production report step does).
repB, out['B_report'] = build_into(B, True)
newB = repB['brain']['status_list']
out['B_vs_A_status_list'] = {
    'identical': newB == new,
    'only_in_B': {h: s for h, s in newB.items() if h not in new},
    'only_in_A': {h: s for h, s in new.items() if h not in newB},
    'changed': {h: [new[h], s] for h, s in newB.items() if h in new and new[h] != s},
}
# Realistic first production cycle: cutover done with A (register=False), first run builds with register=True.
firstB = notify.messages(repB, copy.deepcopy(stA), NOW)
out['first_production_cycle_A_cutover_then_register_true'] = {'count': len(firstB), 'by_kind': categorize(firstB),
                                                               'texts': [t for t in firstB if not t.startswith('A seguir')]}

prod_after = hashes(repo / 'data')
out['production_data_unchanged'] = prod_before == prod_after
out['production_data_sha256'] = prod_before
print(json.dumps(out, indent=1, ensure_ascii=False))
