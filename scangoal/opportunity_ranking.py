"""Pure, read-only and non-certifying opportunity ranking for Goal-ROI issue #56.

Only the already-published Polymarket #55 rule is in the candidate registry.
The comparison is DESCRIPTIVE: the benchmark overlaps the candidate, and neither
the benchmark nor the two-standard-error penalty is a confirmatory test.
"""
from collections import Counter, defaultdict
import math

from . import opportunity_forward, opportunity_results

MIN_RANK_GAMES = 30
TWO_STANDARD_ERRORS = 2.0
MIN_RANK_DAYS = 10
MAX_DAY_SHARE = 0.20
BASELINE = "Polymarket/pre/Asian Total/Over 2.5 (sem filtros de quota e valor)"


def concentration(values):
    buckets = Counter(iter(values))
    n = buckets.total()
    return {"groups": len(buckets), "largest_group": max(buckets.values(), default=0),
            "largest_share": max(buckets.values()) / n if n else None}


def signal_day(signal):
    stamp = opportunity_results.utc(signal["collected_at_utc"])
    return (stamp + opportunity_forward.MIN_LEAD).date()


def statistics(first, results, now):
    """Describe one unit per fixture and a UTC-day clustered descriptive score."""
    counts, profits = opportunity_forward.settled_statistics(first, results, now)
    signals = [signal for _, signal in first.values()]
    spread = {
        "decision_day_utc": concentration(signal_day(s) for s in signals),
        "league": concentration(s.get("league") for s in signals),
        "fixture": concentration(first),
        "collection_batch": concentration(opportunity_results.utc(s["collected_at_utc"]) for s in signals),
    }
    by_day = defaultdict(list)
    for mid, (profit, _) in profits.items():
        by_day[signal_day(first[mid][1])].append(profit)
    settled_spread = concentration(day for day, returns in by_day.items() for _ in returns)
    confirmed_spread = concentration(signal_day(first[mid][1])
                                     for mid, (_, status) in profits.items() if status in ("win", "loss"))
    score = None
    n, days = len(profits), len(by_day)
    if (counts["ft_confirmed"] >= MIN_RANK_GAMES and confirmed_spread["groups"] >= MIN_RANK_DAYS
            and max(settled_spread["largest_share"], confirmed_spread["largest_share"]) <= MAX_DAY_SHARE):
        mean = sum(profit for profit, _ in profits.values()) / n
        # Day clusters may contain arbitrarily correlated bets. This penalty is descriptive.
        variance = days / (days - 1) * sum(
            (sum(returns) - len(returns) * mean) ** 2 for returns in by_day.values()
        ) / n ** 2
        score = round(mean - math.sqrt(variance) * TWO_STANDARD_ERRORS, 4)
    return dict(counts, concentration=spread, liquidated_by_decision_day_utc=settled_spread,
                confirmed_ft_by_decision_day_utc=confirmed_spread,
                descriptive_two_se_score=score)


def overlap(candidate, baseline):
    shared = candidate.keys() & baseline.keys()
    same = sum(candidate[mid][0] == baseline[mid][0] and candidate[mid][1]["odds"] == baseline[mid][1]["odds"]
               for mid in shared)
    return {"shared_fixtures": len(shared), "candidate_only": len(candidate) - len(shared),
            "baseline_only": len(baseline) - len(shared), "shared_same_quote": same,
            "shared_different_quote": len(shared) - same}


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

    n = cur["ft_confirmed"]
    rankable = cur["descriptive_two_se_score"] is not None
    status = ("sem_dados" if cur["forward_games"] == 0
              else "amostra_insuficiente" if n < MIN_RANK_GAMES
              else "amostra_concentrada" if not rankable
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
            "rank_exploratory": 1 if rankable else None,
            "rule_id": protocol["id"],
            "evidence_status": status,
            **cur,
            "roi_minus_baseline_descriptive": round(delta, 4) if delta is not None else None,
            "certified": False,
        }],
        "baseline": {"description": BASELINE, **benchmark},
        "overlap": overlap(candidate, baseline),
        "minimum_forward_settled_for_ranking": MIN_RANK_GAMES,
        "minimum_confirmed_ft_for_ranking": MIN_RANK_GAMES,
        "minimum_decision_days_for_ranking": MIN_RANK_DAYS,
        "maximum_settled_share_in_one_day": MAX_DAY_SHARE,
        "multiplicity": "No corrected confirmatory inference; one preregistered rule only.",
        "comparability": "Baseline overlaps candidate; difference is descriptive, not causal or independent.",
        "score_method": "mean unit profit at odds ×0.98 minus 2 UTC-day clustered standard errors; descriptive only, not a confidence bound.",
        "certification": "not_activated",
        "automatic_alerts_changed": False,
        "statistical_claim": "none",
    }
