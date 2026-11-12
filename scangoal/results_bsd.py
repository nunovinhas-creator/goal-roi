"""Resultados finais pela API BSD (sports.bzzoiro.com), só para jogos que o bot não liquida.

Regras (nunca liquidar por semelhança):
- identidade = nome da casa E nome de fora iguais depois de normalizar (ou via data/team_aliases.json, revisto à mão);
- hora compatível: pré-jogo, início a ±90 min do anunciado; ao vivo, o sinal cai entre o início e 150 min depois;
- exatamente um candidato; caso contrário fica em aberto e é contado.
BSD documenta home_score/away_score como resultado do tempo regulamentar.
Segredo: BSD_API_KEY (enviado só em Authorization: Token; nunca impresso). Sem chave, não faz nada.
"""
import json, os, re, sys, unicodedata
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .store import data_dir

ROOT = 'https://sports.bzzoiro.com/api/v2/events/'
MAX_REQUESTS = int(os.environ.get('BSD_MAX_REQUESTS', '12'))   # por execução
MIN_INTERVAL_MIN = int(os.environ.get('BSD_MIN_INTERVAL_MIN', '50'))
MAX_AGE_DAYS = 3
PAGE_SIZE = 200
UTC = timezone.utc


class BSDError(RuntimeError):
    pass


def norm_team(value):
    value = unicodedata.normalize('NFKD', value or '')
    value = ''.join(c for c in value if not unicodedata.combining(c)).casefold().replace('&', 'and')
    words = re.findall(r'[a-z0-9]+', value)
    designators = {'fc', 'fk', 'sv', 'afc', 'cf', 'sc'}   # só nas pontas; nunca remover u19, women, ii, etc.
    while len(words) > 1 and words[0] in designators:
        words.pop(0)
    while len(words) > 1 and words[-1] in designators:
        words.pop()
    return ' '.join(words)


def request_json(url, token):
    if not url.startswith(ROOT):
        raise BSDError('unexpected URL')
    req = Request(url, headers={'Authorization': 'Token ' + token, 'Accept': 'application/json', 'User-Agent': 'goal-roi/1.0'})
    try:
        with urlopen(req, timeout=20) as r:
            return json.loads(r.read(3_000_000))
    except HTTPError as exc:
        raise BSDError('HTTP %d' % exc.code) from None       # nunca imprimir URL, corpo ou cabeçalhos
    except (URLError, TimeoutError, OSError, ValueError):
        raise BSDError('unavailable or invalid response') from None


def name_of(event, key):
    v = event.get(key)
    return (v.get('name') if isinstance(v, dict) else v if isinstance(v, str) else event.get(key + '_name')) or ''


def kickoff_of(event):
    v = next((event.get(k) for k in ('event_date', 'start_time', 'kickoff', 'scheduled_at') if event.get(k)), None)
    try:
        dt = datetime.fromisoformat(v.replace('Z', '+00:00'))
        return dt.astimezone(UTC) if dt.tzinfo else None
    except (AttributeError, ValueError):
        return None


def time_ok(target, ko):
    if ko is None:
        return False
    if target.get('kickoff_utc'):
        return abs((ko - datetime.fromisoformat(target['kickoff_utc'])).total_seconds()) <= 90 * 60
    signal = datetime.fromisoformat(target['date_utc'])
    return timedelta(0) <= signal - ko <= timedelta(minutes=150)


def score_of(event):
    if event.get('status') != 'finished' or any(event.get(f) is True for f in ('postponed', 'cancelled', 'abandoned')):
        return None
    h, a = event.get('home_score'), event.get('away_score')
    return [h, a] if all(type(n) is int and n >= 0 for n in (h, a)) and type(event.get('id')) is int else None


def match_events(targets, events, aliases):
    """targets: {match_id: {teams, date_utc, kickoff_utc}} -> ({match_id: resultado}, {match_id: quase-correspondências})."""
    names = lambda name: {norm_team(name)} | {norm_team(x) for x in aliases.get(norm_team(name), [])}
    found, near = {}, {}
    index = {}
    for ev in events:
        if isinstance(ev, dict):
            index.setdefault((norm_team(name_of(ev, 'home_team')), norm_team(name_of(ev, 'away_team'))), []).append(ev)
    for mid, t in targets.items():
        homes, aways = names(t['teams'][0]), names(t['teams'][1])
        cands = [ev for h in homes for a in aways for ev in index.get((h, a), []) if time_ok(t, kickoff_of(ev))]
        scored = [(ev, score_of(ev)) for ev in cands]
        if len(cands) == 1 and scored[0][1]:
            ev = cands[0]
            found[mid] = {'ft': scored[0][1], 'bsd_id': ev['id'], 'home': name_of(ev, 'home_team'), 'away': name_of(ev, 'away_team'),
                          'event_date': kickoff_of(ev).isoformat(timespec='minutes')}
        elif not cands:  # diagnóstico para revisão manual de alias: uma das equipas coincide e a hora é compatível
            n = [[name_of(ev, 'home_team'), name_of(ev, 'away_team')] for (h, a), evs in index.items() if (h in homes) != (a in aways)
                 for ev in evs if time_ok(t, kickoff_of(ev))]
            if n:
                near[mid] = {'bot': t['teams'], 'bsd_candidates': n[:3]}
    return found, near


def pending_targets(now, max_age_days=MAX_AGE_DAYS):
    """Jogos com apostas, sem resultado guardado da BSD, já terminados há tempo suficiente e não demasiado antigos."""
    from .report import build_bets, load
    msgs, ft, marks, notes, _ = load()
    out = {}
    for b in build_bets(msgs, ft, marks, notes, strict=False):
        if b['type'] != 'BETFAIR' or not b['teams'] or len(b['teams']) != 2 or not b['match_id']:
            continue
        ref = datetime.fromisoformat(b['kickoff_utc'] or b['date_utc'])
        if timedelta(hours=2, minutes=30) <= now - ref <= timedelta(days=max_age_days):
            out.setdefault(b['match_id'], {'teams': b['teams'], 'date_utc': b['date_utc'], 'kickoff_utc': b['kickoff_utc'],
                                             'days': sorted({ref.date().isoformat(), (ref - timedelta(minutes=150)).date().isoformat()})})
    return out


def load_aliases():
    """data/team_aliases.json: {"nome no bot": ["nome aceite num fornecedor", ...]}. Só entradas revistas à mão."""
    raw = load_json(data_dir() / 'team_aliases.json', {})
    return {norm_team(k): ([v] if isinstance(v, str) else list(v)) for k, v in raw.items() if not k.startswith('_')}


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def run(fetch=request_json, now=None, token=None):
    now = now or datetime.now(UTC)
    token = (token if token is not None else os.environ.get('BSD_API_KEY', '')).strip()
    if not token:
        print('BSD_NOT_CONFIGURED')
        return None
    path = data_dir() / 'results_bsd.json'
    state = load_json(path, {'results': {}, 'near_misses': {}, 'last_run_utc': None, 'stats': {}})
    if state['last_run_utc'] and now - datetime.fromisoformat(state['last_run_utc']) < timedelta(minutes=MIN_INTERVAL_MIN):
        print('BSD_SKIPPED_TOO_SOON')
        return state
    aliases = load_aliases()
    targets = {m: t for m, t in pending_targets(now).items() if m not in state['results']}
    requests, capped, events, per_day = 0, False, [], {}
    if targets:
        days = sorted({d for t in targets.values() for d in t['days']}, reverse=True)   # dias mais recentes primeiro
        for day in days:                                # um dia de cada vez, com limit/offset (padrão já usado no asianodds-h1-lab)
            offset, total = 0, None
            while True:
                if requests >= MAX_REQUESTS:
                    capped = True
                    break
                payload = fetch(ROOT + '?' + urlencode({'date_from': day, 'date_to': day, 'status': 'finished', 'limit': PAGE_SIZE, 'offset': offset}), token)
                requests += 1
                if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
                    raise BSDError('unexpected list shape')
                page = payload['results']
                events.extend(page)
                total = payload.get('count') if isinstance(payload.get('count'), int) else total
                offset += len(page)
                if not page or not payload.get('next') or (total is not None and offset >= total):
                    break
            per_day[day] = {'count_reported': total, 'events_read': offset}
            if capped:
                break
        seen_ids, unique = set(), []
        for ev in events:                               # a mesma página pode repetir eventos
            if isinstance(ev, dict) and ev.get('id') not in seen_ids:
                seen_ids.add(ev.get('id'))
                unique.append(ev)
        events = unique
        found, near = match_events(targets, events, aliases)
        for mid, res in found.items():
            state['results'].setdefault(mid, dict(res, matched_at_utc=now.isoformat(timespec='seconds')))   # nunca reescreve
        state['near_misses'] = {m: n for m, n in {**state.get('near_misses', {}), **near}.items() if m not in state['results']}
    events_seen = len(events)
    sample = sorted({'%s — %s' % (name_of(e, 'home_team'), name_of(e, 'away_team')) for e in events})[:15]
    state['last_run_utc'] = now.isoformat(timespec='seconds')
    state['stats'] = {'targets': len(targets), 'matched_this_run': len([m for m in targets if m in state['results']]),
                      'requests': requests, 'events_seen': events_seen, 'page_cap_reached': capped,
                      'per_day': per_day, 'event_names_sample': sample}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    print('BSD_OK', json.dumps(state['stats']))
    return state


def main():
    try:
        run()
    except BSDError as exc:
        print('BSD_FAILED', exc)     # a recolha e o relatório continuam sem a BSD
    sys.exit(0)


if __name__ == '__main__':
    main()
