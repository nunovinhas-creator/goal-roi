"""Cérebro: gera hipóteses a partir das características dos sinais e testa-as com regras fixas. Sem LLM, sem rede.

Disciplina (fixada a 12/11/2026):
- O gerador só olha para QUANTOS sinais têm cada combinação de características, nunca para os resultados.
  Por isso as hipóteses do gerador podem ser avaliadas em todos os dados.
- Hipóteses de origem 'manual' ou 'llm' (escritas por quem já viu resultados) só contam sinais POSTERIORES ao registo.
- O registo (data/hypotheses.json) só cresce; uma hipótese nunca é apagada nem reescrita.
- Estados: em observação (<30 resultados); promissora (>=30 e prob. >= 95 %, sem correção: pode ser acaso);
  forte (>=100 e prob. corrigida pelo número de hipóteses testadas); confirmada (>=300, idem); abandonada (>=30 e prob. <= 10 %).
- Características que só se conhecem depois do primeiro sinal (reforço, movimento da odd...) tornam a hipótese
  "informativa": explica, mas não dá para apostar no momento do sinal, e não entra na lista "a seguir".
"""
import itertools, json
from datetime import datetime, timezone

from . import insights
from .parser import competition_kind

MIN_SUPPORT = 8          # sinais com a combinação para a hipótese ser registada
MAX_HYPOTHESES = 300
HINT_AT, STRONG_AT = 30, 100
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
    if b['type'] == 'STAKE_LOAD':
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
        match = [b for b, fam, at in have if fam == h['family'] and conds <= at and (h.get('source') == 'gerador' or b['date_utc'] > h['born_utc'])]
        s = insights.summarize(match)
        rows.append(dict(s, id=hid, family=h['family'], conditions=h['conditions'], source=h.get('source'), born_utc=h['born_utc'], reason=h.get('reason'),
                         tradable=not any(c[0] in POST for c in conds), open=[b for b in match if b['w'] is None and not b.get('no_source')]))
    tested = sum(1 for r in rows if r['n'] >= HINT_AT)
    bar = 1 - 0.05 / max(1, tested)
    for r in rows:
        r['status'] = status_of(r['n'], r['prob_above'], bar, min_settled)
    return rows, tested, bar


def build(signals, registry, min_settled):
    rows, tested, bar = evaluate(signals, registry, min_settled)
    counts = {}
    for r in rows:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    good = [r for r in rows if r['status'] in ('promissora', 'forte', 'confirmada') and r['tradable']]
    watch = {}
    for r in sorted(good, key=lambda r: -r['prob_above']):
        for b in r['open']:
            w = watch.setdefault(b['key'], {'date_utc': b['date_utc'], 'match': ' — '.join(b['teams'] or []), 'type': b['type'], 'market': b['market'],
                                            'selection': b['selection'], 'odds': b['odds'], 'kickoff_utc': b['kickoff_utc'], 'reasons': []})
            w['reasons'].append({'id': r['id'], 'status': r['status'], 'n': r['n'], 'hit_rate': r['hit_rate'], 'prob_above': r['prob_above']})
    for r in rows:
        r['open'] = len(r['open'])
    rows.sort(key=lambda r: (-(r['prob_above'] if r['prob_above'] is not None and r['n'] >= 5 else -1), -r['n']))
    return {'hypotheses': len(rows), 'tested': tested, 'corrected_bar': round(bar, 5), 'counts': counts,
            'expected_by_chance': round(0.05 * tested, 1), 'top': [r for r in rows if r['n'] >= 5][:25],
            'abandoned': [r for r in rows if r['status'] == 'abandonada'][:10],
            'llm': sorted((r for r in rows if r['source'] == 'llm'), key=lambda r: r['born_utc'], reverse=True)[:20],
            'llm_total': sum(1 for r in rows if r['source'] == 'llm'),
            'watch': sorted(watch.values(), key=lambda w: w['date_utc'], reverse=True)[:30],
            'rules': {'min_support': MIN_SUPPORT, 'hint_at': HINT_AT, 'strong_at': STRONG_AT, 'confirm_at': min_settled}}


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
