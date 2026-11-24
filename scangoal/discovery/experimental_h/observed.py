"""Receipt-time trajectory views for the experimental H miner."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from ... import event_intelligence as events
from ...observability import contract_key
from .. import dsl
from ..v32 import riskset
from ..v32.tape import Tape

ADAPTER = 'receipt-trajectory-v1'
VOLUME = ('volume_usd', 'volume_cumulative_usd', 'biggest_ticket_usd', 'n_bets', 'count_unit', 'share_pct')


@dataclass(frozen=True)
class Decision:
    at: datetime
    quote: dict
    history: tuple
    conflicts: frozenset

    @property
    def fixture(self):
        return self.quote['fixture_id']

    @property
    def key(self):
        return self.fixture, self.quote['available_utc'], self.quote['message_id']

    def within(self, seconds):
        left = self.at - timedelta(seconds=seconds)
        return tuple(event for event in self.history if events.emitted(event) >= left)


def view_at(tape, fixture, at):
    visible = riskset.view_at(tape, fixture, at)
    groups = defaultdict(list)
    for row in visible.history:
        if row['event_kind'] == 'quote' and (key := contract_key(row)) is not None:
            groups[key, events.emitted(row)].append(row)
    patches = {}
    conflicts = set()
    for (_, issued), rows in groups.items():
        masked = {field: None for field in VOLUME if len({row[field] for row in rows}) > 1}
        if 'count_unit' in masked:
            masked['n_bets'] = None
        if masked:
            conflicts.add(issued)
            for row in rows:
                patches[row['message_id']] = masked

    def project(row):
        return dict(row, **patches[row['message_id']]) if row['message_id'] in patches else row

    return riskset.View([project(row) for row in visible.history],
                        {key: project(row) for key, row in visible.quotes.items()}), frozenset(conflicts)


class Observed:
    """The only runtime input is a completed observation tape, with no FT labels."""

    def __init__(self, tape: Tape):
        self.tape = tape

    def covered(self, decision, left, right):
        return (not any(left <= issued <= right for issued in decision.conflicts)
                and self.tape.covered(decision.fixture, left, right, decision.at))

    def decisions(self, meter):
        """A fresh eligible quote receipt starts a decision, including delayed receipts."""
        for at, fixtures in sorted(self.tape.batches.items()):
            for fixture in sorted(fixtures):
                if not meter.spend():
                    return
                batch = self.tape.history(fixture, at)
                if not meter.charge(len(batch)):
                    return
                if not any(events.seen(row) == at and row['event_kind'] == 'quote' for row in reversed(batch)):
                    continue
                view, conflicts = view_at(self.tape, fixture, at)
                for _, quote in sorted(view.quotes.items(), key=lambda item: dsl.encoded(item[0])):
                    if events.seen(quote) != at or riskset.quote_gate(self.tape, quote, view, at) is not None:
                        continue
                    prefix = tuple(row for row in view.history if row['message_id'] != quote['message_id']
                                   and events.emitted(row) < at)
                    yield Decision(at, dict(quote, emitted_utc=events.iso(at)), prefix, conflicts)
