"""Pure, read-only and non-certifying opportunity ranking for Goal-ROI issue #56.

Only the already-published Polymarket #55 rule is in the candidate registry.
The comparison is DESCRIPTIVE: the benchmark overlaps the candidate, and neither
the benchmark nor the two-standard-error penalty is a confirmatory test.
"""
import math

from . import opportunity_forward

MIN_RANK_GAMES = 30
TWO_STANDARD_ERRORS = 2.0
BASELINE = "Polymarket/pre/Asian Total/Over 2.5 (sem filtros de quota e valor)"


def statistics(first, results, now):
    """One vote per fixture. Conservative score is a descriptive penalty."""
    counts, cut = opportunity_forward.settled_statistics(first, results, now)
    n = len(cut)
    score = None
    if n >= MIN_RANK_GAMES:
        mean = sum(cut) / n
        variance = sum((x - mean) ** 2 for x in cut) / (n - 1)
        score = round(mean - math.sqrt(variance / n) * TWO_STANDARD_ERRORS, 4)
    return dict(counts, descriptive_two_se_score=score)


def build(messages, results, protocol, *, activation_utc, now):
    """Rank the predeclared rule against the wider, overlapping, forward baseline."""
    _, _, cutoff, as_of = opportunity_forward.evaluation_window(protocol, activation_utc, now)
    if cutoff is None:
        return {
            "schema": "goal_roi_exploratory_ranking_v1",
            "status": "not_activated", "ranking": [], "baseline": None,
            "certification": "not_activated", "automatic_alerts_changed": False,
            "statistical_claim": "none",
        }
    candidate, baseline, aliases = opportunity_forward.select_signals(messages, cutoff, as_of)
    evidence = opportunity_forward.fixture_evidence(results, aliases)
    cur = statistics(candidate, evidence, as_of)
    benchmark = statistics(baseline, evidence, as_of)

    n = cur["settled"]
    status = ("sem_dados" if n == 0
              else "amostra_insuficiente" if n < MIN_RANK_GAMES
              else "exploratorio_em_revisao")
    delta = (cur["roi_odds_minus_2pct"] - benchmark["roi_odds_minus_2pct"]
             if cur["roi_odds_minus_2pct"] is not None
             and benchmark["roi_odds_minus_2pct"] is not None else None)
    return {
        "schema": "goal_roi_exploratory_ranking_v1",
        "status": "exploratory_only",
        "cutoff_utc": cutoff.isoformat(),
        "quote_policy": opportunity_forward.QUOTE_POLICY,
        "ranking": [{
            "rank_exploratory": 1 if n >= MIN_RANK_GAMES else None,
            "rule_id": protocol["id"],
            "evidence_status": status,
            **cur,
            "roi_minus_baseline_descriptive": round(delta, 4) if delta is not None else None,
            "certified": False,
        }],
        "baseline": {"description": BASELINE, **benchmark},
        "minimum_forward_settled_for_ranking": MIN_RANK_GAMES,
        "multiplicity": "No corrected confirmatory inference; one preregistered rule only.",
        "comparability": "Baseline overlaps candidate; difference is descriptive, not causal or independent.",
        "score_method": "mean unit profit at odds ×0.98 minus 2 sample standard errors; descriptive only, not a confidence bound.",
        "certification": "not_activated",
        "automatic_alerts_changed": False,
        "statistical_claim": "none",
    }
