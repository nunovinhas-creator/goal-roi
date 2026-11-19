"""Resolve fixture aliases using exact, time-visible fixture identifiers."""

from collections import defaultdict
import unicodedata

from . import chronology
from .eligibility import text_identity


def normalized(value):
    if not isinstance(value, str):
        return None
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()) or None


def components(signal, kickoff=None):
    teams = signal.get("teams")
    home, away = (normalized(team) for team in teams) if isinstance(teams, (list, tuple)) and len(teams) == 2 else (None, None)
    return (kickoff if kickoff is not None else chronology.parse_utc(signal.get("kickoff_utc")),
            home, away, normalized(signal.get("league")))


def fixture_key(signal, kickoff=None):
    parts = components(signal, kickoff)
    return parts if all(parts) and parts[1] != parts[2] else None


def aliases(signals, as_of=None):
    """Map raw identifiers to one representative, or None for contradictory/ambiguous evidence."""
    facts, complete = defaultdict(set), defaultdict(set)
    for row in signals:
        mid = text_identity(row.get("match_id"))
        if (mid is None or row.get('bet') is False or row.get('in_roi') is False
                or any(row.get(flag) for flag in ('edited_at_first_sight', 'had_ft_at_first_sight', 'burst'))):
            continue
        stamp = row.get("signal_seen_utc", row.get("collected_at_utc"))
        if as_of is not None:
            seen = chronology.signal_seen(dict(row, signal_seen_utc=stamp))
            if seen is None or seen > as_of:
                continue
        part = components(row)
        facts[mid].add(part)
        if all(part) and part[1] != part[2]:
            complete[mid].add(part)

    result, groups, incomplete = {}, defaultdict(list), {}
    for mid, evidence in facts.items():
        dimensions = [{value for value in dim if value is not None} for dim in zip(*evidence)]
        if any(len(values) > 1 for values in dimensions):
            result[mid] = None
        elif complete[mid]:
            groups[next(iter(complete[mid]))].append(mid)
        else:
            incomplete[mid] = tuple(next(iter(values), None) for values in dimensions)
    for mids in groups.values():
        root = min(mids)
        result.update(dict.fromkeys(mids, root))

    # Equal time and ordered teams without competition cannot prove an alias.
    partial = defaultdict(list)
    for mid, parts in incomplete.items():
        if all(parts[:3]):
            partial[parts[:3]].append(mid)
    proven = {parts[:3] for parts in groups}
    for mid, parts in incomplete.items():
        key = parts[:3]
        result[mid] = None if key in proven or len(partial[key]) > 1 else mid
    return result


def annotate(signals, as_of=None, *, mapping=None):
    rows = list(signals)
    if mapping is None:
        if all('canonical_match_id' in row for row in rows):
            return rows
        mapping = aliases(rows, as_of)
    return [dict(row, canonical_match_id=mapping.get(text_identity(row.get("match_id")),
                                                     text_identity(row.get("match_id")))) for row in rows]
