"""Análise e interpretação por regras (sem LLM): evidência por tipo, movimento da odd, calibração, segmentos, sorte e risco.

Tudo aqui é exploratório, exceto quando um segmento atinge o limiar de conclusão do relatório.
Dimensões fixadas a 12/11/2026, antes de haver dados suficientes para as escolher pelo resultado.
"""
import math
from datetime import datetime

from .parser import competition_kind

PRIOR_WEIGHT = 10        # pseudo-observações centradas na taxa necessária ("sem vantagem")
MIN_SEGMENT = 10         # resultados mínimos para um segmento aparecer
HINT_AT = 30


def _betacf(a, b, x):
    qab, qap, qam, c, d = a + b, a + 1.0, a - 1.0, 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > 1e-30 else 1e-30)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)), -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > 1e-30 else 1e-30)
            c = 1.0 + aa / c
            c = c if abs(c) > 1e-30 else 1e-30
            delta = d * c
            h *= delta
        if abs(delta - 1.0) < 1e-10:
            break
    return h


def beta_cdf(x, a, b):
    """Função beta incompleta regularizada I_x(a, b)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


LABELS = {'BETFAIR/pre': 'dinheiro na Betfair antes do jogo', 'BETFAIR/live': 'dinheiro na Betfair durante o jogo', 'OPEN_GAME': 'jogo aberto com odd',
          'OPEN_GAME_NO_ODDS': 'jogo aberto sem odd', 'STAKE_LOAD': 'aposta grande na Stake', 'TOTALS_FORM': 'forma de golos',
          'TOTALS_FORM_NO_ODDS': 'forma de golos sem odd'}


def wilson(k, n):
    if not n:
        return None
    z, p = 1.96, k / n
    c, h = (p + z * z / (2 * n)) / (1 + z * z / n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def evidence(hits, misses, break_even):
    """Estimativa prudente da taxa de acerto e probabilidade de estar acima da taxa necessária.

    Prior Beta centrado na taxa necessária com peso de 10 apostas: sem dados, a resposta é "sem vantagem" (50 %).
    """
    n = hits + misses
    if break_even is None or not 0 < break_even < 1:
        return {'prudent_rate': round((hits + 1) / (n + 2), 4) if n else None, 'prob_above': None}
    a, b = break_even * PRIOR_WEIGHT + hits, (1 - break_even) * PRIOR_WEIGHT + misses
    return {'prudent_rate': round(a / (a + b), 4), 'prob_above': round(1.0 - beta_cdf(break_even, a, b), 4)}


def summarize(rows):
    """Resumo de um conjunto de sinais com resultado: acerto, taxa necessária, evidência e ROI à odd anunciada."""
    done = [r for r in rows if r['w'] is not None]
    hits, misses = sum(1 for r in done if r['w'] > 0), sum(1 for r in done if r['w'] < 0)
    priced = [r for r in done if r['odds'] and r['w'] != 0]
    be = sum(1 / r['odds'] for r in priced) / len(priced) if priced else None
    pls = [(r['w'] * (r['odds'] - 1) if r['w'] > 0 else r['w']) for r in done if r['odds']]
    out = {'n': hits + misses, 'signals': len(rows), 'hits': hits, 'misses': misses,
           'hit_rate': round(hits / (hits + misses), 4) if hits + misses else None, 'ci95': wilson(hits, hits + misses),
           'break_even': round(be, 4) if be else None, 'roi': round(sum(pls) / len(pls), 4) if pls else None}
    out.update(evidence(hits, misses, be))
    return out


def family(b):
    return {'BETFAIR': 'Betfair', 'STAKE_LOAD': 'Stake', 'OPEN_GAME': 'Jogo aberto', 'OPEN_GAME_NO_ODDS': 'Jogo aberto',
            'TOTALS_FORM': 'Forma de golos', 'TOTALS_FORM_NO_ODDS': 'Forma de golos'}.get(b['type'], b['type'])


def odds_band(o):
    return None if not o else 'odd até 1,50' if o < 1.5 else 'odd 1,50 a 2,00' if o < 2 else 'odd 2,00 a 3,00' if o < 3 else 'odd acima de 3,00'


def dimensions(b, multi):
    """(dimensão, valor) aplicáveis a um sinal. Lista fixa; não acrescentar sem data."""
    out = [('Competição', b['kind']), ('Faixa de odd', odds_band(b['odds'])),
           ('Outros sinais no mesmo jogo', 'jogo com vários tipos de sinal' if b['match_id'] in multi else 'jogo com um só tipo de sinal'),
           ('Dia', 'fim de semana' if datetime.fromisoformat(b['date_utc']).weekday() >= 5 else 'dia de semana')]
    if b['type'] == 'BETFAIR':
        out += [('Momento', 'antes do jogo' if b['phase'] == 'pre' else 'durante o jogo'),
                ('Reforço', 'sem reforço' if b['max_mult'] == 1 else 'reforço ×2' if b['max_mult'] < 4 else 'reforço ×4 ou mais'),
                ('Quota de mercado', '80 % ou mais' if (b['share'] or 0) >= 80 else 'menos de 80 %'),
                ('Tamanho do mercado', '10 mil $ ou mais' if (b['mkt_volume'] or 0) >= 10000 else 'menos de 10 mil $')]
        if b['n_signals'] > 1 and b['odds_last'] and b['odds_last'] != b['odds']:
            out.append(('Odd depois do sinal', 'desceu' if b['odds_last'] < b['odds'] else 'subiu'))
        if b['phase'] == 'pre' and b['lead_min'] is not None:
            out.append(('Antecedência', 'mais de 60 min' if b['lead_min'] > 60 else '60 min ou menos'))
    if b['type'].startswith('OPEN_GAME') or b['type'].startswith('TOTALS_FORM'):
        out.append(('Parte do jogo', '1.ª parte' if (b['minute'] or 0) < 46 else '2.ª parte'))
        if b['market'] and b['score_at_signal']:
            need = float(b['selection'].split()[1]) - sum(b['score_at_signal'])
            out.append(('Golos em falta para ganhar', 'até 1 golo' if need <= 1 else 'entre 1 e 2 golos' if need <= 2 else 'mais de 2 golos'))
    if b['type'] == 'STAKE_LOAD':
        out += [('Momento', 'antes do jogo' if b['phase'] == 'pre' else 'durante o jogo'),
                ('Mercado', 'golos (total asiático)' if b['market'] == 'Asian Total' else 'outros mercados')]
    return [(d, v) for d, v in out if v]


def segments(signals):
    types_by_match = {}
    for b in signals:
        types_by_match.setdefault(b['match_id'], set()).add(family(b))
    multi = {m for m, t in types_by_match.items() if len(t) > 1}
    cells = {}
    for b in signals:
        b['kind'] = competition_kind(b.get('league'), b.get('teams'))
        for dim, val in dimensions(b, multi):
            cells.setdefault((family(b), dim, val), []).append(b)
    out, tested = [], 0
    for (fam, dim, val), rows in cells.items():
        s = summarize(rows)
        if s['n'] < MIN_SEGMENT:
            continue
        tested += 1
        out.append(dict(s, family=fam, dimension=dim, value=val))
    out.sort(key=lambda s: (-(s['prob_above'] if s['prob_above'] is not None else -1), -s['n']))
    return out, tested


def price_move(signals):
    """Betfair com reforço: a odd do último sinal face à do primeiro. Antes do jogo é um indicador de concordância do mercado."""
    out = {}
    for phase in ('pre', 'live'):
        rows = [b for b in signals if b['type'] == 'BETFAIR' and b['phase'] == phase and b['n_signals'] > 1 and b['odds_last']]
        moves = sorted(math.log(b['odds_last'] / b['odds']) for b in rows)
        down, up = [b for b in rows if b['odds_last'] < b['odds']], [b for b in rows if b['odds_last'] > b['odds']]
        out[phase] = {'n': len(rows), 'shortened': len(down), 'drifted': len(up), 'unchanged': len(rows) - len(down) - len(up),
                      'median_move_pct': round((math.exp(moves[len(moves) // 2]) - 1) * 100, 1) if moves else None,
                      'when_shortened': summarize(down), 'when_drifted': summarize(up)}
    return out


def calibration(signals):
    """Por faixa de odd: taxa de acerto observada contra a que a odd implica (todos os tipos com odd)."""
    out = []
    for name in ('odd até 1,50', 'odd 1,50 a 2,00', 'odd 2,00 a 3,00', 'odd acima de 3,00'):
        s = summarize([b for b in signals if odds_band(b['odds']) == name])
        out.append(dict(s, band=name))
    return out


def luck_and_risk(bets):
    """Saldo real contra o que a pura sorte explicaria se as odds fossem justas; e pior sequência."""
    done = [b for b in bets if b['w'] is not None]
    pls = [(b['w'] * (b['odds'] - 1) if b['w'] > 0 else b['w']) for b in done]
    units, sd = sum(pls), math.sqrt(sum(b['odds'] - 1 for b in done)) if done else 0.0
    peak = total = dd = 0.0
    run = worst_run = 0
    for x in pls:
        total += x
        peak = max(peak, total)
        dd = max(dd, peak - total)
        run = run + 1 if x < 0 else 0
        worst_run = max(worst_run, run)
    return {'n': len(done), 'units': round(units, 2), 'luck_sd': round(sd, 2), 'z': round(units / sd, 2) if sd else None,
            'max_drawdown': round(dd, 2), 'longest_losing_run': worst_run}


def pct(v):
    return ('%.0f %%' % (v * 100))


def dec(v, places=2, plus=False):
    return format(v, ('+' if plus else '') + '.%df' % places).replace('.', ',')


def narrative(acc, segs, tested, move, calib, luck, min_settled):
    """Frases simples, geradas por regras a partir dos números. Nunca afirmam mais do que a amostra permite."""
    out = []
    rated = [(k, g) for k, g in acc.items() if g.get('prob_above') is not None and g['hits'] + g['misses'] >= 5]
    for k, g in sorted(rated, key=lambda kv: -kv[1]['prob_above'])[:1]:
        n = g['hits'] + g['misses']
        out.append('O tipo de sinal com melhor evidência é %s: acertou %d em %d, precisa de %s, e a probabilidade de estar acima do mínimo é %s%s.'
                   % (LABELS.get(k, k), g['hits'], n, pct(g['break_even']), pct(g['prob_above']), '' if n >= HINT_AT else ' (amostra pequena, menos de %d resultados)' % HINT_AT))
    for k, g in sorted(rated, key=lambda kv: kv[1]['prob_above'])[:1]:
        if len(rated) > 1:
            out.append('O mais fraco é %s: %d em %d, precisa de %s, probabilidade de estar acima do mínimo %s.'
                       % (LABELS.get(k, k), g['hits'], g['hits'] + g['misses'], pct(g['break_even']), pct(g['prob_above'])))
    if luck['n'] >= 10 and luck['z'] is not None:
        z = luck['z']
        word = 'dentro do que a sorte explica' if abs(z) < 2 else ('acima do que a sorte costuma explicar' if z > 0 else 'abaixo do que a sorte costuma explicar')
        out.append('Saldo de %s unidades em %d apostas, %s (a oscilação normal nesta amostra é de cerca de ±%s unidades).'
                   % (dec(luck['units'], 2, True), luck['n'], word, dec(2 * luck['luck_sd'], 1)))
    pre = move['pre']
    if pre['n'] >= 5:
        out.append('Antes do jogo, a odd desceu depois do sinal em %d de %d apostas Betfair com reforço: %s.'
                   % (pre['shortened'], pre['n'], 'o mercado tende a acompanhar o sinal' if pre['shortened'] > pre['drifted'] else 'o mercado não tem acompanhado o sinal'))
    strong = [s for s in segs if s['prob_above'] is not None and s['n'] >= HINT_AT and (s['prob_above'] >= 0.95 or s['prob_above'] <= 0.05)]
    for s in strong[:3]:
        out.append('%s, %s = %s: %d em %d (%s contra %s necessários). Indício %s.'
                   % (s['family'], s['dimension'].lower(), s['value'], s['hits'], s['n'], pct(s['hit_rate']), pct(s['break_even']),
                      'positivo' if s['prob_above'] >= 0.95 else 'negativo'))
    if tested:
        out.append('Foram comparados %d segmentos; com tantos cortes, alguns parecem bons ou maus só por acaso. Um segmento só conta depois de se repetir em dados novos e de chegar a %d resultados.'
                   % (tested, min_settled))
    if not out:
        out.append('Ainda não há resultados suficientes para interpretar.')
    return out


def build(signals, bets, accuracy, min_settled):
    for k, g in accuracy.items():
        g.update(evidence(g['hits'], g['misses'], g['break_even']))
    segs, tested = segments(signals)
    move, calib, luck = price_move(signals), calibration(signals), luck_and_risk(bets)
    return {'segments': segs[:40], 'segments_tested': tested, 'price_move': move, 'calibration': calib, 'luck': luck,
            'narrative': narrative(accuracy, segs, tested, move, calib, luck, min_settled),
            'method': {'prior_weight': PRIOR_WEIGHT, 'min_segment': MIN_SEGMENT, 'hint_at': HINT_AT}}
