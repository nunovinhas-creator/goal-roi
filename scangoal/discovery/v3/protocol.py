"""Frozen, versioned definition of temporal-risk-set-v3.

Changing any value below changes PROTOCOL_SHA256. tests/test_discovery_v3.py
pins that digest, so a change requires a new protocol name and identifier.
The v1 and v2 protocols are never edited; their digests are pinned too.
"""

from .. import dsl
from ..replay import SETTLEMENT
from . import inference, layers, riskset

V2_PROTOCOL_SHA256 = '659d7de3788657abfed2b6a07170f16381e3b7acca202084f67a6af18ed98f3a'
V1_PROTOCOL_SHA256 = '1c49b4d9a4d5bba54a1d7b05c2a4eec821dbb05bd834fcc1f6e2c87fde081041'
AUDIT_COMMIT = '01cef30e27d507b3c9ca52696ed273e6747ccda2'
SETTINGS = inference.Settings()
WINDOW_DAYS = 90
MAX_FAMILY = 12
MIN_DISCOVERY_PAIRS = 20

PROTOCOL = {
    'name': 'temporal-risk-set-v3', 'format': 3, 'dsl_version': 2,
    'supersedes': {'temporal-day-contract-v2': V2_PROTOCOL_SHA256, 'temporal-v1': V1_PROTOCOL_SHA256,
                   'audit_commit': AUDIT_COMMIT},
    'window_days': WINDOW_DAYS, 'settlement_days': SETTLEMENT.days,
    'start': 'first_utc_midnight_after_witnessed_plan_and_discovery_cutoff',
    'required_receipt': 'completed_page',
    'case': 'first_observed_sequence_completion_per_fixture',
    'comparator': {
        'design': 'risk_set_at_case_instant_one_reservation_per_fixture',
        'contract_identity': ['source', 'phase', 'native_market', 'period', 'line', 'selection'],
        'fixture_identity': 'canonical_fixture_id_single_known_kickoff_at_instant',
        'information': 'receipts_available_at_or_before_case_instant',
        'entry_opportunity': 'comparator_satisfies_terminal_step',
        'antecedent': 'determinately_absent_at_case_instant',
        'max_quote_age_minutes': riskset.MAX_QUOTE_AGE.total_seconds() / 60,
        'max_live_quote_age_minutes': riskset.MAX_LIVE_QUOTE_AGE.total_seconds() / 60,
        'pre_lead_caliper_minutes': riskset.PRE_LEAD_CALIPER,
        'live_minute_caliper': riskset.LIVE_MINUTE_CALIPER, 'live_score': 'exact',
        'log_odds_caliper': riskset.LOG_ODDS_CALIPER,
        'order': ['same_competition', 'max_scaled_distance', 'quote_age', 'sha256_tiebreak'],
        'tiebreak_salt': riskset.TIEBREAK.hex(),
        'later_exposure': 'comparator_keeps_reservation_and_is_counted',
        'bias_correction': 'linear_outcome_model_on_settled_comparators_log_odds_and_clock_per_layer',
    },
    'layers': {
        'prediction': 'paired_mean_of_won_minus_implied_probability_on_decisive_outcomes',
        'announced': 'paired_mean_net_return_at_announced_price',
        'announced_absolute': 'case_arm_cluster_t_lower_95_above_zero',
        'executable': 'not_demonstrable_offline_slippage_stress_only',
        'costs': {'winning_haircut': layers.HAIRCUT, 'unit_fee': layers.FEE},
        'slippage_grid': list(layers.SLIPPAGE),
    },
    'outcome': {'estimand': 'mean_pair_difference', 'ties': 'zero_differences_retained',
                'pushes': 'excluded_from_prediction_retained_in_return',
                'missing_ft': 'worst_case_lower_bound_tested', 'min_ft_coverage': SETTINGS.min_coverage},
    'inference': {
        'test': 'one_sided_cluster_sign_flip_on_lower_bound_sums',
        'required_clusterings': list(SETTINGS.clusterings), 'reported_clusterings': list(SETTINGS.reported),
        'combine': 'maximum_p_over_estimable_required_clusterings',
        'exact_up_to_clusters': inference.EXACT_CLUSTERS, 'monte_carlo_draws': SETTINGS.draws,
        'monte_carlo_seed': SETTINGS.seed, 'min_pairs': SETTINGS.min_pairs, 'min_clusters': SETTINGS.min_clusters,
        'robustness': {'leave_one_group_out': list(SETTINGS.robustness), 'requirement': 'all_means_positive'},
        'interval': 'cluster_t_95_widest_over_estimable_required_clusterings_lower_to_upper_ft_bounds',
    },
    'multiplicity': {'family': 'all_plan_hypotheses_x_prediction_and_announced',
                     'within_plan': 'holm', 'across_plans': 'alpha_0.05_over_wave_times_wave_plus_1',
                     'max_family_hypotheses': MAX_FAMILY, 'descriptive': 'benjamini_hochberg'},
    'registration': {'selection': 'outcome_blind_support_ranking', 'min_discovery_pairs': MIN_DISCOVERY_PAIRS,
                     'witness': 'rfc3161_receipt_before_window', 'confirmatory_results_at_registration': 0},
    'claims': {'max_status': 'experimental', 'executable_profitability': 'not_demonstrated'},
}
PROTOCOL_SHA256 = dsl.digest(PROTOCOL)


def alpha(wave):
    return .05 / (wave * (wave + 1))
