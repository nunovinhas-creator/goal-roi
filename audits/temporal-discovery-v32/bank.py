"""Independent falsification bank for receipt-time eligibility.

Fixtures, masks and timestamps are defined here, with no dependency on the
v1/v2/v3 synthetic generators or on archived outcomes.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from random import Random

from scangoal import event_intelligence as events, match_identity
from scangoal.discovery import dsl
from scangoal.discovery.replay import Budget
from scangoal.discovery.v32 import engine, protocol
from scangoal.discovery.v32.riskset import RiskSet
from scangoal.discovery.v32.tape import STREAMS, Coverage, Tape, fixture_key

T = datetime(2029, 2, 8, 16, tzinfo=timezone.utc)
WINDOW = 1800
RULE = dict(version=32, within_seconds=WINDOW,
            steps=[[dict(field='source', op='eq', value='Polymarket'), dict(field='volume_usd', op='ge', value=50000)],
                   [dict(field='source', op='eq', value='Stake'), dict(field='direction', op='eq', value='over')]],
            relations=[dict(field='gap_seconds', op='ge', value=1, **{'from': 0, 'to': 1})])


def row(name, when, *, received=None, kind='quote', provider='Stake', kickoff=None, **changes):
    odds = ['2', '2'] if kind == 'quote' else None
    return dict(dict(match_id=name, teams=[f'{name} Home', f'{name} Away'], league='Benchmark league',
                     kickoff_utc=events.iso(kickoff or (T + timedelta(hours=2))), phase='pre',
                     source=provider, signal_type='BENCH', signal_kind='original', market='Asian Total',
                     market_key='match_goals', period='regulation', selection='Over 2.5', direction='over',
                     line='2.5', price_kind='point' if odds else None, odds=odds,
                     price_low=2 if odds else None, price_high=2 if odds else None,
                     receipt='completed_page', event_kind=kind, emitted_utc=events.iso(when),
                     available_utc=events.iso(received or when)), **changes)


def cap(quote, *, at=T, start=None, end=None, available=None, streams=STREAMS):
    start = at - timedelta(seconds=WINDOW) if start is None else start
    end = at if end is None else end
    available = at if available is None else available
    fixture = fixture_key(match_identity.components(quote))
    proof = dsl.digest((fixture, events.iso(start), events.iso(end), events.iso(available), sorted(streams)))
    return Coverage(fixture, start, end, available, proof, streams)


def example(name):
    """Return rows, explicit coverage, and the number of admissible pairs at t."""
    lead = row('case', T - timedelta(minutes=14), kind='alert', provider='Polymarket', volume_usd=80_000)
    case = row('case', T)
    control = row('control', T - timedelta(minutes=11))
    spans = [cap(control)]
    extra = []
    expected = 0

    if name == 'clean_negative':
        expected = 1
    elif name == 'atomic_receipt':
        control = row('control', T)
        spans = [cap(control)]
        expected = 1
    elif name == 'old_quote_new_antecedent':
        extra = [row('control', T - timedelta(minutes=4), received=T, kind='alert', provider='Polymarket',
                     volume_usd=70_000)]
    elif name == 'proved_present_with_incomplete_coverage':
        spans = []
        extra = [row('control', T - timedelta(minutes=4), received=T, kind='alert', provider='Polymarket',
                     volume_usd=70_000)]
    elif name == 'feed_silent':
        spans = []
    elif name == 'one_second_gap':
        spans = [cap(control, end=T - timedelta(seconds=1))]
    elif name == 'coverage_arrives_after_decision':
        spans = [cap(control, available=T + timedelta(seconds=1))]
    elif name == 'coverage_missing_stream':
        spans = [cap(control, streams=STREAMS - {'alert'})]
    elif name == 'contradictory_points':
        extra = [dict(control, odds=['2.1', '2.1'], price_low=2.1, price_high=2.1)]
    elif name == 'same_number_interval':
        control = dict(control, price_kind='interval', odds=None)
    elif name == 'incompatible_period':
        control = dict(control, period='first_half')
    elif name == 'incompatible_provider':
        control = dict(control, source='ScanGoal')
    elif name == 'incompatible_selection':
        control = dict(control, direction='under', selection='Under 2.5')
    elif name == 'incompatible_kickoff':
        control = row('control', T - timedelta(minutes=11), kickoff=T + timedelta(hours=3))
        spans = [cap(control)]
    elif name == 'identity_collision_known':
        extra = [row('control', T - timedelta(minutes=5), received=T, teams=['Reused', 'Identifier'])]
    elif name == 'identity_collision_not_yet_known':
        extra = [row('control', T - timedelta(minutes=5), received=T + timedelta(seconds=1),
                     teams=['Reused', 'Identifier'])]
        expected = 1
    elif name == 'future_antecedent_no_proof_of_absence':
        spans = []
        extra = [row('control', T - timedelta(minutes=4), received=T + timedelta(seconds=1), kind='alert',
                     provider='Polymarket', volume_usd=70_000)]
    elif name == 'unknown_feature':
        extra = [row('control', T - timedelta(minutes=4), received=T, kind='alert', provider='Polymarket')]
    elif name == 'overlapping_complete_intervals':
        middle = T - timedelta(minutes=12)
        spans = [cap(control, end=middle), cap(control, start=middle)]
        expected = 1
    elif name == 'out_of_order_newer_price_retained':
        extra = [row('control', T - timedelta(minutes=22), received=T, odds=['4', '4'], price_low=4, price_high=4)]
        expected = 1
    else:
        raise ValueError(name)
    return [lead, case, control, *extra], spans, expected


SCENARIOS = (
    'clean_negative', 'atomic_receipt', 'old_quote_new_antecedent', 'proved_present_with_incomplete_coverage',
    'feed_silent', 'one_second_gap', 'coverage_arrives_after_decision', 'coverage_missing_stream',
    'contradictory_points', 'same_number_interval', 'incompatible_period', 'incompatible_provider',
    'incompatible_selection', 'incompatible_kickoff', 'identity_collision_known', 'identity_collision_not_yet_known',
    'future_antecedent_no_proof_of_absence', 'unknown_feature', 'overlapping_complete_intervals',
    'out_of_order_newer_price_retained',
)
SEEDS = (10937, 38119, 54869, 70493, 96181)


def compare(tape, *, at=T):
    cohort = RiskSet(tape).run(RULE, Budget(25_000))
    return dict(pairs_at_t=sum(c.at == at for c, _, _ in cohort.pairs), counts=dict(cohort.counts),
                admissible_sha256=dsl.digest(cohort.admissible), pairing_sha256=engine.pair_signature(cohort))


def run():
    output = []
    for name in SCENARIOS:
        rows, spans, expected = example(name)
        reference = Tape(rows, T, spans)
        result = compare(reference)
        if result['pairs_at_t'] != expected:
            raise AssertionError((name, expected, result))
        # Rebuild from scratch with random arrival order, unrelated IDs and
        # repeated records. Re-run and advance the cutoff beyond late receipts.
        for seed in SEEDS:
            rng = Random(seed)
            copies = [dict(row, message_id=rng.randrange(10**12), journal_line=rng.randrange(10**12),
                           record_sha256=dsl.digest((seed, index))) for index, row in enumerate([*rows, *rows])]
            rng.shuffle(copies)
            reordered = Tape(copies, T, reversed(spans))
            if reordered.snapshot_sha256 != reference.snapshot_sha256 or compare(reordered) != result:
                raise AssertionError((name, seed, 'restart_or_permutation_changed_result'))
            later = Tape(copies, T + timedelta(seconds=1), spans)
            if compare(later)['pairs_at_t'] != expected:
                raise AssertionError((name, seed, 'later_receipt_changed_earlier_pairs'))
        output.append(dict(scenario=name, expected_pairs=expected, **result))
    return dict(schema='v32_falsification_bank', protocol_sha256=protocol.PROTOCOL_SHA256,
                seeds=list(SEEDS), scenarios=output,
                pair_expectations=dict(Counter(row['expected_pairs'] for row in output)),
                confirmatory=False, verdict=engine.VERDICT)


if __name__ == '__main__':
    print(json.dumps(run(), sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False))
