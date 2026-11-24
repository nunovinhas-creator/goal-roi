"""Re-evaluate the six oracle rules and the rules actually discovered under v3.

Uses the recorded v1/v2 bank (seeds 83/84/85) without changing its draws.
For every rule it separates three stages that v2 conflated:

* entry opportunity: does the rule reserve the planted fixtures, at the
  planted terminal, and not before the terminal was observable?
* selection: is the decision's contract the generator's target contract?
* outcome: given the decision actually taken, what is the known expected
  increment over the risk-set comparator (from the generator's FT law), and
  what do the observed v3 layers say?

The protocol design is reported together with a relaxed, non-protocol
sensitivity design (wider quote age, clock and price calipers).
Oracles, plants and targets are read only by this audit.
"""

import argparse
from collections import Counter
from datetime import timedelta
import json
from math import fsum
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'temporal-discovery-v2'))

import benchmark as bench  # noqa: E402
import scientific_audit as v2audit  # noqa: E402
from search_audit import projection_variant  # noqa: E402
from scangoal import event_audit, event_intelligence as events  # noqa: E402
from scangoal.discovery import dsl, dsl_v2, engine_v2, study  # noqa: E402
from scangoal.discovery.replay import Budget  # noqa: E402
from scangoal.discovery.replay_v2 import Timeline  # noqa: E402
from scangoal.discovery.v3 import engine, protocol, riskset  # noqa: E402

BANK = bench.BANK
RELAXED = dict(quote_age=timedelta(hours=6), live_age=timedelta(minutes=20), lead=180., minute=15., log_odds=.35)


def win_probability(target, anchor, p):
    """P(decision wins) under the bank's FT law: goals uniform over the target's win/loss set."""
    direction, line, _ = target
    wins, losses = [], []
    for goals in range(7):
        value = event_audit.payoff(anchor, (goals, 0))
        if value is None:
            return None
        hit = (goals > float(line)) == (direction == 'over')
        (wins if hit else losses).append(value > 0)
    return p * fsum(wins) / len(wins) + (1 - p) * fsum(losses) / len(losses)


def probability(planted, fixture):
    pattern = planted.get(fixture, (None,))[0]
    return v2audit.EFFECT if pattern and pattern != 'false_promising_2' else v2audit.BASE


def stages(full, planted, targets, name, cohort, window):
    start, end = window
    future = {fx for fx, (p, rows) in planted.items() if p == name and start <= events.kickoff(rows[-1]) < end}
    cases = cohort.cases
    hit = [d for d in cases if d.fixture in future]
    entry = dict(planted_forward=len(future), cases=len(cases), cases_on_planted=len(hit),
                 exact_terminal=sum(d.anchor['message_id'] == planted[d.fixture][1][-1]['message_id'] for d in hit),
                 before_planted_terminal=sum(d.at < events.seen(planted[d.fixture][1][-1]) for d in hit))
    selection = dict(planted_cases_on_target_contract=sum(
        (d.anchor['direction'], d.anchor['line']) == targets[d.fixture][:2] for d in hit),
        all_cases_on_target_contract=sum((d.anchor['direction'], d.anchor['line']) == targets[d.fixture][:2]
                                         for d in cases))
    increments, on_plant = [], []
    for case, control, meta in cohort.pairs:
        a = win_probability(targets[case.fixture], case.anchor, probability(planted, case.fixture))
        b = win_probability(targets[control.fixture], control.anchor, probability(planted, control.fixture))
        if a is None or b is None:
            continue
        value = (a - 1 / meta['case_odds']) - (b - 1 / meta['control_odds'])
        increments.append(value)
        if case.fixture in future:
            on_plant.append(value)
    outcome = dict(pairs=len(cohort.pairs), pairs_with_known_law=len(increments),
                   expected_prediction_increment=fsum(increments) / len(increments) if increments else None,
                   expected_increment_on_planted_cases=fsum(on_plant) / len(on_plant) if on_plant else None)
    return dict(entry=entry, selection=selection, outcome=outcome)


def evaluate(full, planted, targets, rules, window, discovery, design_kwargs):
    design = riskset.RiskSet(full, start=window[0], end=window[1], discovery_cutoff=discovery, **design_kwargs)
    evidences, rows = [], []
    for item in rules:
        evidence, cohort = engine.assess(full, item['rule'], budget=Budget(engine.MAX_WORK), design=design)
        layers = evidence['layers']
        row = dict(label=item['label'], id=engine.hypothesis_id(item['rule']), counts=evidence['counts'],
                   balance=evidence['balance'],
                   observed={n: dict(mean=layers[n]['mean_observed'], bounds=layers[n]['mean_bounds'],
                                     p=layers[n]['p'], insufficiency=layers[n]['insufficiency'],
                                     clusters={k: v['clusters'] for k, v in layers[n]['tests'].items()})
                             for n in engine.LAYERS},
                   executable=layers['executable']['status'])
        if item.get('pattern'):
            row.update(stages(full, planted, targets, item['pattern'], cohort, window))
        rows.append(row)
        evidences.append(evidence)
    for row, verdict in zip(rows, engine.decide(evidences, 1, closed=True)):
        row['v3_statistics'] = verdict
    return rows


def seed_report(seed):
    full, planted, targets = v2audit.recorded_world(seed)
    discovery = bench.at_cut(full.audit, BANK.START + timedelta(days=30))
    start = discovery.as_of + timedelta(days=1)
    window = start, start + study.WINDOW
    with projection_variant('current'):
        report, registered = engine_v2.discover(discovery)
    before = Timeline(discovery)
    structures = [(r, {d.fixture for d in before.run(r['rule'], Budget(2_000_000), structural=True).decisions})
                  for r in report['investigations']]
    oracles, selected = [], []
    for name, rule in BANK.ORACLES.items():
        canonical = dsl_v2.canonical(dict(rule, version=2))
        oracles.append(dict(label='oracle:' + name, pattern=name, rule=canonical))
        started = {fx for fx, (p, _) in planted.items() if p == name and fx in discovery.history}
        best, found = max(structures, key=lambda x: (BANK.score(x[1], started)['jaccard'], x[0]['id']))
        selected.append(dict(label='selected:' + name, pattern=name, rule=best['rule'],
                             discovery_jaccard=BANK.score(found, started)['jaccard']))
    discovered = [dict(label='discovered:' + r['id'][:20], rule=r['rule']) for r in registered
                  if r.get('track_prospectively') and 'rule' in r]
    out = dict(seed=seed, window=[events.iso(t) for t in window], discovered_tracked=len(discovered))
    for name, kwargs in (('protocol', {}), ('relaxed_sensitivity_non_protocol', RELAXED)):
        out[name] = dict(oracles=evaluate(full, planted, targets, oracles, window, discovery.as_of, kwargs),
                         selected=evaluate(full, planted, targets, selected, window, discovery.as_of, kwargs))
        for row, item in zip(out[name]['selected'], selected):
            row['discovery_jaccard'] = item['discovery_jaccard']
    out['protocol']['discovered_family'] = evaluate(full, planted, targets, discovered, window, discovery.as_of, {})
    out['protocol']['discovered_family_states'] = dict(Counter(
        r['v3_statistics']['status'] for r in out['protocol']['discovered_family']))
    return out


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--seed', type=int, action='append')
    args = cli.parse_args(argv)
    seeds = args.seed or list(bench.REFERENCE_SEEDS)
    print(json.dumps(dict(protocol_sha256=protocol.PROTOCOL_SHA256, interpreter_sha256=engine.INTERPRETER_SHA256,
                          seeds=[seed_report(s) for s in seeds], verdict=engine.VERDICT),
                     ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False, default=str))


if __name__ == '__main__':
    main()
