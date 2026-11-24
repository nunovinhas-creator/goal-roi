"""Offline coverage audit: why the real snapshot yields almost no v3 risk-set pairs.

Read-only. It never relaxes the v3 criteria used for inference. Counterfactual
counts ("how many pairs would exist if...") are labelled as such and are
upper bounds for planning PR E, never evidence.

Failure classes, in the order a case meets them:
  identity     - fixture or contract identity incomplete
  market       - no other fixture ever quoted the exact same contract
  clock        - no same-contract fixture at the same relative clock
  availability - the same-contract fixture was not observed by the instant
  staleness    - it was observed, but the last quote is older than the limit
  price        - comparator quote not a point price, or outside the caliper
  reservation  - every admissible comparator was already reserved
  correspondence - terminal step not met or antecedent present/indeterminate
  results      - (pairs) FT not available for both fixtures
"""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime
import json
from math import log
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scangoal import chronology, event_audit, event_intelligence as events, parser  # noqa: E402
from scangoal.discovery import dsl, engine_v2  # noqa: E402
from scangoal.discovery.replay import Budget, Decision, Replay  # noqa: E402
from scangoal.discovery.replay_v2 import Sequence  # noqa: E402
from scangoal.discovery.v3 import engine, layers, protocol, riskset  # noqa: E402

CLOCK_REASONS = {'last_unproven_live_market_window', 'last_signal_outside_decision_window',
                 'last_phase_conflicts_with_kickoff', 'last_live_market_window_elapsed',
                 'last_unproven_first_half_window', 'last_first_half_window_elapsed',
                 'last_unproven_second_half_window'}
CATEGORY = {**{r: 'clock' for r in CLOCK_REASONS}, 'last_unproven_market_period': 'market',
            'fixture_identifier_collision': 'identity', 'ft_already_known': 'results'}
INPUT_CATEGORY = {'incomplete_fixture_identity': 'identity', 'missing_match_id': 'identity',
                  'reused_match_id': 'identity', 'unproven_independence': 'source',
                  'betfair_burst': 'source', 'first_read_with_ft': 'availability',
                  'edited_at_first_read': 'availability'}


def form(event):
    odds = event.get('odds')
    if odds is None:
        return 'indeterminate'
    return 'point' if odds[0] == odds[1] else 'interval'


def quantiles(values, points=(.1, .25, .5, .75, .9)):
    values = sorted(values)
    if not values:
        return None
    return {f'p{int(q * 100)}': round(values[min(len(values) - 1, int(q * len(values)))], 3) for q in points}


def minutes(delta):
    return delta.total_seconds() / 60


def inventory(audit, replay):
    rows = audit['events']
    by_source = defaultdict(Counter)
    widths = defaultdict(list)
    lag = defaultdict(list)
    for e in rows:
        source = e['source']
        price = form(e)
        by_source[source][price] += 1
        by_source[source]['receipt_' + e['receipt']] += 1
        if e.get('exclusion'):
            by_source[source]['input_exclusion'] += 1
        if price == 'interval':
            low, high = map(float, e['odds'])
            widths[source].append(100 * (high - low) / low)
        if price == 'point' and float(e['odds'][0]) < parser.MIN_ODDS:
            by_source[source]['point_below_min_odds'] += 1
        lag[source].append(minutes(events.seen(e) - events.emitted(e)))
    anchors = Counter((e['source'], form(e)) for _, e in replay.anchors)
    ambiguous = Counter()
    for at, e in replay.anchors:
        anchor, _ = replay.prefix(e, at, 60)
        if e['odds'] is not None and anchor['odds'] is None:
            ambiguous[e['source']] += 1
    return dict(
        events=len(rows),
        by_source={s: dict(c) for s, c in sorted(by_source.items())},
        interval_width_percent=({s: dict(quantiles(v), n=len(v), at_most_1pct=sum(w <= 1 for w in v),
                                         at_most_2pct=sum(w <= 2 for w in v)) for s, v in sorted(widths.items())}),
        emission_to_availability_minutes={s: quantiles(v) for s, v in sorted(lag.items())},
        decision_anchors=dict(total=len(replay.anchors),
                              by_source_and_price_form={f'{s}/{f}': n for (s, f), n in sorted(anchors.items())},
                              same_second_conflicting_prices_made_indeterminate=dict(ambiguous)))


def funnel(audit, replay):
    episodes = [x for x in audit['episodes'] if x['kind'] == 'quote']
    reasons = Counter(r for x in episodes if x['decision_utc'] is None for r in x['exclusion_reasons'])
    categories = Counter()
    for reason, n in reasons.items():
        categories[CATEGORY.get(reason, 'other')] += n
    inputs = Counter()
    for reason, n in audit['input_exclusions'].items():
        inputs[INPUT_CATEGORY.get(reason, 'other')] += n
    receipts = Counter(e['receipt'] for _, e in replay.anchors)
    by_day = defaultdict(Counter)
    for e in audit['events']:
        by_day[e['emitted_utc'][:10]][e['receipt']] += 1
    return dict(quote_episodes=len(episodes), with_decision_clock=sum(x['decision_utc'] is not None for x in episodes),
                anchors_after_receipt_check=len(replay.anchors), anchors_by_receipt=dict(receipts),
                events_by_emission_day_and_receipt={d: dict(c) for d, c in sorted(by_day.items())},
                episode_exclusion_reasons=dict(reasons.most_common()), episode_exclusions_by_class=dict(categories),
                input_exclusions=dict(audit['input_exclusions']), input_exclusions_by_class=dict(inputs))


def contracts(replay):
    stats = defaultdict(lambda: dict(quotes=0, point=0, fixtures=set(), sources=set()))
    markets = defaultdict(lambda: dict(quotes=0, point=0, fixtures=set()))
    for _, e in replay.anchors:
        key = riskset.contract(e)
        market = (e['source'], e['phase'], dsl.FIELDS['native_market'].read(e), e['period'])
        for table, k in ((stats, key), (markets, market)):
            if k is None:
                continue
            row = table[k]
            row['quotes'] += 1
            row['point'] += form(e) == 'point'
            row['fixtures'].add(e['fixture_id'])
    keyed = sum(riskset.contract(e) is not None for _, e in replay.anchors)

    def dump(table, limit):
        ordered = sorted(table.items(), key=lambda kv: (-len(kv[1]['fixtures']), -kv[1]['quotes'], dsl.encoded(kv[0])))
        return [dict(key=list(k), quotes=v['quotes'], point=v['point'], fixtures=len(v['fixtures'])) for k, v in ordered[:limit]]

    per_contract = [len(v['fixtures']) for v in stats.values()]
    return dict(anchors_with_exact_contract=keyed, anchors_without_exact_contract=len(replay.anchors) - keyed,
                distinct_contracts=len(stats), contracts_with_one_fixture=sum(n == 1 for n in per_contract),
                fixtures_per_contract=quantiles(per_contract), top_contracts=dump(stats, 20),
                markets=dump(markets, 20))


def spacing(replay):
    """Time between comparable observations of the same exact contract."""
    by_contract = defaultdict(list)
    for at, e in replay.anchors:
        key = riskset.contract(e)
        if key is not None and form(e) == 'point':
            by_contract[key].append((at, e['fixture_id'], replay.kickoff_known(e['fixture_id'], at)))
    within, across, same_clock = [], [], []
    for rows in by_contract.values():
        rows.sort(key=lambda r: r[0])
        last = {}
        for at, fixture, kickoff in rows:
            if fixture in last:
                within.append(minutes(at - last[fixture]))
            others = [(minutes(at - t), k) for t, f, k in rows if f != fixture and t <= at]
            if others:
                across.append(min(m for m, _ in others))
            clocked = [m for m, k in others if k is not None and kickoff is not None
                       and abs(minutes(k - kickoff)) <= riskset.PRE_LEAD_CALIPER]
            if clocked:
                same_clock.append(min(clocked))
            last[fixture] = at
    limit = riskset.MAX_QUOTE_AGE.total_seconds() / 60
    return dict(
        same_fixture_requote_minutes=dict(quantiles(within) or {}, n=len(within)),
        nearest_other_fixture_same_contract_minutes=dict(quantiles(across) or {}, n=len(across),
                                                        within_protocol_age=sum(m <= limit for m in across)),
        nearest_other_fixture_same_contract_and_clock_minutes=dict(quantiles(same_clock) or {}, n=len(same_clock),
                                                                  within_protocol_age=sum(m <= limit for m in same_clock)))


class Index:
    def __init__(self, replay):
        self.replay = replay
        self.by_contract = defaultdict(list)
        for at, e in replay.anchors:
            key = riskset.contract(e)
            if key is not None:
                self.by_contract[key].append((at, e))
        for rows in self.by_contract.values():
            rows.sort(key=lambda r: (r[0], r[1]['message_id']))


def classify(index, case, sequence, reserved, results):
    """First stage at which no admissible comparator remains for one case."""
    replay = index.replay
    anchor, t = case.anchor, case.at
    key = riskset.contract(anchor)
    if key is None:
        return 'identity_contract', None
    odds = riskset.priced(anchor)
    if odds is None:
        return ('price_case_interval' if form(anchor) == 'interval' else 'price_case_indeterminate'), None
    reference = riskset.clock(anchor, case.kickoff, t)
    if reference is None:
        return 'identity_or_clock_case_kickoff_unknown', None
    others = defaultdict(list)
    for at, e in index.by_contract[key]:
        if e['fixture_id'] != case.fixture:
            others[e['fixture_id']].append((at, e))
    if not others:
        return 'market_contract_unique_to_fixture', None
    live = anchor['phase'] == 'live'

    def same_clock(rows):
        kickoff = replay.kickoff_known(rows[0][1]['fixture_id'], rows[-1][0])
        if live:
            return any(e.get('score') == anchor.get('score') and e.get('minute') is not None
                       and abs(e['minute'] - reference) <= riskset.LIVE_MINUTE_CALIPER for _, e in rows)
        return kickoff is not None and abs(minutes(kickoff - t) - reference) <= riskset.PRE_LEAD_CALIPER

    clocked = {f: rows for f, rows in others.items() if same_clock(rows)}
    if not clocked:
        return 'clock_no_same_contract_fixture_at_same_clock', None
    seen = {f: [r for r in rows if r[0] <= t] for f, rows in clocked.items()}
    seen = {f: rows for f, rows in seen.items() if rows}
    # Counterfactual: a fresh re-observation of the same contract at t, at the
    # comparator's nearest known price, would pass the price caliper.
    potential = sum(1 for f, rows in clocked.items()
                    if (p := riskset.priced(min(rows, key=lambda r: abs(r[0] - t))[1])) is not None
                    and abs(log(p) - log(odds)) <= riskset.LOG_ODDS_CALIPER and f not in reserved
                    and results.get(f))
    if not seen:
        return 'availability_not_yet_observed_at_instant', potential
    age = riskset.MAX_LIVE_QUOTE_AGE if live else riskset.MAX_QUOTE_AGE
    fresh = {f: rows[-1] for f, rows in seen.items() if events.emitted(rows[-1][1]) >= t - age}
    if not fresh:
        return 'staleness_last_quote_older_than_limit', potential
    priced = {f: r for f, r in fresh.items() if riskset.priced(r[1]) is not None}
    if not priced:
        return 'price_comparator_not_point', potential
    calipered = {f: r for f, r in priced.items()
                 if abs(log(riskset.priced(r[1])) - log(odds)) <= riskset.LOG_ODDS_CALIPER}
    if not calipered:
        return 'price_outside_caliper', potential
    free = {f: r for f, r in calipered.items() if f not in reserved}
    if not free:
        return 'reservation_all_comparators_reserved', potential
    return 'correspondence_terminal_or_antecedent', potential


def waterfall(replay, candidates):
    index = Index(replay)
    design = riskset.RiskSet(replay)
    results = {f: bool(rows) for f, rows in replay.audit.get('fixture_results', {}).items()}
    stages, unique_stages, potential = Counter(), {}, Counter()
    matched = pairs_with_ft = 0
    half, interval = Counter(), Counter()
    for item in candidates:
        cohort = design.run(item['rule'], Budget(engine.MAX_WORK))
        sequence = Sequence(item['rule'])
        paired = {c.anchor['message_id'] for c, _, _ in cohort.pairs}
        reserved = {d.fixture for pair in cohort.pairs for d in pair[:2]} | {c.fixture for c in cohort.cases}
        for case in cohort.cases:
            key = (case.fixture, case.anchor['message_id'])
            label = replay.label(case)
            half['cases_half_settlement'] += label['payoff'] in (.5, -.5)
            if case.anchor['message_id'] in paired:
                stage, extra = 'matched', None
            else:
                stage, extra = classify(index, case, sequence, reserved - {case.fixture}, results)
            stages[stage] += 1
            if stage == 'price_case_interval':
                low, high = map(float, case.anchor['odds'])
                width = 100 * (high - low) / low
                for limit in (1, 2):
                    if width <= limit:
                        interval[f'width_at_most_{limit}pct'] += 1
                        if key not in unique_stages:
                            interval[f'unique_width_at_most_{limit}pct'] += 1
            unique_stages.setdefault(key, stage)
            if extra:
                potential[stage] += 1
        for case, control, _ in cohort.pairs:
            matched += 1
            pairs_with_ft += all(replay.label(d)['status'] == 'settled' for d in (case, control))
            half['pair_decisions_half_settlement'] += sum(replay.label(d)['payoff'] in (.5, -.5) for d in (case, control))
    return dict(
        case_decisions_summed_over_hypotheses=sum(stages.values()), unique_case_decisions=len(unique_stages),
        first_failing_stage=dict(stages.most_common()),
        first_failing_stage_unique_cases=dict(Counter(unique_stages.values()).most_common()),
        strict_pairs=matched, strict_pairs_with_ft_both_arms=pairs_with_ft,
        counterfactual_fresh_same_contract_observation=dict(
            note='Upper bound for planning only: cases whose blocking stage was clock-compatible availability, '
                 'staleness or price, and which have at least one clock-compatible same-contract fixture with a '
                 'point price within the caliper at its nearest known quote, not reserved, with FT. Correspondence '
                 '(antecedent absent at the instant) is not checked.',
            cases=sum(potential.values()), by_blocking_stage=dict(potential)),
        counterfactual_interval_cases=dict(
            note='Cases blocked only because their own quote is an interval; counts by interval width. '
                 'v3.1 keeps requiring a point price; using bounds needs a new pre-registered protocol.',
            **interval),
        half_settlements=dict(half))


def results_availability(replay):
    status = Counter()
    by_source = defaultdict(Counter)
    for at, e in replay.anchors:
        label = replay.label(Decision(e, at, replay.kickoff_known(e['fixture_id'], at), 'case', (), ()))
        status[label['status']] += 1
        by_source[e['source']][label['status']] += 1
    half = Counter((e['source'], e['line']) for at, e in replay.anchors
                   if replay.label(Decision(e, at, replay.kickoff_known(e['fixture_id'], at), 'case', (), ()))['payoff']
                   in (.5, -.5))
    return dict(anchor_label_status=dict(status), by_source={s: dict(c) for s, c in sorted(by_source.items())},
                half_settlements=dict(total=sum(half.values()),
                                      by_source_and_line={f'{s}/{l}': n for (s, l), n in half.most_common()}))


GOALS = re.compile(r'^⚽\s+\d+', re.M)
FT = re.compile(r'🏁 FT \d+:\d+')
VOLUME = re.compile(r'Σ on this outcome overall|Total on .* this match')
BIGGEST = re.compile(r'biggest \$')


def preserved(data_dir):
    """What the collector already stores in text but the event audit does not turn into fields."""
    raw = [json.loads(line) for line in (data_dir / 'messages.jsonl').open()]
    latest = {}
    for row in raw:
        latest[row['message_id']] = row
    text = [r['text'] or '' for r in latest.values()]
    versions = Counter(r['message_id'] for r in raw)
    lags = [(datetime.fromisoformat(r['collected_at_utc']) - datetime.fromisoformat(r['date_utc'])).total_seconds() / 60
            for r in raw]
    return dict(
        stored_lines=len(raw), messages=len(latest), messages_with_several_versions=sum(v > 1 for v in versions.values()),
        receipt_version_field=sum('receipt_version' in r for r in raw),
        texts_with_goal_timeline=sum(bool(GOALS.search(t)) for t in text),
        goal_timeline_lines=sum(len(GOALS.findall(t)) for t in text),
        texts_with_ft_line=sum(bool(FT.search(t)) for t in text),
        texts_with_cumulative_outcome_volume=sum(bool(VOLUME.search(t)) for t in text),
        texts_with_biggest_ticket=sum(bool(BIGGEST.search(t)) for t in text),
        texts_with_match_page_link=sum('scangoal.com/match/' in t for t in text),
        collection_lag_minutes=quantiles(lags))


def observer(data_dir):
    rows = [json.loads(line) for line in (data_dir / 'polymarket_observations.jsonl').open() if line.strip()]
    runs = [json.loads(line) for line in (data_dir / 'polymarket_observer_runs.jsonl').open() if line.strip()]
    return dict(observations=len(rows), reasons=dict(Counter(r.get('reason') for r in rows)),
                orders_sent=sum(r.get('orders_sent', 0) for r in rows),
                credentials_used=any(r.get('credentials_used') for r in rows),
                runs=len({r.get('run_id') for r in runs}))


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--data-dir', type=Path, default=Path('data'))
    cli.add_argument('--as-of', required=True)
    args = cli.parse_args(argv)
    audit = event_audit.build(args.data_dir, chronology.parse_utc(args.as_of), quote_only=True)
    replay = Replay(audit)
    _, records = engine_v2.discover(replay)
    candidates = [r for r in records if 'rule' in r]
    report = dict(
        as_of_utc=args.as_of, snapshot_sha256=replay.snapshot_sha256, protocol_sha256=protocol.PROTOCOL_SHA256,
        criteria='v3.1 risk-set criteria unchanged; counterfactuals are labelled upper bounds',
        inventory=inventory(audit, replay), decision_clock_funnel=funnel(audit, replay),
        contracts=contracts(replay), spacing=spacing(replay), results=results_availability(replay),
        risk_set_waterfall=waterfall(replay, candidates), candidates=len(candidates),
        collector_text_already_stored=preserved(args.data_dir), polymarket_observer=observer(args.data_dir),
        confirmatory=False, verdict=engine.VERDICT)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False, default=str))


if __name__ == '__main__':
    main()
