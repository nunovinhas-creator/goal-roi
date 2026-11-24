"""PR J independent evaluation. Written after freeze.json (4d14cc8).

Order enforced by run():
  1. verify the lock and the published eligibility (computed without any rule);
  2. mine and select on the mining banks for H, I and J, then hash every choice;
  3. only then build the validation banks and score the frozen choices.
"""

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path

import bank_j
from scangoal.discovery import dsl
from scangoal.discovery.experimental_h import miner as miner_h, observed, rules
from scangoal.discovery.experimental_i import miner as miner_i
from scangoal.discovery.experimental_j import observe, selector
from scangoal.discovery.v32 import engine, riskset
from scangoal.discovery.v32.tape import Tape
from scangoal import chronology, event_audit

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[1]
LOCK_COMMIT = '4d14cc8'


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified_lock():
    path = DIRECTORY / 'freeze.json'
    lock = json.loads(path.read_bytes())
    names = {str(p.relative_to(ROOT)) for p in (ROOT / 'scangoal/discovery/experimental_j').glob('*.py')}
    if names != set(lock['algorithm_sha256']):
        raise ValueError('algorithm_inventory_changed')
    for section in ('algorithm_sha256', 'frozen_inputs_sha256'):
        if any(_sha(ROOT / name) != expected for name, expected in lock[section].items()):
            raise ValueError(f'{section}_does_not_match_lock')
    return lock, _sha(path)


def _tape(bank):
    return Tape(bank.observations, bank.cutoff, bank.coverage)


class Evaluator:
    """Owns the labels of one bank. Engines never receive this object."""

    def __init__(self, bank, lock):
        self.bank, self.lock = bank, lock
        self.source = observed.Observed(_tape(bank))
        meter = rules.Meter(lock['selector']['matcher_work'])
        self.decisions = list(self.source.decisions(meter))
        if meter.exhausted:
            raise RuntimeError('decision_index_budget')
        self.cache = {}

    def availability(self):
        bank, tape = self.bank, self.source.tape
        eligible = {bank_j.key(d.fixture, d.quote) for d in self.decisions}
        reasons = Counter()
        for k in sorted(set(bank.truth) - eligible):
            fixture, clock, _ = k
            at = chronology.parse_utc(clock)
            view, _ = observed.view_at(tape, fixture, at)
            anchor = next((q for q in view.quotes.values() if bank_j.key(fixture, q) == k), None)
            cause = (event_audit.timely(anchor, at, tape.parts[fixture][0])
                     or riskset.quote_gate(tape, anchor, view, at)) if anchor is not None else 'quote_not_reconstructed'
            reasons[cause or 'no_eligible_receipt_decision'] += 1
        mechanisms = Counter(v for k, v in bank.counterexamples.items() if k in eligible)
        return dict(targets=len(bank.truth), eligible_targets=len(set(bank.truth) & eligible),
                    counterexamples=len(bank.counterexamples), eligible_counterexamples=sum(mechanisms.values()),
                    eligible_mechanisms=len(mechanisms), mechanisms=len(set(bank.counterexamples.values())),
                    background=len(bank.background), unavailable_target_reasons=dict(reasons))

    def measure(self, proposal):
        if proposal is None:
            return dict(abstained=True, detections=0, exact_terminals=0, exact_paths=0, targets=len(self.bank.truth),
                        false_detections=0, precision=None, terminal_recall=0.0, exact_path_recall=0.0, gate=False)
        if proposal['id'] in self.cache:
            return self.cache[proposal['id']]
        bank = self.bank
        rule = rules.Rule(proposal['rule'])
        meter = rules.Meter(self.lock['selector']['matcher_work'], stage='matching')
        states, hits = {}, {}
        for decision in self.decisions:
            answer = rule.match(self.source, decision, meter)
            k = bank_j.key(decision.fixture, decision.quote)
            states[k] = answer.state
            if answer.state == 'present':
                hits[k] = tuple(bank_j.claim(row, terminal=i == len(answer.witness) - 1)
                                for i, row in enumerate(answer.witness))
            if meter.exhausted:
                break
        found = hits.keys() & bank.truth.keys()
        exact = {k for k in found if hits[k] == bank.truth[k]}
        false = hits.keys() - found
        by_fixture = {k[0]: v for k, v in bank.counterexamples.items()}
        by_fixture.update({k[0]: 'background' for k in bank.background})
        counter_states = {}
        for k, mechanism in sorted(bank.counterexamples.items()):
            counter_states.setdefault(mechanism, Counter())[states.get(k, 'terminal_unavailable')] += 1
        t = self.lock['thresholds']
        precision = Fraction(len(found), len(hits)) if hits else None
        metrics = dict(abstained=False, detections=len(hits), exact_terminals=len(found), exact_paths=len(exact),
                       targets=len(bank.truth), false_detections=len(false),
                       false_mechanisms=dict(Counter(by_fixture.get(f, 'different_decision') for f, _, _ in false)),
                       counterexample_states={m: dict(c) for m, c in counter_states.items()},
                       precision=None if precision is None else float(precision),
                       terminal_recall=float(Fraction(len(found), len(bank.truth))),
                       exact_path_recall=float(Fraction(len(exact), len(bank.truth))),
                       matcher_complete=not meter.exhausted,
                       witness_sha256=dsl.digest(sorted(hits.items())))
        metrics['gate'] = bool(not meter.exhausted and precision is not None and precision >= Fraction(str(t['precision']))
                               and Fraction(len(found), len(bank.truth)) >= Fraction(str(t['terminal_recall']))
                               and Fraction(len(exact), len(bank.truth)) >= Fraction(str(t['exact_path_recall'])))
        self.cache[proposal['id']] = metrics
        return metrics


def _eligible(row):
    return row['eligible_targets'] >= 8 and row['eligible_mechanisms'] >= 5


def eligibility(lock):
    """Uses labels only to count observable targets. No rule is matched."""
    out = {}
    design = lock['design']
    for index, (name, phase, _, _) in enumerate(bank_j.FAMILIES):
        rows = []
        for role, seeds, fresh in (('mining', design['mining_seeds'], False),
                                   ('validation', design['validation_seeds'], True)):
            for seed in seeds:
                bank = bank_j.build(index, seed, fresh=fresh)
                row = dict(role=role, seed=seed, nuisance=bank.nuisance, **Evaluator(bank, lock).availability())
                row['eligible'] = _eligible(row)
                rows.append(row)
        out[name] = dict(phase=phase, banks=rows, assessable=all(r['eligible'] for r in rows))
    return dict(schema='goal_roi_pr_j_eligibility_v1', computed_before_scoring=True, families=out)


def _choices(lock):
    plans = {}
    design = lock['design']
    for index, (name, phase, _, _) in enumerate(bank_j.FAMILIES):
        for seed in design['mining_seeds']:
            bank = bank_j.build(index, seed, fresh=False)
            tape = _tape(bank)  # only the observation tape crosses this line
            work = lock['selector']['mining_work']
            h = miner_h.discover(tape, work=work)
            j = selector.investigate(tape, phase=phase, work=work)
            moved = bank_j.transformed(bank, seed + design['transformation_seed_offset'])
            j_moved = selector.investigate(_tape(moved), phase=phase, work=work)
            i_candidates = [c for c in miner_i.discover(tape, work=work)['candidates'] if c['phase'] == phase]
            plans[name, seed] = dict(
                h=h['candidates'][0] if h['candidates'] else None,
                i=i_candidates[0] if i_candidates else None,
                j=j['choice'], i_portfolio=i_candidates,
                j_audit=j['audit'], j_abstained=j['abstained'],
                j_transport_same_choice=(j['choice'] or {}).get('id') == (j_moved['choice'] or {}).get('id'),
                search_complete=j['mining_work']['search_complete'],
                h_search_complete=not (h['work']['exhausted'] or h['work']['anchor_scan_capped']))
    return plans


def _summary(scores):
    measured = [s for s in scores if not s['abstained']]
    return dict(evaluations=len(scores), abstentions=len(scores) - len(measured),
                targets=sum(s['targets'] for s in scores), detections=sum(s['detections'] for s in measured),
                exact_terminals=sum(s['exact_terminals'] for s in measured),
                exact_paths=sum(s['exact_paths'] for s in measured),
                false_detections=sum(s['false_detections'] for s in measured), gates=sum(s['gate'] for s in scores))


def run():
    lock, lock_hash = verified_lock()
    published = json.loads((DIRECTORY / 'eligibility.json').read_bytes())
    if eligibility(lock) != published:
        raise ValueError('eligibility_differs_from_published_pre_check')
    plans = _choices(lock)
    sealed = dsl.digest(sorted((name, seed, engine_name, (plan[engine_name] or {}).get('id'))
                               for (name, seed), plan in plans.items() for engine_name in ('h', 'i', 'j')))
    families = {}
    for index, (name, phase, _, _) in enumerate(bank_j.FAMILIES):
        tests = []
        for seed in lock['design']['validation_seeds']:
            bank = bank_j.build(index, seed, fresh=True)
            moved = bank_j.transformed(bank, seed + lock['design']['transformation_seed_offset'])
            tests.append((seed, Evaluator(bank, lock), Evaluator(moved, lock)))
        folds = []
        for seed in lock['design']['mining_seeds']:
            plan = plans[name, seed]
            trials = []
            for vseed, test, control in tests:
                scores = {e: test.measure(plan[e]) for e in ('h', 'i', 'j')}
                trials.append(dict(
                    seed=vseed, **scores,
                    j_invariant=plan['j'] is None or scores['j'] == control.measure(plan['j']),
                    i_retrospective_best_exact_paths=max((test.measure(c)['exact_paths']
                                                          for c in plan['i_portfolio']), default=0)))
            folds.append(dict(
                mining_seed=seed, j_abstained=plan['j_abstained'], j_abstain_reason=plan['j_audit']['abstain_reason'],
                j_audit=plan['j_audit'], j_choice=plan['j'], j_transport_same_choice=plan['j_transport_same_choice'],
                h_choice_id=(plan['h'] or {}).get('id'), i_choice_id=(plan['i'] or {}).get('id'),
                i_choice_length=len(plan['i']['rule']['steps']) if plan['i'] else None,
                search_complete=plan['search_complete'], h_search_complete=plan['h_search_complete'],
                trials=trials))
        families[name] = dict(
            phase=phase, folds=folds, assessable=published['families'][name]['assessable'],
            j_same_choice_across_mining_seeds=len({(f['j_choice'] or {}).get('id') for f in folds}) == 1,
            summary={e: _summary([t[e] for f in folds for t in f['trials']]) for e in ('h', 'i', 'j')})
    passed = all(
        fam['assessable'] and all(
            not f['j_abstained'] and f['search_complete'] and f['j_transport_same_choice']
            and all(t['j']['gate'] and t['j_invariant'] for t in f['trials']) for f in fam['folds'])
        for fam in families.values())
    totals = {e: _summary([t[e] for fam in families.values() for f in fam['folds'] for t in f['trials']])
              for e in ('h', 'i', 'j')}
    return dict(schema='goal_roi_pr_j_independent_evaluation_v1', lock_commit=LOCK_COMMIT, lock_sha256=lock_hash,
                choices_sha256=sealed, thresholds=lock['thresholds'],
                evaluator_sha256={n: _sha(DIRECTORY / n) for n in ('evaluate.py', 'bank_j.py')},
                eligibility_sha256=_sha(DIRECTORY / 'eligibility.json'),
                families=families, totals=totals,
                decision=dict(offline_research='GO', independent_selection='GO' if passed else 'NO-GO',
                              prospective_confirmation='NO-GO'),
                confirmatory=False, verdict=engine.VERDICT)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--eligibility', action='store_true', help='only count observable targets; match no rule')
    args = parser.parse_args()
    lock, _ = verified_lock()
    result = eligibility(lock) if args.eligibility else run()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
