"""Offline temporal episodes from the ScanGoal journal. Experimental schema v1."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
import re

from . import chronology, match_identity, opportunity_results, parser, store

SOURCES = {'POLYMARKET': 'Polymarket', 'STAKE_LOAD': 'Stake', 'BETFAIR': 'Betfair'}
NON_SIGNALS = {None, 'SERVICE', 'FULL_TIME_NOTE'}
MARGIN = timedelta(minutes=chronology.OBSERVATION_MARGIN_MIN)
PERIOD_PREFIX = re.compile(rf'^({parser.FIRST_HALF}|{parser.SECOND_HALF})\s*(?:-\s*)?(?=\w)', re.I)
TOTAL = re.compile(r'(over|under)\s+([0-9]+(?:[.,][0-9]+)?)', re.I)
PRICE = r'[0-9]+(?:\.[0-9]+)?'
PRICE_RANGE = re.compile(rf'@\s*({PRICE})(?:[–-]({PRICE}))?(?=\s|$)')
STAKE_QUOTE = re.compile(rf'📊 (.+?) → (.+?) @ +({PRICE})(?: *[–-] *({PRICE}))?')
STAKE_VOLUME = re.compile(r'💸+ 🟦 Stake(?: money load|: load growing ×[1-9][0-9]*) — \$(.+)')
LIVE_TOTAL = re.compile(rf'^💰\s+(?:(1H|2H)\s+)?(O|U|Over|Under)\s+({PRICE}):\s*({PRICE})\s*$', re.M)
VOLUME_SOURCES = {'Polymarket', 'Stake'}
HANDICAP = re.compile(r'(?:AH[12]|.+ )\(([+−-]?[0-9]+(?:\.[0-9]+)?)\)', re.I)


def iso(stamp):
    return chronology.iso(stamp, timespec='auto')


@dataclass(frozen=True)
class Version:
    evidence: dict
    available: datetime
    published: datetime
    text: str
    parsed: dict

    @property
    def message_id(self):
        return self.evidence['message_id']


def read_versions(path, as_of):
    """Order proven snapshots by availability, independently of journal order."""
    versions, exclusions = [], Counter()
    for line, row in store.message_rows(path):
        emitted = chronology.parse_utc(row.get('date_utc'))
        read = chronology.parse_utc(row.get('collected_at_utc'))
        edit = row.get('edit_date_utc')
        edited = chronology.parse_utc(edit)
        if read is not None and read > as_of:
            continue
        if emitted is None or read is None or (edit and (edited is None or edited < emitted)):
            exclusions['unproven_version_clock'] += 1
            continue
        published = edited or emitted
        exact = row.get('receipt_version') == 1
        if row.get('receipt_version') not in (None, 1):
            exclusions['unknown_receipt_type'] += 1
            continue
        available = read if exact else read + MARGIN
        if available > as_of or published > as_of:
            continue
        if published > available:
            exclusions['publication_after_receipt'] += 1
            continue
        body = row.get('text')
        message_id = row.get('message_id')
        if not isinstance(body, str) or type(message_id) is not int:
            exclusions['invalid_message_record'] += 1
            continue
        digest = hashlib.sha256(body.encode()).hexdigest()
        if row.get('sha256') != digest:
            exclusions['unverified_text_hash'] += 1
            continue
        record = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        evidence = dict(message_id=message_id, journal_line=line, text_sha256=digest,
                        record_sha256=hashlib.sha256(record.encode()).hexdigest(),
                        emitted_utc=iso(emitted), edit_utc=iso(edited), first_read_utc=iso(read),
                        available_utc=iso(available), receipt='completed_page' if exact else 'legacy_upper_bound')
        parsed = parser.parse(body)
        links = list(parser.URL.finditer(body))
        for button in row.get('buttons') or ():
            if isinstance(button, (list, tuple)) and len(button) == 2 and isinstance(button[1], str):
                links.extend(parser.URL.finditer(button[1]))
        ids = sorted({match[1] for match in links})
        parsed.update(explicit_match_ids=ids, match_id=ids[0] if len(ids) == 1 else None,
                      date_utc=iso(emitted), signal_seen_utc=iso(available), message_id=message_id)
        versions.append(Version(evidence, available, published, body, parsed))
    return sorted(versions, key=lambda v: (v.available, v.published, v.message_id, v.evidence['record_sha256'])), exclusions


def quote(version):
    """Preserve the advertised range rather than using the parser's low as a point."""
    p, body = version.parsed, version.text
    kind = p['type']
    if (kind or '').startswith(('OPEN_GAME', 'TOTALS_FORM')):
        live = LIVE_TOTAL.findall(body)
        if len(live) == 1 and p.get('score_at_signal') is not None:
            half, side, line, price = live[0]
            direction = 'Under' if side in ('Under', 'U') else 'Over'
            period = {'1H': 'first_half', '2H': 'second_half'}.get(half, 'regulation')
            market = {'1H': '1st Half - Asian Total', '2H': '2nd Half - Asian Total'}.get(half, 'Asian Total')
            return market, period, f'{direction} {line}', (price, price)
        if kind == 'OPEN_GAME_NO_ODDS' and 'heading for over' in body.splitlines()[0].casefold():
            return 'Asian Total', 'regulation', 'Over', None
    market, selection, period = p.get('market'), p.get('selection'), p.get('period')
    if market is None or selection is None:
        return market, period, selection, None
    lines = body.strip().splitlines()
    if kind == 'STAKE_LOAD':
        quotes = [row.strip() for row in lines if row.startswith('📊 ') and ' → ' in row]
        parsed = STAKE_QUOTE.fullmatch(quotes[0]) if len(quotes) == 1 else None
        if not parsed or parsed.group(1, 2) != (market, selection) or not p.get('bet'):
            return None, None, None, None
        low, high = parsed.group(3, 4)
        return market, period, selection, (low, high or low)
    quoted = lines[2] if len(lines) > 2 and kind in ('POLYMARKET', 'BETFAIR') else ''
    spans = PRICE_RANGE.findall(quoted)
    if len(spans) == 1 and p.get('bet'):
        low, high = spans[0]
        prices = low, high or low
    elif kind == 'WEAKENED_LINEUP' and p.get('bet'):
        prices = str(p['odds']), str(p['odds'])
    else:
        prices = None
    return market, period, selection, prices


def market_identity(market, period, scope, selection):
    """Use only explicit vocabulary equivalences, preserving source-specific scopes."""
    raw = match_identity.normalized(market)
    if not raw:
        return None, period
    base = raw
    prefix = PERIOD_PREFIX.match(base)
    if prefix:
        period = period or parser.market_tag(prefix[1])
        base = base[prefix.end():]
    suffix = re.fullmatch(r'.+ after ([0-9]+) minutes', base)
    if suffix:
        # A partial absolute clock cannot disambiguate a simultaneous half label.
        return base, f'first_{int(suffix[1])}_minutes' if not prefix and 0 < int(suffix[1]) <= 90 else None
    canonical = {'asian total': 'match_goals', 'total goals': 'match_goals',
                 'total corners': 'corners', '1x2': 'winner', 'match result': 'winner',
                 'half time result': 'winner', 'both teams to score': 'both_teams_to_score',
                 'asian handicap': 'asian_handicap'}.get(base, base)
    if raw == 'polymarket':
        canonical = scope or raw
        if canonical == 'handicap' and re.fullmatch(r'AH[12]\([+-][0-9.]+\)', selection or '', re.I):
            canonical = 'asian_handicap'
    if prefix and base == 'result':
        canonical = 'winner'
    if period is None and (canonical in {'match_goals', 'corners', 'winner', 'both_teams_to_score', 'asian_handicap',
                                        'total bookings', 'draw no bet', 'double chance'}
                           or re.fullmatch(r'.+ total(?: goals| corners| bookings)?', base)):
        period = 'regulation'
    return canonical, period


def event_of(version):
    p = version.parsed
    market, period, selection, prices = quote(version)
    market_key, period = market_identity(market, period, p.get('market_scope'), selection)
    total = TOTAL.fullmatch(selection or '')
    direction = total[1].lower() if total else selection.lower() if selection in ('Over', 'Under') else None
    handicap = HANDICAP.fullmatch(selection or '') if market_key == 'asian_handicap' else None
    line = parser.decimal_label(total[2]) if total else handicap[1].replace('−', '-') if handicap else None
    if (market_key == 'match_goals' and line is not None and Decimal(line) % 1 != Decimal('0.5')
            and (p['type'] == 'POLYMARKET' or match_identity.normalized(market) == 'total goals')):
        market_key = 'goal_total_unspecified_settlement'
    low, high = (Decimal(price) for price in prices) if prices else (None, None)
    odds = None if low is None or low <= 1 or high < low else [format(value.normalize(), 'f') for value in (low, high)]
    phase, kind = p.get('phase'), p['type'].removesuffix('_NO_ODDS')
    header = version.text.strip().splitlines()[0]
    multiplier = re.search(r'×([1-9][0-9]*)', header)
    volume = p.get('sel_volume' if kind == 'BETFAIR' else 'total')
    if kind == 'STAKE_LOAD':
        amount = STAKE_VOLUME.fullmatch(header)
        volume = parser.polymarket_amount(amount[1]) if amount else None
    if type(volume) not in (float, int) or not 0 <= volume < float('inf'):
        volume = None
    minute = p.get('minute')
    if minute is None and phase == 'live' and kind == 'STAKE_LOAD':
        stamp = re.search(r" · (?:LIVE )?([0-9]+)(?:\+[0-9]+)?['′](?:\b|\s|$)",
                          version.text.strip().splitlines()[1], re.I)
        if stamp:
            minute = int(stamp[1])
    return dict(version.evidence, match_id=p.get('match_id'), league=p.get('league'), teams=p.get('teams'),
                source=SOURCES.get(kind, 'ScanGoal'), signal_type=kind, kickoff_utc=p.get('kickoff_utc'),
                phase=phase, market=market, market_key=market_key, period=period, selection=selection,
                direction=direction, line=line, odds=odds, volume_usd=volume, share_pct=p.get('share'),
                signal_kind='reinforcement' if multiplier and int(multiplier[1]) > 1 else 'original',
                multiplier=int(multiplier[1]) if multiplier else None, minute=minute, score=p.get('score_at_signal'),
                context={key: p[key] for key in ('mkt_volume', 'n_bets', 'rhythm', 'bot_over_pct',
                                                'window', 'bot_prob', 'usual_prob') if p.get(key) is not None})


def reconstruct(versions, as_of, *, burst_size):
    """An edit can describe the history of a message but cannot create an alert."""
    groups = defaultdict(list)
    for version in versions:
        groups[version.message_id].append(version)
    found, excludes, revisions = [], [], []
    for rows in groups.values():
        first = rows[0]
        p = first.parsed
        def record(version, reason):
            return dict(version.evidence, reason=reason, match_id=version.parsed.get('match_id'),
                        signal_type=version.parsed['type'])

        for version in rows:
            if version.evidence['edit_utc'] is not None:
                revisions.append(record(version, 'edited_version'))
        if p['type'] in NON_SIGNALS:
            continue
        if p.get('ft') is not None or first.evidence['edit_utc'] is not None:
            excludes.append(record(first, 'first_read_with_ft' if p.get('ft') is not None else 'edited_at_first_read'))
            continue
        if any(v.available == first.available and v.published == first.published
               and (v.evidence['text_sha256'], v.parsed['explicit_match_ids']) !=
                   (first.evidence['text_sha256'], p['explicit_match_ids']) for v in rows[1:]):
            excludes.append(record(first, 'ambiguous_first_version'))
            continue
        event = event_of(first)
        if len(p['explicit_match_ids']) > 1:
            event['exclusion'] = 'ambiguous_match_links'
        elif not event['match_id']:
            event['exclusion'] = 'missing_match_id'
        elif not all(match_identity.components(event)[1:]):
            event['exclusion'] = 'incomplete_fixture_identity'
        found.append(event)

    betfair_lots = Counter((e['emitted_utc'], e['available_utc']) for e in found if e['source'] == 'Betfair')
    for e in found:
        if e['source'] == 'Betfair' and betfair_lots[e['emitted_utc'], e['available_utc']] >= burst_size:
            e.setdefault('exclusion', 'betfair_burst')

    # Only verified bodies with an explicit ID may establish fixture identity.
    signals = [dict(e, date_utc=e['emitted_utc'], signal_seen_utc=e['available_utc']) for e in found
               if not e.get('exclusion')]
    identified, bindings = match_identity.resolve(signals, as_of)
    identities = {p['message_id']: p for p in identified}
    kickoff_by_fixture = {}
    for event in found:
        identity = identities.get(event['message_id'], {})
        event['fixture_id'] = identity.get('canonical_match_id')
        if event['fixture_id'] is None:
            event.setdefault('exclusion', identity.get('match_identity_exclusion', 'unproven_fixture'))
        elif (ko := kickoff(event)) is not None and kickoff_by_fixture.setdefault(event['fixture_id'], ko) != ko:
            event.update(exclusion='conflicting_fixture_kickoff', fixture_id=None)
            binding = bindings[event['match_id']]
            if binding.collision is None or seen(event) < binding.collision:
                binding.collision = seen(event)

    # Keep duplicate provenance without creating a new exposure from the same body.
    originals = {}
    for event in found:
        if event.get('exclusion'):
            continue
        key = event['fixture_id'], event['source'], event['text_sha256']
        prior = originals.setdefault(key, event['message_id'])
        if prior != event['message_id']:
            event.update(exclusion='duplicate_body', duplicate_of=prior)
    return found, excludes, revisions, bindings, groups


def emitted(event):
    return chronology.parse_utc(event['emitted_utc'])


def seen(event):
    return chronology.parse_utc(event['available_utc'])


def kickoff(event):
    return chronology.parse_utc(event['kickoff_utc'])


def selection_key(event):
    """No equivalence by name fragments, across different providers or periods."""
    period, market, selection = event['period'], event['market_key'], event['selection']
    if not period or not market or not selection:
        return None
    if event['direction']:
        if event['line'] is None:
            return None
        selected = event['direction'], event['line']
    elif market == 'winner':
        selected = parser.selection_1x2(selection, event['teams'])
    else:
        selected = match_identity.normalized(selection)
    if selected is None:
        return None
    return (event['fixture_id'], event['source'], market, match_identity.normalized(event['market']),
            period, selected)


def movement(older, newer, missing):
    """A range can establish direction only if it is wholly above/below the other."""
    row = {'status': 'unproven', 'reason': missing}
    if older is None or newer is None:
        return row
    before_low, before_high = map(Decimal, older['odds'])
    after_low, after_high = map(Decimal, newer['odds'])
    if after_high < before_low:
        status = 'down'
    elif after_low > before_high:
        status = 'up'
    elif before_low == before_high == after_low == after_high:
        status = 'stable'
    else:
        status = 'unproven'
    return dict(status=status, reason='overlapping_price_ranges' if status == 'unproven' else None,
                from_message_id=older['message_id'], to_message_id=newer['message_id'],
                from_odds=older['odds'], to_odds=newer['odds'],
                known_utc=iso(max(seen(older), seen(newer))))


@dataclass(frozen=True)
class ExternalQuote:
    """Inert input contract for a future documented or authorized external quote.

    The provenance identifies the supplied observation, such as a manual receipt
    or an authorized provider record. No acquisition or execution is implied.
    """

    fixture_id: str
    source: str
    market: str
    period: str
    selection: str
    line: str | None
    decimal_odds: Decimal
    quoted_at: datetime
    observed_at: datetime
    provenance: str

    def __post_init__(self):
        if (any(not isinstance(value, str) or not value.strip() for value in
                (self.fixture_id, self.source, self.market, self.period, self.selection, self.provenance))
                or (self.line is not None and (not isinstance(self.line, str)
                                           or HANDICAP.fullmatch('AH1(' + self.line + ')') is None))
                or not isinstance(self.decimal_odds, Decimal) or not self.decimal_odds.is_finite()
                or self.decimal_odds <= 1
                or any(not isinstance(stamp, datetime) or stamp.utcoffset() is None
                       for stamp in (self.quoted_at, self.observed_at))
                or self.quoted_at > self.observed_at):
            raise ValueError('External quote requires exact identity, price, timestamps and provenance')
