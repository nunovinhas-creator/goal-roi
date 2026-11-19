"""Pure, read-only and non-certifying opportunity ranking for Goal-ROI issue #56.

Only the already-published Polymarket #55 rule is in the candidate registry.
The comparison is DESCRIPTIVE: the benchmark overlaps the candidate, and neither
the benchmark nor the two-standard-error penalty is a confirmatory test.
"""
import math
from datetime import timedelta

from . import chronology, opportunity_forward, opportunity_results
from .eligibility import eligible, game_identity

MIN_RANK_GAMES = 30
TWO_STANDARD_ERRORS = 2.0
ODDS_FACTOR = 0.98
BASELINE = "Polymarket/pre/Asian Total/Over 2.5 (sem filtros de quota e valor)"


def baseline_signal(message, cutoff, now):
    """Same-time first-read baseline; never select games using their results."""
    m = message
    if (not isinstance(m, dict) or m.get("type") != "POLYMARKET"
            or m.get("phase") != "pre" or m.get("market") != "Asian Total"
            or m.get("selection") != "Over 2.5"
            or m.get("bet") is not True
            or any(m.get(flag) is not False for flag in
                   ("had_ft_at_first_sight", "edited_at_first_sight", "burst"))
            or not opportunity_forward._number(m.get("odds"), 1.10)
            or game_identity(m) is None
            or not eligible(dict(m, in_roi=True))):
        return None
    sent = chronology.signal_time(m)
    seen = chronology.signal_seen({
        "date_utc": m.get("date_utc"),
        "signal_seen_utc": m.get("collected_at_utc"),
    })
    kickoff = chronology.kickoff_time(m)
    if not (sent and seen and kickoff and cutoff < sent <= seen <= now
            and seen + opportunity_forward.MIN_LEAD < kickoff):
        return None
    return seen


def first_per_game(messages, cutoff, now, eligible_fn):
    """Select the earliest qualifying first-seen quote, independent of outcomes."""
    first = {}
    for m in messages:
        if not isinstance(m, dict):
            continue
        seen = eligible_fn(m, cutoff, now)
        if seen is None:
            continue
        mid = game_identity(m)
        order = (seen, chronology.signal_time(m), str(m.get("message_id")))
        if mid not in first or order < first[mid][0]:
            first[mid] = (order, m)
    return first


def payoff(signal, outcome, now):
    """Return (status, realized unit profit, profit with -2% odds).

    Quarantine conflicting results even if a timeout has passed. No unknown
    or future result is silently treated as a success.
    """
    kickoff = chronology.kickoff_time(signal)
    if not kickoff:
        return ("quarantined", None, None)
    result = opportunity_results.resolve(outcome, kickoff=kickoff, as_of=now)
    if result.conflict:
        return ("quarantined", None, None)
    if result.score is not None:
        if sum(result.score) > 2:
            odd = signal["odds"]
            return ("win", odd - 1.0, odd * ODDS_FACTOR - 1.0)
        return ("loss", -1.0, -1.0)
    if now >= kickoff + opportunity_forward.MAX_UNRESOLVED:
        return ("timeout_loss", -1.0, -1.0)
    return ("pending", None, None)


def statistics(first, results, now):
    """One vote per game. Conservative score is a descriptive penalty, NOT CI."""
    profits, cut, counts = [], [], {
        "forward_games": len(first), "settled": 0, "wins": 0,
        "losses": 0, "timeouts_counted_as_losses": 0, "pending": 0,
        "quarantined": 0,
    }
    for mid, (_, signal) in sorted(first.items()):
        status, p, pc = payoff(signal, results.get(mid), now)
        if status == "win":
            counts["wins"] += 1
        elif status in ("loss", "timeout_loss"):
            counts["losses"] += 1
        if status == "timeout_loss":
            counts["timeouts_counted_as_losses"] += 1
        if status == "pending":
            counts["pending"] += 1
        elif status == "quarantined":
            counts["quarantined"] += 1
        else:
            counts["settled"] += 1
            profits.append(p)
            cut.append(pc)
    n = len(cut)
    mean = sum(cut) / n if n else None
    variance = (sum((x - mean) ** 2 for x in cut) / (n - 1)) if n > 1 else None
    # Diagnostic ranking is suppressed under 30 independent forward results.
    penalty = math.sqrt(variance / n) * TWO_STANDARD_ERRORS if n >= MIN_RANK_GAMES else None
    return dict(counts,
                roi_advertised=round(sum(profits) / n, 4) if n else None,
                roi_odds_minus_2pct=round(mean, 4) if n else None,
                descriptive_two_se_score=round(mean - penalty, 4) if penalty is not None else None)


def build(messages, results, protocol, *, activation_utc, now):
    """Rank the predeclared rule against the wider, overlapping, forward baseline."""
    proof = opportunity_forward.evaluate(
        [], {}, protocol, activation_utc=activation_utc, now=now,
    )
    as_of = chronology.parse_utc(now) if isinstance(now, str) else now
    if as_of is None or as_of.tzinfo is None:
        raise ValueError("No trusted evaluation clock")
    activated = chronology.parse_utc(activation_utc)
    if activated is None:
        return {
            "schema": "goal_roi_exploratory_ranking_v1",
            "status": "not_activated", "ranking": [], "baseline": None,
            "certification": "not_activated", "automatic_alerts_changed": False,
            "statistical_claim": "none",
        }
    cutoff = chronology.parse_utc(proof["forward_window_started_utc"])
    candidate = first_per_game(messages, cutoff, as_of, opportunity_forward.eligible_signal)
    baseline = first_per_game(messages, cutoff, as_of, baseline_signal)
    cur = statistics(candidate, results, as_of)
    benchmark = statistics(baseline, results, as_of)
    # An audit cross-check avoids divergent counting between two independent modules.
    check = opportunity_forward.evaluate(
        messages, results, protocol, activation_utc=activation_utc, now=as_of,
    )
    for field in ("forward_games", "settled", "wins", "losses", "pending",
                  "quarantined", "timeouts_counted_as_losses", "roi_advertised",
                  "roi_odds_minus_2pct"):
        if cur[field] != check[field]:
            raise ValueError("Prospective ledger and ranking disagree on " + field)

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
        "cutoff_utc": chronology.iso(cutoff),
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
