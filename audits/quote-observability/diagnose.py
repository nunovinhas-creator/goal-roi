"""Read-only diagnosis of quote and context coverage against the frozen v3.1 audit."""

import argparse
from collections import Counter, defaultdict
from datetime import timedelta
import hashlib
from itertools import groupby
import json
from pathlib import Path
import re
import sys

# Import the frozen audit and its power kernel without editing either one.
sys.path.append(str(Path(__file__).resolve().parents[1] / 'temporal-discovery-v3'))
import coverage_audit as frozen
from power import ALPHA, PairSample, calculate

from scangoal import chronology, event_audit, event_intelligence as events
from scangoal.discovery import dsl, engine_v2
from scangoal.discovery.replay import Budget, Decision, Replay
from scangoal.discovery.v3 import engine, protocol, riskset
from scangoal.observability import ANALYSIS_EQUIVALENCES, contract_key, read_timeline
from scangoal.observability.contracts import contract_family

CUTOFF = '2026-11-24T08:22:00Z'
FROZEN_SNAPSHOT = '1f2bf65d751dc735d2a0688c700e7ff24ad9b41474e87f7768efc38c9e102944'
WATERFALL_CLASSES = {
    'identity_contract': 'identity',
    'identity_or_clock_case_kickoff_unknown': 'clock',
    'market_contract_unique_to_fixture': 'market_selection',
    'clock_no_same_contract_fixture_at_same_clock': 'clock',
    'availability_not_yet_observed_at_instant': 'availability',
    'staleness_last_quote_older_than_limit': 'availability',
    'reservation_all_comparators_reserved': 'reservation',
    'correspondence_terminal_or_antecedent': 'correspondence',
    'matched': 'matched',
}
SAFE_MARKETS = {
    '1x2', '1st half - 1x2', '2nd half - 1x2', 'asian total', '1st half - asian total',
    '2nd half - asian total', 'total goals', 'half time total goals', 'total corners',
    '1st half - total corners', 'asian handicap', 'half time asian handicap', 'half time result',
    'match result', 'both teams to score', 'polymarket', 'draw no bet', 'double chance',
    'total bookings', 'match_goals', 'corners', 'asian_handicap', 'winner', 'handicap',
    'both_teams_to_score', 'goal_total_unspecified_settlement',
}
SAFE_SELECTION = re.compile(r'(?:(?:over|under) [0-9.]+|ah[12]\([+−-]?[0-9.]+\)|'
                            r'[12x]|1x|12|x2|home|away|draw|(?:btts: )?(?:yes|no))\Z', re.I)


def safe_market(market):
    return market if market in SAFE_MARKETS else 'other'


def safe_selection(value):
    return value.lower() if isinstance(value, str) and SAFE_SELECTION.fullmatch(value) else 'other'


def key_summary(key):
    source, phase, market, period, line, (scope, _, _, selection) = key
    return dict(contract_sha256=dsl.digest(key), source=source, phase=phase, market=safe_market(market),
                period=period, line=line, scope=safe_market(scope),
                selection=selection[0] if isinstance(selection, tuple) else safe_selection(selection))


def price_summary(quotes):
    counts = Counter(q.price_kind for q in quotes)
    return dict(quotes=len(quotes), point=counts['point'], interval=counts['interval'],
                indeterminate=counts['indeterminate'],
                point_fraction=counts['point'] / len(quotes) if quotes else None)


def inventory(timeline, replay):
    sources, contracts = defaultdict(list), defaultdict(list)
    recovered = Counter()
    for quote in timeline.quotes:
        sources[quote.source].append(quote)
        baseline = replay.by_id.get(quote.message_id)
        if baseline is not None and quote.price_kind != 'indeterminate' and baseline['odds'] is None:
            recovered[quote.source] += 1
        if quote.contract_key is not None:
            contracts[quote.contract_key].append(quote)
    return dict(total_quotes=len(timeline.quotes), without_exact_contract=sum(q.contract_key is None for q in timeline.quotes),
                explicit_price_recovered_from_text=dict(recovered),
                by_source={source: price_summary(rows) for source, rows in sorted(sources.items())},
                contracts=[dict(key_summary(key), **price_summary(rows),
                                fixtures=len({q.fixture_id for q in rows if q.fixture_id and not q.exclusion}))
                           for key, rows in sorted(contracts.items(), key=lambda item: dsl.encoded(item[0]))])


def enrichment(timeline):
    def counts(rows):
        return dict(versions=len(rows), messages=len({r.message_id for r in rows}),
                    fixtures=len({r.fixture_id for r in rows if r.fixture_id is not None}),
                    goal_lines=sum(sum(s.kind == 'goal' for s in r.scores) for r in rows),
                    distinct_fixture_goal_events=len({(r.fixture_id, s) for r in rows if r.fixture_id is not None
                                                       for s in r.scores if s.kind == 'goal'}),
                    versions_with_goal_timeline=sum(any(s.kind == 'goal' for s in r.scores) for r in rows),
                    alert_score_versions=sum(any(s.kind == 'alert' for s in r.scores) for r in rows),
                    final_score_versions=sum(any(s.kind == 'final' for s in r.scores) for r in rows),
                    biggest_ticket_versions=sum(r.biggest_ticket_usd is not None for r in rows),
                    bet_count_versions=sum(r.n_bets is not None for r in rows),
                    by_count_unit=dict(Counter(r.count_unit for r in rows if r.count_unit)),
                    cumulative_volume_versions=sum(r.volume_cumulative_usd is not None for r in rows),
                    issues=dict(Counter(issue for r in rows for issue in r.issues)))

    linked = [r for r in timeline.versions if r.fixture_id is not None]
    return dict(all_verified_versions=counts(timeline.versions), fixture_linked=counts(linked),
                linked_completed_page=counts([r for r in linked if r.receipt == 'completed_page']),
                linked_edited=counts([r for r in linked if r.published_utc > r.emitted_utc]),
                alert_versions_with_biggest_ticket=sum(q.biggest_ticket_usd is not None for q in timeline.quotes),
                alert_versions_with_bet_count=sum(q.n_bets is not None for q in timeline.quotes),
                alert_versions_with_cumulative_volume=sum(q.volume_cumulative_usd is not None for q in timeline.quotes),
                input_exclusions=dict(timeline.input_exclusions))


def density(replay):
    """Hourly arrival buckets and trailing-hour comparisons at receipt time."""
    arrivals = defaultdict(lambda: defaultdict(list))
    latest = defaultdict(dict)
    counts = defaultdict(Counter)
    for at, group in groupby(replay.anchors, key=lambda item: item[0]):
        at_instant = [(key, event) for _, event in group if (key := contract_key(event)) is not None]
        # The entire completed page is available at its receipt time.
        for key, event in at_instant:
            latest[key][event['fixture_id']] = event
        for key, event in at_instant:
            hour = at.replace(minute=0, second=0, microsecond=0)
            arrivals[key][hour].append(event)
            counts[key]['reference_observations'] += 1
            kickoff = replay.kickoff_known(event['fixture_id'], at)
            fresh = [prior for fixture, prior in latest[key].items() if fixture != event['fixture_id']
                     and at - events.emitted(prior) <= timedelta(hours=1)]
            in_window = [prior for prior in fresh if kickoff is not None
                         and (other_ko := replay.kickoff_known(prior['fixture_id'], at)) is not None
                         and abs(other_ko - kickoff) <= timedelta(minutes=45)]
            counts[key]['references_with_other_fixture_last_hour'] += bool(fresh)
            counts[key]['references_with_other_fixture_last_hour_and_kickoff_within_45m'] += bool(in_window)
            counts[key]['references_with_point_in_both_and_kickoff_within_45m'] += (
                riskset.priced(event) is not None and any(riskset.priced(prior) is not None for prior in in_window))
    return [dict(key_summary(key), **counts[key], hourly_arrivals=[
        dict(hour_utc=events.iso(hour), observations=len(rows), fixtures=len({r['fixture_id'] for r in rows}),
             point_observations=sum(frozen.form(r) == 'point' for r in rows))
        for hour, rows in sorted(arrivals[key].items())
    ]) for key in sorted(counts, key=dsl.encoded)]


def unsupported_settlements(replay):
    result = Counter()
    for at, event in replay.anchors:
        decision = Decision(event, at, replay.kickoff_known(event['fixture_id'], at), 'case', (), ())
        if replay.label(decision)['status'] == 'unsupported_settlement':
            key = contract_key(event)
            native_market = dsl.FIELDS['native_market'].read(event)
            selection = event['selection']
            label = (event['source'], event['phase'], safe_market(native_market), event['period'],
                     safe_selection(selection), dsl.digest((native_market, selection)),
                     dsl.digest(key) if key is not None else None)
            result[label] += 1
    return [dict(zip(('source', 'phase', 'market', 'period', 'selection', 'market_selection_sha256', 'contract_sha256'), fields), count=count)
            for fields, count in sorted(result.items(), key=lambda row: dsl.encoded(row[0]))]


def _span(times, end):
    first = min(times, default=None)
    return (first.date(), (end.date() - first.date()).days + 1) if first is not None else (None, 0)


def pair_diagnostics(replay, candidates, horizons, repetitions):
    """Collect ages and actual pairs with the same risk-set implementation."""
    full = riskset.RiskSet(replay)
    first_complete = min((events.seen(e) for e in replay.by_id.values() if e['receipt'] == 'completed_page'),
                         default=None)
    end = replay.as_of + timedelta(microseconds=1)
    receipt_day_start = first_complete.replace(hour=0, minute=0, second=0, microsecond=0) if first_complete else None
    window = riskset.RiskSet(replay, start=receipt_day_start, end=end) if receipt_day_start is not None else None
    first, complete_days = _span((first_complete,) if first_complete is not None else (), replay.as_of)
    _, historical_days = _span((at for at, _ in replay.anchors), replay.as_of)
    index = frozen.Index(replay)
    ages, nearest, comparator = [], [], []
    by_hypothesis, full_sums, complete_sums = [], Counter(), Counter()
    historical_families = defaultdict(set)
    complete_families = defaultdict(set)
    for item in candidates:
        rule = item['rule']
        cohort = full.run(rule, Budget(engine.MAX_WORK))
        historical, complete = defaultdict(list), defaultdict(list)
        for case in cohort.cases:
            ages.append((case.at - events.emitted(case.anchor)).total_seconds())
            eligible = [e for at, e in index.by_contract.get(contract_key(case.anchor), ())
                        if at <= case.at and e['fixture_id'] != case.fixture]
            if eligible:
                nearest.append(min((case.at - events.emitted(e)).total_seconds() for e in eligible))
        for case, control, match in cohort.pairs:
            comparator.append(match['comparator_quote_age_seconds'])
            family = contract_family(contract_key(case.anchor))
            identity = case.anchor['message_id'], control.anchor['message_id']
            historical_families[family].add(identity)
            historical[family].append(identity)
            full_sums['with_both_settled'] += all(replay.label(d)['status'] == 'settled' for d in (case, control))
        if window is not None:
            for case, control, match in window.run(rule, Budget(engine.MAX_WORK)).pairs:
                family = contract_family(contract_key(case.anchor))
                complete_families[family].add((case.anchor['message_id'], control.anchor['message_id']))
                complete[family].append(PairSample(
                    case.at.date(), (case.kickoff or case.at).date(), case.anchor['source'],
                    engine._labels(case, control)['competition'], (match['case_odds'], match['control_odds']),
                    (match['case_clock'], match['control_clock']),
                    tuple(None if (payoff := replay.label(d)['payoff']) is None else abs(payoff)
                          for d in (case, control)),
                ))
        full_sums['cases'] += len(cohort.cases)
        full_sums['pairs'] += len(cohort.pairs)
        complete_sums['pairs'] += sum(map(len, complete.values()))
        families = sorted(historical.keys() | complete.keys())
        by_hypothesis.append(dict(hypothesis_sha256=engine.hypothesis_id(rule), historical_pairs=len(cohort.pairs),
                                  completed_receipt_pairs=sum(map(len, complete.values())), families=[
            dict(family=family, historical_pairs=len(historical[family]),
                 historical_pairs_per_day=len(historical[family]) / historical_days,
                 completed_receipt_power=calculate(tuple(complete[family]), first, complete_days, horizons, repetitions))
            for family in families
        ]))
    families = sorted(historical_families.keys() | complete_families.keys())
    return dict(
        strict_pairs=full_sums['pairs'], strict_pairs_with_both_settled=full_sums['with_both_settled'],
        distinct_historical_pair_identities=sum(map(len, historical_families.values())),
        case_decisions=full_sums['cases'], completed_receipt_pairs_summed=complete_sums['pairs'],
        age_seconds={name: dict(n=len(values), percentiles=frozen.quantiles(values)) for name, values in (
            ('case_quote', ages), ('nearest_other_fixture_same_contract_available', nearest), ('matched_comparator', comparator))},
        power=dict(alpha_two_layers_single_hypothesis=ALPHA, historical_exposure_utc_days=historical_days,
                   completed_receipt_exposure_utc_days=complete_days, repetitions=repetitions,
                   lookahead_days=list(horizons), default_zero_pair_power=calculate((), first, complete_days, horizons, repetitions),
                   by_hypothesis=by_hypothesis,
                   deduplicated_family_pair_count_for_coverage_only=[
                       dict(family=family, historical=len(historical_families[family]),
                            completed_receipt=len(complete_families[family])) for family in families],
                   assumptions='Observed receipt-day schedules repeated with zero days intact. Earliest complete receipt '
                               'through cutoff inclusive; partial UTC days count in full. Synthetic independent outcomes '
                               'at fair price plus case shift on decisive stake. Observed prices, competitions, '
                               'missing-result and push/half-stake patterns. v3.1 pair-difference kernel and pair, '
                               'cluster, coverage, leave-one-out, lower-bound and two-layer alpha gates. '
                               'Fitted correction and absolute announced-return gate. Sampled effects describe planning only.'),
    )


def report(data_dir, as_of, horizons=(90, 180, 365), repetitions=100, *, verify_frozen=False):
    data_dir = Path(data_dir)
    audit = event_audit.build(data_dir, as_of, quote_only=True)
    replay = Replay(audit)
    timeline = read_timeline(data_dir, as_of)
    _, records = engine_v2.discover(replay)
    candidates = [row for row in records if 'rule' in row]
    waterfall = frozen.waterfall(replay, candidates)
    details = pair_diagnostics(replay, candidates, horizons, repetitions)
    for name, digest in audit['input_sha256'].items():
        if hashlib.sha256((data_dir / name).read_bytes()).hexdigest() != digest:
            raise ValueError('audit inputs changed during reconstruction; use a stable snapshot')
    reproduced = (details['case_decisions'], details['strict_pairs'], details['strict_pairs_with_both_settled'])
    expected = tuple(waterfall[field] for field in ('case_decisions_summed_over_hypotheses', 'strict_pairs',
                                                     'strict_pairs_with_ft_both_arms'))
    if reproduced != expected:
        raise ValueError('v3.1 pair diagnostics disagree with the frozen cascade')
    if verify_frozen and (replay.snapshot_sha256 != FROZEN_SNAPSHOT or reproduced != (1851, 13, 13)
                          or len(candidates) != 54 or waterfall['unique_case_decisions'] != 505):
        raise ValueError('input does not reproduce the 2026-11-24 frozen audit')
    failures = Counter()
    for stage, count in waterfall['first_failing_stage'].items():
        failures[WATERFALL_CLASSES.get(stage, 'price' if stage.startswith('price_') else 'other')] += count
    failures['results_after_matching'] = details['strict_pairs'] - details['strict_pairs_with_both_settled']
    return dict(schema='goal_roi_quote_observability_v1', as_of_utc=audit['as_of_utc'],
                snapshot_sha256=replay.snapshot_sha256, input_sha256=audit['input_sha256'],
                protocol_sha256=protocol.PROTOCOL_SHA256, autonomous_hypotheses=len(candidates),
                price_inventory=inventory(timeline, replay), text_enrichment=enrichment(timeline),
                cross_source_families=[dict(source=source, native_market=market, scope=scope, family=family)
                                       for (source, market, scope), family in sorted(ANALYSIS_EQUIVALENCES.items())],
                density_by_contract=density(replay), v3_decision_clock_funnel=frozen.funnel(audit, replay),
                v3_anchor_price_inventory=frozen.inventory(audit, replay),
                v3_spacing=frozen.spacing(replay), v3_waterfall=waterfall, v3_waterfall_by_class=dict(failures),
                unsupported_settlements=unsupported_settlements(replay), v3_results=frozen.results_availability(replay),
                **details, confirmatory=False, verdict=engine.VERDICT)


def summary(result):
    compact = {key: value for key, value in result.items() if key not in ('density_by_contract', 'price_inventory')}
    inventory = result['price_inventory']
    contracts = inventory['contracts']
    compact['price_inventory'] = dict({key: value for key, value in inventory.items() if key != 'contracts'},
                                      distinct_exact_contracts=len(contracts),
                                      top_contracts=sorted(contracts, key=lambda row: -row['fixtures'])[:8])
    density = result['density_by_contract']
    metrics = ('reference_observations', 'references_with_other_fixture_last_hour',
               'references_with_other_fixture_last_hour_and_kickoff_within_45m',
               'references_with_point_in_both_and_kickoff_within_45m')
    compact['density'] = dict(
        {key: sum(row[key] for row in density) for key in metrics},
        contract_hours=sum(len(row['hourly_arrivals']) for row in density),
        contract_hours_with_multiple_fixtures=sum(sum(hour['fixtures'] > 1 for hour in row['hourly_arrivals'])
                                                  for row in density))
    return compact


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--data-dir', type=Path, default=Path('data'))
    cli.add_argument('--as-of', default=CUTOFF, help='ISO 8601 with explicit timezone')
    cli.add_argument('--verify-frozen', action='store_true')
    cli.add_argument('--summary', action='store_true', help='omit per-contract hours and the full inventory')
    cli.add_argument('--days', type=int, action='append', help='power horizon in days (repeatable)')
    cli.add_argument('--repetitions', type=int, default=100)
    args = cli.parse_args(argv)
    try:
        result = report(args.data_dir, chronology.parse_utc(args.as_of), tuple(sorted(set(args.days or (90, 180, 365)))),
                        args.repetitions, verify_frozen=args.verify_frozen)
    except (ValueError, OSError) as exc:
        cli.error(str(exc))
    print(json.dumps(summary(result) if args.summary else result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
