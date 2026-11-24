"""K development ablation, as frozen in plan.json (7c3a102). Development evidence only.

Runs J, J+K1, J+K2, J+K3 and K on the I, H, J and KDEV development sources,
scores every family x mining seed x validation seed cell, the 3 x 3 threshold
grid on the J and KDEV sources, transport invariance, and the lock rules L1-L5.
"""

from fractions import Fraction
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
J_DIR = ROOT / 'audits/selective-abstention'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


known = _load('goal_roi_k_view_of_j_known', J_DIR / 'known.py')
j_eval = _load('goal_roi_k_view_of_j_evaluate', J_DIR / 'evaluate.py')
bank_j = j_eval.bank_j
dev_bank = _load('goal_roi_k_dev_bank', HERE / 'dev_bank.py')

from scangoal.discovery.experimental_h.observed import Observed  # noqa: E402
from scangoal.discovery.experimental_i import miner as miner_i  # noqa: E402
from scangoal.discovery.experimental_j.observe import Index  # noqa: E402
from scangoal.discovery.experimental_k import selector as K  # noqa: E402
from scangoal.discovery.v32.tape import Tape  # noqa: E402

J_LOCK, _ = j_eval.verified_lock()
DEFAULT = (Fraction(4, 5), Fraction(3, 4))
GRID = [(c, r) for c in (Fraction(3, 4), Fraction(4, 5), Fraction(5, 6))
        for r in (Fraction(2, 3), Fraction(3, 4), Fraction(4, 5))]
ADJACENT = [(Fraction(3, 4), Fraction(3, 4)), (Fraction(5, 6), Fraction(3, 4)),
            (Fraction(4, 5), Fraction(2, 3)), (Fraction(4, 5), Fraction(4, 5))]


def _key(point):
    return f'{point[0]}|{point[1]}'


class Source:
    def __init__(self, name, family, phase, mining, validation, scorer, tape, transform=None):
        self.name, self.family, self.phase = name, family, phase
        self.mining, self.validation, self.scorer, self.tape, self.transform = mining, validation, scorer, tape, transform


def sources():
    for fam in known.families():
        yield Source(fam.origin, fam.family, fam.phase, fam.mining, fam.validation,
                     lambda bank, f=fam: f.scorer(bank),
                     lambda bank, o=fam.origin: known._tape(o, bank))
    for module, name, mining, validation in (
            (bank_j, 'J', J_LOCK['design']['mining_seeds'], J_LOCK['design']['validation_seeds']),
            (dev_bank, 'KDEV', dev_bank.MINING_SEEDS, dev_bank.VALIDATION_SEEDS)):
        for i, (family, phase, _, _) in enumerate(module.FAMILIES):
            yield Source(name, family, phase,
                         [(s, module.build(i, s, fresh=False)) for s in mining],
                         [(s, module.build(i, s, fresh=True)) for s in validation],
                         lambda bank: j_eval.Evaluator(bank, J_LOCK),
                         lambda bank: Tape(bank.observations, bank.cutoff, bank.coverage),
                         transform=module.transformed)


def _cell(evaluator, choice):
    if choice is None:
        return dict(abstained=True, detections=0, exact_terminals=0, exact_paths=0,
                    targets=len(getattr(evaluator, 'bank', None) and evaluator.bank.truth or evaluator.mining_bank.truth), gate=False)
    m = evaluator.measure(choice) if hasattr(evaluator, 'measure') else evaluator.score(choice)
    d, t, p, n = m['detections'], m['exact_terminals'], m['exact_paths'], m['targets']
    gate = bool(d and Fraction(t, d) >= Fraction(9, 10) and Fraction(t, n) >= Fraction(4, 5)
                and Fraction(p, n) >= Fraction(3, 4) and m.get('matcher_complete', m.get('search_complete', True)))
    return dict(abstained=False, detections=d, exact_terminals=t, exact_paths=p, targets=n, gate=gate)


def run():
    rows, transport = [], []
    for src in sources():
        tests = [(s, src.scorer(bank)) for s, bank in src.validation]
        points = GRID if src.name in ('J', 'KDEV') else [DEFAULT]
        for seed, bank in src.mining:
            tape = src.tape(bank)
            index, mined = Index(Observed(tape)), miner_i.discover(tape)
            for variant, base in K.VARIANTS.items():
                for point in points:
                    config = K.with_thresholds(base, *point)
                    out = K.investigate(tape, phase=src.phase, config=config, index=index, mined=mined)
                    choice = out['choice']
                    for vseed, evaluator in tests:
                        rows.append(dict(source=src.name, family=src.family, phase=src.phase, variant=variant,
                                         grid=_key(point), mining=seed, validation=vseed,
                                         abstain_reason=out['audit']['abstain_reason'],
                                         shape_length=len(choice['shape']) if choice else None,
                                         enumerated_shapes=out['audit']['enumerated_shapes'],
                                         **_cell(evaluator, choice)))
                    if point == DEFAULT and src.transform is not None:
                        moved = src.tape(src.transform(bank, seed + J_LOCK['design']['transformation_seed_offset']))
                        again = K.investigate(moved, phase=src.phase, config=config)
                        transport.append(dict(source=src.name, family=src.family, variant=variant, mining=seed,
                                              same_choice=(choice or {}).get('id') == (again['choice'] or {}).get('id')))
    return rows, transport


def summarise(rows):
    table = {}
    for r in rows:
        k = (r['source'], r['family'], r['variant'], r['grid'])
        t = table.setdefault(k, dict(cells=0, gates=0, abstained=0, detections=0, exact_terminals=0, exact_paths=0,
                                     targets=0))
        t['cells'] += 1
        t['gates'] += r['gate']
        t['abstained'] += r['abstained']
        for f in ('detections', 'exact_terminals', 'exact_paths', 'targets'):
            t[f] += r[f]
    return table


def _passed(rows, variant, grid):
    return {(r['source'], r['family'], r['mining'], r['validation']) for r in rows
            if r['variant'] == variant and r['grid'] == grid and r['gate']}


def _cells(rows, grid, predicate):
    return {(r['source'], r['family'], r['mining'], r['validation']) for r in rows
            if r['variant'] == 'J' and r['grid'] == grid and predicate(r)}


def rules(rows, transport):
    default = _key(DEFAULT)
    j_pass = _passed(rows, 'J', default)
    required = _cells(rows, default, lambda r: (r['source'] == 'J' and r['phase'] == 'pre') or r['source'] == 'KDEV')
    live_j = _cells(rows, default, lambda r: r['source'] == 'J' and r['phase'] == 'live')
    out = {}
    for variant in K.VARIANTS:
        p = _passed(rows, variant, default)
        at_default = dict(L1=j_pass <= p, L2=required <= p, L3=live_j <= p)
        adjacent = {}
        for point in ADJACENT:
            p = _passed(rows, variant, _key(point))
            adjacent[_key(point)] = dict(L1=j_pass <= p, L2=required <= p, L3=live_j <= p)
        out[variant] = dict(
            passing_cells=len(_passed(rows, variant, default)), total_cells=len({(r['source'], r['family'], r['mining'], r['validation'])
                                                                              for r in rows if r['variant'] == variant and r['grid'] == default}),
            lost_vs_j=sorted(map(list, j_pass - _passed(rows, variant, default))),
            required_failed=len(required - _passed(rows, variant, default)), **at_default,
            L4=all(at_default.values()) and any(all(v.values()) for v in adjacent.values()), adjacent=adjacent,
            transport_invariant=all(t['same_choice'] for t in transport if t['variant'] == variant))
    return out


def l5():
    text = '\n'.join(p.read_text() for p in (ROOT / 'scangoal/discovery/experimental_k').glob('*.py'))
    names = set()
    for module in (bank_j, dev_bank, known.bank_i):
        for family in getattr(module, 'FAMILIES', getattr(module, 'SCENARIOS', ())):
            names.add(family[0])
            names.update(family[3])
    for family in known.bank_h.WORLDS:
        names.add(family[0])
        names.update(family[2])
    leaked = sorted(n for n in names if n in text)
    j_lock = json.loads((J_DIR / 'freeze.json').read_bytes())
    intact = all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h
                 for section in ('algorithm_sha256', 'frozen_inputs_sha256') for p, h in j_lock[section].items())
    return dict(names_checked=len(names), leaked=leaked, hij_hashes_intact=intact, passed=not leaked and intact)


def main():
    rows, transport = run()
    summary = summarise(rows)
    lock_rules = rules(rows, transport)
    guard = l5()
    eligible = [v for v, r in lock_rules.items() if r['L1']]
    best = max(eligible, key=lambda v: (lock_rules[v]['passing_cells'], -sum(c in v for c in '123'), v == 'J'),
               default=None)
    candidate = lock_rules.get(best, {})
    go = bool(best and best != 'J' and candidate['L1'] and candidate['L2'] and candidate['L3'] and candidate['L4']
              and candidate['transport_invariant'] and guard['passed'])
    return dict(schema='goal_roi_k_development_ablation_v1', plan_sha256=hashlib.sha256((HERE / 'plan.json').read_bytes()).hexdigest(),
                confirmatory=False, blind_banks_used=False,
                summary=[dict(source=k[0], family=k[1], variant=k[2], grid=k[3], **v) for k, v in sorted(summary.items())],
                transport=transport, lock_rules=lock_rules, L5=guard, candidate=best,
                recommendation=dict(definitive_k_lock='GO' if go else 'NO-GO', independent_validation='NOT RUN',
                                    scientific_independence='NOT DECLARED', final_decision='human'),
                cells=rows, verdict='RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.')


if __name__ == '__main__':
    print(json.dumps(main(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False, default=str))
