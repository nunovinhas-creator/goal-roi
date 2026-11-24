"""Exact contract identity and decimal CLOB book replay for offline fixtures.

The two envelopes are lab schemas. They make protocol identity visible and
refuse mixed representations. They are not a declaration of live API shape.
"""

from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal
import re

from scangoal import event_intelligence as events
from scangoal.discovery.v32.tape import fixture_key
from .manifest import Invalid, POLL, Slot, sha, stamp

CADENCE = timedelta(minutes=15)
LEAD = timedelta(hours=6)
KICKOFF_GAP = timedelta(minutes=45)
MAX_BOOK_AGE = timedelta(minutes=30)
REGULATION = 'regulation_90_plus_stoppage_excludes_extra_time'
PRICE_UNIT = 'native_token_price'
SPECS = {('total_goals', '2.5', 'over'), ('handicap', '-0.5', 'home'), ('handicap', '+0.5', 'home')}
CTF = 'CTF'
V2 = 'V2'
ID = re.compile(r'[a-zA-Z0-9_-]{1,80}\Z')
TOKEN = re.compile(r'[1-9][0-9]{0,77}\Z')
CONDITION = re.compile(r'0x[0-9a-f]{64}\Z')
DECIMAL = re.compile(r'(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,8})?\Z')


@dataclass(frozen=True)
class Game:
    id: str
    kickoff: str
    home: str
    away: str
    competition: str

    @property
    def fixture(self):
        return fixture_key((stamp(self.kickoff), self.home, self.away, self.competition))


@dataclass(frozen=True)
class Contract:
    game: Game
    protocol: str
    market_id: str
    instrument: str
    venue_version: str
    family: str
    period: str
    line: str
    selection: str
    side: str
    token: str
    position: str
    rules: str

    @property
    def id(self):
        return sha(asdict(self))

    @property
    def scope(self):
        # An individual token identifies one game; contract equality across
        # games compares all settlement conventions and the exact selection.
        return (self.protocol, self.venue_version, self.family, self.period,
                self.line, self.selection, self.side, self.rules, PRICE_UNIT)


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _game(row):
    if not isinstance(row, dict) or set(row) != {'id', 'kickoff', 'home', 'away', 'competition'}:
        raise Invalid('invalid_game_identity')
    if not all(_matches(ID, row[k]) for k in ('id', 'home', 'away', 'competition')) or row['home'] == row['away']:
        raise Invalid('invalid_game_identity')
    stamp(row['kickoff'])
    return Game(**row)


def catalog(document, trusted_games):
    if not isinstance(document, dict) or set(document) != {'schema', 'events'} or document['schema'] != 'mock-polymarket-v1':
        raise Invalid('unknown_catalogue_schema')
    if not isinstance(document['events'], list):
        raise Invalid('invalid_catalogue_entries')
    seen, fixtures, tokens, instruments, contracts = set(), set(), set(), set(), []
    for event in document['events']:
        if not isinstance(event, dict) or set(event) != {'game', 'markets'} or not isinstance(event['markets'], list):
            raise Invalid('invalid_catalogue_event')
        game = _game(event['game'])
        if game.id in seen or game.fixture in fixtures or game != trusted_games.get(game.id):
            raise Invalid('ambiguous_or_changed_fixture_identity')
        seen.add(game.id)
        fixtures.add(game.fixture)
        scopes, families = set(), set()
        market_ids = set()
        for item in event['markets']:
            new = _market(game, item)
            core = new[0].scope[:-3] + (new[0].rules,)
            instrument = (new[0].protocol, new[0].instrument)
            if (core in scopes or new[0].market_id in market_ids
                    or instrument in instruments
                    or any((x.protocol, x.token) in tokens for x in new)):
                raise Invalid('ambiguous_contract_or_token_reuse')
            scopes.add(core)
            families.add((new[0].family, new[0].line, new[0].selection))
            instruments.add(instrument)
            market_ids.add(new[0].market_id)
            tokens.update((x.protocol, x.token) for x in new)
            contracts.extend(new)
        if families != SPECS:
            raise Invalid('missing_expected_catalogue_contract')
    if seen != set(trusted_games):
        raise Invalid('missing_expected_catalogue_fixture')
    return tuple(sorted(contracts, key=lambda item: item.id))


def _market(game, item):
    common = {'protocol', 'market_id', 'family', 'period', 'line', 'selection', 'rules', 'tokens'}
    if not isinstance(item, dict) or item.get('protocol') not in (CTF, V2):
        raise Invalid('unknown_market_protocol')
    protocol = item['protocol']
    fields = ('condition_id', 'exchange') if protocol == CTF else ('v2_market_ref', 'version')
    if (set(item) != common | set(fields) or not _matches(ID, item['market_id'])
            or item['period'] != 'regulation' or item['rules'] != REGULATION
            or not all(isinstance(item[key], str) for key in ('family', 'line', 'selection'))
            or (item['family'], item['line'], item['selection']) not in SPECS
            or not isinstance(item['tokens'], list) or len(item['tokens']) != 2):
        raise Invalid('market_period_line_or_settlement_unproven')
    instrument, version = item[fields[0]], item[fields[1]]
    if protocol == CTF and (not _matches(CONDITION, instrument) or version != 'ctf-exchange'):
        raise Invalid('invalid_ctf_condition_or_venue')
    if protocol == V2 and (not _matches(ID, instrument) or version != 'v2'):
        raise Invalid('invalid_v2_position_namespace')
    out = []
    for token in item['tokens']:
        names = {'side', 'asset_id'} if protocol == CTF else {'side', 'token_id', 'position_id'}
        if not isinstance(token, dict) or set(token) != names or token['side'] not in ('YES', 'NO'):
            raise Invalid('outcome_token_shape')
        asset = token['asset_id'] if protocol == CTF else token['token_id']
        position = token.get('position_id', token['side'])
        if not _matches(TOKEN, asset) or not _matches(ID, position):
            raise Invalid('invalid_token_or_position')
        out.append(Contract(game, protocol, item['market_id'], instrument, version, item['family'], item['period'],
                            item['line'], item['selection'], token['side'], asset, position, item['rules']))
    if {item.side for item in out} != {'YES', 'NO'} or len({item.token for item in out}) != 2 or len({item.position for item in out}) != 2:
        raise Invalid('outcome_token_collision')
    return out


def plan_slots(contracts, references):
    refs = [contract for contract in contracts if contract.game.id in references]
    included = [contract for contract in contracts if any(
        abs(stamp(contract.game.kickoff) - stamp(anchor.game.kickoff)) <= KICKOFF_GAP for anchor in refs)]
    for contract in included:
        start = stamp(contract.game.kickoff) - LEAD
        for tick in range(int(LEAD / CADENCE)):
            clock = events.iso(start + tick * CADENCE)
            yield Slot(contract.game.fixture, 'book', POLL, clock, clock, contract.id)


def _decimal(value):
    if not _matches(DECIMAL, value):
        raise Invalid('noncanonical_book_number')
    return Decimal(value)


def _levels(levels, tick, *, descending):
    if not isinstance(levels, list) or len(levels) > 500:
        raise Invalid('incomplete_or_unbounded_book_side')
    out = {}
    for row in levels:
        if not isinstance(row, dict) or set(row) != {'price', 'size'}:
            raise Invalid('invalid_book_level')
        price, size = _decimal(row['price']), _decimal(row['size'])
        if not 0 < price < 1 or size <= 0 or price % tick != 0:
            raise Invalid('invalid_price_size_or_tick')
        if price in out:
            raise Invalid('duplicate_price_level')
        out[price] = size
    return sorted(out.items(), reverse=descending)


def identity(contract):
    fields = dict(game_id=contract.game.id, fixture=contract.game.fixture, market_id=contract.market_id,
                  protocol=contract.protocol, period=contract.period, line=contract.line,
                  selection=contract.selection, side=contract.side, kickoff=contract.game.kickoff,
                  rules=contract.rules, venue_version=contract.venue_version)
    if contract.protocol == CTF:
        return dict(fields, condition_id=contract.instrument, asset_id=contract.token)
    return dict(fields, v2_market_ref=contract.instrument, token_id=contract.token, position_id=contract.position)


def read_book(contract, row):
    expected = identity(contract)
    body = row['payload']
    fields = set(expected) | {'tick_size', 'timestamp_utc', 'market_state', 'bids', 'asks'}
    if not isinstance(body, dict) or set(body) != fields:
        raise Invalid('incomplete_book_or_wrong_protocol')
    if any(body[key] != value for key, value in expected.items()):
        raise Invalid('book_identity_changed_or_conflicted')
    if body['timestamp_utc'] != row['source_utc'] or stamp(row['available_utc']) >= stamp(contract.game.kickoff):
        raise Invalid('book_time_or_kickoff_unproven')
    if body['market_state'] not in ('open', 'reopened'):
        raise Invalid('market_suspended' if body['market_state'] == 'suspended' else 'unknown_market_state')
    tick = _decimal(body['tick_size'])
    if not 0 < tick < 1:
        raise Invalid('invalid_tick')
    bids = _levels(body['bids'], tick, descending=True)
    asks = _levels(body['asks'], tick, descending=False)
    if not bids or not asks:
        raise Invalid('no_two_sided_liquidity')
    if bids[0][0] >= asks[0][0]:
        raise Invalid('crossed_or_locked_book')

    def side(levels):
        return [dict(price=str(price), shares=str(size)) for price, size in levels]

    return dict(price_unit=PRICE_UNIT, buy_best_ask=str(asks[0][0]), sell_best_bid=str(bids[0][0]),
                ask_depth_shares=str(sum(size for _, size in asks)), bid_depth_shares=str(sum(size for _, size in bids)),
                asks=side(asks), bids=side(bids), market_state=body['market_state'], tick_size=str(tick))
