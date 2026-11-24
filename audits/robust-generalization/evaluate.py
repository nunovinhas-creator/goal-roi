"""Seal proposal order before constructing fresh banks and score each scenario separately."""

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from resource import RUSAGE_SELF, getrusage
from time import perf_counter

import bank_i
import history
from scangoal import chronology, event_audit
from scangoal.discovery import dsl
from scangoal.discovery.experimental_h import miner as previous, observed, rules
from scangoal.discovery.experimental_i import miner
from scangoal.discovery.v32 import engine, riskset
from scangoal.discovery.v32.tape import Tape

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[1]
SEALED_COMMIT = '6ca1b1cc5b43392a79e5351c9030de2e14343162'


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified_lock():
    path = DIRECTORY / 'freeze.json'
    lock = json.loads(path.read_bytes())
    names = {str(p.relative_to(ROOT)) for p in (ROOT / 'scangoal/discovery/experimental_i').glob('*.py')}
    if names != set(lock['algorithm_sha256']):
        raise ValueError('algorithm_inventory_changed')
    for section in ('algorithm_sha256', 'frozen_inputs_sha256'):
        if any(_sha(ROOT / name) != expected for name, expected in lock[section].items()):
            raise ValueError(f'{section}_does_not_match_lock')
    return lock, _sha(path)


def _tape(bank):
    return Tape(bank.observations, bank.cutoff, bank.coverage)


class Evaluator:
    def __init__(self, bank, lock):
        self.mining_bank = bank
        self.lock = lock
        self.source = observed.Observed(_tape(bank))
        clock = rules.Meter(lock['budgets']['matcher'])
        self.decisions = list(self.source.decisions(clock))
        if clock.exhausted:
            raise RuntimeError('decision_index_budget')
        self.cache = {}

    def availability(self):
        bank = self.mining_bank
        tape = self.source.tape
        eligible = {bank_i.key(d.fixture, d.quote) for d in self.decisions}
        targets, counterexamples = set(bank.truth), set(bank.counterexamples)
        reasons = Counter()
        for key in sorted(targets - eligible):
            fixture, clock, _ = key
            at = chronology.parse_utc(clock)
            view, _ = observed.view_at(tape, fixture, at)
            anchor = next((candidate for candidate in view.quotes.values()
                           if bank_i.key(fixture, candidate) == key), None)
            cause = (event_audit.timely(anchor, at, tape.parts[fixture][0])
                     or riskset.quote_gate(tape, anchor, view, at)) if anchor is not None else 'quote_not_reconstructed'
            reasons[cause or 'no_eligible_receipt_decision'] += 1
        return dict(targets=len(targets), eligible_targets=len(targets & eligible),
                    counterexamples=len(counterexamples), eligible_counterexamples=len(counterexamples & eligible),
                    unavailable_target_reasons=dict(reasons))

    def measure(self, proposed):
        identity = proposed['id']
        if identity in self.cache:
            return self.cache[identity]
        bank = self.mining_bank
        rule = rules.Rule(proposed['rule'])
        meter = rules.Meter(self.lock['budgets']['matcher'], stage='matching')
        decisions, hits = {}, {}
        for decision in self.decisions:
            answer = rule.match(self.source, decision, meter)
            key = bank_i.key(decision.fixture, decision.quote)
            if key in decisions:
                raise RuntimeError('ambiguous_decision_key')
            decisions[key] = answer.state
            if answer.state == 'present':
                hits[key] = tuple(bank_i.claim(row, terminal=i == len(answer.witness) - 1)
                                  for i, row in enumerate(answer.witness))
            if meter.exhausted:
                break
        found = hits.keys() & bank.truth.keys()
        exact = {key for key in found if hits[key] == bank.truth[key]}
        counterexamples = {}
        for key, mechanism in sorted(bank.counterexamples.items()):
            counterexamples.setdefault(mechanism, Counter())[decisions.get(key, 'terminal_unavailable')] += 1
        false = hits.keys() - found
        mechanisms_by_fixture = {key[0]: mechanism for key, mechanism in bank.counterexamples.items()}
        false_modes = Counter(mechanisms_by_fixture.get(fixture, 'different_decision')
                              for fixture, _, _ in false)
        metrics = dict(detections=len(hits), exact_terminals=len(found), exact_paths=len(exact),
                       targets=len(bank.truth), false_detections=len(false),
                       false_mechanisms=dict(false_modes), counterexamples=counterexamples,
                       precision=float(Fraction(len(found), len(hits) or 1)),
                       terminal_recall=float(Fraction(len(found), len(bank.truth))),
                       exact_path_recall=float(Fraction(len(exact), len(bank.truth))),
                       matcher_operations=meter.used, matcher_complete=not meter.exhausted,
                       witness_sha256=dsl.digest(sorted(hits.items())),
                       observation_state_sha256=dsl.digest(sorted(decisions.items())))
        minimum = self.lock['thresholds']
        metrics['gate'] = (not meter.exhausted and len(bank.truth) >= minimum['minimum_targets']
                           and len(counterexamples) >= minimum['minimum_counterexample_mechanisms']
                           and metrics['precision'] >= minimum['precision']
                           and metrics['terminal_recall'] >= minimum['terminal_recall']
                           and metrics['exact_path_recall'] >= minimum['exact_path_recall'])
        self.cache[identity] = metrics
        return metrics


def _least_margin(measure, limits):
    ratios = (Fraction(measure['exact_terminals'], measure['detections'] or 1),
              Fraction(measure['exact_terminals'], measure['targets']),
              Fraction(measure['exact_paths'], measure['targets']))
    goals = [Fraction(str(limits[k])) for k in ('precision', 'terminal_recall', 'exact_path_recall')]
    return min(rate / goal for rate, goal in zip(ratios, goals))


def _best_development(table, lock, candidates):
    complete = [row for row in candidates if table[row['id']]['matcher_complete']]

    def rank(candidate):
        score = table[candidate['id']]
        return -_least_margin(score, lock['thresholds']), score['false_detections'], -score['exact_paths'], candidate['id']

    return min(complete, key=rank, default=None)


def _diversity(candidates):
    shape = lambda row: tuple(next((atom['value'] for atom in step if atom['field'] == 'event_kind'), '*')
                              for step in row['rule']['steps'])
    edges = {edge['field'] for row in candidates for edge in row['rule']['relations']}
    return dict(count=len(candidates), shapes=len({shape(row) for row in candidates}),
                witness_fingerprints=len({row.get('witness_sha256') for row in candidates
                                          if row.get('witness_sha256') is not None}),
                steps=sorted({len(row['rule']['steps']) for row in candidates}), relations=sorted(edges),
                max_link_span=max((edge['to'] - edge['from'] for row in candidates
                                   for edge in row['rule']['relations']), default=0))


def _development_result(candidates, fit, lock):
    metrics = {row['id']: fit.measure(row) for row in candidates}
    first = candidates[0] if candidates else None
    diagnostic = _best_development(metrics, lock, candidates)
    return dict(candidates=candidates, choice=first, diagnostic=diagnostic, metrics=metrics,
                order_sha256=dsl.digest([row['id'] for row in candidates]))


def _prepare(lock, timings):
    plans = {}
    for index, (scenario, phase, _, _) in enumerate(bank_i.SCENARIOS):
        for seed in lock['design']['development_seeds']:
            bank = bank_i.build(index, seed, fresh=False)
            fit = Evaluator(bank, lock)
            started = perf_counter()
            current = miner.discover(fit.source.tape, work=lock['budgets']['mining'])
            timings['i_mining_seconds'] += perf_counter() - started
            started = perf_counter()
            baseline = previous.discover(fit.source.tape, work=lock['budgets']['mining'])
            timings['h_mining_seconds'] += perf_counter() - started
            transformed = bank_i.transformed(bank, seed + lock['design']['transformation_seed_offset'])
            verified = miner.discover(_tape(transformed), work=lock['budgets']['mining'])
            i_candidates = [row for row in current['candidates'] if row['phase'] == phase]
            h_candidates = baseline['candidates']  # This scenario supplies only one terminal phase.
            plans[scenario, seed] = dict(
                work=current['work'], stable_rank=current == verified,
                development_availability=fit.availability(),
                h_work_complete=not (baseline['work']['exhausted'] or baseline['work']['anchor_scan_capped']),
                i=_development_result(i_candidates, fit, lock),
                h=_development_result(h_candidates, fit, lock),
            )
    return plans


def _stages(output, batches, *, details):
    candidates = output['candidates']
    choice, diagnostic = output['choice'], output['diagnostic']
    initial = output['metrics']
    found = max((row['exact_paths'] for row in initial.values()), default=0)
    passed = {identifier for identifier, row in initial.items() if row['gate']}
    per_seed, fresh_passes = [], []
    for seed, test, control in batches:
        metrics = {row['id']: test.measure(row) for row in candidates}
        fresh_passes.append({identifier for identifier, row in metrics.items() if row['gate']})
        chosen = metrics[choice['id']] if choice else None
        per_seed.append(dict(seed=seed, selected=chosen, diagnostic=metrics[diagnostic['id']] if diagnostic else None,
                             chosen_witness_invariant=chosen == control.measure(choice) if choice else False,
                             best_available_exact_paths=max((m['exact_paths'] for m in metrics.values()), default=0),
                             fits_all_thresholds_in_bank=len(fresh_passes[-1]),
                             all_metrics=metrics if details else None))
    shared = set.intersection(*fresh_passes)
    return dict(discovery=dict(best_development_exact_paths=found, development_qualifying=len(passed),
                              fresh_any_candidate_passes_all_seeds=len(shared)),
                selection=dict(ordered_ids_sha256=output['order_sha256'],
                               selected_id=choice['id'] if choice else None, selected_rule=choice['rule'] if choice else None,
                               selected_development=initial[choice['id']] if choice else None,
                               supervised_diagnostic_id=diagnostic['id'] if diagnostic else None,
                               supervised_diagnostic_development=initial[diagnostic['id']] if diagnostic else None),
                transfer=per_seed, diversity=_diversity(candidates), candidates=candidates if details else None)


def _stability(rows, name):
    models = [row[name] for row in rows]
    identifiers = [item['selection']['selected_id'] for item in models]
    values = [seed['selected'] for item in models for seed in item['transfer'] if seed['selected']]
    percent = {}
    for metric in ('precision', 'terminal_recall', 'exact_path_recall'):
        measured = [row[metric] for row in values]
        percent[metric] = [min(measured), max(measured)] if measured else None
    return dict(same_choice_across_seeds=len(set(identifiers)) == 1, ranges=percent)


def _known_guards(historical, lock):
    allowed = lock['thresholds']['max_known_path_availability_drop']
    return {family: dict(max_loss_vs_h_selected=max(
        0, *(test['h']['exact_paths'] - test['i_best_path_availability'] for dev in rows
             for test in dev['familiar_followup'])))
            for family, rows in historical['worlds'].items()} | dict(max_allowed_loss=allowed)


def run(*, details=False, profile=False):
    lock, lock_hash = verified_lock()
    expected = lock['design']
    if len(bank_i.SCENARIOS) != expected['scenarios'] or Counter(x[1] for x in bank_i.SCENARIOS) != expected['phases']:
        raise ValueError('evaluator_design_mismatch')
    historical = history.replay()
    known = _known_guards(historical, lock)
    timings = Counter()
    plans = _prepare(lock, timings)
    ordered = sorted((scenario, seed, name, item[name]['order_sha256'])
                     for (scenario, seed), item in plans.items() for name in ('i', 'h'))
    # Freeze order and selected identities before constructing any fresh bank.
    selection_hash = dsl.digest(ordered)
    started = perf_counter()
    scenarios = {}
    for index, (scenario, phase, _, counterexamples) in enumerate(bank_i.SCENARIOS):
        batches = []
        for seed in expected['validation_seeds']:
            bank = bank_i.build(index, seed, fresh=True)
            variant = bank_i.transformed(bank, seed + expected['transformation_seed_offset'])
            batches.append((seed, Evaluator(bank, lock), Evaluator(variant, lock)))
        availability = [dict(seed=seed, **test.availability()) for seed, test, _ in batches]
        observable = all(row['eligible_targets'] >= lock['thresholds']['minimum_targets'] for row in availability)
        rows = []
        for seed in expected['development_seeds']:
            plan = plans[scenario, seed]
            rows.append(dict(seed=seed, i=_stages(plan['i'], batches, details=details),
                             h=_stages(plan['h'], batches, details=details),
                             work=plan['work'], stable_rank=plan['stable_rank'], h_work_complete=plan['h_work_complete'],
                             development_availability=plan['development_availability']))
        phase_ok = observable and all(r['work']['search_complete'] and r['stable_rank'] and all(
            trial['chosen_witness_invariant'] and trial['selected'] is not None and trial['selected']['gate']
            for trial in r['i']['transfer']) for r in rows)
        scenarios[scenario] = dict(phase=phase, development=rows, eligibility=phase_ok, counterexample_mechanisms=counterexamples,
                                   availability=availability,
                                   scoring_status='assessable' if observable else 'ineligible_labelled_targets',
                                   stability={name: _stability(rows, name) for name in ('i', 'h')})
    timings['validation_seconds'] = perf_counter() - started
    preserved = all(x['max_loss_vs_h_selected'] <= known['max_allowed_loss'] for name, x in known.items()
                    if name != 'max_allowed_loss')
    passed = preserved and all(world['eligibility'] for world in scenarios.values())
    result = dict(schema='goal_roi_pr_i_robust_generalization_v1', pre_evaluation_commit=SEALED_COMMIT,
                  lock_sha256=lock_hash, selection_sha256=selection_hash,
                  evaluator_sha256={name: _sha(DIRECTORY / name) for name in ('evaluate.py', 'bank_i.py', 'history.py')},
                  known_diagnostics=historical, known_guardrail=known, known_guardrail_passed=preserved,
                  thresholds=lock['thresholds'], scenarios=scenarios,
                  decision=dict(offline_research='GO', independent_transfer='GO' if passed else 'NO-GO',
                                prospective_confirmation='NO-GO'), confirmatory=False, verdict=engine.VERDICT)
    if profile:
        result['profile'] = dict(timings) | dict(peak_rss_kib=getrusage(RUSAGE_SELF).ru_maxrss)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--details', action='store_true', help='include every proposed rule and validation score')
    parser.add_argument('--profile', action='store_true', help='include measured times and process memory')
    args = parser.parse_args()
    print(json.dumps(run(details=args.details, profile=args.profile), ensure_ascii=False, sort_keys=True, indent=2,
                     allow_nan=False))
