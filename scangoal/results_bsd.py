"""Resultados finais pela API BSD (sports.bzzoiro.com), só para jogos que o bot não liquida.

Regras (nunca liquidar por semelhança):
- identidade = nome da casa E nome de fora iguais depois de normalizar (ou via data/team_aliases.json, revisto à mão);
- para um novo vínculo por liga BSD v2, início explícito a ±15 min e competição resolvida pelo catálogo BSD;
- exatamente um candidato; caso contrário fica em aberto e é contado.
BSD documenta home_score/away_score como resultado do tempo regulamentar.
Segredo: BSD_API_KEY (enviado só em Authorization: Token; nunca impresso). Sem chave, não faz nada.
"""
import json, os, re, sys, unicodedata
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import chronology
from .opportunity_results import ft_time_valid, remember_provider_results
from .store import data_dir

ROOT = 'https://sports.bzzoiro.com/api/v2/events/'
PUBLIC_ROOT = 'https://sports.bzzoiro.com/'
MAX_REQUESTS = int(os.environ.get('BSD_MAX_REQUESTS', '12'))   # por execução
MIN_INTERVAL_MIN = int(os.environ.get('BSD_MIN_INTERVAL_MIN', '50'))
MAX_AGE_DAYS = 3
PAGE_SIZE = 200
UTC = timezone.utc


class BSDError(RuntimeError):
    pass


def norm_team(value):
    value = unicodedata.normalize('NFKD', value if isinstance(value, str) else '')
    value = ''.join(c for c in value if not unicodedata.combining(c)).casefold().replace('&', 'and')
    words = re.findall(r'[a-z0-9]+', value)
    designators = {'fc', 'fk', 'sv', 'afc', 'cf', 'sc'}   # só nas pontas; nunca remover u19, women, ii, etc.
    while len(words) > 1 and words[0] in designators:
        words.pop(0)
    while len(words) > 1 and words[-1] in designators:
        words.pop()
    return ' '.join(words)


def request_json(url, token, *, observe=None):
    if not url.startswith(ROOT):
        raise BSDError('unexpected URL')
    req = Request(url, headers={'Authorization': 'Token ' + token, 'Accept': 'application/json', 'User-Agent': 'goal-roi/1.0'})
    try:
        with urlopen(req, timeout=20) as r:
            body = r.read(3_000_000)
            payload = json.loads(body)
            if observe is not None:
                try:
                    observe({k: v for k, v in r.headers.items()
                             if k.lower() in ('ratelimit', 'ratelimit-policy') and token not in str(v)},
                            body, datetime.now(UTC))
                except Exception:
                    print('BSD_RECOVERY_QUOTA_UNAVAILABLE')
            return payload
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
    scheduled = chronology.kickoff_time(target)
    if scheduled is not None:
        return abs(ko - scheduled) <= timedelta(minutes=15)
    signal, minute = chronology.signal_time(target), target.get('minute')
    return (target.get('phase') == 'live' and signal is not None and timedelta(0) <= signal - ko <= timedelta(minutes=150)
            and (minute is None or (type(minute) is int and 0 <= minute <= 120
                                    and timedelta(minutes=-5) <= signal - ko - timedelta(minutes=minute) <= timedelta(minutes=45))))


def score_of(event):
    if (event.get('status') != 'finished'
            or any(event.get(f) is True for f in ('postponed', 'cancelled', 'abandoned', 'awarded'))
            or any(event.get(f) is not None for f in ('extra_time_score', 'penalty_shootout'))):
        return None
    h, a = event.get('home_score'), event.get('away_score')
    return [h, a] if all(type(n) is int and n >= 0 for n in (h, a)) and type(event.get('id')) is int else None


LEAGUE_NOISE = frozenset({'league', 'liga', 'division', 'primera', 'premier', 'the'})
BSD_COMPETITION_IDENTITY = 'bsd_league_id_participants_kickoff'
# Provider names and explicitly reviewed target names. See audits/bsd-settlement/README.md.
# Keep domestic aliases country-scoped when the unqualified name is ambiguous.
BSD_LEAGUES = {
    7: ('Champions League', ('UEFA Champions League',)),
    9: ('Brasileirão Serie A', ('Brazilian BRAZIL SERIE A', 'Brazil Serie A')),
    13: ('Scottish Premiership', ('Scottish Premiership',)),
    18: ('MLS', ('US Major League Soccer', 'USA Major League Soccer')),
    80: ('Categoría Primera A', ('Colombian Primera Division', 'Colombia Primera A')),
    86: ('League One', ('England League 1', 'England League One')),
    91: ('National League', ('England National League',)),
    100: ('Serie B', ('Italian Serie B', 'Italy Serie B')),
    101: ('Serie C, Girone A', ('Italian Serie C - Group A', 'Italy Serie C Group A')),
}


def bsd_competition_proof(event, league_id=None):
    # The optional id is only for checking later observations of an already verified event.
    incoming_id = event.get('league_id')
    if incoming_id is not None:
        if league_id is not None and incoming_id != league_id:
            return None
        league_id = incoming_id
    if type(league_id) is not int or league_id not in BSD_LEAGUES:
        return None
    name, target_names = BSD_LEAGUES[league_id]
    names = {norm_team(label) for label in (name, *target_names)}
    for field in ('league', 'competition'):
        other = event.get(field)
        if isinstance(other, dict):
            other = other.get('id')
        elif isinstance(other, str):
            continue
        if other is not None and (type(other) is not int or other != league_id):
            return None
    if any(norm_team(label) not in names for label in (
            name_of(event, 'league'), name_of(event, 'competition'), event.get('league_name'),
            event.get('competition_name')) if label):
        return None
    return {'field': 'league_id', 'league_url': f'{PUBLIC_ROOT}leagues/{league_id}/', 'name': name}


def bsd_league_matches(expected, league_id):
    return (type(league_id) is int and league_id in BSD_LEAGUES
            and norm_team(expected) in {norm_team(label) for label in BSD_LEAGUES[league_id][1]})


def bsd_record_matches(target, row):
    league_id = row.get('bsd_league_id')
    proof = bsd_competition_proof({'league_id': league_id})
    if (not proof or row.get('competition_provenance') != proof or row.get('league') != proof['name']
            or row.get('fixture_id') != target['match_id'] or type(row.get('bsd_id')) is not int or row['bsd_id'] <= 0
            or row.get('link') != f"{PUBLIC_ROOT}matches/{row['bsd_id']}/"
            or row.get('identity_method') != BSD_COMPETITION_IDENTITY
            or not bsd_league_matches(target.get('league'), league_id) or chronology.kickoff_time(target) is None):
        return False
    return record_teams_match(target, row)


def record_teams_match(target, row):
    observed = [norm_team(row.get(side)) for side in ('home', 'away')]
    expected = [norm_team(name) for name in target['teams']]
    if observed == expected:
        return True
    aliases = load_aliases()
    return all(actual and (actual == wanted or actual in map(norm_team, aliases.get(wanted, ())))
               for wanted, actual in zip(expected, observed))


def leagues_compatible(expected, observed):
    """Same competition written differently: the shorter name's distinctive words all appear in the longer one.

    'la liga' vs 'spanish la liga primera' and 'ligue 2' vs 'french ligue 2' are compatible; 'superliga' vs
    'danish super league' is not, and a single generic word ('league', 'liga') never establishes identity.
    """
    a, b = set(expected.split()), set(observed.split())
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return short <= long and (len(short) >= 2 or bool(short - LEAGUE_NOISE))


def competition_ok(target, event, *, provider_id=None):
    """Competition check, used together with exact team identity, kickoff compatibility and a single candidate.

    When both leagues are known they must be compatible (leagues_compatible), even for a bound provider id.
    Without a provider league only an independently bound provider id is accepted (audit N03): teams and
    kickoff alone never identify a fixture.
    """
    bound = type(provider_id) is int and type(event.get('id')) is int and provider_id == event['id']
    if 'league_id' in event:
        if bsd_competition_proof(event):
            return bsd_league_matches(target.get('league'), event['league_id'])
        return (bound and event['league_id'] is None
                and not any(event.get(field) for field in ('league', 'league_name', 'competition', 'competition_name')))
    expected = norm_team(target.get('league'))
    observed = norm_team(name_of(event, 'league') or name_of(event, 'competition'))
    if expected and observed:
        return leagues_compatible(expected, observed)
    return bound


def match_events(targets, events, aliases, unfinished=None, *, conflicting=None):
    """Match one fixture, retaining score disagreements separately when its identity is proven."""
    names = lambda name: ({norm_team(name)} | {norm_team(x) for x in aliases.get(norm_team(name), [])}) - {''}
    found, near = {}, {}
    index, by_id = {}, {}
    for ev in events:
        if isinstance(ev, dict):
            event_id = ev.get('id')
            if type(event_id) is int and ('league_id' in ev or conflicting is not None):
                group = by_id.setdefault(event_id, [])
                if ev in group:
                    continue
                group.append(ev)
            index.setdefault((norm_team(name_of(ev, 'home_team')), norm_team(name_of(ev, 'away_team'))), []).append(ev)
    bindings = {}
    for mid, t in targets.items():
        homes, aways = names(t['teams'][0]), names(t['teams'][1])
        provider_id = t.get('provider_id')
        candidates = [ev for h in homes for a in aways for ev in index.get((h, a), []) if time_ok(t, kickoff_of(ev))]
        cands = [ev for ev in candidates if (provider_id is None or provider_id == ev.get('id'))
                 and competition_ok(t, ev, provider_id=provider_id)
                 and ('league_id' not in ev or (type(ev.get('id')) is int and ev['id'] > 0
                                               and chronology.kickoff_time(t) is not None))]
        if any('league_id' in ev for ev in cands):
            # Unresolved competition on another plausible event prevents a new binding.
            if provider_id is None and any(not bsd_competition_proof(ev) for ev in candidates):
                cands = []
        event_ids = {ev['id'] for ev in cands if type(ev.get('id')) is int}
        if len(event_ids) == 1:
            event_id, = event_ids
            if len(by_id.get(event_id, ())) > len(cands):
                cands = []  # Same ID on an incompatible event row makes its identity ambiguous.
        candidate_rows = [(event_binding(mid, t, ev), score_of(ev)) for ev in cands]
        scored = [dict(row, ft=score) for row, score in candidate_rows if score is not None]
        if len(cands) == 1 and scored:
            found[mid] = scored[0]
        elif (conflicting is not None and len(event_ids) == 1 and len(scored) > 1
              and len({tuple(row['ft']) for row in scored}) > 1
              and all(row == candidate_rows[0][0] for row, _ in candidate_rows[1:])):
            conflicting[mid] = scored
        elif len(cands) == 1 and unfinished is not None:
            unfinished[mid] = dict(event_binding(mid, t, cands[0]), status=str(cands[0].get('status')), id=cands[0].get('id'))
        elif not cands:  # diagnóstico para revisão manual de alias: uma das equipas coincide e a hora é compatível
            n = [[name_of(ev, 'home_team'), name_of(ev, 'away_team')] for (h, a), evs in index.items() if (h in homes) != (a in aways)
                 for ev in evs if time_ok(t, kickoff_of(ev))]
            if n or candidates:
                near[mid] = {'bot': t['teams'], 'bsd_candidates': n[:3], 'competition_review': [
                    {'provider_id': ev.get('id'), 'league': name_of(ev, 'league') or name_of(ev, 'competition') or None,
                     **({'bsd_league_id': ev['league_id']} if type(ev.get('league_id')) is int else {}),
                     'event_date': kickoff_of(ev).isoformat()} for ev in candidates[:3]]}
        binding = found.get(mid)
        if conflicting is not None and mid in conflicting:
            binding = conflicting[mid][0]
        if binding and (binding['identity_method'] == BSD_COMPETITION_IDENTITY or conflicting is not None):
            bindings.setdefault(binding['bsd_id'], []).append(mid)
    for mids in bindings.values():
        if len(mids) > 1:
            for mid in mids:
                found.pop(mid, None)
                if conflicting is not None:
                    conflicting.pop(mid, None)
    return found, near


def event_binding(mid, target, event):
    row = {'fixture_id': mid, 'bsd_id': event.get('id'), 'home': name_of(event, 'home_team'),
            'away': name_of(event, 'away_team'), 'event_date': kickoff_of(event).isoformat(),
            'league': name_of(event, 'league') or name_of(event, 'competition') or None,
            'target_league': target.get('league'), 'target_teams': target.get('teams'),
            'target_kickoff_utc': target.get('kickoff_utc'), 'target_phase': target.get('phase'),
            'identity_method': 'provider_id' if type(target.get('provider_id')) is int else 'competition_participants_kickoff',
            'link': event.get('url')}
    proof = bsd_competition_proof(event)
    if proof:
        row.update(league=proof['name'], bsd_league_id=event['league_id'], competition_provenance=proof,
                   identity_method=BSD_COMPETITION_IDENTITY,
                   link=f"{PUBLIC_ROOT}matches/{event['id']}/")
    return row


def capture_result_versions(state, found, events, observed_at, *, conflicting=None):
    """Capture every FT version returned by the ordinary quota-limited queries."""
    previous_bindings = {}
    for snapshots in (state['results'], state.get('first_results')):
        if isinstance(snapshots, dict):
            for mid, stored in snapshots.items():
                if isinstance(stored, dict) and type(provider_id := stored.get('bsd_id')) is int:
                    previous_bindings.setdefault(provider_id, set()).add(mid)
    for mid, versions in state.get('ft_versions', {}).items():
        if not isinstance(versions, list):
            continue
        for version in versions:
            if not isinstance(version, dict):
                continue
            snapshot = version.get('snapshot')
            ids = (version.get('provider_id'), snapshot.get('bsd_id') if isinstance(snapshot, dict) else None)
            for provider_id in ids:
                if type(provider_id) is int:
                    previous_bindings.setdefault(provider_id, set()).add(mid)
    batch = {mid: [row] for mid, row in found.items()}
    batch.update(conflicting or {})
    batch = {mid: rows for mid, rows in batch.items()
             if ft_time_valid(chronology.parse_utc(rows[0].get('event_date')), observed_at, observed_at, None, observed_at)
             and previous_bindings.get(rows[0]['bsd_id'], {mid}) == {mid}}
    by_provider_id = {}
    for event in events:
        if isinstance(event, dict) and type(event.get('id')) is int:
            by_provider_id.setdefault(event['id'], []).append(event)
    for match_id, stored in {**state['results'], **{mid: rows[0] for mid, rows in batch.items()}}.items():
        if stored.get('recheck_conflict') is True:
            continue  # The recheck already matched and deduplicated these evidence-only rows.
        bound = stored.get('identity_method') in ('provider_id', BSD_COMPETITION_IDENTITY)
        bound_id = stored.get('bsd_id') if match_id in batch or bound else None
        by_league = stored.get('identity_method') == BSD_COMPETITION_IDENTITY
        for event in by_provider_id.get(stored.get('bsd_id'), ()):
            score, kickoff = score_of(event), kickoff_of(event)
            same_competition = (bool(bsd_competition_proof(event, stored['bsd_league_id'])) if by_league
                                else competition_ok(stored, event, provider_id=bound_id))
            if (score is not None and ft_time_valid(kickoff, observed_at, observed_at, None, observed_at)
                    and kickoff == chronology.parse_utc(stored.get('event_date'))
                    and same_competition
                    and all(norm_team(name_of(event, side + '_team')) == norm_team(stored.get(side, ''))
                            for side in ('home', 'away'))):
                update = dict(stored, ft=score, home=name_of(event, 'home_team'), away=name_of(event, 'away_team'))
                if 'url' in event and not by_league:
                    update['link'] = event['url']
                if update not in batch.setdefault(match_id, []):
                    batch[match_id].append(update)
    for match_id, rows in batch.items():
        has_conflict = rows[0].get('recheck_conflict') is True or len({tuple(row['ft']) for row in rows}) > 1
        if has_conflict:
            rows = sorted(rows, key=lambda row: (row['ft'], json.dumps(row, sort_keys=True)), reverse=True)
        for row in rows:
            remember_provider_results(state, {match_id: row}, observed_at, update_snapshot=not has_conflict)


def load_result_state(path):
    """An unreadable existing provider archive must not be replaced with an empty one."""
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'results': {}, 'near_misses': {}, 'last_run_utc': None, 'stats': {}}
    if (not isinstance(doc, dict) or not isinstance(doc.get('results'), dict)
            or not isinstance(doc.get('ft_versions', {}), dict)):
        raise ValueError('Invalid provider result archive')
    return doc


def save_result_state(path, state):
    from .chronology import archive_snapshot

    path.parent.mkdir(parents=True, exist_ok=True)
    state.update(archive_snapshot(state, load_result_state(path), 'near_misses', 'not_covered', 'not_regular_time'))
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    temporary.replace(path)


def pending_targets(now, max_age_days=MAX_AGE_DAYS):
    """Jogos com apostas, sem resultado guardado da BSD, já terminados há tempo suficiente e não demasiado antigos."""
    from .report import build_bets, load
    from .prospective import registered_matches
    prospective = registered_matches()
    chron = {}
    msgs, ft, marks, notes, _ = load(chron, as_of=now)
    out = {}
    for b in build_bets(msgs, ft, marks, notes, strict=False, chronology=chron, now=now):
        # Betfair: sempre (o bot nunca dá o resultado). Outros tipos: só se o bot não o deu, ou se o jogo está no
        # registo prospetivo, que exige confirmação independente (a consulta é por dia: não gasta pedidos extra).
        if (not (b['type'] == 'BETFAIR' or b['ft'] is None or b['match_id'] in prospective)
                or not b['teams'] or len(b['teams']) != 2 or not b['match_id']):
            continue
        ref = datetime.fromisoformat(b['kickoff_utc'] or b['date_utc'])
        # Só o estado "terminado" da fonte liquida; por isso pode pedir-se cedo e repetir: 1h50 depois do início, ou 1h45 depois de um sinal ao vivo.
        wait = timedelta(minutes=110) if b['kickoff_utc'] else timedelta(minutes=105)
        if wait <= now - ref <= timedelta(days=max_age_days):
            out.setdefault(b['match_id'], {'teams': b['teams'], 'date_utc': b['date_utc'], 'kickoff_utc': b['kickoff_utc'], 'league': b.get('league'),
                                             'phase': b.get('phase'), 'minute': b.get('minute'),
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


def run(fetch=request_json, now=None, token=None, *, after_bsd=None):
    observed_clock = (lambda stamp=now: stamp) if now is not None else (lambda: datetime.now(UTC))
    now = observed_clock()
    token = (token if token is not None else os.environ.get('BSD_API_KEY', '')).strip()
    if not token:
        print('BSD_NOT_CONFIGURED')
        return None
    path = data_dir() / 'results_bsd.json'
    state = load_result_state(path)
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
            if isinstance(ev, dict) and type(ev.get('id')) is int and ev['id'] not in seen_ids:
                seen_ids.add(ev.get('id'))
                unique.append(ev)
        # Preserve contradictory v2 observations for identity checks before establishing a first binding.
        conflicting = {}
        found, near = match_events(targets, events, aliases, conflicting=conflicting)
        if capped:
            found = {mid: row for mid, row in found.items() if row['identity_method'] != BSD_COMPETITION_IDENTITY}
            conflicting = {}
        capture_result_versions(state, found, events, observed_clock(), conflicting=conflicting)
        events = unique
        state['near_misses'] = {m: n for m, n in {**state.get('near_misses', {}), **near}.items() if m not in state['results']}
    events_seen = len(events)
    sample = sorted({'%s — %s' % (name_of(e, 'home_team'), name_of(e, 'away_team')) for e in events})[:15]
    state['last_run_utc'] = now.isoformat(timespec='seconds')
    state['stats'] = {'targets': len(targets), 'matched_this_run': len([m for m in targets if m in state['results']]),
                      'requests': requests, 'events_seen': events_seen, 'page_cap_reached': capped,
                      'per_day': per_day, 'event_names_sample': sample}
    save_result_state(path, state)
    print('BSD_OK', json.dumps(state['stats']))
    # Optional tail only after normal queries and their saved results.
    if after_bsd is not None:
        try:
            after_bsd()
        except Exception:
            print('BSD_RECOVERY_TAIL_SKIPPED')
    return state


FAILED_EXIT = 3     # falha efetiva da API: o passo fica assinalado (continue-on-error no workflow), a recolha continua


def main():
    try:
        run()
    except BSDError as exc:
        print('BSD_FAILED', exc)     # a recolha e o relatório continuam sem a BSD
        sys.exit(FAILED_EXIT)
    sys.exit(0)                      # consulta feita, omitida pelo intervalo mínimo ou sem chave


if __name__ == '__main__':
    main()
