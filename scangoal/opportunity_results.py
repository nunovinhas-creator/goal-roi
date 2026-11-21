"""Regulation-time result observations for the prospective opportunity study."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import chronology


REGULATION_TIME = timedelta(minutes=90)
SETTLEMENT_WINDOW_DAYS = 3


@dataclass(frozen=True)
class Observation:
    source: str
    record_id: str
    period: str
    score: tuple[int, int] | None
    observed_at: datetime | None
    available_at: datetime | None
    published_at: datetime | None = None
    kickoff: datetime | None = None
    fixture_id: str | None = None
    link: str | None = None
    provider_record: dict | None = None


def score_pair(score):
    if (isinstance(score, (list, tuple)) and len(score) == 2
            and all(type(goals) is int and goals >= 0 for goals in score)):
        return tuple(score)
    return None


def utc(value):
    try:
        return chronology.parse_utc(value)
    except OverflowError:
        return None


def collected(source, record_id, score, stamp, *, period="regulation", published=None, kickoff=None, exact=False,
              fixture_id=None, link=None):
    """Legacy collectors stamp the start of their step. Apply its observation margin."""
    observed = utc(stamp)
    publication = utc(published)
    margin = timedelta(minutes=0 if exact else chronology.OBSERVATION_MARGIN_MIN)
    try:
        available = observed + margin if observed is not None else None
    except OverflowError:
        available = None
    if source.startswith("telegram") and (publication is None or available is None or publication > available
                                          or (exact and publication > observed)):
        available = None
    return Observation(source, str(record_id), period, score_pair(score), observed, available, publication, utc(kickoff), fixture_id, link)


def ft_time_valid(kickoff, observed, available, published, as_of):
    """A regulation FT must have existed only after a provable playing interval."""
    if None in (kickoff, observed, available, as_of):
        return False
    try:
        earliest = kickoff + REGULATION_TIME
    except OverflowError:
        return False
    return earliest <= observed <= available <= as_of and (published is None or earliest <= published <= available)


@dataclass(frozen=True)
class Resolution:
    score: tuple[int, int] | None = None
    conflict: bool = False
    available_at: datetime | None = None
    first_available_at: datetime | None = None
    first_score: tuple[int, int] | None = None
    first_consensus_at: datetime | None = None


def settlement(evidence, *, kickoff, as_of, deadline):
    """Resolve the primary result at its deadline and report later changes separately."""
    cutoff = min(as_of, deadline) if deadline is not None else as_of
    primary = resolve(evidence, kickoff=kickoff, as_of=cutoff)
    if cutoff < as_of:
        current = resolve(evidence, kickoff=kickoff, as_of=as_of)
        if (current.score, current.conflict) != (primary.score, primary.conflict):
            return primary, current
    return primary, None


def resolve(evidence, *, kickoff, as_of):
    """Resolve each record's latest valid version visible at the evaluation cutoff."""
    rows = [evidence] if isinstance(evidence, dict) else evidence
    if not isinstance(rows, (list, tuple)):
        return Resolution()

    events = {}
    for i, row in enumerate(rows):
        if isinstance(row, dict):
            known = utc(row.get("known_utc"))
            score = score_pair(row.get("ft"))
            if (ft_time_valid(kickoff, known, known, None, as_of)
                    and (score is not None or row.get("conflict") is True)):
                events.setdefault(known, []).append((("legacy", i), known, None if row.get("conflict") else score))
            continue
        if (not isinstance(row, Observation) or row.period != "regulation" or row.score is None
                or (row.source.startswith("telegram") and row.published_at is None)
                or not ft_time_valid(kickoff or row.kickoff, row.observed_at, row.available_at, row.published_at, as_of)):
            continue
        events.setdefault(row.available_at, []).append(
            ((row.source, row.record_id), row.published_at or row.observed_at, row.score))

    latest, score, conflict, since = {}, None, False, None
    first_score, first_consensus = None, None
    for available, versions in sorted(events.items()):
        for key, version, value in versions:
            current = latest.get(key)
            if current is None or version > current[0]:
                latest[key] = (version, {value})
            elif version == current[0]:
                current[1].add(value)
        scores = {value for _, values in latest.values() for value in values}
        now_conflict = len(scores) != 1 or None in scores
        now_score = None if now_conflict else next(iter(scores))
        if (score, conflict) != (now_score, now_conflict):
            score, conflict, since = now_score, now_conflict, available
        if first_consensus is None and score is not None:
            first_score, first_consensus = score, available
    return Resolution(score, conflict, since, min(events, default=None), first_score, first_consensus)


def settle_research_bet(bet, as_of):
    """Materialize a versioned research row at a cutoff, including its first confirmed payoff."""
    from .parser import settle

    versions = bet["_ft_observations"]
    decision = chronology.decision_time(bet)
    deadline = decision + timedelta(days=SETTLEMENT_WINDOW_DAYS) if decision is not None else None
    cutoff = min(as_of, deadline) if deadline is not None else as_of
    kickoff = chronology.kickoff_time(bet)
    if kickoff is None:
        visible = {v.kickoff for v in versions if isinstance(v, Observation) and v.kickoff is not None
                   and v.available_at is not None and v.available_at <= cutoff}
        kickoff = next(iter(visible)) if len(visible) == 1 else None
    if kickoff is None and bet.get('phase') == 'live' and type(bet.get('minute')) is int and 0 <= bet['minute'] <= 120:
        sent = chronology.signal_time(bet)
        if sent is not None:
            kickoff = sent - timedelta(minutes=max(0, bet['minute'] - 1))
    result, late = settlement(versions, kickoff=kickoff, as_of=as_of, deadline=deadline)

    def payout(score):
        market = bet.get("market")
        if score is not None and market and not (bet.get("phase") == "live" and market.strip().lower() == "asian handicap"):
            return settle(market, bet["selection"], score, bet.get("teams"))
        return None

    won = payout(result.score)
    row = dict(bet, ft=list(result.score) if result.score is not None else None, w=won,
               result_known_utc=None, result_available_utc=chronology.iso(result.available_at),
               result_known_source="versioned_ft" if result.score is not None else None,
               settled_by="versioned_ft" if won is not None else None,
               match_result_first_seen_utc=chronology.iso(result.first_available_at),
               result_quarantined=result.conflict,
               result_quarantined_utc=chronology.iso(result.available_at) if result.conflict else None,
               first_confirmed_w=payout(result.first_score),
               first_confirmed_utc=chronology.iso(result.first_consensus_at))
    row["result_reconciliation"] = (
        {"ft": late.score, "conflict": late.conflict, "available_utc": chronology.iso(late.available_at)}
        if late is not None else None)
    return row


def remember_provider_results(state, matched, stamp):
    """Archive the score and its full provider record in the same observation."""
    for match_id, info in matched.items():
        score = score_pair(info.get("ft"))
        if score is None:
            continue
        snapshot = deepcopy({k: v for k, v in info.items() if k != "matched_at_utc"})
        snapshot["ft"] = list(score)
        row = {"ft": list(score), "period": info.get("period", "regulation"),
               "provider_id": info.get("bsd_id"), "kickoff_utc": info.get("event_date"),
               "fixture_id": match_id, "snapshot": snapshot}
        history = state.setdefault("ft_versions", {}).setdefault(match_id, [])
        if history and all(history[-1].get(key) == value for key, value in row.items()):
            continue
        history.append(dict(row, observed_at=stamp.isoformat(), available_at=stamp.isoformat()))
        previous = state["results"].get(match_id)
        if previous is not None:
            state.setdefault("first_results", {}).setdefault(match_id, previous)
        state["results"][match_id] = dict(deepcopy(snapshot), matched_at_utc=stamp.isoformat())


def provider_observations(state, source):
    """Load archived versions. A current-only snapshot does not prove past content."""
    versions = state.get("ft_versions") if isinstance(state, dict) else None
    if not isinstance(versions, dict):
        return {}
    return {
        mid: [Observation(source, mid, row.get("period"), score_pair(row.get("ft")),
                          utc(row.get("observed_at")), utc(row.get("available_at")),
                          kickoff=utc(row.get("kickoff_utc")), fixture_id=mid,
                          link=(row.get("snapshot") or {}).get("link"), provider_record=row.get("snapshot") or {})
              for row in rows if isinstance(row, dict) and row.get("fixture_id", mid) == mid]
        for mid, rows in versions.items() if isinstance(rows, list)
    }


def _provider_record(mid, version, source, as_of):
    score = score_pair(version.get("ft"))
    kickoff = utc(version.get("kickoff_utc"))
    observed, available = utc(version.get("observed_at")), utc(version.get("available_at"))
    if (score is None or not ft_time_valid(kickoff, observed, available, None, as_of)
            or version.get("period") != "regulation" or version.get("fixture_id", mid) != mid):
        return None
    snapshot = version.get("snapshot")
    if snapshot is None:
        # Old journals only attest these fields. The current snapshot may be a later version.
        snapshot = {"bsd_id": version.get("provider_id"), "event_date": version["kickoff_utc"], "ft": list(score)}
    if (not isinstance(snapshot, dict) or snapshot.get("fixture_id", mid) != mid or score_pair(snapshot.get("ft")) != score
            or snapshot.get("bsd_id") != version.get("provider_id") or utc(snapshot.get("event_date")) != kickoff):
        return None
    return dict(deepcopy(snapshot), fixture_id=mid, period="regulation",
                provider=snapshot.get("provider", source), observed_at=observed.isoformat(),
                available_at=available.isoformat(), matched_at_utc=available.isoformat())


def provider_snapshot(state, source, as_of):
    """Return one complete, temporally valid provider record per fixture at the cutoff."""
    snapshots, first = state.get("results") or {}, state.get("first_results") or {}
    histories, visible = state.get("ft_versions") or {}, {}
    for mid in snapshots.keys() | histories.keys() | first.keys():
        versions = histories.get(mid, [])
        versions = [row for row in versions if isinstance(row, dict)] if isinstance(versions, list) else []
        records = [_provider_record(mid, row, source, as_of) for row in versions if isinstance(row, dict)]
        # The original snapshot is immutable once updates start. Only restore its own fields.
        legacy = first.get(mid) if mid in first else snapshots.get(mid) if not versions else None
        if isinstance(legacy, dict):
            stamp = legacy.get("matched_at_utc")
            observed = utc(stamp)
            if observed is not None and not any(utc(row.get("observed_at")) == observed for row in versions):
                records.append(_provider_record(mid, {"ft": legacy.get("ft"), "period": legacy.get("period", "regulation"),
                                                       "provider_id": legacy.get("bsd_id"), "kickoff_utc": legacy.get("event_date"),
                                                       "observed_at": stamp, "available_at": stamp, "snapshot": legacy}, source, as_of))
        candidates = [row for row in records if row is not None]
        if not candidates:
            continue
        def order(row):
            return utc(row["observed_at"]), utc(row["available_at"])
        latest = max(candidates, key=order)
        # Two different versions at the same observation leave the result in conflict.
        tied = [row for row in candidates if order(row) == order(latest)]
        if all(row == latest for row in tied):
            visible[mid] = latest
    return visible


def merge_sources(bot, providers):
    found = {mid: list(rows) for mid, rows in bot.items()}
    for source, state in providers.items():
        for mid, observations in provider_observations(state, source).items():
            found.setdefault(mid, []).extend(observations)
    return found
