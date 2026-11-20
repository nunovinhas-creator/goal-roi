"""Commit fixture identity in the order evidence became available."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
import unicodedata

from . import chronology
from .eligibility import text_identity


# Same regulation-time window used for matching a live signal to a provider kickoff.
LIVE_WINDOW = timedelta(minutes=150)
UNPROVEN = datetime.max.replace(tzinfo=chronology.UTC)


@dataclass
class Binding:
    canonical: str
    parts: tuple
    start: datetime
    end: datetime
    available: datetime
    collision: datetime | None = None


def normalized(value):
    if not isinstance(value, str):
        return None
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()) or None


def components(signal):
    teams = signal.get("teams")
    home, away = (normalized(team) for team in teams) if isinstance(teams, (list, tuple)) and len(teams) == 2 else (None, None)
    return chronology.parse_utc(signal.get("kickoff_utc")), home, away, normalized(signal.get("league"))


def compatible(binding, parts, start, end):
    return (start <= binding.end and binding.start <= end
            and all(not (old and new) or old == new for old, new in zip(binding.parts, parts)))


def signal_seen(row):
    stamp = row.get("signal_seen_utc", row.get("collected_at_utc"))
    return chronology.signal_seen(dict(row, signal_seen_utc=stamp))


def resolve(signals, as_of=None):
    """Return per-signal identities and the original bindings needed to assign results."""
    rows = list(signals)
    assigned = [dict(row, canonical_match_id=None) for row in rows]
    ordered = []
    for i, row in enumerate(rows):
        mid = text_identity(row.get("match_id"))
        if mid is None:
            continue
        if row.get('bet') is False or row.get('in_roi') is False or any(
                row.get(flag) for flag in ('edited_at_first_sight', 'had_ft_at_first_sight', 'burst')):
            # These rows can be described historically but cannot establish an exposure or alias.
            assigned[i]['canonical_match_id'] = mid
            continue
        seen = signal_seen(row)
        if as_of is not None and (seen is None or seen > as_of):
            continue
        sent = chronology.signal_time(row)
        ordered.append((seen or UNPROVEN, sent or UNPROVEN, mid, str(row.get("message_id", row.get("key"))), i))

    bindings, exact, by_teams = {}, {}, defaultdict(list)
    for seen, sent, mid, _, i in sorted(ordered):
        row = rows[i]
        parts = components(row)
        kickoff, home, away, league = parts
        complete = all(parts) and home != away
        if kickoff is not None:
            start = end = kickoff
        elif row.get('phase') == 'live' and sent != UNPROVEN:
            start, end = sent - LIVE_WINDOW, sent
        else:
            start, end = datetime.min.replace(tzinfo=chronology.UTC), UNPROVEN

        binding = bindings.get(mid)
        if binding is not None:
            if compatible(binding, parts, start, end):
                assigned[i]['canonical_match_id'] = binding.canonical
            else:
                if binding.collision is None and seen != UNPROVEN:
                    binding.collision = seen
                assigned[i]['match_identity_exclusion'] = 'reused_match_id'
            continue

        # Only exact fixture evidence can join independently named aliases.
        canonical = exact.get(parts) if complete else None
        similar = by_teams[home, away]
        if (not home or not away or home == away or not league or end == UNPROVEN
                or (canonical is None and any(compatible(other, parts, start, end) for other in similar))):
            assigned[i]['match_identity_exclusion'] = 'unproven_independence'
            continue
        binding = bindings[mid] = Binding(canonical or mid, parts, start, end, seen)
        assigned[i]['canonical_match_id'] = binding.canonical
        if canonical is None:
            similar.append(binding)
            if complete:
                exact[parts] = mid
    return assigned, bindings


def annotate(signals, as_of=None):
    rows = list(signals)
    if all('canonical_match_id' in row for row in rows):
        return rows
    return resolve(rows, as_of)[0]
