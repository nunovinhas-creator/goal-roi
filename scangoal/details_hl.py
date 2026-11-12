"""Minutos dos golos pela Highlightly (GET /matches/{id}), para jogos já identificados pela lista de resultados.

Serve para: medir "Goal brewing" (golo dentro da janela), obter o resultado aos 90 minutos em jogos com prolongamento,
e medir o tempo até ao golo seguinte com uma fonte independente do bot.
Só se usa um jogo cujos golos, somados, dão o resultado final da própria fonte ('consistent').
Orçamento: de 2 em 2 horas, <= 5 pedidos por execução, e pára se restarem menos de 15 pedidos no dia.
"""
import json, os, re, sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .results_bsd import load_json
from .store import data_dir

ROOT = 'https://sports.highlightly.net/football/matches/'
MAX_REQUESTS = int(os.environ.get('HL_DETAILS_MAX', '5'))
MIN_INTERVAL_MIN = int(os.environ.get('HL_DETAILS_INTERVAL_MIN', '120'))
MIN_REMAINING = 15
GOAL_TYPES = {'goal': 0, 'penalty': 0, 'own goal': 1}     # 1 = pode estar atribuído à equipa de quem marcou na própria baliza
UTC = timezone.utc


class HLError(RuntimeError):
    pass


def request_json(match_id, token):
    req = Request(ROOT + str(int(match_id)), headers={'x-rapidapi-key': token, 'Accept': 'application/json', 'User-Agent': 'goal-roi/1.0'})
    try:
        with urlopen(req, timeout=25) as r:
            remaining = r.headers.get('x-ratelimit-requests-remaining')
            return json.loads(r.read(5_000_000)), int(remaining) if remaining and remaining.isdigit() else None
    except HTTPError as exc:
        raise HLError('HTTP %d' % exc.code) from None      # nunca imprimir URL, corpo ou cabeçalhos
    except (URLError, TimeoutError, OSError, ValueError):
        raise HLError('unavailable or invalid response') from None


def tally(goals, upto=None):
    h = sum(1 for m, _, s in goals if s == 'home' and (upto is None or m <= upto))
    a = sum(1 for m, _, s in goals if s == 'away' and (upto is None or m <= upto))
    return [h, a]


def to_details(payload):
    """Resposta de /matches/{id} -> {status, final, goals [[minuto, desconto, lado]], consistent, score_90}."""
    m = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(m, dict):
        return None
    state = m.get('state') or {}
    sc = re.match(r'^\s*(\d+)\s*-\s*(\d+)\s*$', str((state.get('score') or {}).get('current') or ''))
    names = {(m.get('homeTeam') or {}).get('name'): 'home', (m.get('awayTeam') or {}).get('name'): 'away'}
    goals, own = [], []
    for ev in m.get('events') or []:
        if not isinstance(ev, dict):
            continue
        kind = str(ev.get('type') or '').strip().lower()
        t = re.match(r'^\s*(\d+)(?:\s*\+\s*(\d+))?', str(ev.get('time') or ''))
        side = names.get((ev.get('team') or {}).get('name'))
        if kind in GOAL_TYPES and t and side:
            goals.append([int(t[1]), int(t[2] or 0), side])
            own.append(GOAL_TYPES[kind])
    final = [int(sc[1]), int(sc[2])] if sc else None
    status = str(state.get('description'))
    in_play = [g for g in goals if g[0] <= 120]             # o desempate por penáltis não conta como golo
    flipped = [[g[0], g[1], ('away' if g[2] == 'home' else 'home') if o else g[2]] for g, o in zip(in_play, own)]
    use = next((v for v in (in_play, flipped) if final is not None and tally(v) == final), None)
    out = {'status': status, 'final': final, 'goals': sorted(use) if use is not None else sorted(in_play), 'consistent': use is not None}
    out['score_90'] = tally(out['goals'], 90) if out['consistent'] else None
    return out


def wanted(results, state, extra_time, priority):
    """Jogos a pedir: primeiro os que desbloqueiam uma medição, depois os restantes, mais recentes primeiro."""
    have = state['details']
    pool = {m: r.get('bsd_id') for m, r in results.items()}
    pool.update({m: s.get('id') for m, s in extra_time.items() if isinstance(s, dict)})
    todo = [(m, i) for m, i in pool.items() if m not in have and isinstance(i, int)]
    first = set(priority) | set(extra_time)
    urgent = sorted(x for x in todo if x[0] in first)
    rest = sorted((x for x in todo if x[0] not in first), key=lambda mi: results.get(mi[0], {}).get('matched_at_utc') or '', reverse=True)
    return urgent + rest


def priority_matches():
    """Jogos com sinais que só os minutos dos golos permitem medir (Goal brewing)."""
    from .report import load
    msgs = load()[0]
    return {m['match_id'] for m in msgs if m.get('type') == 'GOAL_BREWING' and m.get('match_id')}


def run(fetch=request_json, now=None, token=None, priority=None):
    now = now or datetime.now(UTC)
    token = (token if token is not None else os.environ.get('HIGHLIGHTLY_KEY', '')).strip()
    if not token:
        print('HL_DETAILS_NOT_CONFIGURED')
        return None
    path = data_dir() / 'details_hl.json'
    state = load_json(path, {'details': {}, 'last_run_utc': None, 'stats': {}})
    if state['last_run_utc'] and now - datetime.fromisoformat(state['last_run_utc']) < timedelta(minutes=MIN_INTERVAL_MIN):
        print('HL_DETAILS_SKIPPED_TOO_SOON')
        return state
    hl = load_json(data_dir() / 'results_hl.json', {})
    todo = wanted(hl.get('results') or {}, state, hl.get('not_regular_time') or {}, priority if priority is not None else priority_matches())
    requests, remaining, stopped, shape = 0, None, None, None
    for mid, hl_id in todo:
        if requests >= MAX_REQUESTS:
            stopped = 'max_requests'
            break
        if remaining is not None and remaining < MIN_REMAINING:
            stopped = 'daily_quota_low'
            break
        payload, remaining = fetch(hl_id, token)
        requests += 1
        d = to_details(payload)
        first = payload[0] if isinstance(payload, list) and payload else payload
        shape = sorted(first)[:25] if isinstance(first, dict) else type(payload).__name__     # só nomes de campos, para diagnóstico
        if d is None:
            continue
        if not re.search(r'^finished', d['status'], re.I):
            continue                                        # ainda não terminou na fonte: tenta-se mais tarde
        state['details'][mid] = dict(d, hl_id=hl_id, fetched_at_utc=now.isoformat(timespec='seconds'))
    state['last_run_utc'] = now.isoformat(timespec='seconds')
    state['stats'] = {'pending': len(todo), 'requests': requests, 'stopped': stopped, 'quota_remaining': remaining, 'fields_seen': shape,
                      'stored': len(state['details']), 'consistent': sum(1 for d in state['details'].values() if d['consistent'])}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    print('HL_DETAILS_OK', json.dumps(state['stats']))
    return state


def main():
    try:
        run()
    except HLError as exc:
        print('HL_DETAILS_FAILED', exc)
    sys.exit(0)


if __name__ == '__main__':
    main()
