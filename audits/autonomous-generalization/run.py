"""Reproduce the discovery-only transfer test with a locked search implementation."""

import argparse
from collections import Counter
from datetime import timedelta
import hashlib
import json
from pathlib import Path
from resource import RUSAGE_SELF, getrusage
from time import perf_counter

import banks
from scangoal.discovery import dsl
from scangoal.discovery.experimental_h import miner, observed, research, rules
from scangoal.discovery.v32 import engine, language
from scangoal.discovery.v32.tape import Tape
from scangoal.offline_pilot import discovery as previous

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[1]
ALGORITHM_COMMIT = '85c3c7ff9d73c0e6a1b7324979811b0ee3bc4f2c'


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze():
    path = DIRECTORY / 'freeze.json'
    lock = json.loads(path.read_bytes())
    algorithm = ROOT / 'scangoal/discovery/experimental_h'
    actual = {str(p.relative_to(ROOT)): _sha(p) for p in sorted(algorithm.glob('*.py'))}
    if lock['algorithm_sha256'] != actual:
        raise ValueError('algorithm_differs_from_pre_holdout_lock')
    reference = json.loads((ROOT / 'audits/offline-observation/frozen.json').read_bytes())
    if any(_sha(ROOT / name) != sha for name, sha in reference['sha256'].items()):
        raise ValueError('frozen_protocol_file_changed')
    return lock, _sha(path)


def _tape(bank):
    return Tape(bank.observations, bank.cutoff)


class Evaluator:
    """The matching engine receives typed observations. This class owns truth."""

    def __init__(self, bank, lock):
        self.bank = bank
        self.lock = lock
        self.source = observed.Observed(_tape(bank))
        meter = rules.Meter(lock['work']['matcher_per_candidate_per_bank'])
        self.decisions = list(self.source.decisions(meter))
        if meter.exhausted:
            raise RuntimeError('benchmark_decision_budget_exhausted')
        self.cache = {}

    def score(self, item):
        if item['id'] in self.cache:
            return self.cache[item['id']]
        sequence = rules.Rule(item['rule'])
        meter = rules.Meter(self.lock['work']['matcher_per_candidate_per_bank'], stage='matching')
        matched = {}
        outcomes = Counter()
        uncertain = set()
        truth = self.bank.truth
        for decision in self.decisions:
            answer = sequence.match(self.source, decision, meter)
            outcomes[answer.state] += 1
            key = banks.key(decision.fixture, decision.quote)
            if answer.state == 'present':
                path = tuple(banks.claim(row) for row in answer.witness)
                matched[key] = path
            elif answer.state == 'indeterminate':
                uncertain.add(decision.fixture)
            if meter.exhausted:
                break
        exact = matched.keys() & truth.keys()
        complete = {key for key in exact if matched[key] == truth[key]}
        false_matches = {key for key in matched if key not in truth}
        group = self.bank.counterexamples
        negative_fired = {fixture for fixture, *_ in false_matches if fixture in group}
        negative_clear = set(group) - negative_fired
        precision = len(exact) / len(matched) if matched else 0.
        recall = len(exact) / len(truth)
        witness_recall = len(complete) / len(truth)
        minimum = self.lock['minimum']
        scores = dict(detections=len(matched), targets=len(truth), exact_terminals=len(exact), exact_paths=len(complete),
                      precision=precision, terminal_recall=recall, exact_path_recall=witness_recall,
                      negative_fixtures=len(group), negative_fixtures_not_detected=len(negative_clear),
                      negatives_with_indeterminate_decisions=len(negative_clear & uncertain),
                      false_detections_by_mechanism=dict(Counter(group.get(fixture, 'wrong_time_on_target')
                                                                for fixture, *_ in false_matches)),
                      negative_fixtures_detected_by_mechanism=dict(Counter(group[fixture] for fixture in negative_fired)),
                      search_complete=not meter.exhausted, matching_operations=meter.used, states=dict(outcomes),
                      detections_sha256=dsl.digest(sorted(matched.items())))
        scores['gate'] = (not meter.exhausted and len(truth) >= minimum['holdout_target_fixtures']
                          and len(set(group.values())) >= minimum['holdout_negative_mechanisms']
                          and precision >= minimum['terminal_precision'] and recall >= minimum['terminal_recall']
                          and witness_recall >= minimum['exact_path_recall'])
        self.cache[item['id']] = scores
        return scores


def _legacy(tape):
    _, proposals = previous._candidates(tape)
    return [dict(row, id=language.identifier(row['rule'])) for row in proposals]


def _diversity(candidates):
    shapes = set()
    fields, relations, lengths = set(), set(), set()
    max_compound, max_span = 0, 0
    for item in candidates:
        rule = item['rule']
        lengths.add(len(rule['steps']))
        shapes.add(tuple(next((atom['value'] for atom in step if atom['field'] == 'event_kind'), '*')
                         for step in rule['steps']))
        fields.update(atom['field'] for step in rule['steps'] for atom in step)
        relations.update(edge['field'] for edge in rule['relations'])
        max_span = max(max_span, *(edge['to'] - edge['from'] for edge in rule['relations']), 0)
        max_compound = max(max_compound, sum(len(step) for step in rule['steps']) + len(rule['relations']))
    return dict(count=len(candidates), typed_shapes=len(shapes), lengths=sorted(lengths),
                unary_fields=sorted(fields), relation_fields=sorted(relations), max_link_span=max_span,
                max_predicates_in_rule=max_compound)


def _selection(candidates, evaluation):
    scores = {item['id']: evaluation.score(item) for item in candidates}
    valid = [item for item in candidates if scores[item['id']]['search_complete']]

    def rank(item):
        score = scores[item['id']]
        return -score['exact_paths'], -score['precision'], -score['exact_terminals'], item['id']

    return min(valid, key=rank, default=None), scores


def _regression():
    recorded = json.loads((ROOT / 'audits/offline-observation/report.json').read_bytes())['discovery']
    rerun = previous.run(try_model=False)
    rerun['oracle'].pop('rule')
    rerun['deterministic'].pop('all_candidates')
    rerun['deterministic']['selected_on_development'].pop('rule')
    retained = recorded == rerun
    if not retained:
        raise ValueError('previous_diagnostic_did_not_reproduce')
    legacy = rerun['deterministic']
    tape = Tape(previous.bank(previous.TRAIN)[0], previous.BASE + timedelta(days=7))
    _, old = previous._candidates(tape)
    max_overlay = max(sum(len(step) - 1 for step in item['rule']['steps']) + len(item['rule']['relations'])
                      for item in old if item['origin'] == 'typed_trajectory')
    return dict(reproduced=retained, candidates=legacy['candidates'], holdout_passes=legacy['holdout_passes'],
                best_historical=legacy['selected_on_development'], max_typed_overlay=max_overlay,
                frozen_discovery_entrypoint=rerun['frozen_entrypoint'])


def _develop(lock, model, durations):
    plans = {}
    for i, (world, _, _) in enumerate(banks.WORLDS):
        for seed in lock['development_seeds']:
            bank = banks.build(i, seed, holdout=False)
            tape = _tape(bank)
            evaluator = Evaluator(bank, lock)
            start = perf_counter()
            new = miner.discover(tape, work=lock['work']['miner'])
            durations['successor_mining_seconds'] += perf_counter() - start
            start = perf_counter()
            old = _legacy(tape)
            durations['previous_mining_seconds'] += perf_counter() - start
            transformed = banks.irrelevant(bank, seed + lock['invariance_seed_offset'])
            alternate = miner.discover(_tape(transformed), work=lock['work']['miner'])
            local = research.investigate(tape, model)
            engines = dict(successor=new['candidates'], previous=old, llm=local['candidates'])
            plans[world, seed] = dict(
                complete=not (new['work']['exhausted'] or new['work']['anchor_scan_capped']),
                generation_invariant=new == alternate, successor_work=new['work'], llm_status=local['status'],
                llm_refused=len(local['rejected']),
                engines={name: (items, *_selection(items, evaluator)) if items is not None else (None, None, None)
                         for name, items in engines.items()},
            )
    return plans


def _holdout_results(candidates, selected, scores, tests, *, details=False):
    if candidates is None:
        return None
    diagnostics, comparisons = [], []
    for seed, evaluator, transformed in tests:
        assessed = {item['id']: evaluator.score(item) for item in candidates}
        diagnostics.append({identity for identity, row in assessed.items() if row['gate']})
        fixed = evaluator.score(selected) if selected is not None else None
        robust = fixed == transformed.score(selected) if selected is not None else False
        comparisons.append(dict(seed=seed, selected=fixed, noise_invariant=robust,
                                shortlist_passes=sum(row['gate'] for row in assessed.values()),
                                intrinsic_first=evaluator.score(candidates[0]) if candidates else None,
                                all_candidates=assessed if details else None))
    return dict(diversity=_diversity(candidates), selected_id=selected['id'] if selected else None,
                selected_rule=selected['rule'] if selected else None,
                development=scores[selected['id']] if selected else None,
                holdout=comparisons,
                shortlist_passes_every_seed=len(set.intersection(*diagnostics)),
                candidates=candidates if details else None)


def _stability(world_rows, engine_name):
    selected = [row[engine_name] for row in world_rows if row[engine_name]]
    if len(selected) < 2:
        return None
    signatures = [row['selected_id'] for row in selected]
    values = [fold['selected']['exact_path_recall'] for row in selected for fold in row['holdout']
              if fold['selected'] is not None]
    rates = [fold['selected']['precision'] for row in selected for fold in row['holdout'] if fold['selected'] is not None]
    return dict(same_rule_across_development_seeds=len(set(signatures)) == 1,
                exact_path_recall_range=[min(values), max(values)] if values else None,
                terminal_precision_range=[min(rates), max(rates)] if rates else None,
                every_selected_fold_passed=all(fold['selected'] is not None and fold['selected']['gate']
                                               for row in selected for fold in row['holdout']))


def run(*, details=False, profile=False, try_model=True):
    lock, fingerprint = freeze()
    if len(banks.WORLDS) != lock['bank_worlds']:
        raise ValueError('wrong_number_of_preregistered_worlds')
    model = research.available_model() if try_model else None
    durations = Counter()
    regression = _regression()
    plan = _develop(lock, model, durations)
    # No holdout data are even constructed until all proposal lists and choices exist.
    sealed_choices = sorted((world, seed, name, selected['id'] if selected else None,
                             sorted(item['id'] for item in (candidates or ())))
                            for (world, seed), entry in plan.items()
                            for name, (candidates, selected, _) in entry['engines'].items())
    selection_sha256 = dsl.digest(sealed_choices)
    started = perf_counter()
    worlds = {}
    for i, (world, _, _) in enumerate(banks.WORLDS):
        tests = []
        for seed in lock['holdout_seeds']:
            bank = banks.build(i, seed, holdout=True)
            variant = banks.irrelevant(bank, seed + lock['invariance_seed_offset'])
            tests.append((seed, Evaluator(bank, lock), Evaluator(variant, lock)))
        rows = []
        for seed in lock['development_seeds']:
            entry = plan[world, seed]
            engines = {name: _holdout_results(*values, tests, details=details)
                       for name, values in entry['engines'].items()}
            rows.append(dict(development_seed=seed, **engines,
                             **{key: value for key, value in entry.items() if key != 'engines'}))
        worlds[world] = dict(development=rows, stability={name: _stability(rows, name)
                                                        for name in ('successor', 'previous', 'llm')})
    durations['holdout_seconds'] += perf_counter() - started
    successor = [item for world in worlds.values() for item in world['development']]
    passed = all(item['complete'] and item['generation_invariant'] and all(
        fold['noise_invariant'] and fold['selected'] is not None and fold['selected']['gate']
        for fold in item['successor']['holdout']) for item in successor)
    result = dict(schema='goal_roi_autonomous_generalization_v1', pre_holdout_algorithm_commit=ALGORITHM_COMMIT,
                  algorithm_lock_sha256=fingerprint, selection_sha256=selection_sha256,
                  evaluator_sha256={name: _sha(DIRECTORY / name) for name in ('run.py', 'banks.py')},
                  previous_regression=regression, worlds=worlds,
                  criteria=lock['minimum'], model=dict(model_basename=model.name if model else None,
                         status='available' if model else 'local_model_or_runtime_unavailable'),
                  decision=dict(offline_research='GO', synthetic_transfer='GO' if passed else 'NO-GO',
                                prospective_confirmation='NO-GO'), confirmatory=False, verdict=engine.VERDICT)
    if profile:
        result['profile'] = dict(durations) | dict(peak_rss_kib=getrusage(RUSAGE_SELF).ru_maxrss)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--details', action='store_true', help='include all rules and their diagnostic scores')
    parser.add_argument('--profile', action='store_true', help='include non-deterministic elapsed time and RSS')
    parser.add_argument('--deterministic-only', action='store_true', help='skip optional local model inference')
    options = parser.parse_args()
    result = run(details=options.details, profile=options.profile, try_model=not options.deterministic_only)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
