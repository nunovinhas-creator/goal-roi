"""Reproduce PR H sampling losses and compare on its already-known banks."""

import importlib.util
import json
from pathlib import Path
import sys

from scangoal.discovery.experimental_h import miner as prior
from scangoal.discovery.experimental_h.observed import Observed
from scangoal.discovery.experimental_h.rules import Meter
from scangoal.discovery.experimental_i import miner, paths

ROOT = Path(__file__).resolve().parents[2]
H = ROOT / 'audits/autonomous-generalization'


def _load_h():
    spec = importlib.util.spec_from_file_location('goal_roi_frozen_h_evaluator', H / 'run.py')
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(H))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


old = _load_h()


def exact_sampled(bank, buckets):
    found = set()
    for witnesses in buckets.values():
        for decision, path in witnesses:
            key = old.banks.key(decision.fixture, decision.quote)
            if tuple(map(old.banks.claim, path)) == bank.truth.get(key):
                found.add(key)
    return len(found)


def _specificity(rule):
    return dict(
        length=len(rule['steps']),
        absolute_price_predicates=[p for step in rule['steps'] for p in step if p['field'] == 'point_odds'],
        signed_odds_links=[edge for edge in rule['relations']
                           if edge['field'] == 'odds_delta_bp' and edge['value'] == 0],
    )


def replay():
    lock, _ = old.freeze()
    recorded = json.loads((H / 'report.json').read_bytes())
    output = {}
    for i, (family, _, _) in enumerate(old.banks.WORLDS):
        dev = recorded['worlds'][family]['development']
        banks = [old.banks.build(i, seed, holdout=True) for seed in lock['holdout_seeds']]
        evaluation = [old.Evaluator(bank, lock) for bank in banks]
        rows = []
        for entry in dev:
            seed = entry['development_seed']
            train = old.banks.build(i, seed, holdout=False)
            tape = old._tape(train)
            truth = old.Evaluator(train, lock)
            before, _, _ = prior._observations(Observed(tape), Meter(prior.WORK))
            after, _, _ = paths.sample(Observed(tape), Meter(miner.WORK // 5))
            proposals = miner.discover(tape)
            h = prior.discover(tape)
            selected, scores = old._selection(proposals['candidates'], truth)
            candidate_set = proposals['candidates']
            h_selected = entry['successor']['selected_rule']
            known_scores = []
            for fold, parallel in zip(evaluation, entry['successor']['holdout']):
                metrics = [fold.score(rule) for rule in candidate_set]
                known_scores.append(dict(
                    seed=parallel['seed'], h=parallel['selected'], g=next(
                        item['selected'] for item in entry['previous']['holdout'] if item['seed'] == parallel['seed']),
                    i_development_pick=fold.score(selected) if selected else None,
                    i_intrinsic=fold.score(candidate_set[0]) if candidate_set else None,
                    i_best_path_availability=max((result['exact_paths'] for result in metrics), default=0),
                    i_best_high_precision_path_availability=max((result['exact_paths'] for result in metrics
                                                                  if result['precision'] >= .9), default=0),
                ))
            shapes = {tuple(v[0] for v in path) for path in train.truth.values()}
            rows.append(dict(
                seed=seed, eligible_targets=len(train.truth), sampled_exact_paths=dict(
                    h=exact_sampled(train, before), i=exact_sampled(train, after)),
                h_work_reference=entry['successor_work'], i_work=proposals['work'],
                h_mask_collisions=h['work'].get('equivalent_features', 0),
                h_bare_target_shape=any(not x['rule']['relations'] and all(len(step) == 1 for step in x['rule']['steps'])
                                        and tuple(step[0]['value'] for step in x['rule']['steps']) in shapes
                                        for x in h['candidates']),
                i_structural_target_in_portfolio=any(x['channel'] == 'structural' and x['shape'] in shapes for x in candidate_set),
                h_selected_specificity=_specificity(h_selected),
                i_best_development_paths=max((r['exact_paths'] for r in scores.values()), default=0),
                i_development_pick_id=selected['id'] if selected else None,
                i_intrinsic_first_id=candidate_set[0]['id'] if candidate_set else None,
                familiar_followup=known_scores,
            ))
        output[family] = rows
    return dict(schema='goal_roi_pr_i_known_diagnostics_v1', confirmatory=False, worlds=output)


if __name__ == '__main__':
    print(json.dumps(replay(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
