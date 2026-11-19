"""Regulation-time result observations for the prospective opportunity study."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from . import chronology


REGULATION_TIME = timedelta(minutes=90)


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


def collected(source, record_id, score, stamp, *, period="regulation", published=None):
    """Legacy collectors stamp the start of their step. Apply its observation margin."""
    observed = utc(stamp)
    publication = utc(published)
    margin = timedelta(minutes=chronology.OBSERVATION_MARGIN_MIN)
    try:
        available = observed + margin if observed is not None else None
    except OverflowError:
        available = None
    if source == "telegram" and (publication is None or available is None or publication > available):
        available = None
    return Observation(source, str(record_id), period, score_pair(score), observed, available, publication)


@dataclass(frozen=True)
class Resolution:
    score: tuple[int, int] | None = None
    conflict: bool = False
    available_at: datetime | None = None
    first_available_at: datetime | None = None


def resolve(evidence, *, kickoff, as_of):
    """Resolve each record's latest valid version visible at the evaluation cutoff."""
    earliest_ft = kickoff + REGULATION_TIME if kickoff is not None else None
    if isinstance(evidence, dict):
        # Existing callers can supply an already observed result with explicit availability.
        known = utc(evidence.get("known_utc"))
        if known is None or known > as_of or (earliest_ft is not None and known < earliest_ft):
            return Resolution()
        if evidence.get("conflict") is True:
            return Resolution(conflict=True, available_at=known, first_available_at=known)
        return Resolution(score_pair(evidence.get("ft")), available_at=known, first_available_at=known)

    if not isinstance(evidence, (list, tuple)):
        return Resolution()
    latest, first = {}, None
    for row in evidence:
        if (not isinstance(row, Observation) or row.period != "regulation" or row.score is None or row.observed_at is None
                or row.available_at is None or (kickoff is not None and row.observed_at <= kickoff)
                or not row.observed_at <= row.available_at <= as_of
                or (earliest_ft is not None and (row.available_at < earliest_ft
                    or (row.published_at is not None and row.published_at < earliest_ft)))):
            continue
        first = min(first, row.available_at) if first is not None else row.available_at
        key = (row.source, row.record_id)
        # A late read of an older Telegram edit cannot supersede a newer edit.
        version = row.published_at or row.observed_at
        current = latest.get(key)
        if current is None or version > current[0]:
            latest[key] = (version, {row.score}, row.available_at)
        elif version == current[0]:
            current[1].add(row.score)

    scores = {score for _, values, _ in latest.values() for score in values}
    if not scores:
        return Resolution()
    available = max(stamp for _, _, stamp in latest.values())
    return Resolution(next(iter(scores)) if len(scores) == 1 else None,
                      conflict=len(scores) > 1, available_at=available, first_available_at=first)


def remember_provider_results(state, matched, stamp):
    """Preserve the operational first result and append each observed FT version."""
    for match_id, info in matched.items():
        score = score_pair(info.get("ft"))
        if score is None:
            continue
        row = {"ft": list(score), "period": info.get("period", "regulation"),
               "provider_id": info.get("bsd_id"), "kickoff_utc": info.get("event_date")}
        history = state.setdefault("ft_versions", {}).setdefault(match_id, [])
        if not history or any(history[-1].get(key) != value for key, value in row.items()):
            history.append(dict(row, observed_at=stamp.isoformat(), available_at=stamp.isoformat()))
        state["results"].setdefault(match_id, dict(info, matched_at_utc=stamp.isoformat(timespec="seconds")))


def provider_observations(state, source):
    """Load archived versions. A current-only snapshot does not prove past content."""
    versions = state.get("ft_versions") if isinstance(state, dict) else None
    if not isinstance(versions, dict):
        return {}
    return {
        mid: [Observation(source, mid, row.get("period"), score_pair(row.get("ft")),
                          utc(row.get("observed_at")), utc(row.get("available_at")),
                          kickoff=utc(row.get("kickoff_utc")))
              for row in rows if isinstance(row, dict)]
        for mid, rows in versions.items() if isinstance(rows, list)
    }


def merge_sources(bot, providers):
    found = {mid: list(rows) for mid, rows in bot.items()}
    for source, state in providers.items():
        for mid, observations in provider_observations(state, source).items():
            found.setdefault(mid, []).extend(observations)
    return found
