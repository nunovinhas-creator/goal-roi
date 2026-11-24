"""Post-hoc diagnostics written after report.json was published.

Nothing here changes the verdict, the algorithm or any threshold. It explains
the published NO-GO:
  abstentions   what J would have chosen without the replication check
  generation    whether the frozen I generator proposed the labelled shape
  consensus     which labelled near-miss mechanisms the chosen rule did not exclude
"""

import json
from pathlib import Path

import bank_j
import evaluate
from scangoal.discovery.experimental_h.observed import Observed
from scangoal.discovery.experimental_i import miner as miner_i
from scangoal.discovery.experimental_j import observe, selector

DIRECTORY = Path(__file__).resolve().parent


def run():
    lock, _ = evaluate.verified_lock()
    report = json.loads((DIRECTORY / 'report.json').read_bytes())
    out = dict(schema='goal_roi_pr_j_posthoc_v1', written_after_report_sha256=evaluate._sha(DIRECTORY / 'report.json'),
               changes_verdict=False, families={})
    for index, (name, phase, _, _) in enumerate(bank_j.FAMILIES):
        folds = []
        tests = [evaluate.Evaluator(bank_j.build(index, s, fresh=True), lock) for s in lock['design']['validation_seeds']]
        for fold in report['families'][name]['folds']:
            seed = fold['mining_seed']
            bank = bank_j.build(index, seed, fresh=False)
            tape = evaluate._tape(bank)
            proposals = miner_i.discover(tape)['candidates']
            index_j = observe.Index(Observed(tape))
            labelled_shapes = sorted({tuple(row[0] for row in path) for path in bank.truth.values()})
            pool_shapes = {tuple(p['shape']) for p in proposals if p['phase'] == phase}
            row = dict(mining_seed=seed, published_abstain_reason=fold['j_abstain_reason'],
                       labelled_shapes=[list(s) for s in labelled_shapes],
                       labelled_shape_in_generator_pool=all(s in pool_shapes for s in labelled_shapes))
            if fold['j_abstained']:
                unreplicated, audit = selector.select(proposals, index_j, phase, replicate=False)
                row['without_replication'] = dict(
                    abstain_reason=audit['abstain_reason'],
                    shape=list(unreplicated['shape']) if unreplicated else None,
                    validation=[{k: t.measure(unreplicated)[k] for k in ('detections', 'exact_terminals', 'exact_paths', 'gate')}
                                for t in tests])
            else:
                row['missed_mechanisms'] = sorted({m for t in fold['trials'] for m in t['j']['false_mechanisms']})
            folds.append(row)
        out['families'][name] = folds
    return out


if __name__ == '__main__':
    print(json.dumps(run(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
