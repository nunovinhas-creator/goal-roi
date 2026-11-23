"""P1 — observação SOMBRA, só leitura, do livro Polymarket para sinais Over 2,5 pré-jogo.

O que faz: para um sinal do bot (POLYMARKET, pré-jogo, Asian Total Over 2.5, jogo inteiro, 90 minutos), encontra o
contrato Polymarket regulamentar correspondente, verifica a identidade do encontro e as regras de liquidação, lê o
livro público do token "Over" e calcula, a partir dos níveis "ask", o preço médio e as odds decimais líquidas da taxa
de taker para stakes fixas.

O que NUNCA faz: enviar ordens, usar credenciais ou cookies, pedir a hosts fora de ALLOWED_HOSTS, usar métodos que
não sejam GET, escrever em data/, mexer em alertas, estratégias congeladas, certificação ou histórico.

Separação entre dados atuais e históricos: cada observação tem a hora de receção. Uma cotação só conta como evidência
para um sinal (`evidence_for_signal=True`) quando foi recebida depois de o sinal ter sido visto e no máximo
MAX_DECISION_LAG depois, antes do início. Uma cotação lida mais tarde é marcada `current_only` e não prova nada sobre
a exequibilidade desse sinal no passado.
"""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_UP, Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from . import chronology
from .results_bsd import norm_team

SCHEMA = 'goal-roi/polymarket-shadow-observation/v1'
GAMMA = 'https://gamma-api.polymarket.com'
CLOB = 'https://clob.polymarket.com'
ALLOWED_HOSTS = frozenset({'gamma-api.polymarket.com', 'clob.polymarket.com'})
# Cabeçalhos fixos: genéricos, sem identificação pessoal e sem qualquer credencial. O Cloudflare recusa (erro 1010)
# o User-Agent por omissão do urllib, por isso é explícito.
REQUEST_HEADERS = {'User-Agent': 'goal-roi-shadow/0.1', 'Accept': 'application/json'}
MAX_BODY = 5_000_000
TIMEOUT = 20

LINE = Decimal('2.5')
STAKES_USDC = (Decimal('10'), Decimal('50'), Decimal('100'), Decimal('500'))
CLOCK_SKEW = timedelta(seconds=2)
MAX_DECISION_LAG = timedelta(seconds=120)
QUIET_BOOK = timedelta(minutes=5)
FEE_DECIMALS = Decimal('0.00001')
RULE_OVER = '3 or more goals'
RULE_REGULATION = 'first 90 minutes of regular play plus stoppage time'
RULE_VOID = 'resolve 50-50'
CONDITION_ID = re.compile(r'^0x[0-9a-f]{64}$')
TOKEN_ID = re.compile(r'^[0-9]{10,80}$')


class Refusal(Exception):
    """Falha fechada: a observação não é válida e o motivo fica registado."""

    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------------------------- transporte (só GET)

def guard_url(url):
    parts = urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname not in ALLOWED_HOSTS or parts.username or parts.password
            or parts.port not in (None, 443) or parts.fragment):
        raise Refusal('host_not_allowed', url)
    return url


def utc_now():
    return datetime.now(timezone.utc)


def http_get(url, *, opener=urlopen, clock=utc_now):
    """GET público. Devolve corpo, hash e as duas horas locais (pedido e receção)."""
    guard_url(url)
    request = Request(url, headers=dict(REQUEST_HEADERS), method='GET')
    requested = clock()
    try:
        with opener(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_BODY + 1)
            status = getattr(response, 'status', 200)
            headers = {k.lower(): v for k, v in response.headers.items()
                       if k.lower() in ('date', 'cf-cache-status', 'age', 'content-type')}
    except HTTPError as exc:
        raise Refusal('http_error', exc.code) from None
    except (URLError, TimeoutError, OSError) as exc:
        raise Refusal('network_error', type(exc).__name__) from None
    received = clock()
    if len(body) > MAX_BODY:
        raise Refusal('body_too_large', url)
    if status != 200:
        raise Refusal('http_error', status)
    try:
        payload = json.loads(body)
    except ValueError:
        raise Refusal('invalid_json', url) from None
    return {'url': url, 'payload': payload, 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body),
            'requested_utc': requested, 'received_utc': received, 'headers': headers}


# --------------------------------------------------------------------------------------------- valores estritos

def dec(value, field):
    if isinstance(value, bool) or value is None:
        raise Refusal('invalid_number', field)
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise Refusal('invalid_number', field) from None
    if not out.is_finite():
        raise Refusal('invalid_number', field)
    return out


def utc(value, field):
    parsed = chronology.parse_utc(value)
    if parsed is None:
        raise Refusal('invalid_time', field)
    return parsed


def json_list(value, field):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise Refusal('invalid_list', field) from None
    if not isinstance(value, list):
        raise Refusal('invalid_list', field)
    return value


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z') if value else None


# --------------------------------------------------------------------------------------------- âmbito do sinal

def in_scope(signal):
    return (signal.get('type') == 'POLYMARKET' and signal.get('phase') == 'pre'
            and signal.get('market') == 'Asian Total' and signal.get('selection') == 'Over 2.5'
            and signal.get('market_scope') == 'match_goals' and signal.get('period') == 'regulation')


# --------------------------------------------------------------------------------------------- identidade do encontro

def team_keys(team):
    keys = {norm_team(team.get(k)) for k in ('name', 'alias') if isinstance(team.get(k), str)}
    keys.discard('')
    return keys


def bot_keys(name, aliases):
    keys = {norm_team(name)} | {norm_team(a) for a in aliases.get(name, ()) if isinstance(a, str)}
    keys.discard('')
    return keys


def event_matches(event, signal, aliases):
    """Igualdade exata depois de normalizar (ou alias revisto à mão), casa e fora na ordem certa e hora exata."""
    teams = event.get('teams')
    if not isinstance(teams, list) or len(teams) != 2:
        return False
    by_order = {t.get('ordering'): t for t in teams if isinstance(t, dict)}
    if set(by_order) != {'home', 'away'}:
        return False
    names = signal.get('teams') or []
    if len(names) != 2:
        return False
    kickoff = chronology.parse_utc(signal.get('kickoff_utc'))
    start = chronology.parse_utc(event.get('startTime'))
    return (kickoff is not None and start == kickoff
            and bool(bot_keys(names[0], aliases) & team_keys(by_order['home']))
            and bool(bot_keys(names[1], aliases) & team_keys(by_order['away'])))


def main_event(search_payload, signal, aliases):
    if not isinstance(search_payload, dict):
        raise Refusal('search_invalid')
    events = search_payload.get('events')
    if events is None:                       # a pesquisa devolve null quando não há eventos
        events = []
    if not isinstance(events, list):
        raise Refusal('search_invalid')
    found = [e for e in events if isinstance(e, dict) and not e.get('parentEventId')
             and event_matches(e, signal, aliases)]
    if not found:
        raise Refusal('identity_not_found')
    if len(found) > 1:
        raise Refusal('identity_ambiguous', [e.get('slug') for e in found])
    return found[0]


def verify_child(child, parent, signal, aliases):
    if not isinstance(child, dict) or str(child.get('parentEventId')) != str(parent.get('id')):
        raise Refusal('child_event_not_linked')
    if child.get('gameId') != parent.get('gameId') or not event_matches(child, signal, aliases):
        raise Refusal('child_event_identity_mismatch')
    return child


# --------------------------------------------------------------------------------------------- contrato e regras

def select_contract(events, signal):
    found = []
    for event in events:
        for market in event.get('markets') or []:
            if not isinstance(market, dict) or market.get('sportsMarketType') != 'totals':
                continue
            try:
                line = dec(market.get('line'), 'line')
            except Refusal:
                continue
            if line == LINE:
                found.append((event, market))
    if not found:
        raise Refusal('contract_not_found')
    if len(found) > 1:
        raise Refusal('contract_ambiguous', [m.get('slug') for _, m in found])
    event, market = found[0]
    return verify_contract(market, signal)


def verify_contract(market, signal):
    condition = market.get('conditionId')
    if not isinstance(condition, str) or not CONDITION_ID.match(condition):
        raise Refusal('condition_id_invalid')
    outcomes = json_list(market.get('outcomes'), 'outcomes')
    tokens = json_list(market.get('clobTokenIds'), 'clobTokenIds')
    if sorted(outcomes) != ['Over', 'Under'] or len(tokens) != 2 or len(set(tokens)) != 2:
        raise Refusal('outcomes_invalid')
    if not all(isinstance(t, str) and TOKEN_ID.match(t) for t in tokens):
        raise Refusal('token_invalid')
    rules = market.get('description')
    if not isinstance(rules, str) or RULE_OVER not in rules or RULE_REGULATION not in rules:
        raise Refusal('rules_not_regulation_over25')
    if market.get('negRisk') is not False:
        raise Refusal('neg_risk_market')
    kickoff = utc(signal.get('kickoff_utc'), 'signal.kickoff_utc')
    if utc(market.get('gameStartTime'), 'gameStartTime') != kickoff:
        raise Refusal('kickoff_mismatch')
    over = outcomes.index('Over')
    return {
        'market_id': str(market.get('id')), 'slug': market.get('slug'), 'question': market.get('question'),
        'condition_id': condition, 'question_id': market.get('questionID'),
        'token_over': tokens[over], 'token_under': tokens[1 - over],
        'rules_sha256': hashlib.sha256(rules.encode('utf-8')).hexdigest(),
        'rules_regulation_90_plus_stoppage': True, 'rules_void_50_50': RULE_VOID in rules,
        'resolution_source': market.get('resolutionSource'),
        'restricted': market.get('restricted'),
        'available': {k: market.get(k) for k in ('active', 'closed', 'acceptingOrders', 'enableOrderBook')},
        'gamma_cached_quote_not_used': {k: market.get(k) for k in ('bestBid', 'bestAsk', 'lastTradePrice')},
    }


def verify_clob_market(info, contract, signal):
    """Parâmetros do CLOB: mesmo conditionId, tokens/outcomes, início, aceita ordens e calendário de taxas."""
    if not isinstance(info, dict) or info.get('c') != contract['condition_id']:
        raise Refusal('clob_condition_mismatch')
    pairs = {t.get('t'): t.get('o') for t in info.get('t') or [] if isinstance(t, dict)}
    if pairs != {contract['token_over']: 'Over', contract['token_under']: 'Under'}:
        raise Refusal('clob_tokens_mismatch')
    if utc(info.get('gst'), 'gst') != utc(signal.get('kickoff_utc'), 'signal.kickoff_utc'):
        raise Refusal('clob_kickoff_mismatch')
    a = contract['available']
    if not (a.get('active') is True and a.get('closed') is False and a.get('enableOrderBook') is True
            and a.get('acceptingOrders') is True and info.get('ao') is True):
        raise Refusal('market_not_accepting_orders')
    fee = info.get('fd')
    schedule = None
    if isinstance(fee, dict) and fee.get('to') is True:
        rate, exponent = dec(fee.get('r'), 'fd.r'), dec(fee.get('e'), 'fd.e')
        if Decimal('0') <= rate < Decimal('1') and exponent > 0:
            schedule = {'rate': rate, 'exponent': exponent}
    tick = dec(info.get('mts'), 'mts')
    min_size = dec(info.get('mos'), 'mos')
    if not (Decimal('0') < tick < Decimal('1')) or min_size <= 0:
        raise Refusal('clob_params_invalid')
    return {'fee': schedule, 'tick': tick, 'min_order_size': min_size,
            'taker_delay_seconds': info.get('sd'), 'clear_book_on_start': info.get('cbos')}


# --------------------------------------------------------------------------------------------- livro

def parse_levels(levels, tick, field):
    if not isinstance(levels, list):
        raise Refusal('book_invalid', field)
    out = {}
    for level in levels:
        if not isinstance(level, dict):
            raise Refusal('book_invalid', field)
        price, size = dec(level.get('price'), field + '.price'), dec(level.get('size'), field + '.size')
        if not (Decimal('0') < price < Decimal('1')) or size <= 0:
            raise Refusal('book_level_out_of_range', field)
        if price % tick != 0:
            raise Refusal('book_off_tick', field)
        if price in out:
            raise Refusal('book_duplicate_level', field)
        out[price] = size
    return out


def read_book(book, *, token_id, condition_id, tick):
    if not isinstance(book, dict):
        raise Refusal('book_invalid')
    if book.get('asset_id') != token_id or book.get('market') != condition_id:
        raise Refusal('book_identity_mismatch')
    stamp = book.get('timestamp')
    if not isinstance(stamp, str) or not stamp.isdigit():
        raise Refusal('book_timestamp_invalid')
    source = datetime.fromtimestamp(int(stamp) / 1000, timezone.utc)
    if book.get('tick_size') is not None and dec(book.get('tick_size'), 'tick_size') != tick:
        raise Refusal('book_tick_mismatch')
    asks = sorted(parse_levels(book.get('asks'), tick, 'asks').items())
    bids = sorted(parse_levels(book.get('bids'), tick, 'bids').items(), reverse=True)
    if not asks:
        raise Refusal('book_no_asks')
    if bids and bids[0][0] >= asks[0][0]:
        raise Refusal('book_crossed_or_locked')
    return {'source_utc': source, 'hash': book.get('hash'), 'asks': asks, 'bids': bids}


def level_fee(shares, price, schedule):
    return shares * schedule['rate'] * (price * (1 - price)) ** schedule['exponent']


def fill(asks, stake, schedule, min_size):
    """Compra a mercado de `stake` USDC no lado ask, nível a nível. Taxa de taker por nível; arredondada para cima."""
    remaining, shares, fee, levels, worst = stake, Decimal('0'), Decimal('0'), 0, None
    for price, size in asks:
        if remaining <= 0:
            break
        spend = min(remaining, price * size)
        got = spend / price
        shares += got
        remaining -= spend
        levels += 1
        worst = price
        if schedule is not None:
            fee += level_fee(got, price, schedule)
    out = {'stake_usdc': str(stake), 'levels_used': levels, 'worst_price': str(worst) if worst else None}
    if remaining > 0:
        return dict(out, filled=False, reason='insufficient_depth', depth_usdc=str(stake - remaining))
    if shares < min_size:
        return dict(out, filled=False, reason='below_min_order_size')
    vwap = stake / shares
    result = dict(out, filled=True, shares=str(shares.quantize(Decimal('0.0001'))),
                  vwap=str(vwap.quantize(Decimal('0.000001'))),
                  gross_decimal_odds=str((shares / stake).quantize(Decimal('0.0001'))))
    if schedule is None:
        return dict(result, fee_usdc=None, net_decimal_odds=None, reason='fee_schedule_unknown')
    fee = fee.quantize(FEE_DECIMALS, rounding=ROUND_UP)
    return dict(result, fee_usdc=str(fee), net_decimal_odds=str((shares / (stake + fee)).quantize(Decimal('0.0001'))))


def mirror_consistent(over, under):
    """No CLOB binário o ask de Over deve ser 1 − melhor bid de Under (e vice-versa)."""
    if not under['bids'] or not over['bids'] or not under['asks']:
        return None
    return over['asks'][0][0] == 1 - under['bids'][0][0] and over['bids'][0][0] == 1 - under['asks'][0][0]


# --------------------------------------------------------------------------------------------- tempo e evidência

def classify_timing(signal, received, source, kickoff):
    if source > received + CLOCK_SKEW:
        raise Refusal('source_timestamp_in_future')
    if received >= kickoff:
        raise Refusal('after_kickoff')
    sent = chronology.parse_utc(signal.get('date_utc'))
    seen = chronology.parse_utc(signal.get('collected_at_utc'))
    if sent is None or seen is None or seen < sent:
        relation = 'signal_time_unproven'
    elif received < seen:
        relation = 'before_signal_seen'
    elif received - seen <= MAX_DECISION_LAG:
        relation = 'contemporaneous'
    else:
        relation = 'current_only'
    return {
        'relation_to_signal': relation,
        'evidence_for_signal': relation == 'contemporaneous',
        'signal_sent_utc': iso(sent), 'signal_seen_utc': iso(seen),
        'lag_after_signal_seen_seconds': round((received - seen).total_seconds(), 3) if seen else None,
        'source_age_seconds': round((received - source).total_seconds(), 3),
        'quiet_book': received - source > QUIET_BOOK,
        'seconds_to_kickoff': round((kickoff - received).total_seconds(), 3),
    }


# --------------------------------------------------------------------------------------------- orquestração

def receipt(response):
    return {'url': response['url'], 'sha256': response['sha256'], 'bytes': response['bytes'],
            'requested_utc': iso(response['requested_utc']), 'received_utc': iso(response['received_utc']),
            'headers': response['headers']}


def locate(signal, fetch, aliases, statuses=('active',)):
    """Evento principal → evento "More Markets" ligado → contrato O/U 2.5 regulamentar. Sem preços."""
    if not in_scope(signal):
        raise Refusal('signal_not_in_scope')
    utc(signal.get('kickoff_utc'), 'signal.kickoff_utc')
    teams = signal.get('teams') or []
    parent = None
    for status in statuses:
        query = urlencode({'q': ' '.join(teams), 'limit_per_type': 10, 'events_status': status})
        try:
            parent = main_event(fetch(GAMMA + '/public-search?' + query)['payload'], signal, aliases)
            break
        except Refusal as exc:
            if exc.code != 'identity_not_found' or status == statuses[-1]:
                raise
    child = fetch(GAMMA + '/events/slug/' + quote(str(parent.get('slug')) + '-more-markets', safe=''))['payload']
    verify_child(child, parent, signal, aliases)
    contract = select_contract([parent, child], signal)
    event = {'parent_slug': parent.get('slug'), 'child_slug': child.get('slug'),
             'game_id': parent.get('gameId'), 'start_utc': parent.get('startTime'),
             'teams': [t.get('name') for t in sorted(parent['teams'], key=lambda t: t.get('ordering') != 'home')]}
    return event, contract


def coverage(signal, *, get=http_get, aliases=None):
    """Só identidade e contrato (eventos ativos ou fechados). Nunca lê livros nem preços."""
    row = {'match_id': signal.get('match_id'), 'kickoff_utc': signal.get('kickoff_utc'), 'league': signal.get('league')}
    try:
        event, contract = locate(signal, lambda url: get(url), aliases or {}, statuses=('active', 'closed'))
        row.update(status='contract_verified', slug=contract['slug'], condition_id=contract['condition_id'],
                   game_id=event['game_id'], closed=contract['available'].get('closed'))
    except Refusal as exc:
        row.update(status=exc.code if exc.code not in ('http_error', 'network_error') else 'unavailable',
                   detail=exc.detail)
    return row


def observe(signal, *, get=http_get, aliases=None, stakes=STAKES_USDC):
    """Uma observação completa. Nunca lança: devolve status observed / refused / unavailable com o motivo."""
    aliases = aliases or {}
    record = {'schema': SCHEMA, 'mode': 'shadow_read_only', 'orders_sent': 0, 'credentials_used': False,
              'match_id': signal.get('match_id'), 'signal_message_id': signal.get('message_id'),
              'signal_odds': signal.get('odds'), 'kickoff_utc': signal.get('kickoff_utc'), 'requests': []}

    def fetch(url):
        response = get(url)
        record['requests'].append(receipt(response))
        return response

    try:
        record['event'], contract = locate(signal, fetch, aliases)
        record['contract'] = contract
        kickoff = utc(signal.get('kickoff_utc'), 'signal.kickoff_utc')
        clob = verify_clob_market(fetch(CLOB + '/clob-markets/' + contract['condition_id'])['payload'], contract, signal)
        record['clob'] = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in clob.items() if k != 'fee'}
        record['clob']['fee'] = ({k: str(v) for k, v in clob['fee'].items()} if clob['fee'] else None)
        over_response = fetch(CLOB + '/book?' + urlencode({'token_id': contract['token_over']}))
        if str(over_response['headers'].get('cf-cache-status', '')).upper() == 'HIT':
            raise Refusal('book_served_from_cache')
        over = read_book(over_response['payload'], token_id=contract['token_over'],
                         condition_id=contract['condition_id'], tick=clob['tick'])
        under_response = fetch(CLOB + '/book?' + urlencode({'token_id': contract['token_under']}))
        under = read_book(under_response['payload'], token_id=contract['token_under'],
                          condition_id=contract['condition_id'], tick=clob['tick'])
        received = over_response['received_utc']
        record['timing'] = classify_timing(signal, received, over['source_utc'], kickoff)
        best_ask, best_bid = over['asks'][0], (over['bids'][0] if over['bids'] else None)
        record['quote'] = {
            'token': 'Over', 'book_hash': over['hash'], 'source_utc': iso(over['source_utc']),
            'received_utc': iso(received),
            'best_ask': str(best_ask[0]), 'best_ask_size': str(best_ask[1]),
            'best_bid': str(best_bid[0]) if best_bid else None,
            'ask_depth_usdc_top5': str(sum(p * s for p, s in over['asks'][:5]).quantize(Decimal('0.01'))),
            'mirror_consistent': mirror_consistent(over, under),
            'fills': [fill(over['asks'], Decimal(stake), clob['fee'], clob['min_order_size']) for stake in stakes],
        }
        record['status'] = 'observed'
    except Refusal as exc:
        record.update(status='unavailable' if exc.code in ('http_error', 'network_error') else 'refused',
                      reason=exc.code, detail=exc.detail)
    return record
