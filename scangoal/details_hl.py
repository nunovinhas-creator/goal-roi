"""Minutos dos golos pela Highlightly (GET /matches/{id}), para jogos já identificados pela lista de resultados.

Serve para: medir "Goal brewing" (golo dentro da janela), obter o resultado aos 90 minutos em jogos com prolongamento,
e medir o tempo até ao golo seguinte com uma fonte independente do bot.
Só se usa um jogo cujos golos, somados, dão o resultado final da própria fonte ('consistent').
Orçamento: de 2 em 2 horas, <= 5 pedidos por execução, e pára se restarem menos de 15 pedidos no dia.
"""
import fcntl, json, os, re, sys
from datetime import datetime, timedelta, timezone
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import chronology, opportunity_results
from .results_bsd import load_json
from .store import data_dir

ROOT = 'https://sports.highlightly.net/football/matches/'
MAX_REQUESTS = int(os.environ.get('HL_DETAILS_MAX', '5'))
MIN_INTERVAL_MIN = int(os.environ.get('HL_DETAILS_INTERVAL_MIN', '120'))
MIN_REMAINING = 30          # os resultados têm prioridade sobre os minutos dos golos na quota do dia
GOAL_TYPES = {'goal': 0, 'penalty': 0, 'own goal': 1}     # 1 = pode estar atribuído à equipa de quem marcou na própria baliza
UTC = timezone.utc
REFRESH_WINDOW = timedelta(hours=48)
IDENTITY = ('fixture_id', 'event_date', 'league', 'target_league', 'home', 'away',
            'target_teams', 'identity_method', 'link')


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


def load_state(directory):
    try:
        return json.loads((directory / 'details_hl.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'details': {}, 'last_run_utc': None, 'stats': {}}


def versions(state):
    history = state.get('detail_versions', {})
    yield from history.items()
    for mid, row in state.get('details', {}).items():
        if mid not in history:
            yield mid, (row,)


def stamps(row):
    fetched = chronology.parse_utc(row.get('fetched_at_utc'))
    observed = chronology.parse_utc(row.get('observed_at', row.get('fetched_at_utc')))
    available = chronology.parse_utc(row.get('available_at', row.get('fetched_at_utc')))
    if fetched is not None and observed is not None and available is not None and observed <= available and fetched == available:
        return observed, available
    return None, None


def snapshot(state, as_of):
    """Select complete detail snapshots without joining them to a later provider binding."""
    selected = {}
    for mid, history in versions(state):
        visible = [(available, row) for row in history if (available := stamps(row)[1]) is not None
                   and available <= as_of and row.get('fixture_id', mid) == mid]
        if visible:
            last = max(t for t, _ in visible)
            newest = [row for t, row in visible if t == last]
            if all(row == newest[0] for row in newest):
                selected[mid] = newest[0]
    return selected


def observations(state, as_of):
    """Supply independently bound regulation scores to the common result resolver."""
    output = {}
    for mid, history in versions(state):
        for row in history:
            if (row.get('fixture_id', mid) != mid or not row.get('consistent')
                    or not re.search(r'extra time|penalt', row.get('status') or '', re.I)):
                continue
            score = opportunity_results.score_pair(row.get('score_90'))
            observed, available = stamps(row)
            kickoff = chronology.parse_utc(row.get('event_date'))
            if score is None or not opportunity_results.ft_time_valid(kickoff, observed, available, None, as_of):
                continue
            result = opportunity_results.Observation('highlightly_detail', mid, 'regulation', score, observed, available,
                                                      kickoff=kickoff, fixture_id=mid, link=row.get('link'), provider_record=row)
            output.setdefault(mid, []).append(result)
    return output


def source_binding(mid, binding, hl_id):
    fields = {key: binding[key] for key in IDENTITY if key in binding}
    return dict(fields, fixture_id=fields.get('fixture_id', mid), provider='highlightly_detail',
                hl_id=hl_id, link=fields.get('link') or ROOT + str(hl_id), source_ft=binding.get('ft'))


def refresh_target(state, results, extra_time, now):
    candidates = []
    for mid, detail in state['details'].items():
        binding = results.get(mid) or extra_time.get(mid) or {}
        hl_id = binding.get('bsd_id', binding.get('id'))
        if type(hl_id) is not int:
            continue
        current = source_binding(mid, binding, hl_id)
        changed = detail.get('hl_id') != hl_id or (detail.get('provider') and any(detail.get(k) != v for k, v in current.items()))
        fetched = stamps(detail)[1]
        if changed or (fetched is not None and timedelta(0) <= now - fetched <= REFRESH_WINDOW):
            last_check = chronology.parse_utc(state.get('checked_at', {}).get(mid)) or fetched
            if last_check is None or now - last_check >= timedelta(minutes=MIN_INTERVAL_MIN):
                candidates.append((not changed, last_check or datetime.min.replace(tzinfo=UTC), mid, hl_id))
    if candidates:
        _, _, mid, hl_id = min(candidates)
        return [(mid, hl_id)]
    return []


def remember(state, mid, detail):
    old = state['details'].get(mid)
    clocks = {'fetched_at_utc', 'observed_at', 'available_at'}
    if old is not None:
        if {k: v for k, v in old.items() if k not in clocks} == {k: v for k, v in detail.items() if k not in clocks}:
            return False
        previous = stamps(old)[1]
        if previous is not None and stamps(detail)[1] <= previous:
            raise ValueError('Highlightly revision does not follow the existing observation')
    history = state.setdefault('detail_versions', {}).setdefault(mid, [] if old is None else [old])
    history.append(detail)
    state['details'][mid] = detail
    return True


def save(path, state):
    with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.details-', delete=False) as temp:
        temporary = temp.name
        try:
            json.dump(state, temp, indent=1, ensure_ascii=False, sort_keys=True)
            temp.write('\n')
            temp.flush()
            os.fsync(temp.fileno())
            os.replace(temporary, path)
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def priority_matches():
    """Jogos com sinais que só os minutos dos golos permitem medir (Goal brewing)."""
    from .report import load
    msgs = load()[0]
    return {m['match_id'] for m in msgs if m.get('type') == 'GOAL_BREWING' and m.get('match_id')}


def run(fetch=request_json, now=None, token=None, priority=None):
    observed_clock = (lambda stamp=now: stamp) if now is not None else (lambda: datetime.now(UTC))
    now = observed_clock()
    token = (token if token is not None else os.environ.get('HIGHLIGHTLY_KEY', '')).strip()
    if not token:
        print('HL_DETAILS_NOT_CONFIGURED')
        return None
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory.parent / ('.' + directory.name + '.details.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return collect(directory, fetch, now, observed_clock, token, priority)


def collect(directory, fetch, now, observed_clock, token, priority):
    path = directory / 'details_hl.json'
    state = load_state(directory)
    if state['last_run_utc'] and now - datetime.fromisoformat(state['last_run_utc']) < timedelta(minutes=MIN_INTERVAL_MIN):
        print('HL_DETAILS_SKIPPED_TOO_SOON')
        return state
    hl = load_json(directory / 'results_hl.json', {})
    results, extra_time = hl.get('results') or {}, hl.get('not_regular_time') or {}
    todo = wanted(results, state, extra_time, priority if priority is not None else priority_matches())
    refresh = refresh_target(state, results, extra_time, now)
    todo = list(dict.fromkeys([*refresh, *todo]))
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
        stamp = observed_clock().isoformat()
        state.setdefault('checked_at', {})[mid] = stamp
        d = to_details(payload)
        first = payload[0] if isinstance(payload, list) and payload else payload
        shape = sorted(first)[:25] if isinstance(first, dict) else type(payload).__name__     # só nomes de campos, para diagnóstico
        if d is None:
            continue
        if not re.search(r'^finished', d['status'], re.I):
            continue                                        # ainda não terminou na fonte: tenta-se mais tarde
        provenance = source_binding(mid, results.get(mid) or extra_time.get(mid) or {}, hl_id)
        if remember(state, mid, dict(d, **provenance, fetched_at_utc=stamp, observed_at=stamp, available_at=stamp)):
            save(path, state)
    state['last_run_utc'] = now.isoformat(timespec='seconds')
    state['stats'] = {'pending': len(todo), 'requests': requests, 'stopped': stopped, 'quota_remaining': remaining, 'fields_seen': shape,
                      'stored': len(state['details']), 'consistent': sum(1 for d in state['details'].values() if d['consistent'])}
    save(path, state)
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
