"""Parser por regras das mensagens do bot ScanGoal. Sem LLM, sem rede."""
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


def competition_kind(league, teams=()):
    """Categoria simples da competição, só por palavras do nome: feminino, jovens, taça ou liga."""
    blob = ' '.join([league or ''] + list(teams or []))
    if re.search(r'\bWomen\b|Femenil|Feminin|Frauen', blob, re.I):
        return 'feminino'
    if re.search(r'\bU-?(1[5-9]|2[0-3])\b|Youth|Juniors', blob, re.I):
        return 'jovens'
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


def polymarket(m, h, lines, text):
    """Dinheiro na Polymarket: seleção, odd (a mais baixa quando vem um intervalo), valor, número de transações e quota."""
    m['type'] = 'POLYMARKET'
    s = re.match(r'^(.+?) @([\d.]+)(?:[–-][\d.]+)? · \$([\d.]+K?)', lines[2]) if len(lines) > 2 else None
    head = lines[1].rsplit(' · ', 1) if len(lines) > 1 else []
    teams = [t.strip() for t in head[0].split('—', 1)] if head and '—' in head[0] else None
    if not s or not teams or len(teams) != 2:
        return
    live = re.search(r"LIVE (\d+)(?:\+\d+)?' (\d+):(\d+)", h)
    tr, sh = re.search(r'🧾 (\d+) trades?', text), re.search(r'market share (\d+)%', text)
    sel = s[1].strip()
    if re.match(r'^(Over|Under) [\d.]+$', sel):
        market = 'Asian Total'
    elif sel in teams or sel.lower() == 'draw':
        market, sel = '1X2', 'X' if sel.lower() == 'draw' else '12'[teams.index(sel)]
    else:
        market = 'Polymarket'          # mercado que o resultado final não decide por regra: fica para a marca do bot
    m.update(phase='live' if live else 'pre', teams=teams, league=clean_league(head[1]) if len(head) > 1 else None, kickoff_utc=None if live else kickoff_utc(h),
             minute=int(live[1]) if live else None, score_at_signal=[int(live[2]), int(live[3])] if live else None,
             market=market, selection=sel, odds=float(s[2]), total=money(s[3]), n_bets=int(tr[1]) if tr else None, share=int(sh[1]) if sh else None, bet=True)


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
        return 1.0 if selection == ('1' if h > a else '2' if a > h else 'X') else -1.0
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
