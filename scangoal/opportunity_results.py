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


def resolve(evidence, *, kickoff, as_of):
    """Resolve each record's latest valid version visible at the evaluation cutoff."""
    earliest_ft = kickoff + REGULATION_TIME
    if isinstance(evidence, dict):
        # Existing callers can supply an already observed result with explicit availability.
        known = utc(evidence.get("known_utc"))
        if known is None or not earliest_ft <= known <= as_of:
            return Resolution()
        if evidence.get("conflict") is True:
            return Resolution(conflict=True)
        return Resolution(score_pair(evidence.get("ft")))

    if not isinstance(evidence, (list, tuple)):
        return Resolution()
    latest = {}
    for row in evidence:
        if (not isinstance(row, Observation) or row.period != "regulation" or row.score is None or row.observed_at is None
                or row.observed_at <= kickoff or row.available_at is None
                or not row.observed_at <= row.available_at <= as_of
                or row.available_at < earliest_ft
                or (row.published_at is not None and row.published_at < earliest_ft)):
            continue
        key = (row.source, row.record_id)
        # A late read of an older Telegram edit cannot supersede a newer edit.
        version = row.published_at or row.observed_at
        current = latest.get(key)
        if current is None or version > current[0]:
            latest[key] = (version, {row.score})
        elif version == current[0]:
            current[1].add(row.score)

    scores = {score for _, values in latest.values() for score in values}
    if len(scores) > 1:
        return Resolution(conflict=True)
    return Resolution(next(iter(scores), None))
