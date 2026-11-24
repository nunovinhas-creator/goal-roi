"""Trace planted mechanisms, forecasts and frozen v2 decisions on independent windows."""

import argparse
from collections import Counter, defaultdict
from datetime import timedelta
import json
from statistics import mean, median
from tempfile import TemporaryDirectory
from unittest.mock import patch

import benchmark as bench
from search_audit import projection_variant
from scangoal import event_audit, event_intelligence as events, scientific_receipts
from scangoal.discovery import blocks_v2 as blocks, dsl, dsl_v2, engine_v2, ledger, study
from scangoal.discovery.replay import Budget, Replay
from scangoal.discovery.replay_v2 import Sequence, Timeline, arm

BANK = bench.BANK
EFFECT, BASE = .78, .48
NAMES = tuple(BANK.ORACLES)
PREDICTION_PRIOR = 10


def mean_or_none(values):
    values = list(values)
    return mean(values) if values else None


def median_or_none(values):
    values = list(values)
    return median(values) if values else None


def recorded_world(seed):
    """Record the generator's intended contract and events without changing its draws."""
    planted, targets = {}, {}

    def record_plan(name, implementation):
        def planter(w, fixture, kickoff):
            start = len(w.events)
            result = implementation(w, fixture, kickoff)
            planted[fixture] = name, tuple(w.events[start:])
            return result
        return planter

    def record_target(original):
        def settle(w, fixture, kickoff, direction, line, win):
            targets[fixture] = direction, line, win
            original(w, fixture, kickoff, direction, line, win)
        return settle

    plans = {n: record_plan(n, fn) for n, fn in BANK.PLANTERS.items()}
    days = 30 + 1 + study.WINDOW.days + 4
    with patch.dict(BANK.PLANTERS, plans, clear=True), patch.object(BANK.World, 'settle', record_target(BANK.World.settle)):
        full, _ = BANK.build(seed, days=days, effect=EFFECT, base=BASE, false_until=30,
                             as_of=BANK.START + timedelta(days=days))
    return full, planted, targets


def contract(event):
    return event['direction'], event['line']


def expected_roi(target, anchor, p):
    """Expected decision profit under the legacy generator's known FT distribution."""
    direction, line, _ = target
    odds = dsl.point_odds(anchor)
    wins, losses = [], []
    if odds is None:
        return None
    for goals in range(7):
        value = event_audit.payoff(anchor, (goals, 0))
        if value is None:
            return None
        won_target = (goals > float(line)) == (direction == 'over')
        (wins if won_target else losses).append(blocks.net(value * (odds - 1) if value > 0 else value))
    return p * mean(wins) + (1 - p) * mean(losses)


def chart_clock(decisions):
    return dict(
        odds_median=median_or_none(x for d in decisions if (x := dsl.point_odds(d.anchor)) is not None),
        minutes_from_kickoff_median=median_or_none((d.at - d.kickoff).total_seconds() / 60
                                                  for d in decisions if d.kickoff is not None),
        live_minute_median=median_or_none(d.anchor['minute'] for d in decisions if d.anchor['minute'] is not None),
        strata=dict(Counter('/'.join(str(v) for v in d.stratum[1:7]) for d in decisions)),
    )


def eligibility(timeline, definition):
    sequence = Sequence(definition)
    fixtures, counts = set(), Counter()
    for index, (_, row, _) in enumerate(timeline.eligible_anchors):
        context = timeline.context(index, definition['within_seconds'])
        if sequence.eligible(context):
            counts[arm(row['fixture_id'])] += 1
            fixtures.add(row['fixture_id'])
    return dict(anchors_by_arm=dict(counts), fixtures=sorted(fixtures))


def paired_lag(design):
    lags = []
    for _, cases, controls in design.blocks:
        for a, _, ac in cases:
            for b, _, bc in controls:
                if blocks.distance(ac, bc, a.anchor['phase']):
                    lags.append((b.at - b.kickoff - (a.at - a.kickoff)).total_seconds() / 60)
    return dict(comparable_pairs=len(lags), control_minus_case_signed_minutes=mean_or_none(lags),
                absolute_minutes_median=median(map(abs, lags)) if lags else None,
                greater_than_15_minutes=sum(abs(v) >= 15 for v in lags))


def forecast(replay_at_train, prior, replay, cohort):
    """Fit hit-rate offsets on discovery, score them on later complete cases."""
    train = defaultdict(list)
    for decision in prior.decisions:
        label = replay_at_train.label(decision)
        if label['payoff'] in (-1., 1.) and label['odds'] is not None:
            train[decision.state, decision.stratum[1:7]].append(label['payoff'] > 0)
    estimates = defaultdict(list)
    for decision in cohort.decisions:
        label = replay.label(decision)
        if decision.state != 'case' or label['payoff'] not in (-1., 1.) or label['odds'] is None:
            continue
        implied = 1 / label['odds']
        actual = label['payoff'] > 0
        for group in ('case', 'control'):
            values = train[group, decision.stratum[1:7]]
            estimate = (sum(values) + PREDICTION_PRIOR * implied) / (len(values) + PREDICTION_PRIOR)
            estimates[group + '_brier'].append((estimate - actual) ** 2)
            estimates[group + '_training_support'].append(len(values))
        estimates['raw_implied_brier'].append((implied - actual) ** 2)
    return dict(scored=len(estimates['raw_implied_brier']), **{n: mean_or_none(v) for n, v in estimates.items()})


def comparison(replay, cohort, design, planted, targets, *, end, name, include_rows):
    evidence = blocks.profile(replay, cohort, design, end=end)
    evidence['comparison_lag'] = paired_lag(design)
    evidence['clock_by_arm'] = {group: chart_clock([d for d in cohort.decisions if d.state == group])
                                for group in ('case', 'control')}
    evidence['effects'] = {}
    expected = {}
    for group in ('case', 'control'):
        data, anomalies = defaultdict(list), Counter()
        for d in cohort.decisions:
            if d.state != group:
                continue
            label = replay.label(d)
            pattern, original = planted.get(d.fixture, (None, ()))
            if pattern == name:
                anomalies['intended_pattern'] += 1
                anomalies['actual_target_contract'] += contract(d.anchor) == targets[d.fixture][:2]
                anomalies['exact_terminal'] += d.anchor['message_id'] == original[-1]['message_id']
                anomalies['premature'] += d.at < events.seen(original[-1])
                anomalies['exact_witness'] += tuple(e['message_id'] for e in d.witness) == tuple(
                    e['message_id'] for e in original)
                if label['ft'] and d.anchor['score'] is not None:
                    anomalies['ft_below_live_score'] += any(ft < live for ft, live in zip(label['ft'], d.anchor['score']))
            if d.anchor['odds'] is not None:
                p = EFFECT if pattern and pattern != 'false_promising_2' else BASE
                nominal, baseline = (expected_roi(targets[d.fixture], d.anchor, prob) for prob in (p, BASE))
                if nominal is not None:
                    expected[d.fixture] = nominal, baseline
                    data['expected_at_decision'].append(nominal)
                    data['expected_under_no_injection_at_decision'].append(baseline)
                    if pattern == name:
                        data['injected_roi_increment_at_decision_on_planted_cases'].append(nominal - baseline)
            if label['profit'] is not None:
                data['observed_at_decision'].append(blocks.net(label['profit']))
            if include_rows:
                data['decision_rows'].append(dict(proof=d.describe(), terminal_id=original[-1]['message_id']
                                                   if original else None, plant=pattern, target=targets[d.fixture],
                                                   label=label, matched_covariates=blocks.covariates(d)))
        evidence['effects'][group] = dict(anomalies, **{n: v if n == 'decision_rows' else mean_or_none(v)
                                                      for n, v in data.items()})
    day_effects = defaultdict(list)
    for key, cases, controls in design.blocks:
        if not all(d.fixture in expected for rows in (cases, controls) for d, _, _ in rows):
            continue
        day_effects[key[0]].append(tuple(
            blocks.weighted([(expected[d.fixture][idx], w) for d, w, _ in cases])
            - blocks.weighted([(expected[d.fixture][idx], w) for d, w, _ in controls]) for idx in range(2)))
    a, b = (mean_or_none(mean(row[i] for row in rows) for rows in day_effects.values()) for i in range(2))
    evidence['expected_day_difference'] = dict(injected=a, target_probabilities_equalized=b,
                                               incremental=a - b if a is not None else None, days=len(day_effects))
    return evidence


def ablate(replay, planted, name, definition, original):
    """Destroy the planted antecedent while holding terminal quotes and FT fixed."""
    ids = {e['message_id'] for p, rows in planted.values() if p == name for e in rows[:-1]}
    audit = replay.audit
    altered = Replay(dict(audit, events=[e for e in audit['events'] if e['message_id'] not in ids],
                          episodes=[x for x in audit['episodes'] if not ids.intersection(x['message_ids'])]))
    past = {d.fixture for d in original}
    kept = {d.fixture for d in Timeline(altered).run(definition, Budget(2_000_000), structural=True).decisions}
    return len(past & kept)


def publish(replay, discovery, records):
    cutoff = discovery.as_of

    def witness(_, domain, payload, *, as_of):
        return scientific_receipts.Receipt('synthetic', scientific_receipts.digest(domain, payload),
                                           cutoff + timedelta(hours=2))

    with TemporaryDirectory() as registry:
        ledger.append(registry, discovery_as_of=cutoff, manifest=discovery.audit['input_sha256'],
                      snapshot_sha256=discovery.snapshot_sha256, records=records, now=cutoff + timedelta(hours=1),
                      investigation=dict(protocol_sha256=engine_v2.PROTOCOL_SHA256,
                                         interpreter_sha256=engine_v2.INTERPRETER_SHA256))
        report = engine_v2.evaluate(replay, ledger.read(registry), verify=witness)
    return {r['id']: r for r in report['investigations']}


def concise(row):
    if row is None:
        return None
    return {name: row.get(name) for name in ('status', 'reason', 'multiplicity')}


def calibrated_live(seed, delta):
    """An independent LIVE cohort with equal terminal opportunities and physical FT."""
    world = BANK.World(seed)
    cutoff = BANK.START + timedelta(days=30)
    start = cutoff + timedelta(days=1)
    end = start + study.WINDOW
    for day in range(study.WINDOW.days):
        for slot in range(24):
            fx = f'calibrated-live-{day}-{slot}'
            ko = start + timedelta(days=day, hours=11 + slot % 10)
            exposed = world.rng.random() < .5
            world.emit(fx, ko, ko + timedelta(minutes=69), source='ScanGoal', phase='live',
                       signal='GOAL_BREWING' if exposed else 'TOTALS_FORM', minute=54, score=[1, 1])
            world.emit(fx, ko, ko + timedelta(minutes=75), source='Polymarket', signal='POLYMARKET',
                       phase='live', direction='over', line='2.5', odds='2.40', minute=60, score=[1, 1])
            # Consume the same draws at every delta. Only the goal after receipt
            # changes when switching the effect size.
            won = world.rng.random() < BASE + delta * exposed
            available = events.iso(ko + timedelta(minutes=135))
            world.results[fx] = [dict(source='synthetic', record_id=fx, fixture_id=fx, link=None,
                                      score=(2, 1) if won else (1, 1), available_utc=available,
                                      observed_utc=available, published_utc=available, kickoff_utc=events.iso(ko))]
    audit = dict(as_of_utc=events.iso(end + timedelta(days=4)), input_sha256={'messages.jsonl': '0' * 64},
                 events=world.events, episodes=world.episodes, fixture_results=world.results)
    replay = Replay(audit)
    prefix = bench.at_cut(audit, cutoff)
    rule = dsl_v2.canonical(dict(BANK.ORACLES['live_context_x_price_2'], version=2))
    code = dsl_v2.identifier(rule)
    records = [dict(id=code, rule=rule, status='insufficient', origin='oracle',
                    reason='physically_consistent_live_assay', track_prospectively=True)]
    row = publish(replay, prefix, records)[code]
    evidence = row['evidence']
    return dict(seed=seed, delta=delta, fixtures=len(replay.history),
                coverage=evidence['coverage'], days=evidence['days'],
                observed_difference=evidence['mean_day_difference'], official=concise(row))


def seed_report(seed, include_rows=False, generator='current'):
    full, planted, targets = recorded_world(seed)
    discovery = bench.at_cut(full.audit, BANK.START + timedelta(days=30))
    start = discovery.as_of + timedelta(days=1)
    end = start + study.WINDOW
    before, after = Timeline(discovery), Timeline(full, start=start, end=end, discovery_cutoff=discovery.as_of)
    with projection_variant(generator):
        report, registered = engine_v2.discover(discovery)
    candidates = [(row, before.run(row['rule'], Budget(2_000_000), structural=True))
                  for row in report['investigations']]
    oracles = {n: dsl_v2.canonical(dict(rule, version=2)) for n, rule in BANK.ORACLES.items()}
    oracle_records = [dict(id=dsl_v2.identifier(rule), rule=rule, status='insufficient', origin='oracle',
                           reason='scientific_audit_oracle', track_prospectively=True) for rule in oracles.values()]
    oracle_result = publish(full, discovery, oracle_records)
    discovered_result = publish(full, discovery, registered)
    result = []
    for name, rule in oracles.items():
        started = {fx for fx, (p, _) in planted.items() if p == name and fx in discovery.history}
        future = {fx for fx, (p, rows) in planted.items() if p == name and start <= events.kickoff(rows[-1]) < end}
        best, structure = max(candidates, key=lambda x: (BANK.score({d.fixture for d in x[1].decisions}, started)
                                                         ['jaccard'], x[0]['id']))

        def investigate(definition, search, official):
            score = BANK.score({d.fixture for d in search.decisions}, started)
            attained = [d for d in search.decisions if d.fixture in started]
            cohort = after.run(definition, Budget(2_000_000))
            overlap = blocks.overlap(cohort.decisions, Budget(blocks.MAX_BLOCK_WORK))
            completions = {d.fixture: d.at for d in after.run(definition, Budget(2_000_000),
                                                             structural=True).decisions}
            later_exposure = sum(d.state == 'control' and d.fixture in completions and d.at < completions[d.fixture]
                                 for d in cohort.decisions)
            eligibility_report = eligibility(after, definition)
            reached = set(eligibility_report.pop('fixtures'))
            return dict(
                id=dsl_v2.identifier(definition), definition=definition, retrieval=score,
                structural= dict(positive=len(search.decisions), true_plants=len(attained),
                                 exact_terminal=sum(d.anchor['message_id'] == planted[d.fixture][1][-1]['message_id']
                                                    for d in attained),
                                 exact_witness=sum(tuple(e['message_id'] for e in d.witness) == tuple(
                                     e['message_id'] for e in planted[d.fixture][1]) for d in attained),
                                 antecedent_ablation_survives=ablate(discovery, planted, name, definition, attained)),
                eligible=dict(eligibility_report, fixtures=len(reached), intended_plants=len(future & reached),
                              intended_allocated= dict(Counter(arm(fx) for fx in future & reached))),
                predictive=forecast(discovery, before.run(definition, Budget(2_000_000)), full, cohort),
                controls_with_later_exposure=later_exposure,
                forward=comparison(full, cohort, overlap, planted, targets, end=end, name=name,
                                   include_rows=include_rows), official=concise(official),
            )

        result.append(dict(
            pattern=name, planted_discovery=len(started), planted_forward=len(future),
            injected_target_win_probability=BASE if name == 'false_promising_2' else EFFECT,
            target_baseline_probability=BASE,
            discovery_registration=dict(status=best['status'], reason=best['reason'],
                                        tracked=best['track_prospectively']),
            selected=investigate(best['rule'], structure, discovered_result.get(best['id'])),
            oracle=investigate(rule, before.run(rule, Budget(2_000_000), structural=True),
                               oracle_result[dsl_v2.identifier(rule)]),
        ))
    return dict(seed=seed, generator=generator, discovery_cut=events.iso(discovery.as_of),
                start=events.iso(start), end=events.iso(end),
                final=events.iso(full.as_of), protocol_sha256=engine_v2.PROTOCOL_SHA256,
                interpreter_sha256=engine_v2.INTERPRETER_SHA256, discovered_family=len(discovered_result),
                discovered_registry=[dict(id=r['id'], registration=r.get('reason'),
                                          official=concise(discovered_result.get(r['id']))) for r in registered],
                supplied_fixture_count=len(full.history),
                window_anchor_exclusions=dict(full.exclusions),
                window_fixtures=len({e['fixture_id'] for _, e, _ in after.eligible_anchors}), patterns=result)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--seed', type=int, action='append')
    cli.add_argument('--generator', choices=('original', 'current'), default='current')
    cli.add_argument('--decision-rows', action='store_true', help='include individual decisions, proofs and results')
    cli.add_argument('--calibrated-live', action='store_true', help='run the separate physical LIVE assay')
    args = cli.parse_args()
    seeds = args.seed or bench.REFERENCE_SEEDS
    report = {'verdict': study.VERDICT}
    if args.calibrated_live:
        report['calibrated_live'] = [calibrated_live(seed, delta) for seed in seeds for delta in (-.12, 0., .12)]
    else:
        report['seeds'] = [seed_report(seed, args.decision_rows, args.generator) for seed in seeds]
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2))


if __name__ == '__main__':
    main()
