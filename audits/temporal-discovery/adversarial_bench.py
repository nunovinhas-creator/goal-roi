"""Adversarial synthetic benchmark for the temporal discovery engine (read-only audit).

Plants known temporal patterns into synthetic fixtures and measures what the
unchanged miner/replay pipeline recovers. Nothing here is written to `data/`,
the ledger, the collector or any workflow. Run from the repository root:

    PYTHONPATH=. python audits/temporal-discovery/adversarial_bench.py > /tmp/bench.json

The planted relations are encoded ONLY in this file as data-generating
processes and as oracle DSL rules used for scoring. The miner never sees
them: it receives the synthetic audit exactly as it receives a real one.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import random
import sys
from unittest.mock import patch

from scangoal import event_intelligence as events
from scangoal.discovery import dsl, engine, miner
from scangoal.discovery.replay import Budget, Replay

SEED = 83
START = datetime(2027, 1, 4, tzinfo=timezone.utc)
DAYS = 30
PER_DAY = 24


class World:
    def __init__(self, seed=SEED):
        self.rng = random.Random(seed)
        self.mid = 0
        self.events, self.episodes, self.results = [], [], {}
        self.planted = {}

    def emit(self, fx, ko, at, *, source, signal, phase, direction=None, line=None, odds=None,
             volume=None, minute=None, score=None, delay=60, kind='original'):
        self.mid += 1
        seen = at + timedelta(seconds=delay)
        sel = f'{direction.capitalize()} {line}' if direction else None
        e = dict(message_id=self.mid, journal_line=self.mid,
                 text_sha256=hashlib.sha256(b't%d' % self.mid).hexdigest(),
                 record_sha256=hashlib.sha256(b'r%d' % self.mid).hexdigest(),
                 emitted_utc=events.iso(at), edit_utc=None, first_read_utc=events.iso(seen),
                 available_utc=events.iso(seen), receipt='completed_page', match_id=fx, league='Synthetic League',
                 teams=[fx + ' Home', fx + ' Away'], source=source, signal_type=signal, kickoff_utc=events.iso(ko),
                 phase=phase, market='Asian Total' if direction else None,
                 market_key='match_goals' if direction else None, period='regulation' if direction else None,
                 selection=sel, direction=direction, line=line, odds=[odds, odds] if odds else None,
                 volume_usd=volume, share_pct=None, signal_kind=kind, multiplier=1, minute=minute,
                 score=score, context={}, fixture_id=fx)
        self.events.append(e)
        if odds:
            self.episodes.append(dict(episode_id=f'quote:{self.mid}', kind='quote', pattern='synthetic',
                                      fixture_id=fx, message_ids=[self.mid], observed_utc=events.iso(seen),
                                      decision_utc=events.iso(seen), exclusion_reasons=[], features={}))
        return e

    def odds(self, lo=1.6, hi=2.6):
        return f'{self.rng.uniform(lo, hi):.2f}'

    def noise(self, fx, ko, n):
        r = self.rng
        for _ in range(n):
            at = ko - timedelta(minutes=r.randint(20, 330))
            src = r.choice(['Polymarket', 'Stake', 'Stake', 'ScanGoal'])
            if src == 'ScanGoal':
                self.emit(fx, ko, at, source=src, signal=r.choice(['OPEN_GAME', 'TOTALS_FORM']), phase='pre')
            else:
                self.emit(fx, ko, at, source=src, signal='POLYMARKET' if src == 'Polymarket' else 'STAKE_LOAD',
                          phase='pre', direction=r.choice(['over', 'under']), line=r.choice(['2.5', '3.5']),
                          odds=self.odds(), volume=float(r.choice([5000, 8000, 12000, 20000, 40000])))

    def settle(self, fx, ko, direction, line, win):
        target = float(line)
        totals = [g for g in range(7) if (g > target) == (direction == 'over')]
        if not win:
            totals = [g for g in range(7) if g not in totals]
        goals = self.rng.choice(totals)
        home = self.rng.randint(0, goals)
        stamp = ko + timedelta(hours=2, minutes=5)
        self.results[fx] = [dict(source='synthetic', record_id=fx, fixture_id=fx, link=None,
                                 score=(home, goals - home), available_utc=events.iso(stamp + timedelta(minutes=5)),
                                 observed_utc=events.iso(stamp), published_utc=events.iso(stamp),
                                 kickoff_utc=events.iso(ko))]


# Each planter emits a full pattern and returns (direction, line) of the exposure.
def p_multisource_reversal(w, fx, ko):
    t = ko - timedelta(minutes=w.rng.randint(150, 300))
    w.emit(fx, ko, t, source='Stake', signal='STAKE_LOAD', phase='pre', direction='under', line='2.5',
           odds=w.odds(), volume=12000.)
    w.emit(fx, ko, t + timedelta(minutes=40), source='Polymarket', signal='POLYMARKET', phase='pre',
           direction='over', line='2.5', odds=w.odds(), volume=20000.)
    w.emit(fx, ko, t + timedelta(minutes=75), source='Stake', signal='STAKE_LOAD', phase='pre',
           direction='over', line='2.5', odds=w.odds(), volume=12000.)
    return 'over', '2.5'


def p_nonadjacent_delay(w, fx, ko):
    t = ko - timedelta(minutes=w.rng.randint(240, 330))
    w.emit(fx, ko, t, source='Polymarket', signal='POLYMARKET', phase='pre', direction='over', line='3.5',
           odds='2.40', volume=60000.)
    w.emit(fx, ko, t + timedelta(minutes=15), source='ScanGoal', signal='TOTALS_FORM', phase='pre')
    w.emit(fx, ko, t + timedelta(minutes=30), source='ScanGoal', signal='OPEN_GAME', phase='pre')
    w.emit(fx, ko, t + timedelta(minutes=110), source='Stake', signal='STAKE_LOAD', phase='pre',
           direction='over', line='3.5', odds='2.20', volume=8000.)
    return 'over', '3.5'


def p_context_price_live(w, fx, ko):
    minute = w.rng.randint(55, 70)
    goals = w.rng.choice([0, 1])
    at = ko + timedelta(minutes=minute + 15)
    w.emit(fx, ko, at - timedelta(minutes=6), source='ScanGoal', signal='GOAL_BREWING', phase='live',
           minute=minute - 6, score=[goals, goals])
    w.emit(fx, ko, at, source='Polymarket', signal='POLYMARKET', phase='live', direction='over',
           line=f'{2 * goals + .5}', odds=f'{w.rng.uniform(2.3, 2.8):.2f}', volume=15000., minute=minute,
           score=[goals, goals])
    return 'over', f'{2 * goals + .5}'


def p_oscillation_8(w, fx, ko):
    t = ko - timedelta(minutes=200)
    for i in range(7):
        w.emit(fx, ko, t + timedelta(minutes=12 * i), source='Stake', signal='STAKE_LOAD', phase='pre',
               direction='under' if i % 2 == 0 else 'over', line='2.5', odds=w.odds(), volume=5000.)
    w.emit(fx, ko, t + timedelta(minutes=12 * 7), source='Stake', signal='STAKE_LOAD', phase='pre',
           direction='over', line='2.5', odds=w.odds(), volume=5000.)
    return 'over', '2.5'


def p_price_path(w, fx, ko):
    t = ko - timedelta(minutes=w.rng.randint(150, 250))
    w.emit(fx, ko, t, source='Stake', signal='STAKE_LOAD', phase='pre', direction='under', line='2.5',
           odds='1.95', volume=8000.)
    w.emit(fx, ko, t + timedelta(minutes=30), source='Stake', signal='STAKE_LOAD', phase='pre',
           direction='under', line='2.5', odds='2.10', volume=8000.)
    w.emit(fx, ko, t + timedelta(minutes=60), source='Stake', signal='STAKE_LOAD', phase='pre',
           direction='under', line='2.5', odds='1.80', volume=8000.)
    return 'under', '2.5'


def p_delayed_confirmation(w, fx, ko):
    t = ko - timedelta(minutes=w.rng.randint(280, 330))
    w.emit(fx, ko, t, source='Polymarket', signal='POLYMARKET', phase='pre', direction='under', line='3.5',
           odds=w.odds(), volume=9000.)
    w.emit(fx, ko, t + timedelta(minutes=170), source='Polymarket', signal='POLYMARKET', phase='pre',
           direction='under', line='3.5', odds=w.odds(), volume=9000.)
    w.emit(fx, ko, t + timedelta(minutes=178), source='Stake', signal='STAKE_LOAD', phase='pre',
           direction='under', line='3.5', odds=w.odds(), volume=5000.)
    return 'under', '3.5'


def p_false_promising(w, fx, ko):
    t = ko - timedelta(minutes=w.rng.randint(100, 200))
    w.emit(fx, ko, t, source='Polymarket', signal='POLYMARKET', phase='pre', direction='over', line='2.5',
           odds=w.odds(), volume=40000.)
    w.emit(fx, ko, t + timedelta(minutes=20), source='Polymarket', signal='POLYMARKET', phase='pre',
           direction='over', line='2.5', odds=w.odds(), volume=40000.)
    return 'over', '2.5'


PLANTERS = {
    'multisource_reversal_3': p_multisource_reversal,
    'nonadjacent_delay_4': p_nonadjacent_delay,
    'live_context_x_price_2': p_context_price_live,
    'oscillation_8': p_oscillation_8,
    'price_up_then_down_3': p_price_path,
    'delayed_confirmation_3': p_delayed_confirmation,
    'false_promising_2': p_false_promising,
}


def rule(*steps, relations=(), window=21600):
    # Like miner.definition: the anchor must have enough history to express the pattern,
    # otherwise first-eligible reservation fixes the fixture before the chain completes.
    steps = [list(s) for s in steps]
    steps[-1].append(miner.atom('prior_alerts', len(steps) - 1, 'ge'))
    return dict(version=1, within_seconds=window, steps=steps, relations=list(relations))


A, E = miner.atom, miner.edge
# Oracle DSL rules: the analyst's ground truth, used only to score expressibility.
ORACLES = {
    'multisource_reversal_3': rule([A('source', 'Stake'), A('direction', 'under')], [A('source', 'Polymarket'), A('direction', 'over')],
                                   [A('source', 'Stake'), A('direction', 'over')], relations=(E('gap_seconds', 3600, 'le', 1, 2),)),
    'nonadjacent_delay_4': rule([A('source', 'Polymarket'), A('volume_usd', 50000, 'ge')], [A('signal_type', 'TOTALS_FORM')],
                                [A('source', 'Stake'), A('direction', 'over')],
                                relations=(E('gap_seconds', 5400, 'ge', 0, 2), E('same_line', True, 'eq', 0, 2))),
    'live_context_x_price_2': rule([A('signal_type', 'GOAL_BREWING')], [A('phase', 'live'), A('score_state', 'draw'), A('point_odds', 2.3, 'ge')]),
    'oscillation_8': rule(*[[A('direction', 'under' if i % 2 == 0 else 'over'), A('source', 'Stake')] for i in range(7)],
                          [A('direction', 'over'), A('source', 'Stake')]),
    'price_up_then_down_3': rule([A('direction', 'under')], [A('direction', 'under')], [A('direction', 'under')],
                                 relations=(E('odds_delta_bp', 0, 'gt', 0, 1), E('odds_delta_bp', 0, 'lt', 1, 2))),
    'delayed_confirmation_3': rule([A('source', 'Polymarket')], [A('source', 'Polymarket')], [A('source', 'Stake')],
                                   relations=(E('gap_seconds', 7200, 'ge', 0, 1), E('same_line', True, 'eq', 0, 2),
                                              E('same_direction', True, 'eq', 1, 2))),
    'false_promising_2': rule([A('source', 'Polymarket'), A('volume_usd', 30000, 'ge')],
                              [A('source', 'Polymarket'), A('volume_usd', 30000, 'ge')]),
}


def build(seed=SEED, days=DAYS, per_day=PER_DAY, share=.10, effect=.78, base=.48, false_until=None, as_of=None):
    w = World(seed)
    names = list(PLANTERS)
    for day in range(days):
        for slot in range(per_day):
            fx = f'syn-{day:03d}-{slot:02d}'
            ko = START + timedelta(days=day, hours=11 + slot % 10, minutes=15 * (slot // 10))
            w.noise(fx, ko, w.rng.randint(1, 5))
            pattern = None
            if w.rng.random() < share * len(names):
                pattern = names[w.rng.randrange(len(names))]
                direction, line = PLANTERS[pattern](w, fx, ko)
                w.planted.setdefault(pattern, set()).add(fx)
            else:
                direction, line = w.rng.choice(['over', 'under']), w.rng.choice(['2.5', '3.5'])
            if pattern is None or pattern == 'false_promising_2':
                p = effect if pattern and false_until is not None and day < false_until else base
            else:
                p = effect
            w.settle(fx, ko, direction, line, w.rng.random() < p)
    as_of = as_of or START + timedelta(days=days + 4)
    audit = dict(as_of_utc=events.iso(as_of), input_sha256={'messages.jsonl': '0' * 64}, events=w.events,
                 episodes=w.episodes, fixture_results=w.results)
    return Replay(audit), w.planted


def score(cases, truth):
    hit = len(cases & truth)
    return dict(jaccard=round(hit / len(cases | truth), 3) if cases | truth else 0,
                precision=round(hit / len(cases), 3) if cases else 0, recall=round(hit / len(truth), 3))


def case_set(replay, definition):
    decisions, pairs = replay.decide(definition, Budget(10 ** 8))
    return {d.fixture for d in decisions if d.state == 'case'}, decisions, pairs


def generated_rules(replay, anchors=None):
    """Replicate the miner's generator without ranking, to separate generation from ranking."""
    threshold = miner.samples(replay)
    found = {}
    pool = replay.anchors
    n = min(anchors or miner.MAX_ANCHORS, len(pool))
    sampled = [pool[(2 * i + 1) * len(pool) // (2 * n)] for i in range(n)]
    for at, event in sampled:
        anchor, prefix = replay.prefix(event, at, miner.DISCOVERY_WINDOW)
        for chain in miner.chains(prefix, anchor):
            for family, r in miner.projections(chain, threshold):
                found.setdefault(dsl.encoded(r), set()).add(event['fixture_id'])
    return found


def best_match(replay, rules, truth, top=15):
    """Screen by sampled support overlap, then replay the most overlapping rules."""
    ranked = sorted(rules.items(), key=lambda kv: -len(kv[1] & truth) / max(1, len(kv[1] | truth)))[:top]
    best = dict(jaccard=0)
    for raw, _ in ranked:
        cases, _, _ = case_set(replay, dsl.strict_json(raw))
        s = score(cases, truth)
        if s['jaccard'] > best['jaccard']:
            best = dict(s, rule=dsl.strict_json(raw))
    return best


def lifecycle(seed=SEED, discovery_days=30, oracle=False):
    """Discover, publish with a synthetic witness, then evaluate 90 future days.

    `false_promising_2` only wins during the discovery period. The true
    patterns keep their effect. Nothing is written outside a temporary folder.
    """
    import tempfile
    from scangoal import scientific_receipts
    from scangoal.discovery import ledger, study
    total = discovery_days + 1 + study.WINDOW.days + 4
    final = START + timedelta(days=total)
    full, planted = build(seed, days=total, false_until=discovery_days, as_of=final)
    cutoff = START + timedelta(days=discovery_days)
    discovery = Replay(dict(full.audit, as_of_utc=events.iso(cutoff)))
    report, records = engine.discover(discovery)
    if oracle:
        # Register the analyst's true rules directly: measures evaluation power
        # independently of what the miner happened to find.
        records = [dict(id=dsl.identifier(r), rule=dsl.canonical(r), status='insufficient', origin='oracle',
                        reason='oracle_registration', track_prospectively=True) for r in ORACLES.values()]
    witnessed = cutoff + timedelta(hours=2)

    def witness(_receipts, domain, content, *, as_of):
        return scientific_receipts.Receipt('synthetic-witness', scientific_receipts.digest(domain, content), witnessed)

    with tempfile.TemporaryDirectory() as folder:
        ledger.append(folder, discovery_as_of=cutoff, manifest=full.audit['input_sha256'],
                      snapshot_sha256=discovery.snapshot_sha256, records=records,
                      investigation=dict(protocol_sha256=report['protocol_sha256'],
                                         interpreter_sha256=report['interpreter_sha256']),
                      now=cutoff + timedelta(hours=1))
        batches = ledger.read(folder)
    result = engine.evaluate(full, batches, verify=witness)
    truth = {name: fixtures for name, fixtures in planted.items()}
    rows = []
    for row in result['investigations']:
        cases = {d.fixture for d in full.decide(row['rule'], Budget(10 ** 8))[0] if d.state == 'case'}
        best = max(((name, score(cases, t)['jaccard']) for name, t in truth.items()), key=lambda x: x[1])
        m = row.get('evidence', {}).get('matched', {})
        rows.append(dict(id=row['id'][:20], oracle=next((n for n, r in ORACLES.items() if dsl.identifier(r) == row['id']), None), status=row['status'], reason=row['reason'],
                         nearest_planted=best[0] if best[1] >= .3 else None, jaccard=best[1],
                         forward_fixtures=row['prospective_fixtures'], pairs=m.get('pairs'),
                         priced_pairs=m.get('priced_pairs'), day_blocks=m.get('day_blocks'),
                         p=m.get('sign_test_p'), adjusted_p=row['multiplicity']['adjusted_p']))
    return dict(seed=seed, discovery_days=discovery_days, total_days=total, tracked=len(rows),
                discovery_states=report['candidate_states'], forward_states=result['candidate_states'],
                forward_reasons=result['reasons'], hypotheses=rows)


def sensitivity(seed=SEED, oracle=False):
    """Pair-lag sensitivity of the lifecycle. Analysis only: the frozen rule is unchanged."""
    from scangoal.discovery import replay as module
    out = {}
    for label, lag in (('15m_frozen', module.PAIR_LAG), ('60m', timedelta(hours=1)),
                       ('6h', timedelta(hours=6)), ('24h', timedelta(days=1))):
        with patch.object(module, 'PAIR_LAG', lag):
            run = lifecycle(seed, oracle=oracle)
        pairs = [h['pairs'] or 0 for h in run['hypotheses']]
        out[label] = dict(forward_states=run['forward_states'], reasons=run['forward_reasons'],
                          max_pairs=max(pairs), median_pairs=sorted(pairs)[len(pairs) // 2],
                          survived=[h for h in run['hypotheses'] if h['status'] == 'experimental'])
    return out


def v2_power(seed=SEED, discovery_days=30):
    """Proposed v2 contrasts on the oracle rules (analysis only, not the frozen v1 rule).

    a) case arm alone: kickoff-day sign test of mean announced-price profit (null: zero).
    b) unpaired day x contract strata: day sign test of case-minus-control stratum means.
    """
    from collections import defaultdict
    from scangoal.discovery import study
    total = discovery_days + 1 + study.WINDOW.days + 4
    final = START + timedelta(days=total)
    full, planted = build(seed, days=total, false_until=discovery_days, as_of=final)
    start = START + timedelta(days=discovery_days + 1)
    end = start + study.WINDOW
    out = {}
    for name, definition in ORACLES.items():
        decisions, _ = full.decide(definition, Budget(10 ** 8), start=start, end=end,
                                   discovery_cutoff=START + timedelta(days=discovery_days))
        day_case, strata = defaultdict(list), defaultdict(lambda: defaultdict(list))
        for d in decisions:
            label = full.label(d)
            if label['profit'] is None or d.state not in ('case', 'control'):
                continue
            day = (d.kickoff or d.at).date()
            if d.state == 'case':
                day_case[day].append(label['profit'])
            strata[(day, d.stratum[1:7])][d.state].append(label['profit'])
        case_days = [study.mean(v) for v in day_case.values()]
        diffs = defaultdict(list)
        for (day, _), arms in strata.items():
            if arms['case'] and arms['control']:
                diffs[day].append(study.mean(arms['case']) - study.mean(arms['control']))
        diff_days = [study.mean(v) for v in diffs.values()]
        out[name] = dict(case_days=len(case_days), case_day_mean=study.mean(case_days),
                         case_sign_p=study.sign_p(case_days),
                         stratified_days=len(diff_days), stratified_mean=study.mean(diff_days),
                         stratified_sign_p=study.sign_p(diff_days))
    return out


def main():
    if sys.argv[1:2] == ['--v2-power']:
        json.dump(v2_power(int(sys.argv[2]) if len(sys.argv) > 2 else SEED), sys.stdout, indent=1, sort_keys=True, default=str)
        print()
        return
    if sys.argv[1] in ('--sensitivity', '--oracle-sensitivity'):
        json.dump(sensitivity(int(sys.argv[2]) if len(sys.argv) > 2 else SEED,
                              oracle=sys.argv[1] == '--oracle-sensitivity'), sys.stdout, indent=1, sort_keys=True, default=str)
        print()
        return
    if sys.argv[1:2] == ['--lifecycle']:
        json.dump(lifecycle(int(sys.argv[2]) if len(sys.argv) > 2 else SEED), sys.stdout, indent=1, sort_keys=True, default=str)
        print()
        return
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else SEED
    replay, planted = build(seed)
    report = dict(seed=seed, fixtures=DAYS * PER_DAY, anchors=len(replay.anchors),
                  planted={k: len(v) for k, v in sorted(planted.items())})
    t0, records = engine.discover(replay)
    proposals = [r for r in records if 'rule' in r]
    report['engine'] = dict(states=t0['candidate_states'], reasons=dict(Counter(r['reason'] for r in records)),
                            work=t0['work'])
    cases_by_proposal = [(r['id'], case_set(replay, r['rule'])[0]) for r in proposals]
    default_rules = generated_rules(replay)
    all_rules = generated_rules(replay, anchors=len(replay.anchors))
    rows = {}
    for name, truth in sorted(planted.items()):
        oracle_cases, decisions, _ = case_set(replay, ORACLES[name])
        prop = max((dict(score(c, truth), id=i) for i, c in cases_by_proposal), key=lambda s: s['jaccard'])
        rows[name] = dict(
            planted=len(truth),
            oracle_expressible=score(oracle_cases, truth),
            best_of_36_proposals=prop,
            best_generated_256_anchors={k: v for k, v in best_match(replay, default_rules, truth).items() if k != 'rule'},
            best_generated_all_anchors={k: v for k, v in best_match(replay, all_rules, truth).items() if k != 'rule'},
        )
        rows[name]['discovered'] = prop['jaccard'] >= .5
        rows[name]['loss_stage'] = (
            'discovered' if prop['jaccard'] >= .5 else
            'ranking_or_36_cap' if rows[name]['best_generated_256_anchors']['jaccard'] >= .5 else
            'anchor_sampling_256' if rows[name]['best_generated_all_anchors']['jaccard'] >= .5 else
            'not_generated_by_projection_grammar' if rows[name]['oracle_expressible']['jaccard'] >= .5 else
            'not_expressible_or_not_reserved')
    report['patterns'] = rows
    related = [i for i, c in cases_by_proposal if any(score(c, t)['jaccard'] >= .3 for t in planted.values())]
    report['proposals_related_to_any_planted_pattern'] = dict(related=len(related), total=len(cases_by_proposal))
    report['generated_rules'] = dict(default_256=len(default_rules), all_anchors=len(all_rules))
    report['summary'] = dict(Counter(r['loss_stage'] for r in rows.values()))
    json.dump(report, sys.stdout, indent=1, sort_keys=True, default=str)
    print()


if __name__ == '__main__':
    main()
