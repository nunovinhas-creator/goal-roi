"""Parser por regras das mensagens do bot ScanGoal. Sem LLM, sem rede."""
import re
from datetime import datetime, timedelta, timezone

HEAD = re.compile(r'^(💷|💸|🔥|⏳|🎯|🟥)')
URL = re.compile(r'https://scangoal\.com/match/(\S+?)\?')
FT = re.compile(r'🏁 FT (\d+):(\d+) · (.+?) (✅|❌)')
KICKOFF = re.compile(r'(\d{1,2}) ([a-z]{3}) (\d{4}) (\d{2}):(\d{2})')
MONTHS = {m: i + 1 for i, m in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split())}
MSK = timezone(timedelta(hours=3))  # o bot mostra horas de Moscovo (UTC+3, sem hora de verão)
MIN_ODDS = 1.10


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
        m['bot_mark'] = ft[4] == '✅'
    try:
        if h.startswith('💷'):
            m.update(type='BETFAIR', phase='live' if 'LIVE' in h else 'pre', kickoff_utc=kickoff_utc(h))
            mult = re.search(r'×(\d+)', h)
            m['mult'] = int(mult[1]) if mult else 1
            s = re.search(r'^([12X]) · (.+?) @([\d.]+) · \$([\d.]+K?)', lines[2])
            m['teams'] = [t.strip() for t in lines[1].split(' · ')[0].split('—', 1)]
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
            m.update(type='STAKE_LOAD', phase=phase, teams=teams, score_at_signal=score,
                     kickoff_utc=kickoff_utc(lines[1]), market=s[1],
                     selection=s[2], odds=float(s[3]), total=money(re.search(r'\$([\d.]+K?)', h)[1]),
                     n_bets=int(b[1]) if b else None, bet=True)
        elif h.startswith('🔥') or h.startswith('🎯'):
            s = re.search(r'💰 O ([\d.]+): ([\d.]+)', text)
            mn = re.search(r"· (\d+)'", lines[1])
            a, b = re.search(r'(\d+)\s*$', lines[2]), re.search(r'(\d+)\s*$', lines[3])
            m['teams'] = [re.sub(r'\s+\d+\s*$', '', lines[2]).strip(), re.sub(r'\s+\d+\s*$', '', lines[3]).strip()]
            m.update(type='OPEN_GAME' if h.startswith('🔥') else 'TOTALS_FORM', phase='live', market='Asian Total',
                     selection='Over %s' % s[1], odds=float(s[2]), minute=int(mn[1]),
                     score_at_signal=[int(a[1]), int(b[1])], bet=True)
        elif h.startswith('⏳'):
            m['type'] = 'GOAL_BREWING'
        elif h.startswith('🟥'):
            m['type'] = 'WEAKENED_LINEUP'
    except (TypeError, IndexError, ValueError):
        m.update(error='fields_missing', bet=False)
    return m


def _side(token, teams):
    """'Draw' -> X; nome de equipa (prefixo/contido, sem ambiguidade) -> 1 ou 2; caso contrário None."""
    t = token.strip().lower()
    if t == 'draw':
        return 'X'
    hits = [k for k, name in zip('12', teams or []) if name and (name.lower().startswith(t) or t in name.lower())]
    return hits[0] if len(hits) == 1 else None


def settle(market, selection, ft, teams=None):
    """Lucro em unidades de (odd-1): 1 ganha, -1 perde, 0 devolve, ±0.5 meia. None = mercado não liquidável aqui."""
    h, a = ft
    if market == 'Double Chance':
        sides = [_side(tok, teams) for tok in selection.split(' or ')]
        if len(sides) != 2 or None in sides or sides[0] == sides[1]:
            return None
        return 1.0 if ('1' if h > a else '2' if a > h else 'X') in sides else -1.0
    if market == 'Asian Total':
        side, line = selection.split()[0], float(selection.split()[1])
        parts = [line] if (line * 2) % 1 == 0 else [line - .25, line + .25]
        sign = 1 if side == 'Over' else -1
        return sum((1 if (h + a - p) * sign > 0 else -1 if (h + a - p) * sign < 0 else 0) for p in parts) / len(parts)
    if market == '1X2':
        return 1.0 if selection == ('1' if h > a else '2' if a > h else 'X') else -1.0
    if market == 'Both Teams to Score':
        return 1.0 if (selection == 'Yes') == (h > 0 and a > 0) else -1.0
    return None
