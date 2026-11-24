"""Offline fixture authorities and synthetic Polymarket catalogue/book envelopes."""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scangoal import event_intelligence as events
from scangoal.discovery.v32.tape import STREAMS
from . import books, manifest

KICKOFF = datetime(2029, 9, 12, 20, tzinfo=timezone.utc)
DECISION = KICKOFF - timedelta(minutes=90)
M = timedelta(minutes=1)
REFERENCES = frozenset({'ref-ctf', 'ref-v2'})
FEED_RULE = dict(version=32, within_seconds=1800,
                 steps=[[dict(field='source', op='eq', value='Polymarket'),
                         dict(field='volume_usd', op='ge', value=50000)],
                        [dict(field='source', op='eq', value='Stake'), dict(field='point_odds', op='ge', value=1.5)]],
                 relations=[])


def authorities():
    # Deliberately public fixture seeds, with no trust outside this laboratory.
    def key(role):
        return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(('goal-roi-public-fixture/' + role).encode()).digest())
    return key('plan-authority'), key('capture')


def games():
    specs = (('ref-ctf', 0, books.CTF), ('edge-ctf', 45, books.CTF), ('gap-ctf', -45, books.CTF),
             ('ref-v2', 0, books.V2), ('pair-v2', 15, books.V2), ('outside-46', 46, books.CTF))
    return {name: (books.Game(name, events.iso(KICKOFF + offset * M), name + '-home',
                             name + '-away', 'lab-league'), protocol) for name, offset, protocol in specs}


def catalogue():
    rows = []
    for game_index, (game, protocol) in enumerate(games().values(), 1):
        markets = []
        for market_index, (family, line, selection) in enumerate(sorted(books.SPECS), 1):
            code = game_index * 100 + market_index * 10
            id_ = f'{game.id}-m{market_index}'
            tokens = []
            for side_index, side in enumerate(('YES', 'NO'), 1):
                if protocol == books.CTF:
                    tokens.append(dict(side=side, asset_id=str(code + side_index)))
                else:
                    tokens.append(dict(side=side, token_id=str(code + side_index), position_id=f'{id_}-{side}'))
            row = dict(protocol=protocol, market_id=id_, family=family, period='regulation', line=line,
                       selection=selection, rules=books.REGULATION, tokens=tokens)
            if protocol == books.CTF:
                row.update(condition_id='0x' + manifest.sha(id_), exchange='ctf-exchange')
            else:
                row.update(v2_market_ref='v2-' + id_, version='v2')
            markets.append(row)
        rows.append(dict(game=asdict(game), markets=markets))
    return dict(schema='mock-polymarket-v1', events=rows)


def typed_event(game, at, kind='quote', **changes):
    """Independent synthetic Stake/alert tape. It has no CLOB price conversion."""
    return dict(dict(match_id=game.id, teams=[game.home, game.away], league=game.competition,
                     kickoff_utc=game.kickoff, phase='pre', source='Stake', signal_type='LAB', signal_kind='original',
                     market='Asian Total', market_key='match_goals', period='regulation', direction='over',
                     line='2.5', selection='Over 2.5', price_kind='point' if kind == 'quote' else None,
                     odds=['2', '2'] if kind == 'quote' else None, price_low=2 if kind == 'quote' else None,
                     price_high=2 if kind == 'quote' else None, event_kind=kind, receipt='completed_page',
                     emitted_utc=events.iso(at), available_utc=events.iso(at)), **changes)


def complete_slots():
    out = []
    for game, _ in games().values():
        if game.id == 'outside-46':
            continue
        for stream in sorted(STREAMS):
            if stream == 'quote':
                payload = [typed_event(game, DECISION - 2 * M)]
            elif stream == 'alert' and game.id in REFERENCES:
                payload = [typed_event(game, DECISION - 5 * M, 'alert', source='Polymarket', volume_usd=70_000)]
            else:
                payload = []
            slot = manifest.Slot(game.fixture, stream, manifest.COMPLETE, events.iso(DECISION - 30 * M),
                                 events.iso(DECISION), manifest.sha((manifest.COMPLETE, game.fixture, stream)),
                                 manifest.sha(payload))
            if game.id == 'gap-ctf' and stream == 'alert':
                row = manifest.receipt(slot, requested=None, available=DECISION, source_clock=None,
                                       status='offline', payload=None)
            elif game.id == 'pair-v2' and stream == 'goal':
                # A complete capture can arrive too late for the decision.
                row = manifest.receipt(slot, requested=DECISION, available=DECISION + M, source_clock=DECISION,
                                       status='empty', payload=payload)
            else:
                row = manifest.receipt(slot, requested=DECISION, available=DECISION, source_clock=DECISION,
                                       status='received' if payload else 'empty', payload=payload)
            out.append((slot, row))
    return out


def mock_book(slot, contract):
    """Replay a predetermined synthetic response without any transport."""
    at = manifest.stamp(slot.end)
    step = int((at - (manifest.stamp(contract.game.kickoff) - books.LEAD)) / books.CADENCE)
    request = at
    available = at + timedelta(seconds=3) if step % 3 == 2 else at
    source = at
    shift = Decimal(step % 4) / 100
    base = (Decimal('0.42') if contract.side == 'YES' else Decimal('0.52')) + shift
    book = dict(books.identity(contract), tick_size='0.01', timestamp_utc=events.iso(source), market_state='open',
                bids=[dict(price=str(base), size='110'), dict(price=str(base - Decimal('0.01')), size='75')],
                asks=[dict(price=str(base + Decimal('0.03')), size='102'),
                      dict(price=str(base + Decimal('0.04')), size='180')])
    status = 'received'
    faults = (contract.game.id == 'ref-ctf' and contract.family == 'total_goals' and contract.side == 'YES')
    if faults:
        if step == 1:
            return None  # missing capture rather than a retrospective fabricated failure
        if step in (2, 3, 4, 5):
            status = {2: 'timeout', 3: 'rate_limited', 4: 'offline', 5: 'disk_error'}[step]
            request = None if step >= 4 else request
            book = source = None
        elif step == 6:
            book['market_state'] = 'suspended'
        elif step == 7:
            book['market_state'] = 'reopened'
        elif step == 8:
            book['asset_id'] = '9999'
        elif step == 9:
            book['asks'] = []
        elif step == 10:
            book['asks'][0]['price'] = book['bids'][0]['price']
        elif step == 11:
            book.pop('bids')
        elif step == 12:
            source = at - timedelta(seconds=90)
            book['timestamp_utc'] = events.iso(source)
        elif step == 13:
            book['kickoff'] = events.iso(manifest.stamp(contract.game.kickoff) + 15 * M)
        elif step == 14:
            available = at + 3 * M
    return manifest.receipt(slot, requested=request, available=available, source_clock=source, status=status, payload=book)


def build(recorded=()):
    authority, capture = authorities()
    raw_catalogue = catalogue()
    targets = {name: game for name, (game, _) in games().items()}
    contracts = books.catalog(raw_catalogue, targets)
    pairs = list(complete_slots())
    by_id = {item.id: item for item in contracts}
    saved = {row['slot']: row for row in recorded}
    if len(saved) != len(recorded):
        raise manifest.Invalid('duplicate_recorded_book_response')
    for slot in books.plan_slots(contracts, REFERENCES):
        item = by_id[slot.target]
        if slot.id in saved:
            row = saved.pop(slot.id)
            manifest.validate_receipt(row, slot)
            books.read_book(item, row)
        else:
            row = mock_book(slot, item)
        pairs.append((slot, row))
        # A later different receipt for the same slot remains a conflict.
        if (item.game.id, item.family, item.side, slot.end) == (
                'ref-ctf', 'total_goals', 'YES', events.iso(KICKOFF - 120 * M)) and row is not None:
            later = manifest.stamp(row['available_utc']) + timedelta(seconds=10)
            body = dict(row['payload'], market_state='reopened')
            other = manifest.receipt(slot, requested=manifest.stamp(row['requested_utc']), available=later,
                                     source_clock=manifest.stamp(row['source_utc']), status='received', payload=body)
            pairs.append((slot, other))
    if saved:
        raise manifest.Invalid('recorded_response_outside_plan')
    slots = {slot.id: slot for slot, _ in pairs}
    sealed_at = min(manifest.stamp(slot.start) for slot in slots.values()) - M
    plan = manifest.make_plan(slots.values(), sealed_at=sealed_at, catalog_sha256=manifest.sha(raw_catalogue),
                              authority=authority)
    rows = sorted((row for _, row in pairs if row is not None), key=lambda row: (row['available_utc'], row['slot']))
    return plan, rows, raw_catalogue, targets, authority, capture
