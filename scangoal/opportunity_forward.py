"""Read-only prospective audit of the *frozen* PR #55 Polymarket rule.

No messages, orders, network requests, certificate, changes to collectors or writes.
This is a theoretical, conservative diagnostic, NOT a certified trading strategy.
"""
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import hashlib
import json
import math

from . import chronology, match_identity, opportunity_results
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


def select_signals(messages, cutoff, now):
    """Pick one first quote per fixture using only proof visible at the evaluation cutoff."""
    rows = [m for m in messages if isinstance(m, dict)]
    annotated, bindings = match_identity.resolve(rows, now)
    ordered, exclusions = [], Counter()
    for original, message in zip(rows, annotated):
        times = baseline_signal(original, cutoff, now)
        if times is not None:
            canonical = game_identity(message)
            if canonical is None:
                exclusions[message.get("match_identity_exclusion", "unproven_independence")] += 1
                continue
            order = (times.collected, times.emitted, str(message.get("message_id")), canonical)
            ordered.append((order, message))
    candidate, baseline = {}, {}
    for order, message in sorted(ordered, key=lambda row: row[0]):
        mid = game_identity(message)
        baseline.setdefault(mid, (order, message))
        if eligible_signal(message, cutoff, now) is not None:
            candidate.setdefault(mid, (order, message))
    return candidate, baseline, bindings, dict(exclusions)


def fixture_evidence(results, bindings):
    """Use only results assigned to an established fixture before the identifier was reused."""
    grouped = {}
    for mid, binding in bindings.items():
        rows = results.get(mid) if isinstance(results, dict) else None
        if isinstance(rows, dict):
            rows = (rows,)
        if not isinstance(rows, (tuple, list)):
            continue
        for row in rows:
            if isinstance(row, dict):
                available, kickoff = opportunity_results.utc(row.get("known_utc")), None
            elif isinstance(row, opportunity_results.Observation):
                available, kickoff = row.available_at, row.kickoff
            else:
                continue
            if (available is None or (binding.collision is not None and available >= binding.collision)
                    or (kickoff is not None and not binding.start <= kickoff <= binding.end)):
                continue
            if mid != binding.canonical and binding.available > available:
                row = (dict(row, known_utc=binding.available.isoformat()) if isinstance(row, dict)
                       else replace(row, available_at=binding.available))
            grouped.setdefault(binding.canonical, []).append(row)
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
        return born, None, None, as_of
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
    profits, cut = [], {}
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
        cut[mid] = (reduced, status)
    counts["roi_advertised"] = round(sum(profits) / len(profits), 4) if profits else None
    reduced_profit = sum(profit for profit, _ in cut.values())
    counts["roi_odds_minus_2pct"] = round(reduced_profit / len(cut), 4) if cut else None
    counts["stake_per_fixture"] = 1
    counts["resolved_stake_units"] = len(cut)
    counts["profit_advertised"] = round(sum(profits), 4)
    counts["profit_odds_minus_2pct"] = round(reduced_profit, 4)
    counts["confirmed_hit_rate"] = round(counts["wins"] / counts["ft_confirmed"], 4) if counts["ft_confirmed"] else None
    counts["expected_profit"] = None
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

    chosen, _, bindings, exclusions = select_signals(messages, cutoff, as_of)
    counts, _ = settled_statistics(chosen, fixture_evidence(results, bindings), as_of)
    return {
        "rule_id": protocol["id"],
        "status": "exploratory_only",
        "quote_policy": QUOTE_POLICY,
        "born_utc": born.isoformat(),
        "actual_activation_utc": activated.isoformat(),
        "forward_window_started_utc": cutoff.isoformat(),
        "identity_exclusions": exclusions,
        **counts,
        "scientific_claim": "none",
        "validation": "not_activated",
        "alerts_changed": False,
    }
