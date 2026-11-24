"""Reproduce v1/v2 retrieval, a frozen shifted holdout and witnessed power scenarios."""

import argparse
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
from time import perf_counter

from scangoal import chronology, event_intelligence as events, scientific_receipts
from scangoal.discovery import dsl, dsl_v2, engine, engine_v2, ledger, miner_v2, study
from scangoal.discovery.replay import Budget, Replay
from scangoal.discovery.replay_v2 import Timeline

RETRIEVAL_THRESHOLD = .60
REFERENCE_SEEDS = (83, 84, 85)
HOLDOUT_SEEDS = (73129, 96403)
HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BANK = load('original_bank', HERE.parent / 'temporal-discovery' / 'adversarial_bench.py')


def at_cut(audit, cutoff):
    """Serialize the original input prefix without later receipts or FT payloads."""
    observed = [e for e in audit['events'] if events.seen(e) <= cutoff]
    ids = {e['message_id'] for e in observed}
    results = {key: known for key, rows in audit.get('fixture_results', {}).items()
               if (known := [r for r in rows if chronology.parse_utc(r['available_utc']) <= cutoff])}
    return Replay(dict(audit, as_of_utc=events.iso(cutoff), events=observed, fixture_results=results,
                       episodes=[e for e in audit['episodes'] if all(i in ids for i in e['message_ids'])]))


def best(name, truth, cases):
    options = [dict(BANK.score(found, truth), steps=len(record['rule']['steps']), id=record['id'])
               for record, found in cases]
    return dict(pattern=name, cases=len(truth), **(max(options, key=lambda r: (r['jaccard'], r['id']))
                                                   if options else dict(jaccard=0, precision=0, recall=0)))


def retrieval(replay, truth, version):
    start = perf_counter()
    if version == 1:
        report, _ = engine.discover(replay)
        work = Budget(2_000_000)
        outcomes = [(r, {d.fixture for d in replay.decide(r['rule'], work)[0] if d.state == 'case'})
                    for r in report['investigations'] if not work.exhausted]
    else:
        report, _ = engine_v2.discover(replay)
        timeline = Timeline(replay)
        work = Budget(miner_v2.MAX_REPLAY_WORK)
        outcomes = [(r, {d.fixture for d in timeline.run(r['rule'], work, structural=True).decisions})
                    for r in report['investigations'] if not work.exhausted]
    truth_only = {name: rows for name, rows in truth.items() if name != 'false_promising_2'}
    scores = {name: best(name, rows, outcomes) for name, rows in sorted(truth.items())}
    recovered = [name for name in truth_only if scores[name]['jaccard'] >= RETRIEVAL_THRESHOLD]
    attributed = sum(any(BANK.score(result, rows)['jaccard'] >= RETRIEVAL_THRESHOLD for rows in truth_only.values())
                     for _, result in outcomes)
    return dict(version=version, elapsed_seconds=round(perf_counter() - start, 3), work=report['work'],
                structural_replay_operations=work.used, structural_replay_truncated=work.exhausted,
                selected=len(outcomes), true_candidates=attributed,
                candidate_precision=attributed / len(outcomes) if outcomes else 0,
                recovered=sorted(recovered), missed=sorted(set(truth_only) - set(recovered)),
                pattern_recall=len(recovered) / len(truth_only),
                false_motif_retrieved=scores.get('false_promising_2', {}).get('jaccard', 0) >= RETRIEVAL_THRESHOLD,
                scores=scores)


def reference(seed, days=30):
    cutoff = BANK.START + timedelta(days=days)
    world, truth = BANK.build(seed, days=days)
    discovery = at_cut(world.audit, cutoff)
    return dict(seed=seed, discovery_days=days, fixture_count=len(discovery.history),
                engines=[retrieval(discovery, truth, v) for v in (1, 2)])


def heldout(seed):
    generator = load('shifted_fixture_bank', HERE / 'heldout.py')
    world, cutoff = generator.build(BANK, seed)
    replay = at_cut(dict(as_of_utc=events.iso(cutoff), input_sha256={'messages.jsonl': '0'*64},
        events=world.events, episodes=world.episodes, fixture_results={}), cutoff)
    return dict(seed=seed, fixture_count=len(replay.history), held_out=True,
                engines=[retrieval(replay, world.planted, v) for v in (1, 2)])


def power(seed, version, *, null=False):
    """Power of the fixed evaluator given oracle rules, independent of mining.

    At discovery the deceptive motif shares the positive example's effect.
    In the prospective window its target probability returns to baseline.
    """
    start = perf_counter()
    discovery = BANK.START + timedelta(days=30)
    days = 30 + 1 + study.WINDOW.days + 4
    final = BANK.START + timedelta(days=days)
    all_games, _ = BANK.build(seed, days=days, false_until=30, effect=.48 if null else .78, as_of=final)
    prefix = at_cut(all_games.audit, discovery)
    app = engine if version == 1 else engine_v2
    language = dsl if version == 1 else dsl_v2
    oracles = {name: language.canonical(dict(rule, version=version)) for name, rule in BANK.ORACLES.items()}
    names = {language.identifier(rule): name for name, rule in oracles.items()}
    records = [dict(id=rid, rule=oracles[name], status='insufficient', origin='oracle',
                    reason='independent_power_assay', track_prospectively=True) for rid, name in names.items()]

    def witness(_, domain, content, *, as_of):
        return scientific_receipts.Receipt('synthetic_timestamp', scientific_receipts.digest(domain, content),
                                           discovery + timedelta(hours=2))

    with tempfile.TemporaryDirectory() as folder:
        ledger.append(folder, discovery_as_of=discovery, manifest=prefix.audit['input_sha256'],
                      snapshot_sha256=prefix.snapshot_sha256, records=records,
                      investigation=dict(protocol_sha256=dsl.digest(app.PROTOCOL),
                                         interpreter_sha256=app.INTERPRETER_SHA256),
                      now=discovery + timedelta(hours=1))
        result = app.evaluate(all_games, ledger.read(folder), verify=witness)
    rows = []
    for r in result['investigations']:
        ev = r.get('evidence', {})
        matched = ev.get('matched', {})
        rows.append(dict(pattern=names[r['id']], status=r['status'], reason=r['reason'],
                        prospective_fixtures=r['prospective_fixtures'],
                        pairs=matched.get('priced_pairs'), day_blocks=ev.get('days', matched.get('day_blocks')),
                        coverage=ev.get('coverage'), bounds=ev.get('mean_day_missing_ft_bounds'),
                        interval=ev.get('median_day_95pct_interval'),
                        insufficient=ev.get('insufficiency', matched.get('insufficiency')),
                        p=r['multiplicity']['adjusted_p']))
    supported = [r['pattern'] for r in rows if r['status'] == 'experimental']
    return dict(seed=seed, version=version, null=null, elapsed_seconds=round(perf_counter() - start, 3), rows=rows,
                true_retained=len([n for n in supported if n != 'false_promising_2']) if not null else 0,
                false_retained=len(supported) if null else int('false_promising_2' in supported))


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--suite', choices=('reference', 'periods', 'holdout', 'power', 'all'), default='reference')
    args = cli.parse_args()
    result = {'jaccard_threshold': RETRIEVAL_THRESHOLD, 'verdict': study.VERDICT}
    if args.suite in ('reference', 'all'):
        result['reference'] = [reference(seed) for seed in REFERENCE_SEEDS]
    if args.suite in ('periods', 'all'):
        result['periods'] = [reference(83, days) for days in (16, 45)]
    if args.suite in ('holdout', 'all'):
        result['holdout'] = [heldout(seed) for seed in HOLDOUT_SEEDS]
    if args.suite in ('power', 'all'):
        result['power'] = [power(seed, version) for seed in REFERENCE_SEEDS for version in (1, 2)]
        result['power_null'] = [power(seed, 2, null=True) for seed in REFERENCE_SEEDS]
    print(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
