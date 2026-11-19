"""Parser por regras das mensagens do bot ScanGoal. Sem LLM, sem rede."""
import math
import re
from datetime import datetime, timedelta, timezone

HEAD = re.compile(r'^(💷|💸|🔥|⏳|🎯|🟥|🏁 Full time|⭐|🎁|✅ Pro|Pro status)')
URL = re.compile(r'https://scangoal\.com/match/(\S+?)\?')
FT = re.compile(r'🏁 FT (\d+):(\d+)(?: · .+? (✅|❌))?')
SERVICE = re.compile(r'^(⭐|🎁|✅ Pro|Pro status)')
KICKOFF = re.compile(r'(\d{1,2}) ([a-z]{3}) (\d{4}) (\d{2}):(\d{2})')
MONTHS = {m: i + 1 for i, m in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split())}
MSK = timezone(timedelta(hours=3))  # o bot mostra horas de Moscovo (UTC+3, sem hora de verão)
MIN_ODDS = 1.10
GOAL = re.compile(r"⚽\s+(\d+)(?:\+\d+)?'\s+(\d+) - (\d+)")
HCAP = re.compile(r'📊 Handicap: (.+?) ([+−-]?[\d.]+) · ([\d.]+)\s+\|\s+(.+?) ([+−-]?[\d.]+) · ([\d.]+)')


def clean_league(text):
    """Nome da competição sem bandeiras/emoji."""
    return re.sub(r'^[^A-Za-z0-9]+', '', text or '').strip() or None


NATIONAL = re.compile(r'Nations League|International Match|Cup of Nations|Nations Cup|World Cup|Gold Cup|Asian Cup|Copa Am[eé]rica|European Championship|\bEURO\b', re.I)


def competition_kind(league, teams=()):
    """Categoria simples da competição, só por palavras do nome: feminino, jovens, seleções, particular, taça ou liga.

    Seleções: jogos entre seleções nacionais (Liga das Nações, qualificações, particulares entre seleções).
    Particular: amigáveis entre clubes. As duas regras de cima (feminino, jovens) têm prioridade.
    """
    blob = ' '.join([league or ''] + list(teams or []))
    if re.search(r'\bWomen\b|Femenil|Feminin|Frauen', blob, re.I):
        return 'feminino'
    if re.search(r'\bU-?(1[5-9]|2[0-3])\b|Youth|Juniors', blob, re.I):
        return 'jovens'
    if NATIONAL.search(league or ''):
        return 'seleções'
    if re.search(r'Friendl', league or '', re.I):
        return 'particular'
    if re.search(r'\bCup\b|Trophy|Copa\b|Coupe|Pokal|Taça|Shield', league or '', re.I):
        return 'taça'
    return 'liga'


def money(s):
    return float(s.replace('K', '')) * (1000 if 'K' in s else 1)


def kickoff_utc(text):
    m = KICKOFF.search(text)
    if not m or m[2] not in MONTHS:
        return None
    local = datetime(int(m[3]), MONTHS[m[2]], int(m[1]), int(m[4]), int(m[5]), tzinfo=MSK)
    return local.astimezone(timezone.utc).isoformat(timespec='minutes')


def split(text):
    """Texto colado com várias mensagens -> blocos (uma mensagem por bloco)."""
    cur = []
    for line in text.splitlines():
        if line.startswith('ScanGoal:'):
            continue
        if HEAD.match(line) and cur:
            yield '\n'.join(cur)
            cur = []
        cur.append(line)
    if cur:
        yield '\n'.join(cur)


def parse(text):
    """Uma mensagem -> dicionário. 'type' None = formato desconhecido; 'error' = campos em falta."""
    lines = [l for l in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return {'type': None}
    h = lines[0]
    url = URL.search(text)
    m = {'type': None, 'match_id': url[1] if url else None, 'bet': False}
    ft = FT.search(text)
    if ft:
        m['ft'] = [int(ft[1]), int(ft[2])]
        m['bot_mark'] = None if ft[3] is None else ft[3] == '✅'
        m['goals'] = [[int(a), int(b), int(c)] for a, b, c in GOAL.findall(text)]   # lista do bot: nem sempre completa
    try:
        if h.startswith('💷'):
            m.update(type='BETFAIR', phase='live' if 'LIVE' in h else 'pre', kickoff_utc=kickoff_utc(h))
            mult = re.search(r'×(\d+)', h)
            m['mult'] = int(mult[1]) if mult else 1
            s = re.search(r'^([12X]) · (.+?) @([\d.]+) · \$([\d.]+K?)', lines[2])
            m['teams'] = [t.strip() for t in lines[1].split(' · ')[0].split('—', 1)]
            m['league'] = clean_league(lines[1].split(' · ', 1)[1]) if ' · ' in lines[1] else None
            v = re.search(r'market share (\d+)% · whole 1X2 market \$([\d.]+K?)', text)
            m.update(market='1X2', selection=s[1], odds=float(s[3]), sel_volume=money(s[4]),
                     share=int(v[1]), mkt_volume=money(v[2]), bet=True)
        elif h.startswith('💸'):
            s = re.search(r'📊 (.+?) → (.+?) @ ([\d.]+)', text)
            b = re.search(r'🧾 (\d+) bets? · biggest \$([\d.]+K?)', text)
            if '—' in lines[2]:
                teams, phase, score = [t.strip() for t in lines[2].split('—', 1)], 'pre', None
            else:
                a, c = re.match(r'(.+?)\s+(\d+)\s*$', lines[2]), re.match(r'(.+?)\s+(\d+)\s*$', lines[3])
                teams, phase, score = [a[1].strip(), c[1].strip()], 'live', [int(a[2]), int(c[2])]
            m.update(type='STAKE_LOAD', phase=phase, teams=teams, score_at_signal=score, league=clean_league(lines[1].split(' · ')[0]),
                     kickoff_utc=kickoff_utc(lines[1]), market=s[1],
                     selection=s[2], odds=float(s[3]), total=money(re.search(r'\$([\d.]+K?)', h)[1]),
                     n_bets=int(b[1]) if b else None, bet=True)
        elif 'Polymarket' in h:
            polymarket(m, h, lines, text)
        elif h.startswith('🔥') or h.startswith('🎯'):
            s = re.search(r'💰 O ([\d.]+): ([\d.]+)', text)
            mn = re.search(r"· (\d+)'", lines[1])
            a, b = re.match(r'(.+?)\s+(\d+)\s*$', lines[2]), re.match(r'(.+?)\s+(\d+)\s*$', lines[3])
            kind = 'OPEN_GAME' if h.startswith('🔥') else 'TOTALS_FORM'
            m.update(phase='live', minute=int(mn[1]), league=clean_league(lines[1].rsplit(' · ', 1)[0]), teams=[a[1].strip(), b[1].strip()], score_at_signal=[int(a[2]), int(b[2])])
            extra = re.search(r'Rhythm (\d+)/100 · Over (\d+)%', text)
            if extra:
                m.update(rhythm=int(extra[1]), bot_over_pct=int(extra[2]))
            if s is None:  # sinal sem linha de odd: mede-se a taxa de acerto, não é aposta
                m['type'] = kind + '_NO_ODDS'
            else:
                m.update(type=kind, market='Asian Total', selection='Over %s' % s[1], odds=float(s[2]), bet=True)
        elif h.startswith('🏁 Full time'):
            m['type'] = 'FULL_TIME_NOTE'
            n = re.match(r'🏁 Full time: (.+?) (\d+):(\d+) (.+)$', h)
            if n:
                m.update(teams=[n[1].strip(), n[4].strip()], ft=[int(n[2]), int(n[3])])
        elif SERVICE.match(h):
            m['type'] = 'SERVICE'
        elif h.startswith('⏳'):
            m['type'] = 'GOAL_BREWING'
            goal_brewing(m, lines, text)
        elif h.startswith('🟥'):
            m['type'] = 'WEAKENED_LINEUP'
            weakened_lineup(m, lines, text)
    except (TypeError, IndexError, ValueError):
        m.update(error='fields_missing', bet=False)
    return m


def polymarket_amount(token):
    """Parse a complete USD amount. A single separator with three digits is ambiguous."""
    value = " ".join(token.upper().split())
    if re.fullmatch(r"[0-9]+(?:[.,][0-9]{1,2})?K", value):
        amount = float(value[:-1].replace(",", ".")) * 1000
    elif re.fullmatch(r"[0-9]+(?:[.,][0-9]{1,2})?", value):
        amount = float(value.replace(",", "."))
    elif re.fullmatch(r"[0-9]{1,3}( [0-9]{3})+(?:[.,][0-9]{1,2})?", value):
        amount = float(value.replace(" ", "").replace(",", "."))
    elif re.fullmatch(r"[0-9]{1,3}(,[0-9]{3})+(?:\.[0-9]{1,2})", value):
        amount = float(value.replace(",", ""))
    elif re.fullmatch(r"[0-9]{1,3}(\.[0-9]{3})+(?:,[0-9]{1,2})", value):
        amount = float(value.replace(".", "").replace(",", "."))
    elif re.fullmatch(r"[0-9]{1,3}(?:,[0-9]{3}){2,}|[0-9]{1,3}(?:\.[0-9]{3}){2,}", value):
        amount = float(value.replace(",", "").replace(".", ""))
    else:
        return None
    return amount if math.isfinite(amount) else None


FIRST_HALF = r"(?:1st[- ]half|first[- ]half|1h|h1|1ª parte|half[- ]time|ht)"
SECOND_HALF = r"(?:2nd[- ]half|second[- ]half|2h|h2|2ª parte)"
REGULATION = r"(?:90\s*(?:min(?:utes?)?)?|full[- ]time|full match|regulation(?: time)?|ft)"
MATCH_GOALS = r"(?:goals?|total goals?|goals? total|match total|match goals|asian total)"
CORNERS = r"(?:corners?|total corners?|corners? total)"
MATCH_WINNER = r"(?:1x2|winner|match winner|moneyline)"
LIVE_SCORE = re.compile(r"LIVE (\d+)(?:\+\d+)?' (\d+):(\d+)", re.I)
POLYMARKET_BRAND = re.compile(r"[^\w]*Polymarket(?:\s+×[1-9][0-9]*)?", re.I)
TOTAL_QUOTE = re.compile(
    rf"(?:(?P<prefix>{FIRST_HALF}|{SECOND_HALF}|{MATCH_GOALS}|{CORNERS})\s+)?"
    rf"(?P<side>Over|Under) (?P<line>[0-9]+(?:[.,][0-9]{{1,2}})?)"
    rf"(?:\s+(?P<suffix>{MATCH_GOALS}|{CORNERS}))?"
    rf"(?:\s*\((?P<period>{REGULATION}|{FIRST_HALF}|{SECOND_HALF})\))?", re.I,
)


def market_tag(value):
    for pattern, tag in ((FIRST_HALF, 'first_half'), (SECOND_HALF, 'second_half'), (REGULATION, 'regulation'),
                         (MATCH_GOALS, 'match_goals'), (CORNERS, 'corners'), (MATCH_WINNER, 'winner')):
        if re.fullmatch(pattern, value, re.I):
            return tag
    return None


def polymarket_header(header):
    brand, *fields = (part.strip() for part in header.split('·'))
    if not POLYMARKET_BRAND.fullmatch(brand):
        return None
    time, scopes, periods = None, set(), set()
    for part in fields:
        if KICKOFF.fullmatch(part) or LIVE_SCORE.fullmatch(part):
            if time is not None:
                return None
            time = part
            continue
        tag = market_tag(part)
        if tag is None:
            return None
        (scopes if tag in ('match_goals', 'corners', 'winner') else periods).add(tag)
    if time is None or len(scopes) > 1 or len(periods) > 1:
        return None
    return time, scopes, periods


def special_quote_scope(selection, teams):
    """Identify explicit formats that still use the existing bot settlement."""
    if re.fullmatch(r"AH[12]\([+-][0-9]+(?:\.[0-9]{1,2})?\)", selection, re.I):
        return 'handicap'
    if re.fullmatch(r"BTTS: (?:Yes|No)", selection, re.I):
        return 'both_teams_to_score'
    if selection in ('1', 'X', '2'):
        return 'winner'
    team = "|".join(re.escape(name) for name in teams)
    if re.fullmatch(rf"Handicap → Spread: (?:{team}) \([+-]?[0-9]+(?:\.[0-9]{{1,2}})?\)(?: \([^()]+\))?", selection, re.I):
        return 'handicap'
    return None


def polymarket(m, h, lines, text):
    """A quote belongs to a positively recognised period and market scope."""
    m.update(type='POLYMARKET', market='Polymarket')
    header = polymarket_header(h)
    quote = re.fullmatch(r"(.+?) @([0-9]+(?:\.[0-9]+)?)(?:[–-]([0-9]+(?:\.[0-9]+)?))? · \$(.+)",
                         lines[2].strip()) if len(lines) > 2 else None
    head = lines[1].rsplit(' · ', 1) if len(lines) > 1 else []
    teams = [t.strip() for t in head[0].split('—', 1)] if head and '—' in head[0] else None
    if not header or not quote or not teams or not all(teams):
        return
    raw_sel, low, high, token = quote.groups()
    amount = polymarket_amount(token)
    low, high = float(low), float(high) if high is not None else None
    if (amount is None or not math.isfinite(low)
            or (high is not None and (not math.isfinite(high) or high < low))):
        return

    time, scopes, periods = header
    live, kickoff = LIVE_SCORE.fullmatch(time), kickoff_utc(time)
    if live is None and kickoff is None:
        return
    total = TOTAL_QUOTE.fullmatch(raw_sel)
    if total:
        for value in (total['prefix'], total['suffix'], total['period']):
            if value:
                tag = market_tag(value)
                (scopes if tag in ('match_goals', 'corners') else periods).add(tag)
        if 'winner' in scopes or len(scopes) > 1 or len(periods) > 1:
            return
        scope, period = next(iter(scopes), 'match_goals'), next(iter(periods), 'regulation')
        market = ('Total Corners' if scope == 'corners' else
                  {'regulation': 'Asian Total', 'first_half': '1st Half - Asian Total',
                   'second_half': '2nd Half - Asian Total'}[period])
        sel = total['side'].title() + ' ' + total['line'].replace(',', '.')
    elif (raw_sel in teams or raw_sel.lower() == 'draw') and scopes <= {'winner'} and periods <= {'regulation'}:
        scope, period, market = 'winner', 'regulation', '1X2'
        sel = 'X' if raw_sel.lower() == 'draw' else '12'[teams.index(raw_sel)]
    else:
        scope = special_quote_scope(raw_sel, teams)
        if scope is None or (scopes and scopes != {scope}) or periods - {'regulation'}:
            return
        period, market, sel = 'regulation', 'Polymarket', raw_sel

    tr = re.search(r'🧾 (\d+) trades?', text)
    sh = re.search(r'market share ([0-9]+(?:[.,][0-9]{1,2})?)%', text)
    share = float(sh[1].replace(",", ".")) if sh else None
    if share is not None and share > 100:
        share = None
    m.update(phase='live' if live else 'pre', period=period, market_scope=scope, teams=teams,
             league=clean_league(head[1]) if len(head) > 1 else None, kickoff_utc=kickoff,
             minute=int(live[1]) if live else None,
             score_at_signal=[int(live[2]), int(live[3])] if live else None,
             market=market, selection=sel, odds=low, total=amount,
             n_bets=int(tr[1]) if tr else None, share=share, bet=True)


def goal_brewing(m, lines, text):
    """Golo a caminho: janela em minutos, probabilidade indicada e habitual. Mede-se com os minutos dos golos (fonte externa)."""
    w, pr = re.search(r'next (\d+)′', lines[0]), re.search(r'Goal in \d+′: (\d+)% · usual (\d+)%', text)
    mn = re.search(r"· (\d+)'", lines[1]) if len(lines) > 1 else None
    a = re.match(r'(.+?)\s+(\d+)\s*$', lines[2]) if len(lines) > 3 else None
    b = re.match(r'(.+?)\s+(\d+)\s*$', lines[3]) if len(lines) > 3 else None
    if w and pr and mn and a and b:
        m.update(phase='live', window=int(w[1]), bot_prob=int(pr[1]), usual_prob=int(pr[2]), minute=int(mn[1]),
                 league=clean_league(lines[1].rsplit(' · ', 1)[0]), teams=[a[1].strip(), b[1].strip()], score_at_signal=[int(a[2]), int(b[2])])


def weakened_lineup(m, lines, text):
    """Alinhamento enfraquecido: aposta contra a equipa enfraquecida, ao handicap e odd da mensagem. Só se houver exatamente uma."""
    teams = [t.strip() for t in lines[2].split('—', 1)] if len(lines) > 2 and '—' in lines[2] else None
    weak = re.findall(r'^🔻 (.+?):', text, re.M)
    hc = HCAP.search(text)
    lead = re.search(r'\(in (\d+) min\)', lines[1]) if len(lines) > 1 else None
    m.update(teams=teams, phase='pre', league=clean_league(lines[1].split(' · ')[0]) if len(lines) > 1 else None,
             lead_from_text=int(lead[1]) if lead else None)
    if not teams or len(teams) != 2 or len(weak) != 1 or weak[0] not in teams or not hc:
        return
    sides = {hc[1].strip(): (hc[2], hc[3]), hc[4].strip(): (hc[5], hc[6])}
    other = teams[1 - teams.index(weak[0])]
    if other not in sides:
        return
    line = float(sides[other][0].replace('−', '-'))
    m.update(market='Asian Handicap', selection='%s (%+g)' % (other, line), odds=float(sides[other][1]), weakened=weak[0], bet=True)


def _side(token, teams):
    """'Draw' -> X; nome de equipa (prefixo/contido, sem ambiguidade) -> 1 ou 2; caso contrário None."""
    t = token.strip().lower()
    if t == 'draw':
        return 'X'
    exact = [k for k, name in zip('12', teams or []) if name and name.lower() == t]
    if len(exact) == 1:
        return exact[0]
    hits = [k for k, name in zip('12', teams or []) if name and (name.lower().startswith(t) or t in name.lower())]
    return hits[0] if len(hits) == 1 else None


def selection_1x2(selection, teams=None):
    """Canonical 1/X/2 or an exact unique full team name. Never guess a side."""
    if not isinstance(selection, str):
        return None
    token = selection.strip()
    if token.upper() in ('1', 'X', '2'):
        return token.upper()
    if token.casefold() == 'draw':
        return 'X'
    if not isinstance(teams, (list, tuple)) or len(teams) != 2 or any(not isinstance(t, str) or not t.strip() for t in teams):
        return None
    hits = [str(i + 1) for i, team in enumerate(teams) if team.strip().casefold() == token.casefold()]
    return hits[0] if len(hits) == 1 else None


def _quarter(diff, line):
    """Resultado de uma linha asiática (inteira, meia ou de quarto) para uma margem 'diff' a favor da seleção."""
    parts = [line] if (line * 2) % 1 == 0 else [line - .25, line + .25]
    return sum((1 if diff + p > 0 else -1 if diff + p < 0 else 0) for p in parts) / len(parts)


def settle(market, selection, ft, teams=None):
    """Lucro em unidades de (odd-1): 1 ganha, -1 perde, 0 devolve, ±0.5 meia. None = mercado não liquidável pelo resultado final."""
    h, a = ft
    mk = market.strip().lower()
    if mk == 'asian total':
        side, line = selection.split()[0], float(selection.split()[1])
        return _quarter((h + a) if side == 'Over' else -(h + a), -line if side == 'Over' else line)
    if mk == '1x2':
        side = selection_1x2(selection, teams)
        if side is None:
            return None
        return 1.0 if side == ('1' if h > a else '2' if a > h else 'X') else -1.0
    if mk == 'both teams to score':
        return 1.0 if (selection.strip().lower() == 'yes') == (h > 0 and a > 0) else -1.0
    if mk == 'double chance':
        sides = [_side(tok, teams) for tok in selection.split(' or ')]
        if len(sides) != 2 or None in sides or sides[0] == sides[1]:
            return None
        return 1.0 if ('1' if h > a else '2' if a > h else 'X') in sides else -1.0
    if mk == 'asian handicap':
        hc = re.match(r'^(.+?) \(([+-]?[\d.]+)\)$', selection.strip())
        side = _side(hc[1], teams) if hc else None
        if side not in ('1', '2'):
            return None
        return _quarter((h - a) if side == '1' else (a - h), float(hc[2]))
    return None
