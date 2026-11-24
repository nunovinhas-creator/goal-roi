"""Exact v3 identity and a separate vocabulary for reporting across sources."""

from typing import Any, Mapping, TypeAlias

from scangoal.discovery import dsl
from scangoal.discovery.v3 import riskset

Selection: TypeAlias = str | tuple[str, str]
ContractKey: TypeAlias = tuple[str, str, str, str, str | None, tuple[str, str, str, Selection]]

# Only semantics explicitly supported by the source parser are listed. These
# are families for inventory and power reporting, never comparator keys.
ANALYSIS_EQUIVALENCES = {
    ('Betfair', '1x2', 'winner'): 'regulation_1x2',
    ('Stake', '1x2', 'winner'): 'regulation_1x2',
    ('Polymarket', '1x2', 'winner'): 'regulation_1x2',
    ('ScanGoal', 'asian total', 'match_goals'): 'asian_goal_total',
    ('Stake', 'asian total', 'match_goals'): 'asian_goal_total',
    ('Polymarket', 'asian total', 'match_goals'): 'asian_goal_total',
    ('Stake', 'asian handicap', 'asian_handicap'): 'asian_handicap',
    ('Polymarket', 'polymarket', 'asian_handicap'): 'asian_handicap',
}


def contract_key(event: Mapping[str, Any]) -> ContractKey | None:
    """Use the frozen matcher as the single definition of an exact contract."""
    fields = ('fixture_id', 'source', 'phase', 'market_key', 'period', 'line', 'selection', 'direction', 'teams')
    normalized = dict.fromkeys(fields)
    normalized.update(event)
    return riskset.contract(normalized)


def contract_family(key: ContractKey) -> tuple[str, str, str, str]:
    """Summarize within a source, phase and period, retaining native scope if unknown."""
    source, phase, market, period, _, (scope, *_) = key
    family = ANALYSIS_EQUIVALENCES.get((source, market, scope))
    if family is None:
        family = 'native:' + dsl.digest((market, scope))
    return source, phase, period, family
