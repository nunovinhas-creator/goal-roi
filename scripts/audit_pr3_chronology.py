"""PR 3 offline audit: chronological validity, prospective evidence and multiple testing under dependence.

Usage: python scripts/audit_pr3_chronology.py > audits/pr3-metrics.json
Reads data/ of this checkout (must equal BASE's data/; hashes are checked and printed), PR 2 modules with `git show BASE:...`
and the git history of data/ (full clone needed: `git fetch --unshallow`). No report.main(), no registration, no notify.run(),
no collectors, no network (sockets are blocked), never writes data/. Fixed clock and fixed seeds.
"""
import collections
import contextlib
import copy
import hashlib
import json
import math
import os
import random
import socket
import subprocess
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from scangoal import brain, certification, chronology, insights, notify, report  # noqa: E402
from scangoal.eligibility import eligible, game_identity  # noqa: E402

BASE = '82ab9d86593055fbc98420265a469728d914288f'       # main with PR 2 at the start of PR 3 (data snapshot and PR 2 code)
FIRST_PR3 = 'ef4606a93d7c576f64a66afc186f9a3d6a2558bf'  # PR 3 as audited in review #5911487915 (Bonferroni/posterior tails)
SECOND_PR3 = 'd946ffdf33198610277ffcf25e42d818ca0bef79' # PR 3 as audited in review #5911603577 (replayed selection order)
NOW = '2026-11-15T14:34:34+00:00'                       # BASE commit time: the report clock for both versions
UTC = timezone.utc
SIMULATIONS, SEED = 400, 20261115
BOOTSTRAP = 2000


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromisoformat(NOW).astimezone(tz)


def blocked(*args, **kwargs):
    raise AssertionError('Network disabled for scientific audit')


def git(*args):
    return subprocess.check_output(['git', *args], text=True)


def digest(folder='data'):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(folder).glob('*')) if p.is_file()}


def base_digest():
    out = {}
    for name in git('ls-tree', '--name-only', BASE, 'data/').split():
        out[Path(name).name] = hashlib.sha256(subprocess.check_output(['git', 'show', '%s:%s' % (BASE, name)])).hexdigest()
    return out


def old_module(name, base=BASE, **extra):
    source = git('show', '%s:scangoal/%s.py' % (base, name))
    module = types.ModuleType('scangoal._%s_%s' % (base[:7], name))
    module.__package__ = 'scangoal'
    exec(compile(source, '%s:%s.py' % (base, name), 'exec'), module.__dict__)
    module.datetime = Clock
    module.__dict__.update(extra)
    return module


@contextlib.contextmanager
def clock():
    with patch.object(report, 'datetime', Clock), patch.object(brain, 'datetime', Clock):
        yield


def build(rep_module):
    t = time.perf_counter()
    out = rep_module.build(None, register=False)
    return out, time.perf_counter() - t


def signals_of(rep_module):
    """The strict=False signals of a report build (the population the brain sees)."""
    cap = {}
    orig = rep_module.build_bets

    def spy(*a, **k):
        res = orig(*a, **k)
        if k.get('strict') is False:
            cap['signals'] = res
        return res
    with patch.object(rep_module, 'build_bets', spy), patch.object(rep_module.brain, 'build', lambda *a, **k: {'top': []}), \
            patch.object(rep_module.brain, 'entry_points', lambda s: []):
        rep_module.build(None, register=False)
    return cap['signals']


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))] if xs else None


def legacy_known(b):
    ref = datetime.fromisoformat(b['date_utc'])
    if b.get('kickoff_utc'):
        ko = datetime.fromisoformat(b['kickoff_utc'])
        ref = max(ref, ko if ko.tzinfo else ko.replace(tzinfo=UTC))
    return ref + timedelta(minutes=150)


# --------------------------------------------------------------------------------------------------------- chronology
def chronology_evidence(signals):
    elig = [b for b in signals if eligible(b)]
    res = [b for b in elig if b['w'] is not None and b['w'] != 0]
    checks = collections.Counter(chronology.result_check(b)[1] for b in res)
    diffs = [((chronology.result_known(b) - legacy_known(b)).total_seconds() / 60, b) for b in res if chronology.result_known(b)]
    late = [(d, b) for d, b in diffs if d > 0]
    lag = [(chronology.decision_time(b) - chronology.signal_time(b)).total_seconds() / 60 for b in elig if chronology.decision_time(b)]
    priced = [b for b in elig if b.get('odds') and b.get('market')]
    not_dec = [b for b in priced if not chronology.decidable(b)]
    return {
        'eligible_signals': len(elig), 'eligible_with_decided_result': len(res),
        'result_time_check': dict(checks), 'result_source': dict(collections.Counter(b['result_known_source'] for b in res)),
        'observed_minus_pr2_approximation_min': {'p0': round(q([d for d, _ in diffs], 0)), 'p10': round(q([d for d, _ in diffs], .1)),
                                                 'p50': round(q([d for d, _ in diffs], .5)), 'p90': round(q([d for d, _ in diffs], .9)),
                                                 'max': round(q([d for d, _ in diffs], 1))},
        'results_known_later_than_pr2_assumed': len(late),
        'late_examples': [{'date_utc': b['date_utc'], 'kickoff_utc': b.get('kickoff_utc'), 'pr2_known': legacy_known(b).isoformat(),
                           'observed': chronology.iso(chronology.result_known(b)), 'source': b['result_known_source'], 'late_min': round(d)}
                          for d, b in sorted(late, key=lambda x: -x[0])[:8]],
        'signal_read_lag_min': {'p50': round(q(lag, .5), 2), 'p90': round(q(lag, .9), 2), 'max': round(q(lag, 1), 1)},
        'signals_read_before_sent_by_seconds': sum(1 for b in elig if chronology.parse_utc(b.get('signal_seen_utc'))
                                                   and chronology.parse_utc(b['signal_seen_utc']) < chronology.signal_time(b)),
        'priced_signals': len(priced), 'not_decidable': len(not_dec),
        'not_decidable_by_read_day': dict(sorted(collections.Counter((b.get('signal_seen_utc') or 'none')[:10] for b in not_dec).items())),
    }


def git_timestamps():
    """Every stored timestamp against the commit that first published it (an independent clock: GitHub push history)."""
    out = {}
    # messages.jsonl: append-only; each added line is a version with collected_at_utc.
    first = {}
    log = git('log', '--reverse', '-p', '--format=COMMIT %H %cI', BASE, '--', 'data/messages.jsonl')
    commit_time = None
    for line in log.splitlines():
        if line.startswith('COMMIT '):
            commit_time = datetime.fromisoformat(line.split()[2])
        elif line.startswith('+{'):
            r = json.loads(line[1:])
            first.setdefault((r['message_id'], r['sha256']), (commit_time, r))
    gaps = [(t - datetime.fromisoformat(r['collected_at_utc'])).total_seconds() / 60 for t, r in first.values()]
    out['message_versions'] = {'checked': len(first), 'collected_after_commit': sum(1 for g in gaps if g < 0),
                               'commit_minus_collected_min': {'p50': round(q(gaps, .5), 1), 'p90': round(q(gaps, .9), 1), 'max': round(q(gaps, 1), 1)},
                               'edit_after_collected': sum(1 for _, r in first.values() if r.get('edit_date_utc') and r['edit_date_utc'] > r['collected_at_utc'])}
    # hypotheses.json: born_utc against the first commit containing the hypothesis.
    seen = {}
    for line in git('log', '--reverse', '--format=%H %cI', BASE, '--', 'data/hypotheses.json').splitlines():
        sha, when = line.split()
        try:
            reg = json.loads(subprocess.check_output(['git', 'show', '%s:data/hypotheses.json' % sha]))
        except ValueError:
            continue
        for hid, h in reg.items():
            seen.setdefault(hid, (datetime.fromisoformat(when), h.get('born_utc')))
    gaps = [(t - chronology.parse_utc(born)).total_seconds() / 60 for t, born in seen.values() if chronology.parse_utc(born)]
    out['hypotheses'] = {'checked': len(seen), 'invalid_born': sum(1 for _, b in seen.values() if not chronology.parse_utc(b)),
                         'born_after_first_commit': sum(1 for g in gaps if g < 0),
                         'commit_minus_born_min': {'p50': round(q(gaps, .5), 1), 'p90': round(q(gaps, .9), 1), 'max': round(q(gaps, 1), 1)},
                         'born_changed_later': 0}
    current = json.loads(subprocess.check_output(['git', 'show', '%s:data/hypotheses.json' % BASE]))
    out['hypotheses']['born_changed_later'] = sum(1 for hid, (_, b) in seen.items() if hid in current and current[hid].get('born_utc') != b)
    out['hypotheses']['removed_later'] = sum(1 for hid in seen if hid not in current)
    for name, field in (('results_hl', 'matched_at_utc'), ('results_bsd', 'matched_at_utc'), ('details_hl', 'fetched_at_utc')):
        seen = {}
        for line in git('log', '--reverse', '--format=%H %cI', BASE, '--', 'data/%s.json' % name).splitlines():
            sha, when = line.split()
            try:
                st = json.loads(subprocess.check_output(['git', 'show', '%s:data/%s.json' % (sha, name)]))
            except ValueError:
                continue
            for mid, r in (st.get('results') or st.get('details') or {}).items():
                seen.setdefault(mid, (datetime.fromisoformat(when), r.get(field)))
        gaps = [(t - chronology.parse_utc(v)).total_seconds() / 60 for t, v in seen.values() if chronology.parse_utc(v)]
        out[name] = {'checked': len(seen), 'missing_stamp': sum(1 for _, v in seen.values() if not chronology.parse_utc(v)),
                     'stamp_after_first_commit': sum(1 for g in gaps if g < 0),
                     'commit_minus_stamp_min': {'p50': round(q(gaps, .5), 1), 'p90': round(q(gaps, .9), 1), 'max': round(q(gaps, 1), 1)} if gaps else None}
    return out


# ------------------------------------------------------------------------------------------------- PR 2 walk-forward leaks
def legacy_walk_forward_trace(signals, registry):
    """PR 2 walk-forward logic, instrumented: what each decision used that the system did not have."""
    signals = [b for b in signals if eligible(b)]
    multi, earlier = brain.multi_matches(signals), brain.earlier_index(signals)
    hyps = [(hid, h['family'], {tuple(c) for c in h['conditions']}, None if h.get('source') == 'gerador' else h['born_utc'], chronology.born(h))
            for hid, h in registry.items() if h['conditions'] and not any(c[0] in brain.POST for c in h['conditions'])]
    fits = {}
    for b in signals:
        fam, at = insights.family(b), set(brain.atoms(b, multi, earlier))
        fits[id(b)] = [i for i, (_, f, conds, born, _) in enumerate(hyps) if f == fam and conds <= at and (born is None or b['date_utc'] > born)]
    results = sorted((b for b in signals if b['w'] is not None and b['w'] != 0), key=legacy_known)
    votes, counts, pos = [insights.Votes() for _ in hyps], [0] * len(hyps), 0
    used = [[] for _ in hyps]
    stats = collections.Counter()
    for b in sorted(signals, key=lambda b: b['date_utc']):
        if not b.get('odds') or b.get('market') is None:
            continue
        when = datetime.fromisoformat(b['date_utc'])
        while pos < len(results) and legacy_known(results[pos]) <= when:
            for i in fits[id(results[pos])]:
                votes[i].add(results[pos])
                counts[i] += 1
                used[i].append(results[pos])
            pos += 1
        support = []
        for i in fits[id(b)]:
            if counts[i] >= brain.BOARD_MIN:
                h, m, be, games = votes[i].totals()
                if games >= brain.BOARD_MIN and (insights.evidence(h, m, be)['prob_above'] or 0) >= brain.BOARD_GOOD:
                    support.append(i)
        if not support:
            continue
        stats['followed'] += 1
        decide = chronology.decision_time(b)
        if not chronology.decidable(b):
            stats['followed_not_reconstructible'] += 1
        if all(hyps[i][4] is None or decide is None or hyps[i][4] > decide for i in support):
            stats['followed_only_by_hypotheses_not_yet_registered'] += 1
        unseen = {id(r) for i in support for r in used[i] if chronology.result_known(r) is None or chronology.result_known(r) > when}
        if unseen:
            stats['followed_with_results_not_yet_observed'] += 1
            stats['unobserved_results_used_max'] = max(stats['unobserved_results_used_max'], len(unseen))
    return dict(stats)


# ------------------------------------------------------------------------------------------------------------- overlap
def overlap(signals, registry, rows_by_id):
    elig = [b for b in signals if eligible(b)]
    multi, earlier = brain.multi_matches(elig), brain.earlier_index(elig)
    have = [(b, insights.family(b), set(brain.atoms(b, multi, earlier))) for b in elig]
    sets = {}
    for hid, r in rows_by_id.items():
        if r['games'] < brain.HINT_AT:
            continue
        h = registry[hid]
        conds, born, gen = {tuple(c) for c in h['conditions']}, chronology.born(h), h.get('source') == 'gerador'
        match = [b for b, fam, at in have if fam == h['family'] and conds <= at and (gen or (born and chronology.signal_time(b) > born))]
        sets[hid] = brain.game_ids(match)
    ids = sorted(sets)
    jac = lambda a, b: len(sets[a] & sets[b]) / len(sets[a] | sets[b])
    pairs = [jac(a, b) for i, a in enumerate(ids) for b in ids[i + 1:]]

    def components(members, thr):
        parent = {m: m for m in members}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if jac(a, b) >= thr:
                    parent[find(a)] = find(b)
        return len({find(m) for m in members})
    good = sorted(h for h in ids if rows_by_id[h]['status'] in ('promissora', 'forte', 'confirmada'))
    gpairs = [jac(a, b) for i, a in enumerate(good) for b in good[i + 1:]]
    families = collections.Counter(registry[h]['family'] for h in ids)
    return {'tested': len(ids), 'tested_by_family': dict(families), 'all_games_in_tested': len(set().union(*sets.values())) if sets else 0,
            'pairwise_jaccard_tested': {'p50': round(q(pairs, .5), 3), 'p90': round(q(pairs, .9), 3), 'share_ge_0_5': round(sum(p >= .5 for p in pairs) / len(pairs), 3)},
            'clusters_tested_by_threshold': {str(t): components(ids, t) for t in (0.5, 0.8, 0.9, 1.0)},
            'promising': len(good), 'promising_pairwise_jaccard_p50': round(q(gpairs, .5), 3) if gpairs else None,
            'promising_clusters_by_threshold': {str(t): components(good, t) for t in (0.5, 0.8, 0.9, 1.0)} if good else {},
            'promising_union_games': len(set().union(*(sets[h] for h in good))) if good else 0,
            'promising_sum_games': sum(len(sets[h]) for h in good)}, sets


# --------------------------------------------------------------------------------------------- null with real dependence
def null_simulation(signals, registry, rows_by_id, sets):
    """Parametric null with the REAL overlap: every (game, bet) is redrawn as a win with probability 1/odds (no edge), once
    per simulation, and shared by every hypothesis that contains it. Then the same statistics as the brain are computed.

    Gives: how many tested hypotheses reach prob >= 0.95 by chance (vs. observed), and the max-statistic (Westfall-Young)
    threshold for FWER 5 % under this dependence, to compare with Bonferroni. Within-game correlation between different
    markets is not modelled (draws are independent per bet; the game vote averages them).
    """
    elig = [b for b in signals if eligible(b) and b['w'] is not None and b['w'] != 0 and b.get('odds')]
    multi, earlier = brain.multi_matches([b for b in signals if eligible(b)]), brain.earlier_index([b for b in signals if eligible(b)])
    tested = sorted(h for h in rows_by_id if rows_by_id[h]['games'] >= brain.HINT_AT)
    members = {}
    for b in elig:
        fam, at = insights.family(b), set(brain.atoms(b, multi, earlier))
        for h in tested:
            reg = registry[h]
            born, gen = chronology.born(reg), reg.get('source') == 'gerador'
            if reg['family'] == fam and {tuple(c) for c in reg['conditions']} <= at and (gen or (born and chronology.signal_time(b) > born)):
                members.setdefault(h, []).append(b)
    bets = sorted({(game_identity(b), b.get('market'), b.get('selection')) for b in elig}, key=str)
    odds = {}
    for b in elig:
        odds.setdefault((game_identity(b), b.get('market'), b.get('selection')), b['odds'])
    rng = random.Random(SEED)
    observed = sum(1 for h in tested if (rows_by_id[h]['prob_above'] or 0) >= brain.PROMOTE_AT)
    counts, maxima, clusters = [], [], []
    for _ in range(SIMULATIONS):
        draw = {k: (1.0 if rng.random() < 1 / odds[k] else -1.0) for k in bets}
        hit = []
        best_tail = 1.0
        for h in tested:
            v = insights.Votes()
            for b in members.get(h, ()):
                v.add(dict(b, w=draw[(game_identity(b), b.get('market'), b.get('selection'))]))
            hh, mm, be, n = v.totals()
            tail = insights.tail_below(hh, mm, be)
            if tail is not None and 1 - tail >= brain.PROMOTE_AT:
                hit.append(h)
            if tail is not None:
                best_tail = min(best_tail, tail)
        counts.append(len(hit))
        maxima.append(best_tail)
        clusters.append(len({frozenset(sets[h]) for h in hit}))
    counts.sort()
    return {'simulations': SIMULATIONS, 'seed': SEED, 'tested': len(tested),
            'observed_tested_with_prob_ge_0_95': observed,
            'null_count_ge_0_95': {'mean': round(sum(counts) / len(counts), 2), 'p50': q(counts, .5), 'p95': q(counts, .95), 'p99': q(counts, .99),
                                   'share_ge_observed': round(sum(c >= observed for c in counts) / len(counts), 3),
                                   'share_zero': round(sum(c == 0 for c in counts) / len(counts), 3)},
            'bonferroni_expected_independent': round(0.05 * len(tested), 1),
            'westfall_young_tail_threshold_fwer_5pct': q(maxima, 0.05),
            'bonferroni_tail_threshold_single_look': 0.05 / len(tested),
            'note': 'promising-by-chance counts come in clumps: same games, same draws'}


def cluster_bootstrap_roi(rows):
    """Bootstrap by game of the ROI of a set of bets (fixed seed): 90 % interval."""
    done = [b for b in rows if b['w'] is not None]
    by = collections.defaultdict(list)
    for b in done:
        by[b['match_id']].append(b['w'] * (b['odds'] - 1) if b['w'] > 0 else b['w'])
    keys = sorted(by)
    if len(keys) < 5:
        return None
    rng, est = random.Random(SEED), []
    for _ in range(BOOTSTRAP):
        pick = [by[keys[rng.randrange(len(keys))]] for _ in keys]
        n = sum(len(p) for p in pick)
        est.append(sum(sum(p) for p in pick) / n)
    return [round(q(est, .05), 4), round(q(est, .95), 4)]


def wf_split(signals, registry):
    """Followed / skipped lists of the PR 3 walk-forward (re-run with a tap on the decisions)."""
    out = {True: [], False: []}
    orig = insights.prob_better

    def tap(a, b):
        out[True], out[False] = list(a), list(b)
        return orig(a, b)
    with patch.object(insights, 'prob_better', tap):
        brain.walk_forward(signals, registry)
    return out


def sensitivity(signals, registry):
    out = {}
    base = brain.walk_forward(signals, registry)
    pick = lambda wf: {'followed': wf['followed']['bets'], 'followed_settled': wf['followed']['settled'], 'roi_followed': wf['followed']['roi'],
                       'roi_cut_followed': wf['followed']['roi_cut'], 'roi_skipped': wf['skipped']['roi'], 'not_reconstructible': wf['not_reconstructible']['bets'],
                       'prob_followed_better': wf['prob_followed_better']}
    out['pr3'] = pick(base)
    for m in (0, 15, 60):
        with patch.object(chronology, 'OBSERVATION_MARGIN_MIN', m):
            out['observation_margin_%d' % m] = pick(brain.walk_forward(signals, registry))
    for d in (0, 5):
        with patch.object(chronology, 'DECISION_DELAY_MAX_MIN', d):
            out['decision_delay_%d' % d] = pick(brain.walk_forward(signals, registry))
    # Ablations: which correction changes what.
    early = datetime(2000, 1, 1, tzinfo=UTC)
    with patch.object(chronology, 'born', lambda h: early if isinstance(h, dict) and h.get('source') == 'gerador' else chronology.parse_utc(h.get('born_utc'))):
        out['ablation_without_registration_gate'] = pick(brain.walk_forward(signals, registry))
    with patch.object(brain, 'known_at', legacy_known):
        out['ablation_pr2_result_times'] = pick(brain.walk_forward(signals, registry))
    with patch.object(chronology, 'decidable', lambda b: True), patch.object(chronology, 'decision_time', chronology.signal_time):
        out['ablation_decide_every_signal_at_send_time'] = pick(brain.walk_forward(signals, registry))
    return out


def notify_rehearsal(new_report):
    state = json.loads((ROOT / 'data' / 'notify_state.json').read_text(encoding='utf-8'))
    now = datetime.fromisoformat(NOW)
    without = copy.deepcopy(state)
    old, new = without.get('statuses') or {}, notify.eligible_statuses(new_report)
    would = sum(1 for h in set(old) | set(new) if old.get(h) != new.get(h))
    msgs = notify.messages(new_report, copy.deepcopy(state), now)
    after = copy.deepcopy(state)
    notify.messages(new_report, after, now)
    status_msgs = [m for m in msgs if m.startswith(('Hipótese', '✅ Tática', '⛔ Tática'))]
    return {'migration_key': notify.migration_key(new_report), 'markers_before': sorted(state.get('migrations') or {}),
            'markers_after': sorted(after.get('migrations') or {}), 'status_messages_with_migration': len(status_msgs),
            'status_changes_without_migration': would,
            'previous_markers_intact': all((after.get('migrations') or {}).get(k) == v for k, v in (state.get('migrations') or {}).items()),
            'note': 'messages() only; nothing sent, nothing written'}


def synthetic(rates, seed, odds=2.0):
    """One signal per game, read when sent, result observed 145 min later; family-wide hypothesis registered before."""
    rng, t0, out = random.Random(seed), datetime(2026, 11, 20, tzinfo=UTC), []
    for i, p in enumerate(rates):
        d = (t0 + timedelta(hours=i)).isoformat()
        w = 1.0 if rng.random() < p else -1.0
        out.append({'match_id': 'g%d' % i, 'w': w, 'odds': odds, 'type': 'OPEN_GAME', 'phase': 'live', 'market': 'Asian Total', 'selection': 'Over 2.5',
                    'date_utc': d, 'kickoff_utc': None, 'minute': 50, 'score_at_signal': [1, 0], 'league': 'Liga', 'teams': ['A', 'B'], 'in_roi': True,
                    'key': ('g%d' % i,), 'n_signals': 1, 'max_mult': 1, 'no_source': False, 'share': None, 'total': None, 'n_bets': None,
                    'signal_seen_utc': d, 'result_known_utc': (t0 + timedelta(hours=i, minutes=145)).isoformat(),
                    'match_result_first_seen_utc': (t0 + timedelta(hours=i, minutes=145)).isoformat()})
    return out


def validation_rates(old_brain, first_pr3_brain):
    """Share of runs where a single hypothesis is EVER validated (forte/confirmada), per rule. Parameters fixed a priori.

    At odds 2.0 the break-even rate is 0.50; at odds -2 % (1.96) it is 0.5102. "no edge" uses 0.50 (profit at odds -2 % < 0).
    """
    reg = {'h': {'family': 'Jogo aberto', 'conditions': [], 'born_utc': '2026-11-19T00:00:00+00:00', 'source': 'gerador'}}
    cases = [('no_edge_p0.50_160_games', [0.5] * 160, 200), ('no_edge_p0.50_300_games', [0.5] * 300, 200),
             ('no_edge_p0.50_1000_games', [0.5] * 1000, 60), ('edge_p0.60_300_games', [0.6] * 300, 200),
             ('edge_p0.60_1000_games', [0.6] * 1000, 60), ('edge_p0.75_160_games', [0.75] * 160, 200)]
    led = certification.activate({}, datetime(2026, 11, 18, tzinfo=UTC))
    certification.append(led, ['h'], reg, datetime(2026, 11, 19, 12, tzinfo=UTC))     # registered before the first game
    selected = certification.selections(led, reg)[0]
    current = types.SimpleNamespace(evaluate=lambda sigs, r, m: brain.evaluate(sigs, r, m, selected=selected))
    rules = {'pr2': old_brain, 'pr3_ef4606a_bonferroni': first_pr3_brain, 'pr3_eprocess_with_ledger': current}
    out = {}
    for name, rates, seeds in cases:
        row = {'seeds': seeds}
        for rule, mod in rules.items():
            hits = 0
            for seed in range(seeds):
                r = mod.evaluate(synthetic(rates, seed), reg, 300)[0][0]
                hits += r['status'] in ('forte', 'confirmada') or any(c['to'] in ('forte', 'confirmada') for c in r['status_changes'])
            row[rule] = round(hits / seeds, 3)
        out[name] = row
    return {'tested_family': 1, 'rates_ever_validated': out,
            'note': 'one hypothesis (ledger rank 1, alpha 0.025 for the e-process): isolates repeated looks; pr2 uses 1 - 0.05/m with rounded prob_above'}


def _sig(match, w, hour, *, seen=0, known=145, first=None, typ='OPEN_GAME', market='Asian Total', selection='Over 2.5', odds=2.0):
    t0 = datetime(2026, 11, 20, tzinfo=UTC)
    at = lambda m: (t0 + timedelta(minutes=m)).isoformat()
    first = known if first is None else first
    return {'match_id': match, 'w': w, 'odds': odds, 'type': typ, 'phase': 'live', 'market': market, 'selection': selection,
            'date_utc': at(hour * 60), 'kickoff_utc': None, 'minute': 50, 'score_at_signal': [1, 0], 'league': 'Liga', 'teams': ['A', 'B'],
            'in_roi': True, 'key': (match, market, selection, hour), 'n_signals': 1, 'max_mult': 1, 'no_source': False, 'share': None,
            'total': None, 'n_bets': None, 'signal_seen_utc': at(hour * 60 + seen), 'result_known_utc': at(hour * 60 + known),
            'match_result_first_seen_utc': at(hour * 60 + first)}


def counterexamples(second_brain):
    """The review's counterexamples executed against d946ffd and against this code (synthetic, deterministic)."""
    out, early = {}, datetime(2026, 11, 19, tzinfo=UTC)
    pattern = ([1.0, 1.0, 1.0, -1.0] * 26)[:102]
    hyp = lambda family='Jogo aberto', conds=(('Minuto', '31 a 60'),): {'family': family, 'conditions': [list(c) for c in conds],
                                                                         'born_utc': '2026-11-01T00:00:00+00:00', 'source': 'gerador'}
    # P1-D: result seen 4 min after the signal, signal read at 8 min (still decidable).
    games = [_sig('d%d' % i, w, i, seen=8, known=4) for i, w in enumerate(pattern[:40])]
    ordered = brain.ordered_games(games)
    out['P1-D_games_counted'] = {'d946ffd': second_brain.confirmation(ordered, early, 0.025)['games'], 'current': brain.confirmation(ordered, early, 0.025)['games']}
    # P1-C: label "one signal type" flipped by later signals of another family.
    reg = {'post': hyp(conds=(('Outros sinais no mesmo jogo', 'jogo com um só tipo de sinal'),))}
    own = [_sig('g%d' % i, w, i) for i, w in enumerate(pattern)]
    later = [_sig('g%d' % i, 1.0, i + 1, typ='POLYMARKET', market='1X2', selection='1') for i in range(0, 102, 2)]
    conf = lambda mod, data: (mod.evaluate(data, reg, 300)[0][0].get('confirmation') or {})
    out['P1-C_post_condition'] = {'d946ffd_games_before_after': [conf(second_brain, own).get('games'), conf(second_brain, own + later).get('games')],
                                  'current_certifiable': brain.certifiable(reg['post'])}
    # P1-E: extra bets with late results in A's games move A's replayed first promotion after B's: ranks swap in d946ffd.
    reg2 = {'A': hyp(), 'B': hyp('Polymarket', (('Momento', 'durante o jogo'),))}
    a = [_sig('a%d' % i, 1.0, i) for i in range(45)]
    b = [_sig('b%d' % i, 1.0, i + 6, typ='POLYMARKET', market='1X2', selection='1') for i in range(45)]
    late = [_sig('a%d' % i, 1.0, i, selection='Over 1.5', known=4 * 24 * 60) for i in range(45)]
    ranks = lambda data: {r['id']: (r.get('confirmation') or {}).get('rank') for r in second_brain.evaluate(data, reg2, 300)[0]}
    led = certification.activate({}, early - timedelta(days=1))
    certification.append(led, ['B'], reg2, early)
    certification.append(led, ['A'], reg2, early + timedelta(hours=1))
    cur = lambda data: {r['id']: (r.get('confirmation') or {}).get('rank') for r in brain.evaluate(data, reg2, 300, selected=certification.selections(led, reg2)[0])[0]}
    out['P1-E_ranks'] = {'d946ffd_before': ranks(a + b), 'd946ffd_after_late_bets': ranks(a + b + late),
                         'current_before': cur(a + b), 'current_after_late_bets': cur(a + b + late)}
    # P2: 5000 winning games at odds 3.0.
    wins = brain.ordered_games([_sig('w%d' % i, 1.0, i, odds=3.0) for i in range(5000)])
    try:
        second_brain.confirmation(wins, early, 0.001)
        out['P2_d946ffd'] = 'ok'
    except OverflowError as exc:
        out['P2_d946ffd'] = 'OverflowError: %s' % exc
    c = brain.confirmation(wins, early, 0.001)
    out['P2_current'] = {'games': c['games'], 'log10_e': c['log10_e'], 'e_value': c['e_value'], 'certified': c['certified_at'] is not None}
    return out


def review_5911603577(signals, registry, rows_by_id, second_brain):
    """P1-C/D/E on the snapshot: certifiable hypotheses, bets decided after a result was seen, the d946ffd replayed selection
    order versus the current (not activated) ledger, and a RETROSPECTIVE e-process simulation that is NOT a certificate."""
    elig = [b for b in signals if eligible(b)]
    multi, earlier = brain.multi_matches(elig), brain.earlier_index(elig)
    have = [(b, insights.family(b), set(brain.atoms(b, multi, earlier))) for b in elig]
    tested = {h: r for h, r in rows_by_id.items() if r['games'] >= brain.HINT_AT}
    promising = sorted(h for h, r in rows_by_id.items() if r['status'] == 'promissora')
    out = {'tested': len(tested), 'tested_certifiable': sum(brain.certifiable(registry[h]) for h in tested),
           'promising': len(promising), 'promising_certifiable': sum(brain.certifiable(registry[h]) for h in promising),
           'promising_not_certifiable': [h for h in promising if not brain.certifiable(registry[h])]}
    # P1-D: bets whose game result the system had seen before the latest moment the bet could be decided.
    bets = [b for b in elig if b['w'] not in (None, 0)]
    first = [brain.first_result_seen([b]) for b in bets]
    out['settled_bets'] = len(bets)
    out['bets_first_result_unproven'] = sum(f is None for f in first)
    out['bets_decided_after_first_result'] = sum(1 for b, f in zip(bets, first) if f is not None and brain.latest_decision(b) is not None
                                                 and brain.latest_decision(b) >= f)
    out['bets_decided_after_first_result_but_d946ffd_operational'] = sum(
        1 for b, f in zip(bets, first) if f is not None and brain.latest_decision(b) is not None and brain.latest_decision(b) >= f
        and second_brain.operational_bet(b, datetime(2000, 1, 1, tzinfo=UTC)))
    # d946ffd: prospective games and the replayed selection order; current: categories and the ledger state.
    old_rows = {r['id']: r for r in second_brain.evaluate(signals, registry, report.MIN_SETTLED)[0]}
    out['d946ffd_prospective_games_tested'] = sum((old_rows[h].get('prospective') or {}).get('games', 0) for h in tested)
    out['current_prospective_games_tested'] = sum((rows_by_id[h].get('prospective') or {}).get('games', 0) for h in tested)
    out['d946ffd_selection_order'] = [{'rank': r['confirmation']['rank'], 'id': h, 'selected_utc': r['confirmation']['selected_utc']}
                                      for h, r in sorted(old_rows.items(), key=lambda kv: (kv[1].get('confirmation') or {}).get('rank') or 1e9)
                                      if r.get('confirmation')]
    out['legacy_at_activation_if_activated_now'] = promising
    # Retrospective simulation (NOT a certificate): what the e-process would show from each replayed first promotion.
    sim = []
    for j, item in enumerate(out['d946ffd_selection_order'], 1):
        h = item['id']
        reg = registry[h]
        conds, born, gen = {tuple(c) for c in reg['conditions']}, chronology.born(reg), reg.get('source') == 'gerador'
        match = [b for b, fam, at in have if fam == reg['family'] and conds <= at and (gen or (born and chronology.signal_time(b) > born))]
        c = brain.confirmation(brain.ordered_games(match), chronology.parse_utc(item['selected_utc']), brain.alpha_for_rank(j))
        sim.append({'id': h, 'certifiable': brain.certifiable(reg), 'games': c['games'], 'log10_e': c['log10_e'], 'threshold': c['threshold'],
                    'would_certify': c['certified_at'] is not None})
    out['retrospective_simulation_not_a_certificate'] = sim
    return out


def prospective_definitions(signals, registry, rows_by_id):
    """Review #5911487915 P1-A/P1-B: prospective games under the ef4606a definitions and under the unified one."""
    elig = [b for b in signals if eligible(b)]
    multi, earlier = brain.multi_matches(elig), brain.earlier_index(elig)
    have = [(b, insights.family(b), set(brain.atoms(b, multi, earlier))) for b in elig]
    old_seen = lambda b, born: chronology.decision_time(b) is not None and chronology.decision_time(b) > born
    out = collections.Counter()
    changed_levels, examples = 0, []
    for hid, r in rows_by_id.items():
        if r['games'] < brain.HINT_AT:
            continue
        h = registry[hid]
        born, gen = chronology.born(h), h.get('source') == 'gerador'
        conds = {tuple(c) for c in h['conditions']}
        match = [b for b, fam, at in have if fam == h['family'] and conds <= at and (gen or (born and chronology.signal_time(b) > born))]
        ordered = brain.ordered_games(match)
        old_report = len({brain.game_identity(b) for b in match if old_seen(b, born) and b['w'] not in (None, 0)})   # signal by signal
        old_replay = sum(1 for _, _, rows in ordered if all(old_seen(b, born) for b in rows))                     # all rows of the game
        new = r['prospective']['games']
        out['hypotheses'] += 1
        out['old_report_games'] += old_report
        out['old_replay_games'] += old_replay
        out['new_games'] += new
        out['old_report_vs_replay_disagree'] += old_report != old_replay
        for cat, n in r['prospective']['categories'].items():
            out['new_' + cat] += n
        if (old_report >= brain.PROSPECTIVE_MIN) != (new >= brain.PROSPECTIVE_MIN):
            changed_levels += 1
            if len(examples) < 6:
                examples.append({'id': hid, 'old_report_games': old_report, 'old_replay_games': old_replay, 'new_games': new,
                                 'categories': r['prospective']['categories']})
    return dict(out, evidence_level_changed=changed_levels, examples=examples)


def summary(rep):
    b = rep['brain']
    wf = b['walk_forward']
    return {'hypotheses': b['hypotheses'], 'tested': b['tested'], 'corrected_bar': b.get('corrected_bar'), 'counts': b['counts'],
            'status_list': b['status_list'], 'exploratory_counts': b['exploratory']['counts'],
            'walk_forward': {k: wf.get(k) for k in ('followed', 'skipped', 'not_reconstructible', 'prob_followed_better', 'first_followed_utc', 'chronology')}}


def main():
    data_before = digest()
    expected = base_digest()
    with patch.object(socket.socket, 'connect', blocked), patch.object(socket, 'create_connection', blocked), clock():
        old_brain = old_module('brain')
        old_report = old_module('report', brain=old_brain)
        pr2, t2 = build(old_report)
        pr3, t3 = build(report)
        t2b, t3b = build(old_report)[1], build(report)[1]
        signals = signals_of(report)
        registry = brain.load_registry(ROOT / 'data' / 'hypotheses.json')
        rows, tested, bar = brain.evaluate(signals, registry, report.MIN_SETTLED)
        by_id = {r['id']: r for r in rows}
        a, b = pr2['brain'], pr3['brain']
        top2, top3 = {h['id']: h for h in a['top']}, {h['id']: h for h in b['top']}
        changed = []
        for hid in sorted(set(a['status_list']) | set(b['status_list'])):
            if a['status_list'].get(hid) != b['status_list'].get(hid):
                r = by_id[hid]
                changed.append({'id': hid, 'pr2': a['status_list'].get(hid, 'em observação'), 'pr3': b['status_list'].get(hid, 'em observação'),
                                'games': r['games'], 'prob_above': r['prob_above'], 'pr3_pending': r['pending_status'],
                                'pr2_last_change': top2.get(hid, {}).get('last_change'), 'born_utc': r['born_utc'], 'at_birth': r.get('at_birth'),
                                'prospective_games': (r.get('prospective') or {}).get('games')})
        ov, sets = overlap(signals, registry, by_id)
        split = wf_split(signals, registry)
        out = {
            'base': BASE, 'clock': NOW, 'python': sys.version.split()[0],
            'data_matches_base': data_before == expected, 'data_sha256': data_before,
            'build_seconds': {'pr2': [round(t2, 2), round(t2b, 2)], 'pr3': [round(t3, 2), round(t3b, 2)]},
            'pr2': summary(pr2), 'pr3': summary(pr3),
            'prob_above_identical': all(top2[h]['prob_above'] == top3[h]['prob_above'] for h in set(top2) & set(top3)),
            'games_identical': all(top2[h]['games'] == top3[h]['games'] for h in set(top2) & set(top3)),
            'compared_hypotheses': len(set(top2) & set(top3)),
            'status_changes_pr2_to_pr3': changed,
            'pr3_status_change_records': {hid: by_id[hid]['status_changes'] for hid in sorted(b['status_list'])},
            'pr3_evidence_levels_tested': b['multiplicity']['evidence_levels'], 'pr3_multiplicity': b['multiplicity'],
            'pr3_validation': b['validation'],
            'prospective_definitions_review_5911487915': prospective_definitions(signals, registry, by_id),
            'chronology': chronology_evidence(signals),
            'git_timestamp_verification': git_timestamps(),
            'pr2_walk_forward_leakage': legacy_walk_forward_trace(signals, registry),
            'overlap': ov,
            'null_simulation': null_simulation(signals, registry, by_id, sets),
            'walk_forward_bootstrap_roi_90': {'followed': cluster_bootstrap_roi(split[True]), 'skipped': cluster_bootstrap_roi(split[False])},
            'sensitivity': sensitivity(signals, registry),
            'notify_rehearsal': notify_rehearsal(pr3),
            'synthetic_validation_rates': validation_rates(old_brain, old_module('brain', base=FIRST_PR3)),
            'review_5911603577': review_5911603577(signals, registry, by_id, old_module('brain', base=SECOND_PR3)),
            'counterexamples_review_5911603577': counterexamples(old_module('brain', base=SECOND_PR3)),
        }
    out['data_unchanged_by_audit'] = digest() == data_before
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
