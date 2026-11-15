"""PR2 before/after audit: PR1 code at BASE vs this checkout, same data snapshot, same clock. Offline; never writes data.

Usage: python scripts/audit_pr2_independence.py > audits/pr2-metrics.json
Reads data/ of this checkout (must equal BASE's data/), the Kashiwa-Kawasaki snapshots with `git show`, and the PR1 modules
with `git show BASE:...`. No report.main(), registration, notifications, collectors or network.
"""
import copy
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import types
from collections import Counter
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from scangoal import brain, insights, notify, report  # noqa: E402
from scangoal.eligibility import bet_identity, game_identity  # noqa: E402

BASE = '13ae250ccc4f14bf8a5bfa18844e68be0d6d5bc4'      # main after PR1 + first production cycles (branch point)
NOW = '2026-11-15T10:20:00+00:00'
KASHIWA_BEFORE, KASHIWA_AFTER = 'd371c4e384af70365201dfa15598649cd87bca26', 'bf3ca31474d43cd661b29c96faa18562a7df2167'
KASHIWA = ['Jogo aberto | Dia = fim de semana & Ritmo indicado pelo bot = menos de 90 & Sinal anterior de outro tipo no mesmo jogo = não',
           'Jogo aberto | Golos em falta para ganhar = até 1 golo',
           'Jogo aberto | Golos em falta para ganhar = até 1 golo & Outros sinais no mesmo jogo = jogo com um só tipo de sinal',
           'Jogo aberto | Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não']
TARGETS = KASHIWA + ['Stake | Dia = fim de semana', 'Stake | Competição = seleções', 'Polymarket | Faixa de odd = odd 1,50 a 2,00',
                     'Stake | Outros sinais no mesmo jogo = jogo com vários tipos de sinal', 'Stake | Sinal anterior de outro tipo no mesmo jogo = sim']


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromisoformat(NOW).astimezone(tz)


def blocked(*args, **kwargs):
    raise AssertionError('Network disabled for scientific audit')


def old_module(name):
    source = subprocess.check_output(['git', 'show', f'{BASE}:scangoal/{name}.py'], text=True)
    module = types.ModuleType(f'scangoal._pr1_{name}')
    module.__package__ = 'scangoal'
    exec(compile(source, f'{BASE}:{name}.py', 'exec'), module.__dict__)
    module.datetime = Clock
    return module


def digest(folder='data'):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(folder).glob('*')) if p.is_file()}


def snapshot(ref):
    folder = Path(tempfile.mkdtemp(prefix='pr2-audit-'))
    tar = subprocess.check_output(['git', 'archive', ref, 'data'])
    subprocess.run(['tar', '-x', '-C', str(folder)], input=tar, check=True)
    return folder / 'data'


def churn_old(oldbrain, oldinsights, signals, registry, bar):
    """Chronological replay with PR1 rules (no memory, n = signals, per-signal votes)."""
    multi, earlier = oldbrain.multi_matches(signals), oldbrain.earlier_index(signals)
    have = [(b, oldinsights.family(b), set(oldbrain.atoms(b, multi, earlier))) for b in signals]
    per = {}
    for hid, h in registry.items():
        conds = {tuple(c) for c in h['conditions']}
        m = [b for b, f, at in have if f == h['family'] and (h.get('source') == 'gerador' or b['date_utc'] > h['born_utc'])
             and conds <= at and b['w'] not in (None, 0)]
        if len(m) < 30:
            continue
        games = {}
        for b in m:
            games.setdefault(b['match_id'], []).append(b)
        order = sorted(games, key=lambda g: (max(oldbrain.known_at(b) for b in games[g]), str(g)))
        votes, n, state, changes = oldinsights.Votes(), 0, 'em observação', []
        for g in order:
            for b in games[g]:
                votes.add(b)
                n += 1
            if n < 30:
                continue
            hh, mm, be, _ = votes.totals()
            new = oldbrain.status_of(n, oldinsights.evidence(hh, mm, be)['prob_above'], bar, report.MIN_SETTLED)
            if new != state:
                changes.append((state, new))
                state = new
        if changes:
            per[hid] = changes
    return per


def churn_summary(per):
    return {'hypotheses_with_changes': len(per), 'total_changes': sum(len(v) for v in per.values()),
            'max_changes_one_hypothesis': max((len(v) for v in per.values()), default=0),
            'hypotheses_with_3_or_more_changes': sum(1 for v in per.values() if len(v) >= 3),
            'by_transition': dict(Counter('%s -> %s' % c for v in per.values() for c in v).most_common())}


def row_view(r):
    keep = ('n', 'games', 'hits', 'prob_above', 'status', 'roi')
    out = {k: r.get(k) for k in keep}
    if 'bets' in r:
        out.update(bets=r['bets'], pending_status=r.get('pending_status'), entry_status=r.get('entry_status'), status_changes=r.get('status_changes'))
    return out


def run_pair(data_dir, oldbrain, oldinsights, oldreport):
    os.environ['SCANGOAL_DATA_DIR'] = str(data_dir)
    registry = brain.load_registry(Path(data_dir) / 'hypotheses.json')
    t = time.perf_counter()
    before = oldreport.build(register=False)
    t_before = time.perf_counter() - t
    old_signals = copy.deepcopy(oldreport.LAST['signals'])
    oldrows, old_tested, old_bar = oldbrain.evaluate(copy.deepcopy(old_signals), registry, report.MIN_SETTLED)
    t = time.perf_counter()
    after = report.build(register=False)
    t_after = time.perf_counter() - t
    new_signals = copy.deepcopy(report.LAST['signals'])
    rows, tested, bar = brain.evaluate(copy.deepcopy(new_signals), registry, report.MIN_SETTLED)
    return before, after, oldrows, rows, (old_tested, old_bar), (tested, bar), old_signals, registry, (t_before, t_after)


def main():
    if subprocess.call(['git', 'diff', '--quiet', BASE, 'HEAD', '--', 'data']):
        raise SystemExit('data/ of this checkout differs from BASE: audit must use the pinned snapshot')
    hashes = digest()
    oldinsights, oldbrain, oldreport = old_module('insights'), old_module('brain'), old_module('report')
    oldbrain.insights = oldinsights
    oldreport.brain, oldreport.insights = oldbrain, oldinsights
    with patch.object(socket.socket, 'connect', blocked), patch.object(socket, 'create_connection', blocked), \
            patch.object(report, 'datetime', Clock), patch.object(brain, 'datetime', Clock):
        before, after, oldrows, rows, (old_tested, old_bar), (tested, bar), signals, registry, timing = run_pair(
            ROOT / 'data', oldbrain, oldinsights, oldreport)
        old_by, new_by = {r['id']: r for r in oldrows}, {r['id']: r for r in rows}
        churn = {'pr1_rules': churn_summary(churn_old(oldbrain, oldinsights, signals, registry, old_bar)),
                 'pr2_rules': churn_summary({r['id']: [(c['from'], c['to']) for c in r['status_changes']] for r in rows if r['status_changes']})}

        # Telegram: the production state at BASE (PR1 reference) receiving the first PR2 report.
        state = json.loads((ROOT / 'data' / 'notify_state.json').read_text(encoding='utf-8'))
        now = Clock.now()
        with_migration = notify.messages(after, copy.deepcopy(state), now)
        blocked_state = copy.deepcopy(state)
        blocked_state.setdefault('migrations', {})[notify.migration_key(after)] = {'counterfactual_no_migration': True}
        without_migration = notify.messages(after, blocked_state, now)
        kinds = lambda msgs: dict(Counter('status_transition' if m.startswith(('Hipótese', '✅', '⛔')) else 'other' for m in msgs))

        # Kashiwa-Kawasaki: the PR1 cycle that promoted four hypotheses on one win, re-evaluated with PR2 rules.
        kashiwa = {}
        for label, ref in (('before_win', KASHIWA_BEFORE), ('after_win', KASHIWA_AFTER)):
            folder = snapshot(ref)
            _, k_after, k_oldrows, k_rows, _, _, _, _, _ = run_pair(folder, oldbrain, oldinsights, oldreport)
            ko, kn = {r['id']: r for r in k_oldrows}, {r['id']: r for r in k_rows}
            kashiwa[label] = {'snapshot': ref, 'pr1': {h: row_view(ko[h]) for h in KASHIWA}, 'pr2': {h: row_view(kn[h]) for h in KASHIWA}}
        os.environ['SCANGOAL_DATA_DIR'] = str(ROOT / 'data')

    changed = {h: {'pr1': old_by[h]['status'], 'pr2': new_by[h]['status'], 'n': new_by[h]['n'], 'games': new_by[h]['games'],
                   'prob_above_pr1': old_by[h]['prob_above'], 'prob_above_pr2': new_by[h]['prob_above'], 'pending_pr2': new_by[h]['pending_status']}
               for h in sorted(new_by) if h in old_by and old_by[h]['status'] != new_by[h]['status']}
    prob_moved = sum(1 for h in new_by if h in old_by and old_by[h]['prob_above'] != new_by[h]['prob_above'])
    wf = lambda rep: {k: rep['brain']['walk_forward'][k] for k in ('followed', 'skipped', 'prob_followed_better')}
    exploratory = after['brain']['eligibility']
    identity = {
        'eligible_without_match_id': sum(1 for b in signals if game_identity(b) is None),
        'eligible_without_selection': sum(1 for b in signals if bet_identity(b)[1] is None),
        'eligible_decided_without_match_id': sum(1 for b in signals if b['w'] not in (None, 0) and game_identity(b) is None),
        'all_signals_excluded_for_identity': {k: exploratory['reasons'].get(k, 0) for k in ('no_match_identity', 'no_selection')},
        'unidentified_in_brain_rows': sum(r.get('unidentified', 0) for r in rows),
        'note': 'Fail-closed since review #5910343843: no match_id -> ineligible and no vote; no selection -> ineligible.'}
    groups_same_roi = all(before['groups'][g]['roi'] == after['groups'][g]['roi'] for g in before['groups']) if before['groups'].keys() == after['groups'].keys() else False
    assert hashes == digest(), 'Historical data changed during the audit'
    assert before['accuracy'] == after['accuracy'], 'Exploratory accuracy changed'
    assert before['operational']['regime'] == after['operational']['regime'], 'Operational regime changed'
    print(json.dumps({
        'baseline_commit': BASE, 'clock_utc': NOW, 'data_sha256': hashes, 'python': sys.version.split()[0],
        'rules': {'pr1': 'n = signals, stateless thresholds', 'pr2': after['brain']['rules']},
        'statuses': {'pr1': before['brain']['counts'], 'pr2': after['brain']['counts'],
                     'pr1_exploratory': before['brain']['exploratory']['counts'], 'pr2_exploratory': after['brain']['exploratory']['counts'],
                     'pending_pr2': dict(Counter(r['pending_status'] for r in rows if r['pending_status'])),
                     'held_by_hysteresis_pr2': {r['id']: {'status': r['status'], 'entry_status': r['entry_status'], 'prob_above': r['prob_above'], 'games': r['games']}
                                                for r in rows if r['status'] != r['entry_status'] and not r['pending_status']}},
        'tested_hypotheses': {'pr1_n_signals_ge_30': old_tested, 'pr2_games_ge_30': tested, 'pr1_bar': round(old_bar, 6), 'pr2_bar': round(bar, 6)},
        'status_changes_pr1_to_pr2': changed, 'hypotheses_with_changed_prob_above': prob_moved,
        'historical_churn_replay': churn,
        'walk_forward': {'pr1': wf(before), 'pr2': wf(after)},
        'board_best': {'pr1': [c['id'] for c in before['brain']['board']['best']], 'pr2': [c['id'] for c in after['brain']['board']['best']]},
        'agenda_counts': {'pr1': before['brain']['agenda']['counts'], 'pr2': after['brain']['agenda']['counts']},
        'targets': {h: {'pr1': row_view(old_by[h]), 'pr2': row_view(new_by[h])} for h in TARGETS if h in new_by},
        'kashiwa_kawasaki': kashiwa,
        'identity': identity,
        'telegram_first_pr2_report_on_production_state': {'with_silent_rules_migration': kinds(with_migration),
                                                          'counterfactual_without_migration': kinds(without_migration),
                                                          'migration_key': notify.migration_key(after)},
        'report_build_seconds': {'pr1': round(timing[0], 2), 'pr2': round(timing[1], 2)},
        'checks': {'historical_data_unchanged': True, 'exploratory_accuracy_unchanged': True, 'operational_regime_unchanged': True,
                   'strict_group_roi_unchanged': groups_same_roi},
        'interpretation': 'Historical sensitivity analysis on one snapshot. Fewer status changes measure stability, not predictive power; '
                          'the walk-forward reuses the current registry and the 150-minute approximation (PR 3 scope).'}, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
