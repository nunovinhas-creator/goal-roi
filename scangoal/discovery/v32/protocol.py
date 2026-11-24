"""Frozen scientific contract for temporal-risk-set-v3.2."""

from copy import deepcopy

from .. import dsl
from ..v3 import protocol as previous

WINDOW_DAYS = previous.WINDOW_DAYS
MAX_FAMILY = previous.MAX_FAMILY
MIN_DISCOVERY_PAIRS = previous.MIN_DISCOVERY_PAIRS
SETTINGS = previous.SETTINGS
alpha = previous.alpha

PROTOCOL = deepcopy(previous.PROTOCOL)
PROTOCOL.update(name='temporal-risk-set-v3.2', format=32, revision=2, dsl_version=32,
                case='first_proven_positive_completion_per_fixture_on_an_atomic_receipt',
                supersedes={**previous.PROTOCOL['supersedes'],
                            'temporal-risk-set-v3.1': previous.PROTOCOL_SHA256})
PROTOCOL['comparator'].update(
    information='all_receipts_at_or_before_t_in_atomic_batches',
    fixture_identity='ordered_teams_competition_exact_kickoff_known_at_receipt_with_collision_barrier',
    entry_opportunity='latest_fresh_unambiguous_point_quote_for_exact_contract_and_terminal_at_t',
    antecedent='present_or_absent_with_full_interval_coverage_or_indeterminate',
    coverage='explicit_closed_interval_for_complete_typed_tape_sealed_by_t_and_no_known_ambiguity',
    sequence='strict_publication_order_ending_at_t_excluding_terminal_quote_from_prefix',
    quote_trajectory_clock='original_publication_for_price_velocity_and_freshness',
    case_trigger='new_observation_or_coverage_in_fixture_with_latest_usable_quote',
    simultaneous_case_contract='first_positive_in_canonical_exact_contract_order',
    absence_sensitive_terms='complete_coverage_for_terminal_prefix_aggregates_and_relation_interior',
    live_state='block_stale_or_contradicted_score_since_quote',
    order=['same_competition', 'max_scaled_distance', 'quote_age', 'stable_fixture_digest'],
    tiebreak_salt='goal-roi/temporal-risk-set-v3.2',
    batch_reservation='enumerate_before_reserving_cases_then_greedily_allocate_in_stable_fixture_order',
)
PROTOCOL['registration'].update(witness_domain='temporal-risk-set-plan-v32',
                                evidence='new_window_fixture_not_known_at_discovery_cutoff_full_lookback_inside_window',
                                evaluation='after_witnessed_window_and_settlement_close')
PROTOCOL_SHA256 = dsl.digest(PROTOCOL)
