"""Cérebro: gera hipóteses a partir das características dos sinais e testa-as com regras fixas. Sem LLM, sem rede.

Disciplina (fixada a 12/11/2026):
- O gerador só olha para QUANTOS sinais têm cada combinação de características, nunca para os resultados.
  Por isso as hipóteses do gerador podem ser avaliadas em todos os dados.
- Hipóteses de origem 'manual' ou 'llm' (escritas por quem já viu resultados) só contam sinais POSTERIORES ao registo.
- O registo (data/hypotheses.json) só cresce; uma hipótese nunca é apagada nem reescrita.
- Estados: em observação (<30 resultados); promissora (>=30 e prob. >= 95 %, sem correção: pode ser acaso);
  forte (>=100 e prob. corrigida pelo número de hipóteses testadas); confirmada (>=300, idem); abandonada (>=30 e prob. <= 10 %).
- A evidência conta um voto por jogo (insights.Votes): sinais repetidos no mesmo jogo não a fazem crescer.
- "Acrescenta ao tipo" (prob_adds): probabilidade de a hipótese ter mais vantagem do que os restantes sinais do mesmo tipo.
  Só informa; não muda o estado.
- "Seguir o cérebro" (walk_forward): cada aposta é decidida só com os resultados que já se conheciam nesse momento.
- Características que só se conhecem depois do primeiro sinal (reforço, movimento da odd...) tornam a hipótese
  "informativa": explica, mas não dá para apostar no momento do sinal, e não entra na lista "a seguir".
"""
import itertools, json
from datetime import datetime, timedelta, timezone

from . import insights
from .parser import competition_kind

MIN_SUPPORT = 8          # sinais com a combinação para a hipótese ser registada
MAX_HYPOTHESES = 300
HINT_AT, STRONG_AT = 30, 100
BOARD_MIN, BOARD_GOOD, BOARD_BAD = 8, 0.80, 0.20   # quadro em destaque: mínimo de resultados e limiares de probabilidade
KNOWN_AFTER_MIN = 150    # um resultado considera-se conhecido 150 minutos depois do início do jogo (ou do sinal, se for ao vivo)
TRACK_MIN = 10           # resultados para uma hipótese entrar no balanço das propostas
POST = {'Outros sinais no mesmo jogo', 'Reforço', 'Odd depois do sinal', 'Quota depois do sinal', 'Velocidade do reforço'}


def earlier_index(signals):
    """Por jogo: (hora, família) de cada sinal, para saber se já havia outro tipo de sinal antes."""
    idx = {}
    for b in signals:
        idx.setdefault(b['match_id'], []).append((b['date_utc'], insights.family(b)))
    return idx


def atoms(b, multi, earlier=None):
    """Características de um sinal: as dimensões fixas da análise e mais algumas próprias do cérebro."""
    b['kind'] = competition_kind(b.get('league'), b.get('teams'))
    out = insights.dimensions(b, multi)
    fam = insights.family(b)
    if b.get('league'):
        out.append(('Liga', b['league']))
    if earlier is not None:
        before = any(d < b['date_utc'] and f != fam for d, f in earlier.get(b['match_id'], ()))
        out.append(('Sinal anterior de outro tipo no mesmo jogo', 'sim' if before else 'não'))
    s = b.get('score_at_signal')
    if s:
        gap = abs(s[0] - s[1])
        out += [('Marcador no momento', 'empatado' if gap == 0 else 'diferença de 1 golo' if gap == 1 else 'diferença de 2 ou mais golos'),
                ('Golos até ao sinal', 'nenhum' if sum(s) == 0 else '1 ou 2' if sum(s) <= 2 else '3 ou mais')]
    if b.get('minute') is not None:
        out.append(('Minuto', 'até ao 30' if b['minute'] <= 30 else '31 a 60' if b['minute'] <= 60 else '61 em diante'))
    if b['type'] == 'BETFAIR':
        out.append(('Seleção', {'1': 'equipa da casa', 'X': 'empate', '2': 'equipa de fora'}.get(b.get('selection'))))
        if b.get('sel_volume') is not None:
            out.append(('Volume na seleção', '10 mil $ ou mais' if b['sel_volume'] >= 10000 else 'menos de 10 mil $'))
    if b['type'] in ('STAKE_LOAD', 'POLYMARKET'):
        if b.get('total') is not None:
            out.append(('Valor da aposta', '10 mil $ ou mais' if b['total'] >= 10000 else 'menos de 10 mil $'))
        if b.get('n_bets') is not None:
            out.append(('Número de apostas', 'uma' if b['n_bets'] == 1 else 'várias'))
    out = [(d, v) for d, v in out if v]
    hour = datetime.fromisoformat(b['date_utc']).hour
    out.append(('Hora (UTC)', 'madrugada' if hour < 6 else 'manhã' if hour < 12 else 'tarde' if hour < 18 else 'noite'))
    if b['type'] == 'BETFAIR':
        if b['n_signals'] > 1 and b.get('share_last') is not None and b['share_last'] != b['share']:
            out.append(('Quota depois do sinal', 'subiu' if b['share_last'] > b['share'] else 'desceu'))
        if b.get('x4_min') is not None:
            out.append(('Velocidade do reforço', '×4 em 30 min ou menos' if b['x4_min'] <= 30 else '×4 em mais de 30 min'))
    return out


def hyp_id(family, conds):
    return family + ' | ' + ' & '.join('%s = %s' % c for c in sorted(conds))


def multi_matches(signals):
    by = {}
    for b in signals:
        by.setdefault(b['match_id'], set()).add(insights.family(b))
    return {m for m, t in by.items() if len(t) > 1}


def candidates(signals):
    """Combinações de 1 e 2 características por família, com o número de sinais que as têm. Não lê resultados."""
    multi, support, earlier = multi_matches(signals), {}, earlier_index(signals)
    for b in signals:
        fam, at = insights.family(b), sorted(set(atoms(b, multi, earlier)))
        combos = [(a,) for a in at] + [c for c in itertools.combinations(at, 2) if c[0][0] != c[1][0]]
        for c in combos:
            support[(fam, c)] = support.get((fam, c), 0) + 1
    return support


def update_registry(signals, registry, now=None):
    """Acrescenta ao registo as combinações com apoio suficiente. Devolve quantas entraram."""
    now = (now or datetime.now(timezone.utc)).isoformat(timespec='seconds')
    added = 0
    ranked = sorted(candidates(signals).items(), key=lambda kv: (len(kv[0][1]), -kv[1], hyp_id(*kv[0])))   # simples primeiro, depois as mais frequentes
    support = dict(ranked)
    for (fam, conds), n in ranked:
        hid = hyp_id(fam, conds)
        if n < MIN_SUPPORT or hid in registry or len(registry) >= MAX_HYPOTHESES:
            continue
        if len(conds) == 2 and any(support.get((fam, (c,))) == n for c in conds):
            continue   # o par apanha exatamente os mesmos sinais que uma das suas metades: não acrescenta nada
        registry[hid] = {'family': fam, 'conditions': [list(c) for c in sorted(conds)], 'born_utc': now, 'source': 'gerador'}
        added += 1
    return added


def status_of(n, prob, corrected_bar, min_settled):
    if prob is None or n < HINT_AT:
        return 'em observação'
    if prob <= 0.10:
        return 'abandonada'
    if n >= STRONG_AT and prob >= corrected_bar:
        return 'confirmada' if n >= min_settled else 'forte'
    return 'promissora' if prob >= 0.95 else 'em observação'


def evaluate(signals, registry, min_settled):
    multi, earlier = multi_matches(signals), earlier_index(signals)
    have = [(b, insights.family(b), set(atoms(b, multi, earlier))) for b in signals]
    rows = []
    for hid, h in registry.items():
        conds = {tuple(c) for c in h['conditions']}
        pool = [(b, at) for b, fam, at in have if fam == h['family'] and (h.get('source') == 'gerador' or b['date_utc'] > h['born_utc'])]
        match = [b for b, at in pool if conds <= at]
        s = insights.summarize(match)
        s['prob_adds'] = insights.prob_better(match, [b for b, at in pool if not conds <= at]) if conds else None
        rows.append(dict(s, id=hid, family=h['family'], conditions=h['conditions'], source=h.get('source'), born_utc=h['born_utc'], reason=h.get('reason'), expect=h.get('expect'), mode=h.get('mode'),
                         tradable=not any(c[0] in POST for c in conds), open=[b for b in match if b['w'] is None and not b.get('no_source')]))
    tested = sum(1 for r in rows if r['n'] >= HINT_AT)
    bar = 1 - 0.05 / max(1, tested)
    for r in rows:
        r['status'] = status_of(r['n'], r['prob_above'], bar, min_settled)
    return rows, tested, bar


def board(rows, now=None, family_n=None):
    """Quadro em destaque: as táticas apostáveis com melhor e pior evidência até agora, e os sinais em aberto que encaixam nas melhores.

    Antes dos 30 resultados uma tática é só um "indício inicial"; o quadro diz sempre quantos resultados tem e quantos faltam.
    """
    now = now or datetime.now(timezone.utc)
    cand = [r for r in rows if r['tradable'] and r['conditions'] and r['prob_above'] is not None and r['n'] >= BOARD_MIN]

    def pick(ordered, limit):
        out, per_family, samples = [], {}, set()
        for r in ordered:
            whole = bool(family_n) and r['n'] >= 0.9 * family_n.get(r['family'], 1e9)   # apanha quase todos os sinais do tipo: é o tipo inteiro
            r['whole'] = whole
            sample = (r['family'], 'todos') if whole else (r['family'], r['n'], r['hits'])
            if sample in samples or per_family.get(r['family'], 0) >= 2:
                continue   # mesma amostra que uma tática já escolhida, ou família já com duas
            samples.add(sample)
            per_family[r['family']] = per_family.get(r['family'], 0) + 1
            out.append(r)
            if len(out) >= limit:
                break
        return out

    def card(r):
        return {'id': r['id'], 'family': r['family'], 'conditions': [] if r.get('whole') else r['conditions'], 'n': r['n'], 'hits': r['hits'], 'hit_rate': r['hit_rate'],
                'break_even': r['break_even'], 'prob_above': r['prob_above'], 'roi': r['roi'], 'source': r['source'], 'reason': r.get('reason'),
                'games': r.get('games'), 'edge': r.get('edge'), 'edge_ci': r.get('edge_ci'), 'prob_above_cut': r.get('prob_above_cut'), 'prob_adds': None if r.get('whole') else r.get('prob_adds'),
                'level': r['status'] if r['status'] != 'em observação' else 'indício inicial', 'missing': max(0, HINT_AT - r['n'])}

    best = pick(sorted((r for r in cand if r['prob_above'] >= BOARD_GOOD and r['status'] != 'abandonada'), key=lambda r: (-r['prob_above'], -r['n'])), 5)
    avoid = pick(sorted((r for r in cand if r['prob_above'] <= BOARD_BAD), key=lambda r: (r['prob_above'], -r['n'])), 3)
    live = {}
    for r in best:
        for b in r['open']:
            ko = datetime.fromisoformat(b['kickoff_utc']) if b.get('kickoff_utc') else None
            fresh = (ko is not None and ko > now) or (now - datetime.fromisoformat(b['date_utc'])).total_seconds() <= 3600
            if fresh and b['key'] not in live:
                teams = b['teams'] or ['equipa da casa', 'equipa de fora']
                sel = {'1': teams[0], '2': teams[1], 'X': 'Empate'}.get(b['selection'], b['selection']) if b['market'] == '1X2' else b['selection']
                live[b['key']] = {'date_utc': b['date_utc'], 'kickoff_utc': b.get('kickoff_utc'), 'match': ' — '.join(b['teams'] or []), 'selection': sel,
                                  'market': b['market'], 'odds': b['odds'], 'tactic': r['id']}
    return {'best': [card(r) for r in best], 'avoid': [card(r) for r in avoid], 'open': sorted(live.values(), key=lambda x: x['date_utc'], reverse=True)[:8],
            'min_results': BOARD_MIN, 'hint_at': HINT_AT}


def build(signals, registry, min_settled):
    rows, tested, bar = evaluate(signals, registry, min_settled)
    fams = {}
    for b in signals:
        if b['w'] is not None and b['w'] != 0 and b['odds']:
            fams[insights.family(b)] = fams.get(insights.family(b), 0) + 1
    brd = board(rows, family_n=fams)
    counts = {}
    for r in rows:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    good = [r for r in rows if r['status'] in ('promissora', 'forte', 'confirmada') and r['tradable']]
    watch = {}
    for r in sorted(good, key=lambda r: -r['prob_above']):
        for b in r['open']:
            w = watch.setdefault(b['key'], {'date_utc': b['date_utc'], 'match': ' — '.join(b['teams'] or []), 'type': b['type'], 'market': b['market'],
                                            'selection': b['selection'], 'odds': b['odds'], 'kickoff_utc': b['kickoff_utc'], 'reasons': []})
            w['reasons'].append({'id': r['id'], 'status': r['status'], 'n': r['n'], 'hit_rate': r['hit_rate'], 'prob_above': r['prob_above'],
                                 'edge': r.get('edge'), 'edge_ci': r.get('edge_ci'), 'prob_above_cut': r.get('prob_above_cut'),
                                 'prudent_rate': r.get('prudent_rate'), 'break_even': r.get('break_even')})
    for r in rows:
        r['open'] = len(r['open'])
    rows.sort(key=lambda r: (-(r['prob_above'] if r['prob_above'] is not None and r['n'] >= 5 else -1), -r['n']))
    return {'hypotheses': len(rows), 'tested': tested, 'corrected_bar': round(bar, 5), 'counts': counts, 'board': brd,
            'track': track_record(rows), 'walk_forward': walk_forward(signals, registry),
            'expected_by_chance': round(0.05 * tested, 1), 'top': [{k: r.get(k) for k in ('id', 'family', 'conditions', 'n', 'hits', 'hit_rate', 'break_even', 'prob_above', 'roi', 'status', 'tradable', 'expect',
                                                                                                    'games', 'edge', 'edge_ci', 'prob_above_cut', 'prob_adds')}
                    for r in rows if r['n'] >= 5][:150],
            'status_list': {r['id']: r['status'] for r in rows if r['status'] != 'em observação'},
            'abandoned': [r for r in rows if r['status'] == 'abandonada'][:10],
            'llm': sorted((r for r in rows if r['source'] == 'llm'), key=lambda r: r['born_utc'], reverse=True)[:20],
            'llm_total': sum(1 for r in rows if r['source'] == 'llm'),
            'watch': sorted(watch.values(), key=lambda w: w['date_utc'], reverse=True)[:30],
            'rules': {'min_support': MIN_SUPPORT, 'hint_at': HINT_AT, 'strong_at': STRONG_AT, 'confirm_at': min_settled}}


def track_record(rows):
    """Balanço do processo de descoberta: das hipóteses com 10 ou mais resultados, quantas foram no sentido esperado, por origem.

    As do modelo de linguagem só contam resultados posteriores à proposta; as do gerador servem de termo de comparação.
    """
    groups, worked, failed = {}, [], []
    for r in rows:
        if not r['conditions']:
            continue
        key = 'gerador' if r['source'] == 'gerador' else '%s/%s' % (r['source'], r.get('mode') or 'novas')
        g = groups.setdefault(key, {'hypotheses': 0, 'judged': 0, 'as_expected': 0})
        g['hypotheses'] += 1
        if r['n'] < TRACK_MIN or r['prob_above'] is None:
            continue
        ok = (r['prob_above'] < 0.5) if r.get('expect') == 'abaixo' else (r['prob_above'] > 0.5)
        g['judged'] += 1
        g['as_expected'] += ok
        if r['source'] == 'llm':
            (worked if ok else failed).append({k: r.get(k) for k in ('id', 'n', 'hit_rate', 'break_even', 'prob_above', 'expect')})
    llm = [g for k, g in groups.items() if k.startswith('llm')]
    return {'by_source': groups, 'min_results': TRACK_MIN, 'llm_judged': sum(g['judged'] for g in llm), 'llm_as_expected': sum(g['as_expected'] for g in llm),
            'worked': sorted(worked, key=lambda r: -r['n'])[:6], 'failed': sorted(failed, key=lambda r: -r['n'])[:6]}


def known_at(b):
    """Momento a partir do qual o resultado de um sinal se considera conhecido."""
    ref = datetime.fromisoformat(b['date_utc'])
    if b.get('kickoff_utc'):
        ko = datetime.fromisoformat(b['kickoff_utc'])
        ref = max(ref, ko if ko.tzinfo else ko.replace(tzinfo=timezone.utc))
    return ref + timedelta(minutes=KNOWN_AFTER_MIN)


def walk_forward(signals, registry):
    """E se tivesse seguido o quadro em destaque desde o início? Sem olhar para o futuro.

    Para cada sinal com odd e origem provada, por ordem: segue-se o sinal se, só com os resultados já conhecidos nesse momento,
    alguma tática apostável tinha pelo menos BOARD_MIN resultados e probabilidade >= BOARD_GOOD (a regra do quadro).
    Compara os sinais seguidos com os não seguidos: se o cérebro acrescenta alguma coisa, os seguidos têm de render mais.
    """
    multi, earlier = multi_matches(signals), earlier_index(signals)
    hyps = [(hid, h['family'], {tuple(c) for c in h['conditions']}, None if h.get('source') == 'gerador' else h['born_utc'])
            for hid, h in registry.items() if h['conditions'] and not any(c[0] in POST for c in h['conditions'])]
    fits = {}
    for b in signals:
        fam, at = insights.family(b), set(atoms(b, multi, earlier))
        fits[id(b)] = [i for i, (_, f, conds, born) in enumerate(hyps) if f == fam and conds <= at and (born is None or b['date_utc'] > born)]
    results = sorted((b for b in signals if b['w'] is not None and b['w'] != 0), key=known_at)
    votes, counts, pos = [insights.Votes() for _ in hyps], [0] * len(hyps), 0
    sides = {True: [], False: []}
    for b in sorted(signals, key=lambda b: b['date_utc']):
        if not b.get('odds') or b.get('market') is None or not b.get('in_roi', True):
            continue
        when = datetime.fromisoformat(b['date_utc'])
        while pos < len(results) and known_at(results[pos]) <= when:
            for i in fits[id(results[pos])]:
                votes[i].add(results[pos])
                counts[i] += 1
            pos += 1
        follow = False
        for i in fits[id(b)]:
            if counts[i] >= BOARD_MIN:
                h, m, be, _ = votes[i].totals()
                if (insights.evidence(h, m, be)['prob_above'] or 0) >= BOARD_GOOD:
                    follow = True
                    break
        sides[follow].append(b)

    def side(rows):
        done = [b for b in rows if b['w'] is not None]
        pl = lambda b, cut: b['w'] * (b['odds'] * (1 - cut) - 1) if b['w'] > 0 else b['w']
        units = sum(pl(b, 0.0) for b in done)
        return {'bets': len(rows), 'settled': len(done), 'games': len({b['match_id'] for b in done}), 'wins': sum(1 for b in done if b['w'] > 0),
                'units': round(units, 2), 'roi': round(units / len(done), 4) if done else None,
                'roi_cut': round(sum(pl(b, insights.ODDS_LOSS) for b in done) / len(done), 4) if done else None}

    curve, total = [], 0.0
    for b in sides[True]:
        if b['w'] is not None:
            total += b['w'] * (b['odds'] - 1) if b['w'] > 0 else b['w']
            curve.append(round(total, 2))
    return {'followed': side(sides[True]), 'skipped': side(sides[False]), 'curve': curve[-400:],
            'prob_followed_better': insights.prob_better(sides[True], sides[False]),
            'first_followed_utc': sides[True][0]['date_utc'] if sides[True] else None,
            'rule': {'min_results': BOARD_MIN, 'min_prob': BOARD_GOOD, 'known_after_min': KNOWN_AFTER_MIN}}


def entry_points(signals):
    """Betfair: entrar ao primeiro sinal ou esperar pelo reforço? Cada linha usa a odd do primeiro sinal que chegou a esse reforço."""
    out = []
    for phase in ('pre', 'live'):
        rows = [b for b in signals if b['type'] == 'BETFAIR' and b['phase'] == phase and b.get('steps')]
        for level in (1, 2, 4, 8, 16):
            virtual = []
            for b in rows:
                odds = next((o for m, o in b['steps'] if m >= level), None)
                if odds is not None:
                    virtual.append(dict(b, odds=odds))
            s = insights.summarize(virtual)
            out.append({'phase': phase, 'level': level, 'signals': s['signals'], 'n': s['n'], 'hits': s['hits'], 'hit_rate': s['hit_rate'],
                        'break_even': s['break_even'], 'roi': s['roi'], 'prob_above': s['prob_above']})
    return out


def load_registry(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
