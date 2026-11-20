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


def collected(source, record_id, score, stamp, *, period="regulation", published=None, kickoff=None):
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
    return Observation(source, str(record_id), period, score_pair(score), observed, available, publication, utc(kickoff))


@dataclass(frozen=True)
class Resolution:
    score: tuple[int, int] | None = None
    conflict: bool = False
    available_at: datetime | None = None
    first_available_at: datetime | None = None


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
    earliest_ft = kickoff + REGULATION_TIME if kickoff is not None else None
    if isinstance(evidence, dict):
        # Existing callers can supply an already observed result with explicit availability.
        known = utc(evidence.get("known_utc"))
        if known is None or known > as_of or (earliest_ft is not None and known < earliest_ft):
            return Resolution()
        if evidence.get("conflict") is True:
            return Resolution(conflict=True, available_at=known, first_available_at=known)
        score = score_pair(evidence.get("ft"))
        return Resolution(score, available_at=known, first_available_at=known) if score is not None else Resolution()

    if not isinstance(evidence, (list, tuple)):
        return Resolution()
    latest, first_by_score = {}, {}
    legacy_scores, legacy_conflicts = set(), []
    for row in evidence:
        if isinstance(row, dict):
            legacy = resolve(row, kickoff=kickoff, as_of=as_of)
            if legacy.conflict:
                legacy_conflicts.append(legacy.available_at)
            if legacy.score is not None:
                legacy_scores.add(legacy.score)
                first_by_score[legacy.score] = min(legacy.available_at,
                                                    first_by_score.get(legacy.score, legacy.available_at))
            continue
        if (not isinstance(row, Observation) or row.period != "regulation" or row.score is None or row.observed_at is None
                or row.available_at is None or (kickoff is not None and row.observed_at <= kickoff)
                or not row.observed_at <= row.available_at <= as_of
                or (earliest_ft is not None and (row.available_at < earliest_ft
                    or (row.published_at is not None and row.published_at < earliest_ft)))):
            continue
        first_by_score[row.score] = min(row.available_at, first_by_score.get(row.score, row.available_at))
        key = (row.source, row.record_id)
        # A late read of an older Telegram edit cannot supersede a newer edit.
        version = row.published_at or row.observed_at
        current = latest.get(key)
        if current is None or version > current[0]:
            latest[key] = (version, {row.score})
        elif version == current[0]:
            current[1].add(row.score)

    scores = {score for _, values in latest.values() for score in values} | legacy_scores
    if not scores and not legacy_conflicts:
        return Resolution()
    score = next(iter(scores)) if len(scores) == 1 and not legacy_conflicts else None
    return Resolution(score, conflict=score is None, available_at=first_by_score.get(score),
                      first_available_at=min([*first_by_score.values(), *legacy_conflicts]))


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
