"""Relatório ScanGoal: ROI por grupo com regras fixas. Só contas; sem LLM, sem rede.

Regra principal: 1 unidade na seleção indicada, à odd do PRIMEIRO sinal de cada (jogo, tipo, mercado, seleção).
Sinal = primeira versão da mensagem; resultado = FT em qualquer versão de qualquer mensagem do mesmo jogo.
Secção "questions": cortes escritos ANTES de haver dados (exploratórios; nunca contam como conclusão).
"""
import collections, csv, json, math, pathlib, re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from . import brain, certification, details_hl, insights, llm_eval, polymarket_alerts
from .eligibility import eligible, exclusion_reasons, game_identity
from . import chronology, match_identity, opportunity_forward, opportunity_results
from .parser import MIN_ODDS, URL, parse, selection_1x2, settle
from .store import data_dir, message_rows

HAIRCUTS = (0.0, 0.02, 0.05)
LAST = {}                     # sinais da última execução de build(), para o módulo llm
MIN_SETTLED = 300
NO_SOURCE_AFTER_DAYS = 3      # sem resultado ao fim deste prazo: as fontes já não são consultadas
BURST = 5                     # sinais Betfair no mesmo segundo: lote (ex.: ao subscrever); a hora do 1.º sinal não está provada
GOALS_PER_MIN = 2.7 / 95      # jogo médio, para a taxa habitual de "mais um golo"
SIGNAL_PART = lambda text: re.split(r'\n(?:🏁 FT|⚠️)', text)[0].strip()   # sem o resultado nem o aviso final, que o bot desloca ao editar


def key_of(p):
    return (p.get('match_id'), p['type'], p.get('market'), p.get('selection'))


def load(chronology=None, *, result_observations=None, as_of=None):
    """-> (mensagens na 1.ª versão, FT por jogo, marcas ✅/❌ do bot por aposta, notas de fim de jogo, auditoria).

    chronology (opcional, PR 3): dicionário preenchido com a PRIMEIRA recolha (collected_at_utc) em que o sistema viu cada
    resultado do bot, cada marca ✅/❌ e cada nota de fim de jogo, para o valor final usado. Não altera nenhum outro retorno.
    result_observations (opcional): versões FT por ID de jogo, em paralelo com o retorno operacional existente.
    """
    first, ft, marks, notes, goals = {}, {}, {}, [], {}
    seen_ft, seen_mark, mark_versions = {}, {}, {}
    embedded, note_observations = {}, []
    seen_any = {}                                  # jogo -> primeira recolha de QUALQUER resultado do bot (mesmo corrigido depois)
    audit = {'versions': 0, 'edits_changing_signal': 0, 'bot_mark_disagreements': 0, 'last_collected_at_utc': None}
    path = data_dir() / 'messages.jsonl'
    if not path.exists():
        return [], ft, marks, notes, audit
    for _, r in message_rows(path):
        got = r.get('collected_at_utc')
        if as_of is not None:
            observed = opportunity_results.utc(got)
            published = opportunity_results.utc(r.get('edit_date_utc') or r.get('date_utc'))
            if observed is None or published is None or max(observed, published) > as_of:
                continue
        audit['versions'] += 1
        audit['last_collected_at_utc'] = max(audit['last_collected_at_utc'] or '', r.get('collected_at_utc') or '')
        p = parse(r['text'])
        links = [*URL.finditer(r['text'])]
        for button in r.get('buttons') or ():
            if isinstance(button, (list, tuple)) and len(button) == 2 and isinstance(button[1], str):
                links.extend(URL.finditer(button[1]))
        ids = {link[1] for link in links}
        p['explicit_match_ids'] = sorted(ids)
        if len(ids) == 1:
            p['match_id'] = next(iter(ids))
        elif len(ids) > 1:
            p['match_id'] = None
        link = links[0][0] if len(ids) == 1 else None
        if p['type'] == 'WEAKENED_LINEUP' and p.get('teams'):   # mensagem sem ligação: identidade pelas equipas e pelo dia
            p['match_id'] = 'wl-%s-%s' % (re.sub(r'[^a-z0-9]+', '-', ' vs '.join(p['teams']).lower()).strip('-'), r['date_utc'][:10])
            if p.get('lead_from_text') is not None:
                p['kickoff_utc'] = (datetime.fromisoformat(r['date_utc']) + timedelta(minutes=p['lead_from_text'])).isoformat(timespec='minutes')
        if p['type'] == 'FULL_TIME_NOTE' and p.get('ft'):
            observation = opportunity_results.collected(
                'telegram_note', r['message_id'], p['ft'], got,
                published=r.get('edit_date_utc') or r.get('date_utc'), exact=r.get('receipt_version') == 1,
                fixture_id=p.get('match_id'), link=link,
            )
            prior_ids = first.get(r['message_id'], {}).get('explicit_match_ids')
            if len(ids) <= 1 and (not prior_ids or set(prior_ids) == ids):
                note_observations.append((p, observation.published_at, observation))
        elif p.get('ft') and p.get('match_id'):
            previous = first.get(r['message_id'])
            if previous:
                old, revised = match_identity.components(previous), match_identity.components(p)
                if previous.get('match_id') != p['match_id'] or any(
                        left and right and left != right for left, right in zip(old, revised)):
                    audit['edits_changing_signal'] += 1
                    continue
            retained_kickoff = p.get('kickoff_utc') or (previous or {}).get('kickoff_utc')
            if retained_kickoff is None and previous:
                retained = settlement_kickoff(previous, None)
                retained_kickoff = retained.isoformat() if retained is not None else None
            observation = opportunity_results.collected(
                'telegram', r['message_id'], p['ft'], got,
                published=r.get('edit_date_utc') or r.get('date_utc'), kickoff=retained_kickoff,
                exact=r.get('receipt_version') == 1, fixture_id=p['match_id'], link=link,
            )
            embedded.setdefault(p['match_id'], []).append(observation)
            cutoff = as_of if as_of is not None else observation.available_at
            valid = opportunity_results.ft_time_valid(observation.kickoff, observation.observed_at,
                                                       observation.available_at, observation.published_at, cutoff)
            if valid:
                ft[p['match_id']] = p['ft']
                seen_ft.setdefault((p['match_id'], tuple(p['ft'])), got)
                seen_any.setdefault(p['match_id'], got)
            elif observation.kickoff is not None:
                audit['impossible_ft_observations'] = audit.get('impossible_ft_observations', 0) + 1
            if p.get('bet') and observation.score is not None:
                if p.get('bot_mark') is not None:
                    mark_versions.setdefault(key_of(p), []).append((observation, p['bot_mark']))
                    if valid:
                        marks[key_of(p)] = p['bot_mark']
                        seen_mark.setdefault((key_of(p), p['bot_mark']), got)
                own = settle(p['market'], p['selection'], p['ft'], p.get('teams'))
                if valid and own is not None and own != 0 and p['bot_mark'] is not None and (own > 0) != p['bot_mark']:
                    audit['bot_mark_disagreements'] += 1
        mid = r['message_id']
        if p.get('goals'):
            goals[mid] = p['goals']
        if mid in first:
            if SIGNAL_PART(r['text']) != first[mid]['signal_text']:
                audit['edits_changing_signal'] += 1  # o bot alterou o sinal depois de publicado
        else:  # ficheiro append-only: a primeira linha de cada id é a versão original
            first[mid] = dict(p, message_id=mid, date_utc=r['date_utc'], had_ft_at_first_sight='ft' in p, edited_at_first_sight=bool(r.get('edit_date_utc')),
                              signal_text=SIGNAL_PART(r['text']), head=r['text'].strip().splitlines()[0][:60], collected_at_utc=got)
    # Só sinais disponíveis na mesma leitura podem estabelecer um lote em conjunto.
    same_second = collections.Counter((m['date_utc'], m['collected_at_utc'])
                                      for m in first.values() if m['type'] == 'BETFAIR')
    for mid, m in first.items():
        m['burst'] = m['type'] == 'BETFAIR' and same_second[m['date_utc'], m['collected_at_utc']] >= BURST
        m['goals'] = goals.get(mid)
    if note_observations:
        notes = link_ft_notes(first.values(), note_observations, as_of=as_of)
    if chronology is not None:
        chronology['ft'] = {m: seen_ft.get((m, tuple(v))) for m, v in ft.items()}
        chronology['marks'] = {k: seen_mark.get((k, v)) for k, v in marks.items()}
        chronology['telegram'] = embedded
        chronology['mark_versions'] = mark_versions
        chronology['ft_any'] = dict(seen_any)
    if result_observations is not None:
        for mid, versions in embedded.items():
            result_observations.setdefault(mid, []).extend(versions)
        for version in notes:
            result_observations.setdefault(version.fixture_id, []).append(version)
    return [first[k] for k in sorted(first)], ft, marks, notes, audit


def link_ft_notes(messages, notes, *, as_of=None):
    """Attribute each standalone FT note to a single fixture provable when it was read."""
    by_teams = collections.defaultdict(list)
    for message in messages:
        if not message.get('match_id'):
            continue
        kickoff, home, away, league = match_identity.components(message)
        seen = match_identity.signal_seen(message)
        if kickoff and home and away and league and seen:
            by_teams[home, away].append(((kickoff, league), seen, message['match_id']))

    linked = []
    for note, published, observation in notes:
        if published is None or observation.available_at is None or len(note.get('explicit_match_ids', ())) > 1:
            continue
        home, away = map(match_identity.normalized, note['teams'])
        fixtures = collections.defaultdict(set)
        for (kickoff, league), seen, mid in by_teams[home, away]:
            if note.get('match_id') and note['match_id'] != mid:
                continue
            league_note = match_identity.normalized(note.get('league'))
            if ((not league_note or league_note == league) and seen <= published
                    and published - kickoff <= opportunity_forward.MAX_UNRESOLVED
                    and opportunity_results.ft_time_valid(kickoff, observation.observed_at, observation.available_at,
                                                          published, as_of if as_of is not None else observation.available_at)):
                fixtures[kickoff, league].add(mid)
        if len(fixtures) == 1:
            (kickoff, _), ids = next(iter(fixtures.items()))
            for mid in ids:
                linked.append(replace(observation, kickoff=kickoff, fixture_id=mid))
    return linked


def lead_minutes(m):
    if not m.get('kickoff_utc'):
        return None
    ko = datetime.fromisoformat(m['kickoff_utc'])
    return round((ko - datetime.fromisoformat(m['date_utc'])).total_seconds() / 60)


def note_match(b, notes):
    """A note can only settle the fixture proved when the note was observed."""
    kickoff = chronology.kickoff_time(b)
    matching = [row for row in notes if isinstance(row, opportunity_results.Observation)
                and row.fixture_id == b.get('match_id') and row.kickoff == kickoff]
    return min(matching, key=lambda row: row.available_at, default=None)


def note_ft(b, notes):
    """Resultado vindo de uma nota '🏁 Full time: A x:y B' posterior ao sinal, só com nomes de equipa iguais."""
    hit = note_match(b, notes)
    return list(hit.score) if hit else None


def external_matches(bet, row):
    """Verify the source-specific fixture association stored with the observation."""
    from .results_bsd import norm_team, time_ok

    if not row or row.get('fixture_id', bet['match_id']) != bet['match_id']:
        return False
    if row.get('provider') == 'manual':
        return row.get('fixture_id') == bet['match_id'] and bool(row.get('source'))
    names = row.get('target_teams') or (row.get('home'), row.get('away'))
    expected = [norm_team(name) for name in bet.get('teams') or ()]
    if len(expected) != 2 or not all(expected) or list(map(norm_team, names)) != expected:
        return False
    league = norm_team(row.get('target_league') or row.get('league'))
    return (bool(league) and league == norm_team(bet.get('league'))
            and time_ok(bet, chronology.parse_utc(row.get('event_date'))))


def settlement_kickoff(bet, external, fixture_times=()):
    kickoff = chronology.kickoff_time(bet)
    if kickoff is not None:
        return kickoff
    if len(fixture_times) == 1:
        return next(iter(fixture_times))
    if external:
        return chronology.parse_utc(external.get('event_date'))
    if (bet.get('phase') == 'live' and type(bet.get('minute')) is int
            and 0 <= bet['minute'] <= 120 and not any(bet.get(flag) for flag in ('had_ft_at_first_sight', 'edited_at_first_sight', 'burst'))):
        # An original in-play message bounds the latest possible start time.
        sent = chronology.signal_time(bet)
        return sent - timedelta(minutes=max(0, bet['minute'] - 1)) if sent is not None else None
    return None


def _observation_times(embedded, notes, legacy_stamp, ext, observed):
    """Every instant at which a new result observation for this fixture became available."""
    times = set()
    for row in list(embedded) + list(notes):
        stamp = observed(row.get('known_utc')) if isinstance(row, dict) else getattr(row, 'available_at', None)
        if stamp is not None:
            times.add(stamp)
    if legacy_stamp is not None:
        times.add(legacy_stamp)
    if ext:
        stamp = observed(ext.get('available_at') or ext.get('known_utc') or ext.get('matched_at_utc'))
        if stamp is not None:
            times.add(stamp)
    return sorted(times)


def _payoff(bet, score):
    """Payoff of a priced or goal-rule bet for a regulation score; None where this rule does not settle it."""
    if score is None or bet['type'] == 'GOAL_BREWING':
        return None
    if bet['market'] is None:
        return 1.0 if sum(score) > sum(bet['score_at_signal']) else -1.0
    if bet['market'].strip().lower() == 'asian handicap' and bet['phase'] == 'live':
        return None
    return settle(bet['market'], bet['selection'], list(score), bet['teams'])


SETTLED_BY_SCORE = ('resultado', 'regra: mais um golo')


def settlement_events(bet, as_of):
    """Payoff versions of one bet, each with the instant it became known, using only observations up to `as_of`.

    A transition to None means the result became contradictory or left quarantine undecided: the stake is reserved
    again until a payoff is known. Returns None when this bet is settled by a source without versions (a bot mark,
    Highlightly goal minutes): the caller then uses its single proven settlement instant.
    """
    decision = chronology.decision_time(bet)
    cuts = bet.get('_result_cuts')
    if decision is None or not cuts:
        return None
    current = bet['w'] if (bet['settled_by'] or '').startswith(SETTLED_BY_SCORE) else None
    if bet['w'] is not None and current is None:
        return None
    resolve_score, times = cuts
    events, last = [], None
    for cut in times:
        if cut <= decision or cut > as_of:
            continue
        f, _, _, _, _, conflict, _ = resolve_score(cut)
        payoff = _payoff(bet, f) if f and not conflict else None
        if payoff != last:
            events.append((cut, payoff))
            last = payoff
    if last != current:
        return None   # the walk does not reproduce the payoff the report uses: never invent a history
    return events


def build_bets(msgs, ft, marks=None, notes=(), strict=True, bsd=None, details=None, not_covered=(),
               chronology=None, group_aliases=True, now=None):
    """strict=True: regra do ROI (exclui sinais vistos já com resultado). strict=False: todos os sinais, para a taxa de acerto.

    PR 3: cada aposta guarda signal_seen_utc (primeira recolha da mensagem do sinal) e result_known_utc/result_known_source
    (primeira observação, pelo sistema, do resultado efetivamente usado). Sem prova, ficam None (hora desconhecida).
    """
    chronology = chronology or {}
    fixtures = collections.defaultdict(set)
    for m in msgs:
        if m.get('match_id'):
            fixtures[m['match_id']].add(match_identity.components(m))
    ambiguous = {mid for mid, parts in fixtures.items()
                 if any(len({p[index] for p in parts if p[index] is not None}) > 1 for index in range(4))}
    out, dead = {}, set()
    for m in msgs:
        no_odds = str(m.get('type')).endswith('_NO_ODDS') or (m.get('type') == 'GOAL_BREWING' and m.get('window'))
        if not (m.get('bet') or (no_odds and not strict)):
            continue
        if m.get('bet') and (not m.get('match_id') or not math.isfinite(m['odds']) or m['odds'] < MIN_ODDS):
            continue
        k = key_of(m) + ((m['minute'],) if m['type'] == 'GOAL_BREWING' else ())   # cada aviso de golo é medido à parte
        identity = {name: m[name] for name in ('canonical_match_id', 'match_identity_exclusion') if name in m}
        fixture = (identity.get('canonical_match_id') if group_aliases else None) or m.get('match_id')
        cohort = (fixture, *k[1:], identity.get('match_identity_exclusion'))
        if strict and (cohort in dead or (cohort not in out and not provenance_ok(m))):
            dead.add(cohort)
            continue
        b = out.get(cohort)
        if b is None:
            b = out[cohort] = {f: m.get(f) for f in ('match_id', 'type', 'phase', 'period', 'market_scope', 'market', 'selection', 'odds', 'teams', 'date_utc', 'kickoff_utc',
                                                 'minute', 'share', 'mkt_volume', 'message_id', 'score_at_signal', 'had_ft_at_first_sight', 'league',
                                                 'edited_at_first_sight', 'burst', 'rhythm', 'bot_over_pct', 'goals', 'window', 'bot_prob', 'usual_prob', 'total', 'n_bets', 'sel_volume')}
            b.update(n_signals=0, max_mult=1, odds_at_x4=None, odds_last=m.get('odds'), lead_min=lead_minutes(m), key=k, share_last=m.get('share'), x4_min=None, steps=[],
                     signal_seen_utc=m.get('collected_at_utc'))
            b.update(identity)
        b['n_signals'] += 1
        if m['type'] == 'BETFAIR':
            b['steps'].append([m.get('mult', 1), m['odds']])
        b['odds_last'], b['share_last'] = m.get('odds'), m.get('share')
        b['max_mult'] = max(b['max_mult'], m.get('mult', 1))
        if m.get('mult', 1) >= 4 and b['odds_at_x4'] is None:
            b['odds_at_x4'] = m['odds']
            b['x4_min'] = round((datetime.fromisoformat(m['date_utc']) - datetime.fromisoformat(b['date_utc'])).total_seconds() / 60)
    bets = sorted(out.values(), key=lambda b: (b['date_utc'], b['message_id']))
    note_versions = collections.defaultdict(list)
    for row in notes:
        if isinstance(row, opportunity_results.Observation) and row.source == 'telegram_note':
            note_versions[row.fixture_id].append(row)
    now = now or datetime.now(timezone.utc)
    for b in bets:
        b['layout_phase'], b['after_kickoff_min'] = b['phase'], None
        if b['type'] == 'STAKE_LOAD' and b['kickoff_utc']:
            b['after_kickoff_min'] = -lead_minutes(b)
            if b['edited_at_first_sight']:   # só vimos a versão editada, em que o bot reescreve as linhas das equipas: a hora decide a fase
                b['phase'] = 'pre' if b['after_kickoff_min'] < 0 else 'live'
        b['in_roi'] = provenance_ok(b)
        b['evidence_eligible'] = eligible(b)
        b['evidence_exclusions'] = exclusion_reasons(b)
        b['group'] = group(b)
        mid = b['match_id']
        ext = (bsd or {}).get(mid)
        ext = ext if mid not in ambiguous and external_matches(b, ext) else None
        det = (details or {}).get(mid) if mid not in ambiguous else None
        kickoff = settlement_kickoff(b, ext or (det if external_matches(b, det) else None),
                                     {parts[0] for parts in fixtures[mid] if parts[0] is not None})
        observed = opportunity_results.utc
        embedded = (chronology.get('telegram') or {}).get(mid, ()) if mid not in ambiguous else ()
        notes_here = [row for row in note_versions[mid] if row.kickoff == kickoff] if mid not in ambiguous else []
        legacy_stamp = observed((chronology.get('ft') or {}).get(mid))

        def resolve_score(cut, embedded=embedded, notes_here=notes_here, legacy_stamp=legacy_stamp, mid=mid,
                          kickoff=kickoff, ext=ext):
            """The score known at `cut`, using only observations available by then (the same rules for every cut)."""
            proof = opportunity_results.resolve(embedded, kickoff=kickoff, as_of=cut)
            f, source, known = proof.score, 'bot_message', proof.available_at
            raw, first_seen, conflict = proof.observed_at, proof.first_available_at, proof.conflict
            if not f and not conflict and mid not in ambiguous:
                proof = opportunity_results.resolve(notes_here, kickoff=kickoff, as_of=cut)
                f, source, known = proof.score, 'bot_full_time_note', proof.available_at
                raw, first_seen, conflict = proof.observed_at, proof.first_available_at, proof.conflict
            if not f and not conflict and mid not in ambiguous and not embedded:
                legacy = opportunity_results.score_pair(ft.get(mid))
                if legacy and opportunity_results.ft_time_valid(kickoff, legacy_stamp, legacy_stamp, None, cut):
                    f, source, known, raw, first_seen = legacy, 'bot_message', legacy_stamp, legacy_stamp, legacy_stamp
            from_bsd = False
            if ext:
                stamp = observed(ext.get('available_at') or ext.get('known_utc') or ext.get('matched_at_utc'))
                seen = observed(ext.get('observed_at')) if 'observed_at' in ext else stamp
                score = opportunity_results.score_pair(ext.get('ft'))
                if score and opportunity_results.ft_time_valid(kickoff, seen, stamp, None, cut):
                    first_seen = min(first_seen, stamp) if first_seen is not None else stamp
                    if not f and not conflict:
                        f, source, known, raw, from_bsd = score, ext.get('provider', 'BSD'), stamp, seen, True
            return f, source, known, raw, first_seen, conflict, from_bsd

        f, source, known, raw, first_seen, conflict, from_bsd = resolve_score(now)
        if strict:
            b['_result_cuts'] = (resolve_score, _observation_times(embedded, notes_here, legacy_stamp, ext, observed))
        f = list(f) if f is not None else None
        b['ft'], b['w'], b['settled_by'] = f, None, None
        b['result_known_utc'], b['result_known_source'] = None, None
        b['result_available_utc'] = known.isoformat() if known is not None and f else None
        b['match_result_first_seen_utc'] = first_seen.isoformat() if first_seen is not None else None
        b['result_quarantined'] = conflict
        b['result_quarantined_utc'] = known.isoformat() if conflict and known is not None else None
        known = raw.isoformat() if raw is not None and f else None
        ref = datetime.fromisoformat(b['kickoff_utc'] or b['date_utc'])
        b['no_source'] = not f and (now - ref > timedelta(days=NO_SOURCE_AFTER_DAYS) or b['match_id'] in not_covered)
        fetched = observed(det.get('fetched_at_utc')) if det else None
        if not (not conflict and det and det.get('consistent') and (external_matches(b, det) or
                (ext and ext.get('provider') == 'HL' and ext.get('bsd_id') == det.get('hl_id') and ext.get('bsd_id') is not None))
                and opportunity_results.ft_time_valid(kickoff, fetched, fetched, None, now)):
            det = None
        b['next_goal_min'] = next_goal(b, det)
        if b['type'] == 'GOAL_BREWING':   # acerta se o golo seguinte ao sinal cair dentro da janela indicada; só com minutos de fonte externa
            if det:
                played = b['minute']
                prefix = [goal for goal in det['goals'] if goal[0] < played]
                if len(prefix) > sum(b['score_at_signal']):
                    continue
                nxt = det['goals'][sum(b['score_at_signal']):][:1]
                b['ft'], b['settled_by'] = det['final'], 'minutos dos golos (Highlightly)'
                b['w'] = 1.0 if nxt and nxt[0][0] <= b['minute'] + b['window'] else -1.0
                b['result_known_utc'], b['result_known_source'] = det.get('fetched_at_utc'), 'HL goal minutes'
                b['result_available_utc'] = det.get('fetched_at_utc')
            continue
        if not f:
            continue
        if b['market'] is None:  # sinal sem odd: acerta se houve pelo menos mais um golo depois do sinal
            b['w'], b['settled_by'] = (1.0 if sum(f) > sum(b['score_at_signal']) else -1.0), 'regra: mais um golo'
            b['result_known_utc'], b['result_known_source'] = known, source
            continue
        own = settle(b['market'], b['selection'], f, b['teams'])
        invalid = own is None and b['market'].strip().casefold() in {
            'asian total', '1x2', 'both teams to score', 'double chance', 'asian handicap'}
        if b['market'].strip().lower() == 'asian handicap' and b['phase'] == 'live':
            own = None   # ao vivo o handicap conta a partir do resultado no momento da aposta: só a marca do bot decide
        if own is not None:
            b['w'], b['settled_by'] = own, ('resultado ' + bsd[b['match_id']].get('provider', 'BSD')) if from_bsd else 'resultado'
            b['result_known_utc'], b['result_known_source'] = known, source
            if from_bsd and source == 'HL aos 90 min':
                b['result_provenance'] = {'source': 'highlightly_detail', 'provider_id': ext.get('hl_id'),
                                          'fixture_id': mid, 'kickoff_utc': ext.get('event_date'),
                                          'league': ext.get('league'), 'link': ext.get('link'),
                                          'observed_at': known, 'available_at': b['result_available_utc']}
        elif not invalid:
            valid_marks = [(row.available_at, mark) for row, mark in (chronology.get('mark_versions') or {}).get(b['key'], ())
                           if row.score == tuple(f) and row.fixture_id == mid and row.published_at is not None
                           and opportunity_results.ft_time_valid(kickoff, row.observed_at, row.available_at, row.published_at, now)]
            if valid_marks:
                latest = max(at for at, _ in valid_marks)
                values = {mark for at, mark in valid_marks if at == latest}
                if len(values) == 1:
                    b['w'], b['settled_by'] = (1.0 if values.pop() else -1.0), 'marca do bot'
                    b['result_known_utc'], b['result_known_source'] = latest.isoformat(), 'bot_mark'
                    b['result_available_utc'] = latest.isoformat()
    return bets


def match_first_seen(b, notes, chronology):
    """Primeira vez que o sistema viu QUALQUER resultado deste jogo, por qualquer fonte (PR 3, revisão #5911603577).

    Fontes: versão do bot com resultado (mesmo que corrigido depois), nota de fim de jogo, Highlightly, BSD, resultado manual,
    minutos dos golos. Não depende de que apostas existem, por isso uma aposta nova nunca muda este instante. Uma fonte sem
    hora provada (por exemplo, manual sem observed_utc) torna-o desconhecido (None): fail-closed.
    """
    mid, stamps, unknown = b['match_id'], [], False
    if mid in (chronology.get('ft_any') or {}):
        stamps.append(chronology['ft_any'][mid])
    hit = note_match(b, notes)
    if hit:
        stamps.append(hit.available_at.isoformat())
    stamps += (chronology.get('external') or {}).get(mid, [])
    if mid in (chronology.get('details') or {}):
        stamps.append(chronology['details'][mid])
    parsed = [_utc(x) for x in stamps]
    if not parsed or any(x is None for x in parsed):
        return None
    return min(parsed).isoformat(timespec='seconds')


def _utc(value):
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else None


def provenance_ok(m):
    """Regra do ROI: só sinais vistos na versão original, antes do resultado, e fora de lotes."""
    return not (m['had_ft_at_first_sight'] or m['edited_at_first_sight'] or m['burst'])


def next_goal(b, det=None):
    """Minutos entre o sinal e o golo seguinte: pela fonte externa quando existe; senão pela lista do bot, só se bater certo."""
    if det and b.get('score_at_signal') and b.get('minute') is not None:
        nxt = det['goals'][sum(b['score_at_signal']):][:1]
        return max(0, nxt[0][0] - b['minute']) if nxt else None
    g, s = b.get('goals'), b.get('score_at_signal')
    if not g or not s or b.get('minute') is None or not b['ft']:
        return None
    ok = g[0][1] + g[0][2] == sum(s) + 1 and g[-1][1:] == list(b['ft']) and g[0][0] >= b['minute']
    return g[0][0] - b['minute'] if ok else None


def baseline_more_goals(minute):
    """Probabilidade habitual de haver pelo menos mais um golo até ao fim, num jogo médio, a partir do minuto dado."""
    return 1 - math.exp(-GOALS_PER_MIN * max(1, 95 - (minute or 0)))


def group(b):
    if b['type'] == 'BETFAIR':
        return 'BETFAIR/%s/%s' % (b['phase'], 'mkt>=10K' if b['mkt_volume'] >= 10000 else 'mkt<10K')
    if b['type'] in ('STAKE_LOAD', 'POLYMARKET'):
        return '%s/%s' % (b['type'], b['phase'])

    return b['type']


def label(b):
    if b['type'] == 'GOAL_BREWING':
        return 'golo nos próximos %d min' % b['window']
    if b['market'] is None:
        return 'mais um golo (sem odd)'
    if b['market'].strip().casefold() == '1x2':
        t = b['teams'] or ['1', '2']
        side = selection_1x2(b['selection'], t)
        return {'1': t[0], '2': t[-1], 'X': 'Empate'}.get(side, b['selection'])
    return b['selection'] if b['market'] == 'Asian Total' else '%s: %s' % (b['market'], b['selection'])


def pl(w, odds, hc=0.0):
    return w * (odds * (1 - hc) - 1) if w > 0 else w


def stats(rows, odds_key='odds'):
    """rows: apostas liquidadas. ROI médio por aposta e IC 95 % (aprox. normal).

    O erro-padrão agrupa as apostas por jogo: várias apostas no mesmo jogo não são independentes.
    """
    n = len(rows)
    if not n:
        return None
    res = {}
    for hc in HAIRCUTS:
        xs = [pl(r['w'], r[odds_key], hc) for r in rows]
        mean = sum(xs) / n
        clusters = {}
        for r, x in zip(rows, xs):
            clusters[r['match_id']] = clusters.get(r['match_id'], 0.0) + (x - mean)
        g = len(clusters)
        if g > 1:
            half = 1.96 * math.sqrt(g / (g - 1) * sum(v * v for v in clusters.values())) / n
            ci = [round(mean - half, 4), round(mean + half, 4)]
        else:
            ci = None
        res['odd-%d%%' % round(hc * 100)] = {'roi': round(mean, 4), 'ci95': ci}
    return res


def block(rows, odds_key='odds'):
    settled = [r for r in rows if r['w'] is not None]
    n = len(settled)
    cum, s = [], 0.0
    for r in settled:
        s += pl(r['w'], r[odds_key])
        cum.append(round(s, 2))
    return {'bets': len(rows), 'settled': n, 'matches': len({r['match_id'] for r in settled}), 'wins': sum(1 for r in settled if r['w'] > 0), 'pushes': sum(1 for r in settled if r['w'] == 0),
            'units': round(s, 2), 'avg_odds': round(sum(r[odds_key] for r in settled) / n, 3) if n else None,
            'roi': stats(settled, odds_key), 'cumulative': cum[-400:],
            'conclusive': n >= MIN_SETTLED, 'note': 'ok' if n >= MIN_SETTLED else 'sem conclusão (<%d liquidadas)' % MIN_SETTLED}


wilson = insights.wilson


def accuracy(signals, now=None):
    """Taxa de acerto por tipo, com TODOS os sinais (inclui os vistos já com resultado e os sem odd)."""
    now = now or datetime.now(timezone.utc)
    by = collections.OrderedDict()
    for b in signals:
        name = b['type'] if b['type'] != 'BETFAIR' else 'BETFAIR/%s' % b['phase']
        by.setdefault(name, []).append(b)
    out = collections.OrderedDict()
    for name, rows in sorted(by.items()):
        done = [r for r in rows if r['w'] is not None]
        hits, miss = sum(1 for r in done if r['w'] > 0), sum(1 for r in done if r['w'] < 0)
        eh, em = insights.effective(done)   # meia vitória = meio acerto; meia derrota = meia falha
        vh, vm, vbe, games = insights.clustered(done)   # um voto por jogo, para a evidência
        priced = [r for r in done if r['odds'] and r['w'] != 0]
        no_odds = [r for r in done if not r['odds'] and r.get('minute') is not None]
        base = round(sum(r['usual_prob'] / 100 if r.get('usual_prob') else baseline_more_goals(r['minute']) for r in no_odds) / len(no_odds), 4) if no_odds and not priced else None
        out[name] = {'signals': len(rows), 'with_result': len(done), 'hits': hits, 'misses': miss, 'pushes': len(done) - hits - miss,
                     'eff_hits': eh, 'eff_misses': em, 'vote_hits': round(vh, 2), 'vote_misses': round(vm, 2), 'vote_break_even': round(vbe, 4) if vbe else None, 'games': games, 'no_source': sum(1 for r in rows if r['no_source']),
                     'unsettleable': sum(1 for r in rows if r['ft'] and r['w'] is None),
                     'hit_rate': round(eh / (eh + em), 4) if eh + em else None, 'ci95': wilson(eh, eh + em),
                     'avg_odds': round(sum(r['odds'] for r in priced) / len(priced), 3) if priced else None,
                     'break_even': round(sum(1 / r['odds'] for r in priced) / len(priced), 4) if priced else base, 'baseline': base is not None,
                     'roi_announced_odds': round(sum(pl(r['w'], r['odds']) for r in done if r['odds']) / len([r for r in done if r['odds']]), 4)
                     if any(r['odds'] for r in done) else None,
                     'seen_late': sum(1 for r in done if not r['in_roi']),
                     'by_bot_mark': sum(1 for r in done if r['settled_by'] == 'marca do bot')}
        days = max(1.0, (now - datetime.fromisoformat(min(r['date_utc'] for r in rows))).total_seconds() / 86400)
        rate = (hits + miss) / days
        out[name]['per_day'] = round(rate, 1)
        out[name]['days_to_hint'] = max(0, math.ceil((30 - hits - miss) / rate)) if rate else None
        out[name]['days_to_conclusion'] = max(0, math.ceil((MIN_SETTLED - hits - miss) / rate)) if rate else None
    return out


# Perguntas pré-definidas (11/11/2026). Exploratórias: servem para decidir o que testar em dados novos.
def questions(bets):
    bf = [b for b in bets if b['type'] == 'BETFAIR']
    pre = [b for b in bf if b['phase'] == 'pre']
    q = collections.OrderedDict()
    q['Q1 Betfair: só apostas que chegaram a ×4, à odd do sinal ×4'] = block([b for b in bf if b['odds_at_x4']], 'odds_at_x4')
    q['Q2 Betfair: sem reforço (sinal único)'] = block([b for b in bf if b['max_mult'] == 1])
    for lo, hi in ((1.10, 1.50), (1.50, 2.50), (2.50, 99)):
        q['Q3 Betfair: odd [%.2f; %s)' % (lo, '%.2f' % hi if hi < 99 else '∞')] = block([b for b in bf if lo <= b['odds'] < hi])
    q['Q4 Betfair: quota de mercado >= 80 %'] = block([b for b in bf if b['share'] >= 80])
    q['Q5 Betfair pré-jogo: sinal a mais de 60 min do início'] = block([b for b in pre if (b['lead_min'] or 0) > 60])
    q['Q5 Betfair pré-jogo: sinal a 60 min ou menos'] = block([b for b in pre if b['lead_min'] is not None and b['lead_min'] <= 60])
    og = [b for b in bets if b['type'] == 'OPEN_GAME']
    q['Q6 Open game: sinal antes do minuto 46'] = block([b for b in og if b['minute'] < 46])
    q['Q6 Open game: sinal do minuto 46 em diante'] = block([b for b in og if b['minute'] >= 46])
    q['Q7 Todas as apostas (referência)'] = block(bets)
    for v in q.values():
        v.pop('cumulative')
    return q


def timeline(bets):
    """Saldo acumulado (unidades) das apostas liquidadas, por ordem do sinal. Últimas 600."""
    out, total = [], 0.0
    for b in bets:
        if b['w'] is None:
            continue
        gain = pl(b['w'], b['odds'])
        total += gain
        out.append({'d': b['date_utc'], 'u': round(total, 2), 'g': round(gain, 2), 'w': b['w'], 't': b['type'] if b['type'] != 'BETFAIR' else 'BETFAIR/%s' % b['phase'], 'm': ' — '.join(b['teams'] or []), 's': label(b), 'o': b['odds']})
    return out[-600:]


def portfolio(bets, as_of):
    """Unit exposure and, per simulated position, every payoff version with the instant it became known.

    `s` lists [instant, payoff] transitions (payoff None: result contradictory or quarantined again), all strictly after
    the decision and no later than `as_of`; `z`/`w` are the payoff in force at `as_of` and the instant it took effect.
    Cutting a later report at an earlier instant reproduces the earlier report: no observation leaks backwards.
    """
    by_fixture = collections.Counter()
    entries = []
    proved = 0
    for b in bets:
        fixture = b.get('canonical_match_id')
        if fixture:
            by_fixture[fixture] += 1
        decision = chronology.decision_time(b)
        if decision is None or decision > as_of:
            continue
        events = settlement_events(b, as_of)
        if events is None:
            known = chronology.result_known(b)
            known = known if b['w'] is not None and known is not None and decision < known <= as_of else None
            events = [(known, b['w'])] if known else []
        last_at, last_w = events[-1] if events else (None, None)
        proved += last_w is not None
        entry = {'d': decision.isoformat(), 'z': last_at.isoformat() if last_w is not None else None,
                 'w': last_w, 'o': b['odds'],
                 't': b['type'] if b['type'] != 'BETFAIR' else 'BETFAIR/%s' % b['phase']}
        if len(events) > 1 or (events and last_w is None):
            entry['s'] = [[at.isoformat(), w] for at, w in events]
        entries.append(entry)
    return {'basis': 'one_advertised_unit_per_historical_bet',
            'total_bets': len(bets), 'settled_bets': sum(b['w'] is not None for b in bets),
            'committed_games': len(by_fixture), 'bets_with_fixture': sum(by_fixture.values()),
            'max_units_per_game': max(by_fixture.values(), default=0),
            'games_with_multiple_units': sum(units > 1 for units in by_fixture.values()),
            'timed_settlements': proved, 'payoff_revisions': sum(len(e.get('s', ())) - 1 for e in entries if e.get('s')),
            'priced_at_execution': None, 'known_costs': None,
            'entries': entries}


def match_stories(match_ids, msgs, ft, bsd, details):
    """Por jogo: todos os sinais por ordem (incluindo os que não são aposta), os golos com minuto e o resultado."""
    keep = ('selection', 'odds', 'mult', 'minute', 'score_at_signal', 'share', 'mkt_volume', 'bot_prob', 'usual_prob', 'window', 'rhythm', 'bot_over_pct', 'market', 'total')
    out = {}
    for m in msgs:
        mid = m.get('match_id')
        if mid not in match_ids or m.get('type') in (None, 'SERVICE'):
            continue
        s = out.setdefault(mid, {'teams': None, 'events': [], 'goals': None, 'goals_source': None, 'ft': None})
        s['teams'] = s['teams'] or m.get('teams')
        ev = {'d': m['date_utc'], 'k': m['type'], 'phase': m.get('phase')}
        ev.update({k: m[k] for k in keep if m.get(k) is not None})
        s['events'].append(ev)
        if m.get('goals') and not s['goals']:
            s['goals'], s['goals_source'] = [[g[0], '%d-%d' % (g[1], g[2])] for g in m['goals']], 'bot'
    for mid, s in out.items():
        det = (details or {}).get(mid)
        if det and det.get('consistent'):
            h = a = 0
            goals = []
            for minute, _, side in det['goals']:
                h, a = h + (side == 'home'), a + (side == 'away')
                goals.append([minute, '%d-%d' % (h, a)])
            s['goals'], s['goals_source'] = goals, 'Highlightly'
        s['ft'] = ft.get(mid) or (bsd.get(mid) or {}).get('ft') or (det or {}).get('final')
        s['events'] = s['events'][:25]
    return out


def unsettled(signals, now=None):
    """Jogos com sinais sem resultado há mais de 2 horas, com o motivo. Um por jogo."""
    now, out = now or datetime.now(timezone.utc), {}
    for b in signals:
        ref = datetime.fromisoformat(b['kickoff_utc'] or b['date_utc'])
        if b['w'] is not None or now - ref < timedelta(hours=2):
            continue
        why = 'mercado que o resultado final não decide' if b['ft'] else 'as fontes não cobrem este jogo' if b['no_source'] else 'à espera da fonte de resultados'
        if b['type'] == 'GOAL_BREWING' and not b['no_source']:
            why = 'à espera dos minutos dos golos'
        u = out.setdefault(b['match_id'], {'match_id': b['match_id'], 'match': ' — '.join(b['teams'] or []), 'league': b.get('league'), 'date_utc': b['date_utc'],
                                           'kickoff_utc': b['kickoff_utc'], 'why': why, 'signals': 0})
        u['signals'] += 1
    return sorted(out.values(), key=lambda u: u['date_utc'], reverse=True)[:60]


def daily(bets):
    by = collections.OrderedDict()
    for b in bets:
        d = by.setdefault(b['date_utc'][:10], {'bets': 0, 'settled': 0, 'wins': 0, 'units': 0.0})
        d['bets'] += 1
        if b['w'] is not None:
            d['settled'] += 1
            d['wins'] += b['w'] > 0
            d['units'] = round(d['units'] + pl(b['w'], b['odds']), 2)
    return [dict(v, day=k) for k, v in by.items()][-45:]


def write_csv(signals, path):
    """Todas as apostas e sinais, um por linha, para análise própria (Excel)."""
    cols = ['date_utc', 'type', 'phase', 'league', 'home', 'away', 'market', 'selection', 'odds', 'odds_last', 'n_signals', 'max_mult', 'share',
            'mkt_volume', 'minute', 'lead_min', 'rhythm', 'bot_over_pct', 'next_goal_min', 'after_kickoff_min', 'ft', 'result', 'units', 'settled_by', 'in_roi']
    with path.open('w', encoding='utf-8', newline='') as f:
        wr = csv.writer(f, delimiter=';')
        wr.writerow(cols)
        for b in signals:
            t = (b['teams'] or ['', '']) + ['']
            res = '' if b['w'] is None else 'ganha' if b['w'] > 0 else 'perdida' if b['w'] < 0 else 'devolvida'
            units = '' if b['w'] is None or not b['odds'] else ('%.2f' % pl(b['w'], b['odds'])).replace('.', ',')
            wr.writerow([b['date_utc'], b['type'], b['phase'], b.get('league') or '', t[0], t[1], b['market'] or '', b['selection'] or '',
                         str(b['odds'] or '').replace('.', ','), str(b['odds_last'] or '').replace('.', ','), b['n_signals'], b['max_mult'], b['share'] or '',
                         b['mkt_volume'] or '', b['minute'] if b['minute'] is not None else '', b['lead_min'] if b['lead_min'] is not None else '',
                         *['' if b[k] is None else b[k] for k in ('rhythm', 'bot_over_pct', 'next_goal_min', 'after_kickoff_min')],
                         '%d-%d' % tuple(b['ft']) if b['ft'] else '', res, units, b['settled_by'] or '', 'sim' if b['in_roi'] else 'não'])


def operational_evidence(signals, now=None):
    """Decision-facing statistics, independent of the legacy exploratory tables."""
    rows = [b for b in signals if eligible(b)]
    return {'scope': 'eligible_bets_only', 'accuracy': accuracy(rows, now),
            'regime': insights.regime(rows)}


def ledger_summary(validation):
    """Describe the externally witnessed prefix evaluated by this report."""
    validation['evaluated_ledger_head_sha256'] = validation.get('ledger_head_sha256')
    validation['evaluated_ledger_entries'] = validation.get('ledger_entries', 0)
    validation['newly_selected'] = []
    validation['candidates_meaning'] = 'promising hypotheses awaiting an independently witnessed selection'


def research_bets(signals, observations, now, *, bindings=None):
    """Settle FT-dependent research rows from the same versioned evidence as the opportunity audit."""
    snapshot_sources = {'bot_message', 'bot_full_time_note', 'HL', 'BSD', 'HL aos 90 min'}
    if bindings is None:
        signals, bindings = match_identity.resolve(signals, now)
    evidence = opportunity_forward.fixture_evidence(observations, bindings)
    for b in signals:
        versions = evidence.get(game_identity(b), ())
        binding = bindings.get(b['match_id'])
        decision = chronology.decision_time(b)
        deadline = decision + timedelta(days=brain.OPEN_TIMEOUT_DAYS) if decision is not None else None
        cutoff = min(now, deadline) if deadline is not None else now
        known = chronology.result_known(b)
        collision = binding.collision if binding is not None else None
        legacy_settlement = b.get('result_known_source') not in snapshot_sources and b['w'] is not None
        if not eligible(b) or (legacy_settlement and (known is None or known <= cutoff)
                               and (collision is None or (known is not None and known < collision))):
            yield b
            continue

        yield opportunity_results.settle_research_bet(dict(b, _ft_observations=tuple(versions)), now)


def build(csv_path=None, register=False, now=None, *, as_of=None, register_certification=False, capture=None):
    """now: relógio do relatório, um só instante para avaliar (prazos da certificação) e para registar seleções."""
    now = as_of if as_of is not None else (now or datetime.now(timezone.utc))
    chron = {}
    bot_observations, providers = {}, {}
    msgs, ft, marks, notes, audit = load(chron, result_observations=bot_observations, as_of=now)
    fixtures = collections.defaultdict(list)
    for msg in msgs:
        if msg.get('match_id') and msg.get('teams'):
            fixtures[msg['match_id']].append(msg)
    ext, bsd, near, st_hl = {}, {}, {}, {}                         # resultados externos: Highlightly primeiro, depois BSD
    for name in ('hl', 'bsd'):
        try:
            st = json.loads((data_dir() / ('results_%s.json' % name)).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            st = {}
        providers['highlightly' if name == 'hl' else name] = st
        res = opportunity_results.provider_snapshot(st, name, now)
        telemetry = chronology.snapshot_at(st, now)
        audit[name + '_results'] = len(res)
        audit[name + '_vs_bot_disagreements'] = sum(1 for m, r in res.items() if m in ft and ft[m] != r['ft'])
        audit[name + '_near_misses'] = len(telemetry.get('near_misses') or {})
        near[name] = telemetry.get('near_misses') or {}
        audit[name + '_last_run_utc'] = telemetry.get('last_run_utc')
        ext[name] = res
        if name == 'hl':
            st_hl = telemetry
        for m, r in res.items():
            if any(external_matches(msg, r) for msg in fixtures[m]):
                bsd.setdefault(m, dict(r, provider=name.upper()))
    detail_state = details_hl.load_state(data_dir())
    details = details_hl.snapshot(detail_state, now)
    audit['goal_minutes_matches'] = len(details)
    audit['goal_minutes_inconsistent'] = sum(1 for d in details.values() if not d.get('consistent'))
    manual = brain.load_registry(data_dir() / 'manual_results.json')   # {jogo: {ft: [c, f], source: onde foi visto}}; só entradas revistas à mão
    manual = {mid: row for mid, row in manual.items()
              if (seen := chronology.parse_utc(row.get('observed_utc'))) is not None and seen <= now}
    audit['manual_results'] = len(manual)
    audit['manual_vs_other_disagreements'] = sum(1 for m, r in manual.items() if (ft.get(m) or (bsd.get(m) or {}).get('ft')) not in (None, tuple(r['ft']), list(r['ft'])))
    chron['external'] = {}
    for res in ext.values():
        for m, r in res.items():
            chron['external'].setdefault(m, []).append(r.get('matched_at_utc'))
    for m, r in manual.items():
        chron['external'].setdefault(m, []).append(r.get('observed_utc'))
    chron['details'] = {m: d.get('fetched_at_utc') for m, d in details.items()}
    for m, r in manual.items():
        if m not in bsd and opportunity_results.score_pair(r.get('ft')) is not None and r.get('source'):
            bsd[m] = dict(r, fixture_id=r.get('fixture_id', m), provider='manual', known_utc=r.get('observed_utc'))
    not_covered = {mid for mid, stamp in (st_hl.get('not_covered') or {}).items()
                   if (seen := chronology.parse_utc(stamp)) is not None and seen <= now} - set(bsd)
    for m, d in details.items():   # jogo decidido depois dos 90 minutos: vale o marcador aos 90, reconstruído pelos minutos dos golos
        if (m not in bsd and d.get('consistent') and d.get('score_90') and re.search(r'extra time|penalt', d.get('status') or '', re.I)
                and any(external_matches(msg, d) for msg in fixtures[m])):
            bsd[m] = dict(d, ft=d['score_90'], provider='HL aos 90 min', known_utc=d.get('fetched_at_utc'))
    try:
        state = json.loads((data_dir() / 'state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    state = chronology.snapshot_at(state, now)
    audit['collector_last_run_utc'] = state.get('last_run_utc') or audit['last_collected_at_utc']
    audit['name_review'] = [dict(n, provider=name.upper()) for name in ('hl', 'bsd') for n in (near.get(name) or {}).values()][:30]
    audit['deleted_by_bot'] = len(state.get('missing_ids') or [])
    audit['ended_after_90'] = len(st_hl.get('not_regular_time') or {})
    audit['hl_vs_bsd_disagreements'] = sum(1 for m, r in ext['hl'].items() if m in ext['bsd'] and ext['bsd'][m]['ft'] != r['ft'])
    identified, bindings = match_identity.resolve(msgs, now)
    bets = build_bets(identified, ft, marks, notes, bsd=bsd, details=details, not_covered=not_covered, chronology=chron, now=now)
    signals = build_bets(identified, ft, marks, notes, strict=False, bsd=bsd, details=details, not_covered=not_covered, chronology=chron, now=now)
    finished_matches = {b['match_id'] for b in signals if b['ft'] or b['match_result_first_seen_utc']}
    by = collections.OrderedDict()
    for b in bets:
        by.setdefault(b['group'], []).append(b)
    unparsed = [m for m in msgs if m.get('type') is None or m.get('error')]
    recent = [{'date_utc': b['date_utc'], 'match_id': b['match_id'], 'group': b['group'], 'match': ' — '.join(b['teams'] or []) or b['match_id'], 'selection': label(b), 'odds': b['odds'], 'ft': b['ft'], 'w': b['w'], 'settled_by': b['settled_by'],
               'n_signals': b['n_signals'], 'max_mult': b['max_mult']}
              | ({'result_provenance': b['result_provenance']} if 'result_provenance' in b else {}) for b in bets[-60:]][::-1]
    stake = [b for b in signals if b['type'] == 'STAKE_LOAD']
    audit['stake_original_seen'] = sum(1 for b in stake if not b['edited_at_first_sight'])
    audit['stake_no_score_after_kickoff'] = sum(1 for b in stake if not b['edited_at_first_sight'] and b['layout_phase'] == 'pre' and (b['after_kickoff_min'] or 0) > 5)
    audit['stake_sent_45_after_kickoff'] = sum(1 for b in stake if (b['after_kickoff_min'] or 0) > 45)
    audit['goal_lists'] = sum(1 for b in signals if b['goals'])
    audit['goal_lists_consistent'] = sum(1 for b in signals if b['next_goal_min'] is not None)
    acc = accuracy(signals, now)
    if csv_path:
        write_csv(signals, csv_path)
    ins = insights.build(signals, bets, acc, MIN_SETTLED)
    reg_path = data_dir() / 'hypotheses.json'
    registry = brain.load_registry(reg_path)
    if register and brain.update_registry(signals, registry, now=now):
        reg_path.write_text(json.dumps(registry, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    captured = LAST if capture is None else capture
    captured['signals'] = [b for b in signals if eligible(b)]
    observations = opportunity_results.merge_sources(bot_observations, providers)
    for mid, versions in details_hl.observations(detail_state, now).items():
        observations.setdefault(mid, []).extend(versions)
    identified.sort(key=lambda row: match_identity.signal_seen(row) or match_identity.UNPROVEN)
    research_input = build_bets(identified, ft, marks, notes, strict=False, bsd=bsd, details=details,
                                not_covered=not_covered, chronology=chron, group_aliases=False, now=now)
    research = list(research_bets(research_input, observations, now, bindings=bindings))
    captured['research_signals'] = [b for b in research if eligible(b)]
    ledger_path = data_dir() / certification.FILE
    ledger = certification.load(ledger_path)          # None até à ativação explícita: nenhuma tática pode ser validada
    receipt_dir = data_dir() / certification.scientific_receipts.DIRECTORY
    brn = brain.build(research, registry, MIN_SETTLED, ledger, receipt_dir=receipt_dir, as_of=now,
                     archive_commitments=register_certification)
    ledger_summary(brn['validation'])
    llm_state = chronology.snapshot_at(brain.load_registry(data_dir() / 'llm_state.json'), now)
    brn['llm_runs'], brn['llm_last_run_utc'], brn['llm_last_outcome'] = llm_state.get('runs', 0), llm_state.get('last_run_utc'), llm_state.get('last_outcome')
    brn['llm_today'] = {k: llm_state.get(k) for k in ('day', 'calls_today', 'neurons_today', 'accepted_today', 'last_mode')}
    try:
        notes = json.loads((data_dir() / 'llm_notes.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        notes = []
    by_id = {h['id']: h for h in brn['top']}
    brn['notes'] = [dict(n, n=by_id[n['id']]['n'], hits=by_id[n['id']]['hits'], hit_rate=by_id[n['id']]['hit_rate'], break_even=by_id[n['id']]['break_even'],
                         status=by_id[n['id']]['status']) for n in notes if isinstance(n, dict) and n.get('id') in by_id
                    and (seen := chronology.parse_utc(n.get('at_utc'))) is not None and seen <= now][:12]
    brn['entry_points'] = brain.entry_points(signals)
    brn['llm_evaluation'] = llm_eval.page_summary(research, registry, as_of=now)
    return {
        'generated_at_utc': now.isoformat(),
        'messages': len(msgs), 'types': dict(collections.Counter(str(m.get('type')) for m in msgs)),
        'unparsed': len(unparsed), 'unparsed_samples': [m['head'] for m in unparsed[-10:]],
        'excluded_first_seen_with_result': sum(1 for m in msgs if m.get('bet') and m['had_ft_at_first_sight']),
        'excluded_edited_before_seen': sum(1 for m in msgs if m.get('bet') and m['edited_at_first_sight'] and not m['had_ft_at_first_sight']),
        'excluded_burst': sum(1 for m in msgs if m.get('bet') and m['burst'] and not m['edited_at_first_sight'] and not m['had_ft_at_first_sight']),
        'excluded_low_odds': sum(1 for m in msgs if m.get('bet') and m['odds'] < MIN_ODDS),
        'audit': audit, 'unique_bets': len(bets), 'settled_bets': sum(1 for b in bets if b['w'] is not None),
        'unsettled_market': sum(1 for b in bets if b['ft'] and b['w'] is None),
        'first_signal_utc': bets[0]['date_utc'] if bets else None, 'min_settled': MIN_SETTLED,
        'groups': {name: block(rows) for name, rows in sorted(by.items())},
        'accuracy': acc, 'insights': ins, 'brain': brn,
        'simulation': portfolio(bets, now),
        'operational': operational_evidence(signals, now),
        polymarket_alerts.REPORT_KEY: polymarket_alerts.candidates(msgs, finished_matches, now),
        'questions': questions(bets), 'recent_bets': recent, 'unsettled': unsettled(signals, now), 'matches': match_stories({b['match_id'] for b in bets[-60:]}, msgs, dict({b['match_id']: b['ft'] for b in signals if b['ft']}, **ft), bsd, details), 'timeline': timeline(bets), 'daily': daily(bets),
    }


def main():
    docs = pathlib.Path('docs')
    report = build(docs / 'bets.csv' if docs.is_dir() else None, register=True)
    out = data_dir() / 'report.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('messages', 'unparsed', 'unique_bets', 'settled_bets', 'audit')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
