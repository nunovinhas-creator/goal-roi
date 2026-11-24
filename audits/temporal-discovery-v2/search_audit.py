"""Outcome-blind projection ablations and a separately generated holdout."""

import argparse
from collections import defaultdict
from contextlib import contextmanager
from datetime import timedelta
import json
from time import perf_counter
from unittest.mock import patch

import benchmark as bench
from scangoal import event_intelligence as events
from scangoal.discovery import dsl_v2, miner_v2 as miner, replay_v2, study
from scangoal.discovery.replay import Budget

FROZEN = '42e0ca947648f38b167957be260f99122303903c'
HOLDOUT_SEEDS = (4619, 97751)


@contextmanager
def projection_variant(method):
    current = miner.projections
    similar = miner.similar

    def project(path, fields, marginals):
        for family, rule in current(path, fields, marginals):
            if method != 'current' and family[0] == 'scoped':
                continue
            if method in ('original', 'without_overlap_filter') and family[0] == 'mixed':
                family = ('event', *family[1:])
            yield family, rule

    with patch.object(miner, 'projections', project), patch.object(
            miner, 'similar', (lambda _a, _b: False) if method == 'without_overlap_filter' else similar):
        yield


def scope_bank(seed, *, days=30, per_day=20):
    """New bank authored after freezing the revised miner.

    Sources, anchor roles, clocks and score trajectories differ from the
    reference. Swapped-order controls share terminal anchors and marginal
    event features. The purely structural bank supplies no outcomes.
    """
    w = bench.BANK.World(seed)
    rng = w.rng
    start = bench.BANK.START + timedelta(days=300)
    names = ('role_exchange', 'draw_after_break', 'two_sources_change_period', 'live_parallel_relay')
    for day in range(days):
        for slot in range(per_day):
            fixture = f'independent-{day:02d}-{slot:02d}'
            ko = start + timedelta(days=day, hours=9 + slot % 13)
            w.noise(fixture, ko, rng.randint(3, 7))
            if rng.random() > .70:
                continue
            pattern = rng.choice(names)
            positive = rng.random() < .58
            if positive:
                w.planted.setdefault(pattern, set()).add(fixture)

            def emit(base, offset, source, side, line, *, minute=None, score=None, period='regulation'):
                signal = {'Betfair': 'BETFAIR', 'Stake': 'STAKE_LOAD', 'Polymarket': 'POLYMARKET'}[source]
                row = w.emit(fixture, ko, base + timedelta(minutes=offset), source=source,
                             signal=signal, phase='live' if minute is not None else 'pre', direction=side,
                             line=line, odds=w.odds(1.50, 3.70),
                             volume=float(rng.choice((3000, 9000, 40000))), minute=minute, score=score,
                             delay=rng.randint(5, 110))
                row['period'] = period

            before, live = ko - timedelta(minutes=rng.randint(127, 215)), ko + timedelta(minutes=15)
            if pattern == 'role_exchange':
                line = rng.choice(['1.5', '4.5'])
                for offset, (source, side) in enumerate((
                        ('Betfair', 'over' if positive else 'under'), ('Stake', 'under' if positive else 'over'),
                        ('Betfair', 'under'))):
                    emit(before, 19 * offset, source, side, line)
            elif pattern == 'draw_after_break':
                emit(live, 3, 'Betfair', 'over', '2.5', minute=18, score=[0, 0])
                emit(live, 15, 'Betfair', 'under', '2.5', minute=30, score=[0, 1] if positive else [1, 0])
                emit(live, 23, 'Betfair', 'over', '2.5', minute=38, score=[1, 1])
            elif pattern == 'two_sources_change_period':
                from_period, to_period = ('first_half', 'second_half') if positive else ('second_half', 'first_half')
                emit(before, 0, 'Polymarket', 'under', '1.5', period=from_period)
                emit(before, 11, 'Betfair', 'under', '1.5', period=to_period)
                emit(before, 24, 'Betfair', 'over', '3.5')
            else:
                middle_line = '4.5' if positive else '3.5'
                emit(live, 0, 'Stake', 'under', '4.5', minute=15, score=[1, 0])
                emit(live, 9, 'Betfair', 'over', middle_line, minute=24, score=[1, 0])
                emit(live, 19, 'Polymarket', 'under', '4.5', minute=34, score=[1, 0])
    cutoff = start + timedelta(days=days)
    audit = dict(as_of_utc=events.iso(cutoff), input_sha256={'messages.jsonl': '0' * 64},
                 events=w.events, episodes=w.episodes, fixture_results={})
    return bench.at_cut(audit, cutoff), w.planted


def probe(replay, truth, method):
    generated, qualities = {}, {}
    observed, quality = miner.Motif.observe, miner.quality

    def observe(self, path, day, population):
        observed(self, path, day, population)
        generated[self.key] = self

    def measure(motif, *args):
        value = quality(motif, *args)
        qualities[motif.key] = value
        return value

    timeline = replay_v2.Timeline(replay)
    with (projection_variant(method), patch.object(miner.Motif, 'observe', observe),
          patch.object(miner, 'quality', measure)):
        start = perf_counter()
        candidates, work = miner.mine(timeline)
        elapsed = perf_counter() - start
    approved_ids = {c.id for c in candidates}
    by_key = {normalized(m.key): m for m in generated.values()}
    shortlist_support = [by_key[candidate_key(c.rule)] for c in candidates]
    rankings = defaultdict(list)
    for m in generated.values():
        if m.key in qualities:
            rankings[m.length].append(m)
    rank = {m.key: i for group in rankings.values()
            for i, m in enumerate(sorted(group, key=lambda m: (-qualities[m.key], -len(m.fixtures), m.key)), 1)}
    result = dict(method=method, mine_seconds=round(elapsed, 3), work=work, patterns={})
    final = [(c, {d.fixture for d in timeline.run(c.rule, Budget(2_000_000), structural=True).decisions})
             for c in candidates]
    truth_only = {n: rows for n, rows in truth.items() if n != 'false_promising_2'}
    result['precision'] = sum(any(bench.BANK.score(found, rows)['jaccard'] >= bench.RETRIEVAL_THRESHOLD
                                  for rows in truth_only.values()) for _, found in final) / max(1, len(final))
    for name, labels in sorted(truth.items()):
        preselected = sorted(generated.values(),
                             key=lambda m: (-bench.BANK.score(m.fixtures, labels)['jaccard'], m.key))[:8]
        before = defaultdict(list)
        for m in preselected:
            definition = dsl_v2.canonical(miner.rule(m.key))
            rid = dsl_v2.identifier(definition)
            exact = {d.fixture for d in timeline.run(definition, Budget(2_000_000), structural=True).decisions}
            before['options'].append(dict(projection=list(m.family), support=len(m.fixtures),
                sampled_jaccard=bench.BANK.score(m.fixtures, labels)['jaccard'],
                replay_jaccard=bench.BANK.score(exact, labels)['jaccard'], rule=definition, selected=rid in approved_ids,
                base_rank_within_length=rank.get(m.key),
                suppressed_if_all_selected_preceded=sum(miner.similar(m.fixtures, generated_candidate.fixtures)
                    for generated_candidate in shortlist_support if generated_candidate.key != m.key)))
        closest, found = max(final, key=lambda r: (bench.BANK.score(r[1], labels)['jaccard'], r[0].id))
        before['best_shortlisted'] = dict(definition=closest.rule, id=closest.id, **bench.BANK.score(found, labels))
        result['patterns'][name] = dict(before)
    result['recovered'] = [n for n in truth_only
                           if result['patterns'][n]['best_shortlisted']['jaccard'] >= bench.RETRIEVAL_THRESHOLD]
    return result


def candidate_key(definition):
    steps = tuple(tuple((a['field'], a['op'], a['value']) for a in step) for step in definition['steps'])
    relations = tuple((r['from'], r['to'], r['field'], r['op'], r['value']) for r in definition['relations'])
    return normalized((steps, relations))


def normalized(key):
    steps, relations = key
    return tuple(tuple(sorted(s)) for s in steps), tuple(sorted(relations))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=('reference', 'holdout', 'all'), default='all')
    args = parser.parse_args()
    results = dict(frozen_miner=FROZEN, verdict=study.VERDICT)
    if args.suite != 'holdout':
        results['reference'] = []
        for seed in bench.REFERENCE_SEEDS:
            full, planted = bench.BANK.build(seed)
            cut = bench.at_cut(full.audit, bench.BANK.START + timedelta(days=30))
            results['reference'].append(dict(seed=seed, alternatives=[
                probe(cut, planted, method) for method in ('original', 'without_overlap_filter', 'split_joint', 'current')]))
    if args.suite != 'reference':
        results['previously_frozen_holdout'] = [bench.heldout(seed) for seed in bench.HOLDOUT_SEEDS]
        results['independent'] = []
        for seed in HOLDOUT_SEEDS:
            cut, planted = scope_bank(seed)
            results['independent'].append(dict(seed=seed, alternatives=[
                probe(cut, planted, method) for method in ('original', 'current')]))
    print(json.dumps(results, allow_nan=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
