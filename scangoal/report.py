"""Relatório ScanGoal: ROI por grupo com regras fixas. Só contas; sem LLM, sem rede.

Regra principal: 1 unidade na seleção indicada, à odd do PRIMEIRO sinal de cada (jogo, tipo, mercado, seleção).
Sinal = primeira versão da mensagem; resultado = FT em qualquer versão de qualquer mensagem do mesmo jogo.
Secção "questions": cortes escritos ANTES de haver dados (exploratórios; nunca contam como conclusão).
"""
import collections, csv, json, math, pathlib, re
from datetime import datetime, timedelta, timezone

from . import insights
from .parser import MIN_ODDS, parse, settle
from .store import data_dir

HAIRCUTS = (0.0, 0.02, 0.05)
MIN_SETTLED = 300
NO_SOURCE_AFTER_DAYS = 3      # sem resultado ao fim deste prazo: as fontes já não são consultadas
BURST = 5                     # sinais Betfair no mesmo segundo: lote (ex.: ao subscrever); a hora do 1.º sinal não está provada
GOALS_PER_MIN = 2.7 / 95      # jogo médio, para a taxa habitual de "mais um golo"
SIGNAL_PART = lambda text: re.split(r'\n(?:🏁 FT|⚠️)', text)[0].strip()   # sem o resultado nem o aviso final, que o bot desloca ao editar


def key_of(p):
    return (p.get('match_id'), p['type'], p.get('market'), p.get('selection'))


def load():
    """-> (mensagens na 1.ª versão, FT por jogo, marcas ✅/❌ do bot por aposta, notas de fim de jogo, auditoria)."""
    first, ft, marks, notes, goals = {}, {}, {}, [], {}
    audit = {'versions': 0, 'edits_changing_signal': 0, 'bot_mark_disagreements': 0, 'last_collected_at_utc': None}
    path = data_dir() / 'messages.jsonl'
    if not path.exists():
        return [], ft, marks, notes, audit
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        audit['versions'] += 1
        audit['last_collected_at_utc'] = max(audit['last_collected_at_utc'] or '', r.get('collected_at_utc') or '')
        p = parse(r['text'])
        if p['type'] == 'WEAKENED_LINEUP' and p.get('teams'):   # mensagem sem ligação: identidade pelas equipas e pelo dia
            p['match_id'] = 'wl-%s-%s' % (re.sub(r'[^a-z0-9]+', '-', ' vs '.join(p['teams']).lower()).strip('-'), r['date_utc'][:10])
            if p.get('lead_from_text') is not None:
                p['kickoff_utc'] = (datetime.fromisoformat(r['date_utc']) + timedelta(minutes=p['lead_from_text'])).isoformat(timespec='minutes')
        if p['type'] == 'FULL_TIME_NOTE' and p.get('ft'):
            notes.append((r['date_utc'], [t.lower() for t in p['teams']], p['ft']))
        elif p.get('ft') and p.get('match_id'):
            ft[p['match_id']] = p['ft']
            if p.get('bet'):
                if p.get('bot_mark') is not None:
                    marks[key_of(p)] = p['bot_mark']
                own = settle(p['market'], p['selection'], p['ft'], p.get('teams'))
                if own is not None and own != 0 and p['bot_mark'] is not None and (own > 0) != p['bot_mark']:
                    audit['bot_mark_disagreements'] += 1
        mid = r['message_id']
        if p.get('goals'):
            goals[mid] = p['goals']
        if mid in first:
            if SIGNAL_PART(r['text']) != first[mid]['signal_text']:
                audit['edits_changing_signal'] += 1  # o bot alterou o sinal depois de publicado
        else:  # ficheiro append-only: a primeira linha de cada id é a versão original
            first[mid] = dict(p, message_id=mid, date_utc=r['date_utc'], had_ft_at_first_sight='ft' in p, edited_at_first_sight=bool(r.get('edit_date_utc')),
                              signal_text=SIGNAL_PART(r['text']), head=r['text'].strip().splitlines()[0][:60])
    same_second = collections.Counter(m['date_utc'] for m in first.values() if m['type'] == 'BETFAIR')
    for mid, m in first.items():
        m['burst'] = m['type'] == 'BETFAIR' and same_second[m['date_utc']] >= BURST
        m['goals'] = goals.get(mid)
    return [first[k] for k in sorted(first)], ft, marks, notes, audit


def lead_minutes(m):
    if not m.get('kickoff_utc'):
        return None
    ko = datetime.fromisoformat(m['kickoff_utc'])
    return round((ko - datetime.fromisoformat(m['date_utc'])).total_seconds() / 60)


def note_ft(b, notes):
    """Resultado vindo de uma nota '🏁 Full time: A x:y B' posterior ao sinal, só com nomes de equipa iguais."""
    teams = [t.lower() for t in (b['teams'] or [])]
    for date, names, score in notes:
        if names == teams and date >= b['date_utc']:
            return score
    return None


def build_bets(msgs, ft, marks=None, notes=(), strict=True, bsd=None):
    """strict=True: regra do ROI (exclui sinais vistos já com resultado). strict=False: todos os sinais, para a taxa de acerto."""
    out, dead = {}, set()
    for m in msgs:
        no_odds = str(m.get('type')).endswith('_NO_ODDS')
        if not (m.get('bet') or (no_odds and not strict)):
            continue
        if m.get('bet') and m['odds'] < MIN_ODDS:
            continue
        k = key_of(m)
        if strict and (k in dead or (k not in out and not provenance_ok(m))):
            dead.add(k)   # o primeiro sinal desta aposta não tem origem provada: a aposta inteira fica fora do ROI
            continue
        b = out.get(k)
        if b is None:
            b = out[k] = {f: m.get(f) for f in ('match_id', 'type', 'phase', 'market', 'selection', 'odds', 'teams', 'date_utc', 'kickoff_utc',
                                                 'minute', 'share', 'mkt_volume', 'message_id', 'score_at_signal', 'had_ft_at_first_sight', 'league',
                                                 'edited_at_first_sight', 'burst', 'rhythm', 'bot_over_pct', 'goals')}
            b.update(n_signals=0, max_mult=1, odds_at_x4=None, odds_last=m.get('odds'), lead_min=lead_minutes(m), key=k)
        b['n_signals'] += 1
        b['odds_last'] = m.get('odds')
        b['max_mult'] = max(b['max_mult'], m.get('mult', 1))
        if m.get('mult', 1) >= 4 and b['odds_at_x4'] is None:
            b['odds_at_x4'] = m['odds']
    bets = sorted(out.values(), key=lambda b: (b['date_utc'], b['message_id']))
    now = datetime.now(timezone.utc)
    for b in bets:
        b['layout_phase'], b['after_kickoff_min'] = b['phase'], None
        if b['type'] == 'STAKE_LOAD' and b['kickoff_utc']:
            b['after_kickoff_min'] = -lead_minutes(b)
            if b['edited_at_first_sight']:   # só vimos a versão editada, em que o bot reescreve as linhas das equipas: a hora decide a fase
                b['phase'] = 'pre' if b['after_kickoff_min'] < 0 else 'live'
        b['in_roi'] = provenance_ok(b)
        b['group'] = group(b)
        f = ft.get(b['match_id']) or note_ft(b, notes)
        from_bsd = False
        if not f and bsd and b['match_id'] in bsd:
            f, from_bsd = bsd[b['match_id']]['ft'], True
        b['ft'], b['w'], b['settled_by'] = f, None, None
        ref = datetime.fromisoformat(b['kickoff_utc'] or b['date_utc'])
        b['no_source'] = not f and now - ref > timedelta(days=NO_SOURCE_AFTER_DAYS)
        b['next_goal_min'] = next_goal(b)
        if not f:
            continue
        if b['market'] is None:  # sinal sem odd: acerta se houve pelo menos mais um golo depois do sinal
            b['w'], b['settled_by'] = (1.0 if sum(f) > sum(b['score_at_signal']) else -1.0), 'regra: mais um golo'
            continue
        own = settle(b['market'], b['selection'], f, b['teams'])
        if b['market'].strip().lower() == 'asian handicap' and b['phase'] == 'live':
            own = None   # ao vivo o handicap conta a partir do resultado no momento da aposta: só a marca do bot decide
        if own is not None:
            b['w'], b['settled_by'] = own, ('resultado ' + bsd[b['match_id']].get('provider', 'BSD')) if from_bsd else 'resultado'
        elif marks and b['key'] in marks:  # mercado que o resultado final não decide: vale a marca do bot
            b['w'], b['settled_by'] = (1.0 if marks[b['key']] else -1.0), 'marca do bot'
    return bets


def provenance_ok(m):
    """Regra do ROI: só sinais vistos na versão original, antes do resultado, e fora de lotes."""
    return not (m['had_ft_at_first_sight'] or m['edited_at_first_sight'] or m['burst'])


def next_goal(b):
    """Minutos entre o sinal e o golo seguinte, só quando a lista de golos do bot bate certo com o marcador."""
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
    if b['type'] == 'STAKE_LOAD':
        return 'STAKE_LOAD/%s' % b['phase']

    return b['type']


def label(b):
    if b['market'] is None:
        return 'mais um golo (sem odd)'
    if b['market'] == '1X2':
        t = b['teams'] or ['1', '2']
        return {'1': t[0], '2': t[-1], 'X': 'Empate'}[b['selection']]
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


def accuracy(signals):
    """Taxa de acerto por tipo, com TODOS os sinais (inclui os vistos já com resultado e os sem odd)."""
    by = collections.OrderedDict()
    for b in signals:
        name = b['type'] if b['type'] != 'BETFAIR' else 'BETFAIR/%s' % b['phase']
        by.setdefault(name, []).append(b)
    out = collections.OrderedDict()
    for name, rows in sorted(by.items()):
        done = [r for r in rows if r['w'] is not None]
        hits, miss = sum(1 for r in done if r['w'] > 0), sum(1 for r in done if r['w'] < 0)
        eh, em = insights.effective(done)   # meia vitória = meio acerto; meia derrota = meia falha
        priced = [r for r in done if r['odds'] and r['w'] != 0]
        no_odds = [r for r in done if not r['odds'] and r.get('minute') is not None]
        base = round(sum(baseline_more_goals(r['minute']) for r in no_odds) / len(no_odds), 4) if no_odds and not priced else None
        out[name] = {'signals': len(rows), 'with_result': len(done), 'hits': hits, 'misses': miss, 'pushes': len(done) - hits - miss,
                     'eff_hits': eh, 'eff_misses': em, 'no_source': sum(1 for r in rows if r['no_source']),
                     'unsettleable': sum(1 for r in rows if r['ft'] and r['w'] is None),
                     'hit_rate': round(eh / (eh + em), 4) if eh + em else None, 'ci95': wilson(eh, eh + em),
                     'avg_odds': round(sum(r['odds'] for r in priced) / len(priced), 3) if priced else None,
                     'break_even': round(sum(1 / r['odds'] for r in priced) / len(priced), 4) if priced else base, 'baseline': base is not None,
                     'roi_announced_odds': round(sum(pl(r['w'], r['odds']) for r in done if r['odds']) / len([r for r in done if r['odds']]), 4)
                     if any(r['odds'] for r in done) else None,
                     'seen_late': sum(1 for r in done if not r['in_roi']),
                     'by_bot_mark': sum(1 for r in done if r['settled_by'] == 'marca do bot')}
        days = max(1.0, (datetime.now(timezone.utc) - datetime.fromisoformat(min(r['date_utc'] for r in rows))).total_seconds() / 86400)
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
        out.append({'d': b['date_utc'], 'u': round(total, 2), 'g': round(gain, 2), 'm': ' — '.join(b['teams'] or []), 's': label(b), 'o': b['odds']})
    return out[-600:]


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


def build(csv_path=None):
    msgs, ft, marks, notes, audit = load()
    ext, bsd, near, st_hl = {}, {}, {}, {}                         # resultados externos: Highlightly primeiro, depois BSD
    for name in ('hl', 'bsd'):
        try:
            st = json.loads((data_dir() / ('results_%s.json' % name)).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            st = {}
        res = st.get('results') or {}
        audit[name + '_results'] = len(res)
        audit[name + '_vs_bot_disagreements'] = sum(1 for m, r in res.items() if m in ft and ft[m] != r['ft'])
        audit[name + '_near_misses'] = len(st.get('near_misses') or {})
        near[name] = st.get('near_misses') or {}
        audit[name + '_last_run_utc'] = st.get('last_run_utc')
        ext[name] = res
        if name == 'hl':
            st_hl = st
        for m, r in res.items():
            bsd.setdefault(m, dict(r, provider=name.upper()))
    try:
        state = json.loads((data_dir() / 'state.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = {}
    audit['collector_last_run_utc'] = state.get('last_run_utc') or audit['last_collected_at_utc']
    audit['name_review'] = [dict(n, provider=name.upper()) for name in ('hl', 'bsd') for n in (near.get(name) or {}).values()][:30]
    audit['deleted_by_bot'] = len(state.get('missing_ids') or [])
    audit['ended_after_90'] = len(st_hl.get('not_regular_time') or {})
    audit['hl_vs_bsd_disagreements'] = sum(1 for m, r in ext['hl'].items() if m in ext['bsd'] and ext['bsd'][m]['ft'] != r['ft'])
    bets = build_bets(msgs, ft, marks, notes, bsd=bsd)
    signals = build_bets(msgs, ft, marks, notes, strict=False, bsd=bsd)
    by = collections.OrderedDict()
    for b in bets:
        by.setdefault(b['group'], []).append(b)
    unparsed = [m for m in msgs if m.get('type') is None or m.get('error')]
    recent = [{'date_utc': b['date_utc'], 'group': b['group'], 'match': ' — '.join(b['teams'] or []) or b['match_id'], 'selection': label(b), 'odds': b['odds'], 'ft': b['ft'], 'w': b['w'], 'settled_by': b['settled_by'],
               'n_signals': b['n_signals'], 'max_mult': b['max_mult']} for b in bets[-60:]][::-1]
    stake = [b for b in signals if b['type'] == 'STAKE_LOAD']
    audit['stake_original_seen'] = sum(1 for b in stake if not b['edited_at_first_sight'])
    audit['stake_no_score_after_kickoff'] = sum(1 for b in stake if not b['edited_at_first_sight'] and b['layout_phase'] == 'pre' and (b['after_kickoff_min'] or 0) > 5)
    audit['stake_sent_45_after_kickoff'] = sum(1 for b in stake if (b['after_kickoff_min'] or 0) > 45)
    audit['goal_lists'] = sum(1 for b in signals if b['goals'])
    audit['goal_lists_consistent'] = sum(1 for b in signals if b['next_goal_min'] is not None)
    acc = accuracy(signals)
    if csv_path:
        write_csv(signals, csv_path)
    ins = insights.build(signals, bets, acc, MIN_SETTLED)
    return {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
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
        'accuracy': acc, 'insights': ins,
        'questions': questions(bets), 'recent_bets': recent, 'timeline': timeline(bets), 'daily': daily(bets),
    }


def main():
    docs = pathlib.Path('docs')
    report = build(docs / 'bets.csv' if docs.is_dir() else None)
    out = data_dir() / 'report.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('messages', 'unparsed', 'unique_bets', 'settled_bets', 'audit')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
