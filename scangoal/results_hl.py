"""Resultados finais pela API Highlightly (plano gratuito: 100 pedidos/dia), para jogos que o bot não liquida.

Mesmas regras de identidade da BSD (nomes iguais depois de normalizar ou alias revisto, hora compatível, um só candidato).
Só conta o estado 'Finished' (90 minutos). 'Finished after extra time/penalties', 'Awarded', etc. ficam em aberto.
Orçamento: no máximo de 4 em 4 horas, <= 8 pedidos por execução, e pára se restarem menos de 15 pedidos no dia.
Segredo: HIGHLIGHTLY_KEY (só no cabeçalho x-rapidapi-key; nunca impresso). Sem chave, não faz nada.
"""
import json, os, re, sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .results_bsd import load_aliases, load_json, match_events, pending_targets
from .store import data_dir

ROOT = 'https://sports.highlightly.net/football/matches'
MAX_REQUESTS = int(os.environ.get('HL_MAX_REQUESTS', '8'))
MIN_INTERVAL_MIN = int(os.environ.get('HL_MIN_INTERVAL_MIN', '90'))
GIVE_UP_AFTER_H, GIVE_UP_CHECKS = 8, 2      # jogo ausente da lista completa do dia, 8 h depois, em 2 leituras: a fonte não o cobre
MIN_REMAINING = 15
MAX_AGE_DAYS = 2
PAGE_SIZE = 100
UTC = timezone.utc


class HLError(RuntimeError):
    pass


def request_json(params, token):
    req = Request(ROOT + '?' + urlencode(params), headers={'x-rapidapi-key': token, 'Accept': 'application/json', 'User-Agent': 'goal-roi/1.0'})
    try:
        with urlopen(req, timeout=25) as r:
            remaining = r.headers.get('x-ratelimit-requests-remaining')
            return json.loads(r.read(5_000_000)), int(remaining) if remaining and remaining.isdigit() else None
    except HTTPError as exc:
        raise HLError('HTTP %d' % exc.code) from None      # nunca imprimir URL, corpo ou cabeçalhos
    except (URLError, TimeoutError, OSError, ValueError):
        raise HLError('unavailable or invalid response') from None


def to_event(m):
    """Jogo Highlightly -> formato comum usado por match_events (status 'finished' só para 90 minutos)."""
    state = m.get('state') or {}
    sc = re.match(r'^\s*(\d+)\s*-\s*(\d+)\s*$', str((state.get('score') or {}).get('current') or ''))
    done = state.get('description') == 'Finished' and sc is not None
    return {'id': m.get('id'), 'status': 'finished' if done else str(state.get('description')), 'event_date': m.get('date'),
            'home_team': (m.get('homeTeam') or {}).get('name') or '', 'away_team': (m.get('awayTeam') or {}).get('name') or '',
            'home_score': int(sc[1]) if done else None, 'away_score': int(sc[2]) if done else None,
            'league': (m.get('league') or {}).get('name')}


def run(fetch=request_json, now=None, token=None):
    now = now or datetime.now(UTC)
    token = (token if token is not None else os.environ.get('HIGHLIGHTLY_KEY', '')).strip()
    if not token:
        print('HL_NOT_CONFIGURED')
        return None
    path = data_dir() / 'results_hl.json'
    state = load_json(path, {'results': {}, 'near_misses': {}, 'last_run_utc': None, 'stats': {}})
    if state['last_run_utc'] and now - datetime.fromisoformat(state['last_run_utc']) < timedelta(minutes=MIN_INTERVAL_MIN):
        print('HL_SKIPPED_TOO_SOON')
        return state
    not_covered, misses = state.setdefault('not_covered', {}), state.setdefault('misses', {})
    targets = {m: t for m, t in pending_targets(now, MAX_AGE_DAYS).items() if m not in state['results'] and m not in not_covered}
    requests, remaining, stopped, events, per_day = 0, None, None, [], {}
    for day in sorted({d for t in targets.values() for d in t['days']}, reverse=True):
        offset, total = 0, None
        while stopped is None:
            if requests >= MAX_REQUESTS:
                stopped = 'max_requests'
                break
            if remaining is not None and remaining < MIN_REMAINING:
                stopped = 'daily_quota_low'
                break
            payload, remaining = fetch({'date': day, 'limit': PAGE_SIZE, 'offset': offset}, token)
            requests += 1
            page = payload.get('data') if isinstance(payload, dict) else None
            if not isinstance(page, list):
                raise HLError('unexpected list shape')
            events.extend(to_event(m) for m in page if isinstance(m, dict))
            t = (payload.get('pagination') or {}).get('totalCount')
            total = t if isinstance(t, int) else total
            offset += len(page)
            if not page or total is None or offset >= total:
                break
        per_day[day] = {'count_reported': total, 'events_read': offset}
        if stopped:
            break
    seen, unique = set(), []
    for ev in events:
        if ev['id'] not in seen:
            seen.add(ev['id'])
            unique.append(ev)
    unfinished = {}
    found, near = match_events(targets, unique, load_aliases(), unfinished)
    # Jogos decididos depois dos 90 minutos: ficam em aberto (o resultado aos 90 não vem na resposta) e são contados.
    late = {m: s for m, s in unfinished.items() if re.search(r'extra time|penalt', s['status'], re.I)}
    state['not_regular_time'] = {m: s for m, s in {**state.get('not_regular_time', {}), **late}.items() if m not in state['results']}
    complete = {d for d, p in per_day.items() if p['count_reported'] is not None and p['events_read'] >= p['count_reported']}
    for mid, t in targets.items():
        ref = datetime.fromisoformat(t['kickoff_utc'] or t['date_utc'])
        if mid in found or mid in near or mid in unfinished or now - ref < timedelta(hours=GIVE_UP_AFTER_H) or not set(t['days']) <= complete:
            continue   # só conta como ausência quando os dias do jogo foram lidos por inteiro e não há nome parecido por rever
        misses[mid] = misses.get(mid, 0) + 1
        if misses[mid] >= GIVE_UP_CHECKS:
            not_covered[mid] = now.isoformat(timespec='seconds')
    for mid, res in found.items():
        state['results'].setdefault(mid, dict(res, matched_at_utc=now.isoformat(timespec='seconds')))   # nunca reescreve
    state['near_misses'] = {m: n for m, n in {**state.get('near_misses', {}), **near}.items() if m not in state['results']}
    state['last_run_utc'] = now.isoformat(timespec='seconds')
    state['stats'] = {'not_covered': len(not_covered), 'targets': len(targets), 'matched_this_run': len([m for m in targets if m in state['results']]), 'requests': requests,
                      'events_seen': len(unique), 'stopped': stopped, 'quota_remaining': remaining, 'per_day': per_day}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    print('HL_OK', json.dumps(state['stats']))
    return state


def main():
    try:
        run()
    except HLError as exc:
        print('HL_FAILED', exc)      # a recolha e o relatório continuam sem a Highlightly
    sys.exit(0)


if __name__ == '__main__':
    main()
