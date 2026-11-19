"""Read-only prospective audit of the *frozen* PR #55 Polymarket rule.

No messages, orders, network requests, certificate, changes to collectors or writes.
This is a theoretical, conservative diagnostic, NOT a certified trading strategy.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
import unicodedata

from . import chronology, opportunity_results
from .eligibility import eligible, game_identity

MIN_LEAD = timedelta(minutes=15)
MAX_UNRESOLVED = timedelta(days=3)
BORN_KEY = "declared_before_forward_samples_at_utc"
EXPECTED_RULE_ID = "polymarket_pre_over25_share_lt80_total_lt10000_v1"
EXPECTED_BORN = "2026-11-19T15:16:22Z"  # GitHub PR #55 merge time.
PROTOCOL_SHA256 = "9a05c3de3923cce26572c2eb16f86fd0da3af0d1f521c49f66ca20552955dc9d"
ODDS_FACTOR = 0.98
QUOTE_POLICY = ("The study commits the first eligible quote available per fixture. "
                "The Telegram notice may use a more recent quote from the same batch "
                "to show current odds. The study models its own selection, not actual fills.")


@dataclass(frozen=True)
class SignalTiming:
    emitted: datetime
    collected: datetime
    available: datetime
    decision: datetime
    kickoff: datetime



def _number(value, lower=0):
    return type(value) in (int, float) and math.isfinite(value) and value >= lower


def signal_timing(message, cutoff, as_of):
    """Prove when the original signal could have been used by the study."""
    sent, collected, kickoff = (
        opportunity_results.utc(message.get(key))
        for key in ("date_utc", "collected_at_utc", "kickoff_utc")
    )
    if not (sent and collected and kickoff and cutoff < min(sent, collected)):
        return None
    available = collected + timedelta(minutes=chronology.OBSERVATION_MARGIN_MIN)
    decision = collected + MIN_LEAD
    if not sent <= available <= decision <= as_of or decision >= kickoff:
        return None
    return SignalTiming(sent, collected, available, decision, kickoff)


def baseline_signal(m, cutoff, now):
    if (not isinstance(m, dict) or m.get("type") != "POLYMARKET" or m.get("phase") != "pre"
            or m.get("market") != "Asian Total" or m.get("selection") != "Over 2.5"
            or m.get("period") != "regulation" or m.get("market_scope") != "match_goals" or m.get("bet") is not True
            or any(m.get(flag) is not False
                   for flag in ("had_ft_at_first_sight", "edited_at_first_sight", "burst"))
            or not eligible(dict(m, in_roi=True))):
        return None
    return signal_timing(m, cutoff, now)


def eligible_signal(m, cutoff, now):
    if (not isinstance(m, dict) or not _number(m.get("share")) or m["share"] >= 80
            or not _number(m.get("total")) or m["total"] >= 10000):
        return None
    return baseline_signal(m, cutoff, now)


def fixture_key(message, kickoff):
    teams = message.get("teams")
    league = message.get("league")
    if (not isinstance(teams, (list, tuple)) or len(teams) != 2
            or not all(isinstance(v, str) and v.strip() for v in (*teams, league))):
        return None
    fields = tuple(" ".join(unicodedata.normalize("NFKC", v).casefold().split()) for v in (*teams, league))
    return (kickoff, *fields)


def select_signals(messages, cutoff, now):
    """Pick the first eligible quote per fixture using only known, eligible signals."""
    ordered = []
    for message in messages:
        times = baseline_signal(message, cutoff, now)
        if times is not None:
            order = (times.collected, times.emitted, str(message.get("message_id")), game_identity(message))
            ordered.append((order, times, message))
    candidate, baseline, aliases, fixtures = {}, {}, {}, {}
    for order, times, message in sorted(ordered, key=lambda row: row[0]):
        mid = game_identity(message)
        fixture = fixture_key(message, times.kickoff)
        canonical_id = aliases.setdefault(mid, fixtures.get(fixture, mid))
        if fixture is not None:
            fixtures.setdefault(fixture, canonical_id)
        baseline.setdefault(canonical_id, (order, message))
        if eligible_signal(message, cutoff, now) is not None:
            candidate.setdefault(canonical_id, (order, message))
    return candidate, baseline, aliases


def fixture_evidence(results, aliases):
    grouped = {}
    for mid, canonical in aliases.items():
        rows = results.get(mid) if isinstance(results, dict) else None
        if isinstance(rows, dict):
            grouped.setdefault(canonical, []).append(rows)
        elif isinstance(rows, (tuple, list)):
            grouped.setdefault(canonical, []).extend(rows)
    return grouped


def evaluation_window(protocol, activation_utc, now):
    if not isinstance(protocol, dict):
        raise ValueError("Unrecognized or changed preregistration")
    try:
        canonical = json.dumps(protocol, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Unrecognized or changed preregistration") from exc
    if hashlib.sha256(canonical.encode()).hexdigest() != PROTOCOL_SHA256:
        raise ValueError("Unrecognized or changed preregistration")
    born = opportunity_results.utc(protocol[BORN_KEY])
    activated = opportunity_results.utc(activation_utc)
    as_of = opportunity_results.utc(now) if isinstance(now, str) else now
    if not isinstance(as_of, datetime) or as_of.utcoffset() is None:
        raise ValueError("Invalid prospective audit clock")
    cutoff = max(born, activated) if activated is not None else None
    if cutoff is not None and as_of < cutoff:
        raise ValueError("Audit precedes activation")
    return born, activated, cutoff, as_of


def payoff(signal, evidence, now):
    """Return primary settlement and any later reconciliation that differs from it."""
    kickoff = opportunity_results.utc(signal.get("kickoff_utc"))
    if kickoff is None:
        return "quarantined", None, None, None
    deadline = kickoff + MAX_UNRESOLVED
    primary, current = opportunity_results.settlement(evidence, kickoff=kickoff, as_of=now, deadline=deadline)
    late = None
    if current is not None:
        late = "quarantined" if current.conflict else "ft_confirmed" if current.score is not None else None
    if primary.conflict:
        return "quarantined", None, None, late
    if primary.score is not None:
        if sum(primary.score) >= 3:
            odd = signal["odds"]
            return "win", odd - 1.0, odd * ODDS_FACTOR - 1.0, late
        return "loss", -1.0, -1.0, late
    if now >= deadline:
        return "timeout_loss", -1.0, -1.0, late
    return "pending", None, None, late


def settled_statistics(first, results, now):
    counts = dict(forward_games=len(first), settled=0, ft_confirmed=0, wins=0, losses=0,
                  pending=0, quarantined=0, timeouts_counted_as_losses=0,
                  late_ft_confirmed=0, late_quarantined=0)
    profits, cut = [], []
    for mid, (_, signal) in sorted(first.items()):
        status, profit, reduced, late = payoff(signal, results.get(mid), now)
        if late is not None:
            counts["late_" + late] += 1
        if status in ("pending", "quarantined"):
            counts[status] += 1
            continue
        counts["settled"] += 1
        counts["wins" if status == "win" else "losses"] += 1
        counts["timeouts_counted_as_losses" if status == "timeout_loss" else "ft_confirmed"] += 1
        profits.append(profit)
        cut.append(reduced)
    counts["roi_advertised"] = round(sum(profits) / len(profits), 4) if profits else None
    counts["roi_odds_minus_2pct"] = round(sum(cut) / len(cut), 4) if cut else None
    return counts, cut


def evaluate(messages, results, protocol, *, activation_utc, now):
    """Aggregate historical or current prospective results per match.

    Results map each match ID to observations or an already observed result.
    A no-result match older than 3 days is counted as a loss.
    A visible disagreement between valid observations puts the match in quarantine.
    """
    born, activated, cutoff, as_of = evaluation_window(protocol, activation_utc, now)
    if cutoff is None:
        counts, _ = settled_statistics({}, {}, as_of)
        return {"rule_id": protocol["id"], "status": "not_activated", **counts, "scientific_claim": "none"}

    chosen, _, aliases = select_signals(messages, cutoff, as_of)
    counts, _ = settled_statistics(chosen, fixture_evidence(results, aliases), as_of)
    return {
        "rule_id": protocol["id"],
        "status": "exploratory_only",
        "quote_policy": QUOTE_POLICY,
        "born_utc": born.isoformat(),
        "actual_activation_utc": activated.isoformat(),
        "forward_window_started_utc": cutoff.isoformat(),
        **counts,
        "scientific_claim": "none",
        "validation": "not_activated",
        "alerts_changed": False,
    }
