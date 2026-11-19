"""Read-only prospective audit of the *frozen* PR #55 Polymarket rule.

No messages, orders, network requests, certificate, changes to collectors or writes.
This is a theoretical, conservative diagnostic, NOT a certified trading strategy.
"""
from datetime import timedelta
import math

from . import chronology
from .eligibility import eligible, game_identity

MIN_LEAD = timedelta(minutes=15)
MAX_UNRESOLVED = timedelta(days=3)
BORN_KEY = "declared_before_forward_samples_at_utc"
EXPECTED_RULE_ID = "polymarket_pre_over25_share_lt80_total_lt10000_v1"
EXPECTED_BORN = "2026-11-19T15:16:22Z"  # GitHub PR #55 merge time.
EXPECTED_SHA = "b7c7ba6720e37ec7a38c28c49e2223101fc45f9a"



def _number(value, lower=0):
    return type(value) in (int, float) and math.isfinite(value) and value >= lower


def eligible_signal(m, cutoff, now):
    """All four fixed conditions and chronology are checked on the original signal."""
    if (m.get("type") != "POLYMARKET"
            or m.get("phase") != "pre"
            or m.get("market") != "Asian Total"
            or m.get("selection") != "Over 2.5"):
        return None
    if (not _number(m.get("share")) or m["share"] >= 80
            or not _number(m.get("total")) or m["total"] >= 10000
            or not _number(m.get("odds"), 1.10)):
        return None
    if (m.get("bet") is not True
            or any(m.get(flag) is not False
                   for flag in ("had_ft_at_first_sight", "edited_at_first_sight", "burst"))):
        return None
    if not eligible(dict(m, in_roi=True)) or game_identity(m) is None:
        return None
    sent, seen, kickoff = (
        chronology.signal_time(m),
        chronology.signal_seen({
            "date_utc": m.get("date_utc"),
            "signal_seen_utc": m.get("collected_at_utc"),
        }),
        chronology.kickoff_time(m),
    )
    # Both original publication and first read must be after the frozen decision.
    if not (sent and seen and kickoff and cutoff < sent <= seen <= now
            and seen + MIN_LEAD < kickoff):
        return None
    return seen


def evaluate(messages, results, protocol, *, activation_utc, now):
    """Aggregate per independent match, no history replay and no financial claims.

    results: {match_id: {ft: [home,away], known_utc: ISO8601, conflict?: bool}}
    A no-result match older than 3 days is *lost*, not silently dropped.
    Conflicting/invalid known scores are quarantined, never promoted as wins.
    """
    if (not isinstance(protocol, dict) or protocol.get("id") != EXPECTED_RULE_ID
            or protocol.get(BORN_KEY) != EXPECTED_BORN
            or protocol.get("born_merge_sha") != EXPECTED_SHA):
        raise ValueError("Unrecognized or changed preregistration")
    born = chronology.parse_utc(protocol.get(BORN_KEY))
    activated = chronology.parse_utc(activation_utc)
    as_of = chronology.parse_utc(now) if isinstance(now, str) else now
    if born is None or as_of is None or as_of.tzinfo is None:
        raise ValueError("Invalid prospective audit clock or published birth")
    if activated is None:
        return {
            "rule_id": protocol["id"], "status": "not_activated",
            "forward_games": 0, "settled": 0, "pending": 0,
            "wins": 0, "losses": 0, "roi_advertised": None,
            "roi_odds_minus_2pct": None, "scientific_claim": "none",
        }
    cutoff = max(born, activated)
    if as_of < cutoff:
        raise ValueError("Audit precedes activation")

    chosen = {}
    for m in messages:
        if not isinstance(m, dict):
            continue
        seen = eligible_signal(m, cutoff, as_of)
        if seen is None:
            continue
        mid = game_identity(m)
        order = (seen, chronology.signal_time(m), str(m.get("message_id")))
        if mid not in chosen or order < chosen[mid][0]:
            chosen[mid] = (order, m)

    settled = wins = losses = pending = timeouts = quarantined = 0
    pnl = pnl_cut = 0.0
    for mid, (_, m) in sorted(chosen.items()):
        ko = chronology.kickoff_time(m)
        result = results.get(mid) if isinstance(results, dict) else None
        if isinstance(result, dict) and result.get("conflict") is True:
            quarantined += 1
            continue
        ft = result.get("ft") if isinstance(result, dict) else None
        known = chronology.parse_utc(result.get("known_utc")) if isinstance(result, dict) else None
        valid_ft = (isinstance(ft, list) and len(ft) == 2
                    and all(type(x) is int and x >= 0 for x in ft))
        # Observed result must be later than the decision, kickoff and actual report cutoff.
        if valid_ft and known and known >= ko and known > chosen[mid][0][0] and known <= as_of:
            win = sum(ft) >= 3
            wins += int(win)
            losses += int(not win)
            settled += 1
            pnl += (m["odds"] - 1.0) if win else -1.0
            pnl_cut += (m["odds"] * 0.98 - 1.0) if win else -1.0
        elif as_of >= ko + MAX_UNRESOLVED:
            losses += 1
            timeouts += 1
            settled += 1
            pnl -= 1.0
            pnl_cut -= 1.0
        else:
            pending += 1

    # One independent game receives one vote; previews are never scientific certification.
    return {
        "rule_id": protocol["id"],
        "status": "exploratory_only",
        "born_utc": chronology.iso(born),
        "actual_activation_utc": chronology.iso(activated),
        "forward_window_started_utc": chronology.iso(cutoff),
        "forward_games": len(chosen),
        "settled": settled,
        "pending": pending,
        "quarantined": quarantined,
        "timeouts_counted_as_losses": timeouts,
        "wins": wins,
        "losses": losses,
        "roi_advertised": round(pnl / settled, 4) if settled else None,
        "roi_odds_minus_2pct": round(pnl_cut / settled, 4) if settled else None,
        "scientific_claim": "none",
        "validation": "not_activated",
        "alerts_changed": False,
    }
