"""Point-in-time evidence for consumers of the offline journal."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, TypeAlias

from .contracts import ContractKey

Receipt: TypeAlias = Literal['completed_page', 'legacy_upper_bound']
PriceKind: TypeAlias = Literal['point', 'interval', 'indeterminate']
ScoreKind: TypeAlias = Literal['alert', 'goal', 'final']
Score: TypeAlias = tuple[int, int]


@dataclass(frozen=True)
class Provenance:
    emitted_utc: datetime
    published_utc: datetime
    available_utc: datetime
    receipt: Receipt
    message_id: int
    record_sha256: str
    text_sha256: str


@dataclass(frozen=True)
class QuoteObservation(Provenance):
    fixture_id: str | None
    contract_key: ContractKey | None
    source: str
    phase: str | None
    price_kind: PriceKind
    price_low: Decimal | None
    price_high: Decimal | None
    price_point: Decimal | None
    volume_alert_usd: Decimal | None
    volume_cumulative_usd: Decimal | None
    biggest_ticket_usd: Decimal | None
    n_bets: int | None
    kickoff_utc: datetime | None
    minute: int | None
    score: Score | None
    exclusion: str | None


@dataclass(frozen=True)
class ScoreEvent:
    kind: ScoreKind
    score: Score
    minute: int | None = None
    stoppage: int = 0


@dataclass(frozen=True)
class ContextObservation(Provenance):
    """Facts reported by one message version. A missing field stays unknown."""

    fixture_id: str | None
    contract_key: ContractKey | None
    source: str | None
    scores: tuple[ScoreEvent, ...]
    biggest_ticket_usd: Decimal | None
    n_bets: int | None
    count_unit: Literal['bets', 'trades'] | None
    volume_cumulative_usd: Decimal | None
    issues: tuple[str, ...]
