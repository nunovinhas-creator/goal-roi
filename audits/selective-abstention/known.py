"""PR J development audit on banks whose results are already known.

Everything here is development evidence. The PR H and PR I banks, including
the PR I validation banks, were inspected before PR J was written. Nothing in
this file is an independent validation.

Sections:
  ranking      why the PR I first choice failed (ordering, length, support, redundancy)
  coincidences candidates that look good on development and collapse elsewhere
  selection    J choice or abstention, compared with H and I first choices
  sensitivity  J outcomes over a grid of its two consensus thresholds
  ineligible   audit of the PR I score-confirmation scenario
"""

import argparse
from collections import Counter
from contextlib import contextmanager
from fractions import Fraction
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
I_DIR = ROOT / 'audits/robust-generalization'
H_DIR = ROOT / 'audits/autonomous-generalization'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


I_EVAL = _load('goal_roi_j_view_of_i_evaluator', I_DIR / 'evaluate.py')
H_RUN = I_EVAL.history.old
bank_i, bank_h = I_EVAL.bank_i, H_RUN.banks

from scangoal.discovery import dsl  # noqa: E402
from scangoal.discovery.experimental_h import miner as miner_h  # noqa: E402
from scangoal.discovery.experimental_h.observed import Observed  # noqa: E402
from scangoal.discovery.experimental_i import miner as miner_i  # noqa: E402
from scangoal.discovery.experimental_j import observe, refine, selector  # noqa: E402

I_LOCK, _ = I_EVAL.verified_lock()
H_LOCK, _ = H_RUN.freeze()
GRID = dict(consensus=[Fraction(3, 4), Fraction(4, 5), Fraction(5, 6)],
            regular=[Fraction(2, 3), Fraction(3, 4), Fraction(4, 5)])
ABSOLUTE = frozenset({'point_odds', 'price_low', 'price_high', 'biggest_ticket_usd', 'volume_usd',
                      'volume_cumulative_usd', 'n_bets', 'share_pct', 'minute', 'spread_bp', 'goals'})


class Known:
    """One family of known banks: mining banks plus already-inspected validation banks."""

    def __init__(self, origin, family, phase, mining, validation, scorer):
        self.origin, self.family, self.phase = origin, family, phase
        self.mining, self.validation, self.scorer = mining, validation, scorer


def families():
    for i, (name, phase, _, _) in enumerate(bank_i.SCENARIOS):
        mining = [(s, bank_i.build(i, s, fresh=False)) for s in I_LOCK['design']['development_seeds']]
        validation = [(s, bank_i.build(i, s, fresh=True)) for s in I_LOCK['design']['validation_seeds']]
        yield Known('I', name, phase, mining, validation, lambda bank: I_EVAL.Evaluator(bank, I_LOCK))
    for i, (name, _, _) in enumerate(bank_h.WORLDS):
        mining = [(s, bank_h.build(i, s, holdout=False)) for s in H_LOCK['development_seeds']]
        validation = [(s, bank_h.build(i, s, holdout=True)) for s in H_LOCK['holdout_seeds']]
        yield Known('H', name, 'pre' if i == 0 else 'live', mining, validation,
                    lambda bank: H_RUN.Evaluator(bank, H_LOCK))


def _tape(origin, bank):
    return I_EVAL._tape(bank) if origin == 'I' else H_RUN._tape(bank)


def _score(evaluator, proposal):
    if proposal is None:
        return None
    row = evaluator.measure(proposal) if hasattr(evaluator, 'measure') else evaluator.score(proposal)
    return dict(detections=row['detections'], exact_terminals=row['exact_terminals'], exact_paths=row['exact_paths'],
                targets=row['targets'], gate=row['gate'],
                false_detections=row['detections'] - row['exact_terminals'])


def _absolute(rule):
    return sum(atom['field'] in ABSOLUTE for step in rule['steps'] for atom in step)


def _ranking(candidates, evaluator, tests):
    rows = []
    best = sorted(candidates, key=lambda c: -c['rank_score'])
    for position, c in enumerate(candidates):
        dev = _score(evaluator, c)
        val = [_score(t, c) for t in tests]
        rows.append(dict(position=position, length=len(c['rule']['steps']), channel=c['channel'],
                         rank_score=c['rank_score'], score_rank=best.index(c), support=c['support'],
                         contrast=c['observed_contrast_fixtures'], absolute_predicates=_absolute(c['rule']),
                         predicates=sum(map(len, c['rule']['steps'])) + len(c['rule']['relations']),
                         witness_sha256=c['witness_sha256'], development=dev,
                         validation_exact_paths=[v['exact_paths'] for v in val],
                         validation_false=[v['false_detections'] for v in val]))
    first = rows[0] if rows else None
    inversions = sum(a['rank_score'] < b['rank_score'] for a, b in zip(rows, rows[1:]))
    witnesses = Counter(r['witness_sha256'] for r in rows)
    return dict(
        candidates=len(rows),
        first=None if first is None else {k: first[k] for k in ('length', 'channel', 'rank_score', 'score_rank',
                                                               'support', 'predicates', 'validation_exact_paths')},
        max_length=max((r['length'] for r in rows), default=0),
        min_support=min((r['support'] for r in rows), default=0),
        adjacent_score_inversions=inversions,
        duplicated_witness_sets=sum(n - 1 for n in witnesses.values()),
        rows=rows)


def _coincidences(candidates, evaluator, tests):
    """Development fit that does not survive other banks of the same family."""
    out = []
    for position, c in enumerate(candidates):
        dev = _score(evaluator, c)
        if not dev['targets'] or Fraction(dev['exact_paths'], dev['targets']) < Fraction(3, 4):
            continue
        val = [_score(t, c) for t in tests]
        worst = min(v['exact_paths'] for v in val)
        if Fraction(worst, dev['targets']) < Fraction(1, 4):
            out.append(dict(position=position, length=len(c['rule']['steps']),
                            absolute_predicates=_absolute(c['rule']), development_exact_paths=dev['exact_paths'],
                            validation_exact_paths=[v['exact_paths'] for v in val]))
    return out


@contextmanager
def thresholds(consensus, regular):
    saved = refine.CONSENSUS, selector.REGULAR
    refine.CONSENSUS, selector.REGULAR = consensus, regular
    try:
        yield
    finally:
        refine.CONSENSUS, selector.REGULAR = saved


def _j(tape, phase, mined):
    index = observe.Index(Observed(tape))
    choice, audit = selector.select(mined['candidates'], index, phase)
    return choice, audit


def run(*, sensitivity=True):
    out = dict(schema='goal_roi_pr_j_known_development_v1', confirmatory=False,
               note='All banks below were inspected before PR J. Development evidence only.', families={})
    for fam in families():
        tests = [fam.scorer(bank) for _, bank in fam.validation]
        folds = []
        for seed, bank in fam.mining:
            tape = _tape(fam.origin, bank)
            fit = fam.scorer(bank)
            mined_i = miner_i.discover(tape)
            mined_h = miner_h.discover(tape)
            i_candidates = [c for c in mined_i['candidates'] if c['phase'] == fam.phase]
            ranking = _ranking(i_candidates, fit, tests) if fam.origin == 'I' else None
            choice, audit = _j(tape, fam.phase, mined_i)
            picks = dict(h=mined_h['candidates'][0] if mined_h['candidates'] else None,
                         i=i_candidates[0] if i_candidates else None, j=choice)
            fold = dict(seed=seed, j_abstained=choice is None, j_abstain_reason=audit['abstain_reason'],
                        j_checks=audit['checks'], j_shape=list(choice['shape']) if choice else None,
                        j_rule=choice['rule'] if choice else None,
                        truth_lengths=sorted({len(path) for path in bank.truth.values()}),
                        picks={name: dict(development=_score(fit, p), validation=[_score(t, p) for t in tests])
                               for name, p in picks.items()})
            if ranking is not None:
                fold['ranking'] = {k: v for k, v in ranking.items() if k != 'rows'}
                fold['ranking_rows'] = ranking['rows']
            fold['coincidences'] = dict(i=_coincidences(i_candidates, fit, tests),
                                        h=_coincidences(mined_h['candidates'], fit, tests))
            if sensitivity:
                grid = {}
                for c in GRID['consensus']:
                    for r in GRID['regular']:
                        with thresholds(c, r):
                            alt, alt_audit = _j(tape, fam.phase, mined_i)
                        scores = [_score(t, alt) for t in tests]
                        grid[f'{c}|{r}'] = dict(abstain=alt_audit['abstain_reason'],
                                                gates=sum(bool(s and s['gate']) for s in scores),
                                                exact_paths=[s['exact_paths'] if s else 0 for s in scores])
                fold['sensitivity'] = grid
            folds.append(fold)
        availability = [dict(seed=s, **I_EVAL.Evaluator(b, I_LOCK).availability())
                        for s, b in fam.validation] if fam.origin == 'I' else None
        out['families'][f'{fam.origin}:{fam.family}'] = dict(origin=fam.origin, phase=fam.phase, folds=folds,
                                                             validation_availability=availability)
    out['summary'] = summary(out)
    return out


def summary(out):
    table = {}
    for name, fam in out['families'].items():
        assessable = fam['validation_availability'] is None or all(
            a['eligible_targets'] >= I_LOCK['thresholds']['minimum_targets'] for a in fam['validation_availability'])
        row = dict(assessable=assessable)
        for engine in ('h', 'i', 'j'):
            vals = [v for f in fam['folds'] for v in f['picks'][engine]['validation']]
            scored = [v for v in vals if v is not None]
            row[engine] = dict(evaluations=len(vals), abstained=len(vals) - len(scored),
                               exact_paths=sum(v['exact_paths'] for v in scored),
                               targets=sum(v['targets'] for v in scored),
                               detections=sum(v['detections'] for v in scored),
                               exact_terminals=sum(v['exact_terminals'] for v in scored),
                               gates=sum(v['gate'] for v in scored))
        table[name] = row
    return table


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-sensitivity', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(sensitivity=not args.no_sensitivity), ensure_ascii=False, sort_keys=True, indent=2,
                     allow_nan=False, default=str))
