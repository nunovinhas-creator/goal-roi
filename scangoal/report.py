"""Relatório ScanGoal: ROI por grupo com regras fixas. Só contas; sem LLM, sem rede.

Regra principal: 1 unidade na seleção indicada, à odd do PRIMEIRO sinal de cada (jogo, tipo, mercado, seleção).
Sinal = primeira versão da mensagem; resultado = FT em qualquer versão de qualquer mensagem do mesmo jogo.
Secção "questions": cortes escritos ANTES de haver dados (exploratórios; nunca contam como conclusão).
"""
import collections, json, math
from datetime import datetime, timezone

from .parser import MIN_ODDS, parse, settle
from .store import data_dir

HAIRCUTS = (0.0, 0.02, 0.05)
MIN_SETTLED = 300
SIGNAL_PART = lambda text: text.split('\n🏁 FT')[0].strip()


def key_of(p):
    return (p.get('match_id'), p['type'], p.get('market'), p.get('selection'))


def load():
    """-> (mensagens na 1.ª versão, FT por jogo, marcas ✅/❌ do bot por aposta, notas de fim de jogo, auditoria)."""
    first, ft, marks, notes = {}, {}, {}, []
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
        if mid in first:
            if SIGNAL_PART(r['text']) != first[mid]['signal_text']:
                audit['edits_changing_signal'] += 1  # o bot alterou o sinal depois de publicado
        else:  # ficheiro append-only: a primeira linha de cada id é a versão original
            first[mid] = dict(p, message_id=mid, date_utc=r['date_utc'], had_ft_at_first_sight='ft' in p,
                              signal_text=SIGNAL_PART(r['text']), head=r['text'].strip().splitlines()[0][:60])
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


def build_bets(msgs, ft, marks=None, notes=(), strict=True):
    """strict=True: regra do ROI (exclui sinais vistos já com resultado). strict=False: todos os sinais, para a taxa de acerto."""
    out = {}
    for m in msgs:
        no_odds = str(m.get('type')).endswith('_NO_ODDS')
        if not (m.get('bet') or (no_odds and not strict)):
            continue
        if m.get('bet') and m['odds'] < MIN_ODDS:
            continue
        if strict and m['had_ft_at_first_sight']:
            continue  # vista pela 1.ª vez já com resultado: a hora do sinal não está provada
        k = key_of(m)
        b = out.get(k)
        if b is None:
            b = out[k] = {f: m.get(f) for f in ('match_id', 'type', 'phase', 'market', 'selection', 'odds', 'teams', 'date_utc', 'kickoff_utc',
                                                 'minute', 'share', 'mkt_volume', 'message_id', 'score_at_signal', 'had_ft_at_first_sight')}
            b.update(n_signals=0, max_mult=1, odds_at_x4=None, odds_last=m.get('odds'), lead_min=lead_minutes(m), key=k)
        b['n_signals'] += 1
        b['odds_last'] = m.get('odds')
        b['max_mult'] = max(b['max_mult'], m.get('mult', 1))
        if m.get('mult', 1) >= 4 and b['odds_at_x4'] is None:
            b['odds_at_x4'] = m['odds']
    bets = sorted(out.values(), key=lambda b: (b['date_utc'], b['message_id']))
    for b in bets:
        b['group'] = group(b)
        f = ft.get(b['match_id']) or note_ft(b, notes)
        b['ft'], b['w'], b['settled_by'] = f, None, None
        if not f:
            continue
        if b['market'] is None:  # sinal sem odd: acerta se houve pelo menos mais um golo depois do sinal
            b['w'], b['settled_by'] = (1.0 if sum(f) > sum(b['score_at_signal']) else -1.0), 'regra: mais um golo'
            continue
        own = settle(b['market'], b['selection'], f, b['teams'])
        if own is not None:
            b['w'], b['settled_by'] = own, 'resultado'
        elif marks and b['key'] in marks:  # mercado que o resultado final não decide: vale a marca do bot
            b['w'], b['settled_by'] = (1.0 if marks[b['key']] else -1.0), 'marca do bot'
    return bets


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
    """rows: apostas liquidadas. ROI médio por aposta, IC 95 % (aprox. normal) e variantes com perda de odd."""
    n = len(rows)
    if not n:
        return None
    res = {}
    for hc in HAIRCUTS:
        xs = [pl(r['w'], r[odds_key], hc) for r in rows]
        mean = sum(xs) / n
        if n > 1:
            half = 1.96 * math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1)) / math.sqrt(n)
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
    return {'bets': len(rows), 'settled': n, 'wins': sum(1 for r in settled if r['w'] > 0), 'pushes': sum(1 for r in settled if r['w'] == 0),
            'units': round(s, 2), 'avg_odds': round(sum(r[odds_key] for r in settled) / n, 3) if n else None,
            'roi': stats(settled, odds_key), 'cumulative': cum[-400:],
            'conclusive': n >= MIN_SETTLED, 'note': 'ok' if n >= MIN_SETTLED else 'sem conclusão (<%d liquidadas)' % MIN_SETTLED}


def wilson(k, n):
    if not n:
        return None
    z, p = 1.96, k / n
    c, h = (p + z * z / (2 * n)) / (1 + z * z / n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


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
        priced = [r for r in done if r['odds'] and r['w'] != 0]
        out[name] = {'signals': len(rows), 'with_result': len(done), 'hits': hits, 'misses': miss, 'pushes': len(done) - hits - miss,
                     'hit_rate': round(hits / (hits + miss), 4) if hits + miss else None, 'ci95': wilson(hits, hits + miss),
                     'avg_odds': round(sum(r['odds'] for r in priced) / len(priced), 3) if priced else None,
                     'break_even': round(sum(1 / r['odds'] for r in priced) / len(priced), 4) if priced else None,
                     'roi_announced_odds': round(sum(pl(r['w'], r['odds']) for r in done if r['odds']) / len([r for r in done if r['odds']]), 4)
                     if any(r['odds'] for r in done) else None,
                     'seen_late': sum(1 for r in done if r['had_ft_at_first_sight']),
                     'by_bot_mark': sum(1 for r in done if r['settled_by'] == 'marca do bot')}
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


def build():
    msgs, ft, marks, notes, audit = load()
    bets = build_bets(msgs, ft, marks, notes)
    signals = build_bets(msgs, ft, marks, notes, strict=False)
    by = collections.OrderedDict()
    for b in bets:
        by.setdefault(b['group'], []).append(b)
    unparsed = [m for m in msgs if m.get('type') is None or m.get('error')]
    recent = [{'date_utc': b['date_utc'], 'group': b['group'], 'match': ' — '.join(b['teams'] or []) or b['match_id'], 'selection': label(b), 'odds': b['odds'], 'ft': b['ft'], 'w': b['w'], 'settled_by': b['settled_by'],
               'n_signals': b['n_signals'], 'max_mult': b['max_mult']} for b in bets[-60:]][::-1]
    return {
        'generated_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'messages': len(msgs), 'types': dict(collections.Counter(str(m.get('type')) for m in msgs)),
        'unparsed': len(unparsed), 'unparsed_samples': [m['head'] for m in unparsed[-10:]],
        'excluded_first_seen_with_result': sum(1 for m in msgs if m.get('bet') and m['had_ft_at_first_sight']),
        'excluded_low_odds': sum(1 for m in msgs if m.get('bet') and m['odds'] < MIN_ODDS),
        'audit': audit, 'unique_bets': len(bets), 'settled_bets': sum(1 for b in bets if b['w'] is not None),
        'unsettled_market': sum(1 for b in bets if b['ft'] and b['w'] is None),
        'first_signal_utc': bets[0]['date_utc'] if bets else None, 'min_settled': MIN_SETTLED,
        'groups': {name: block(rows) for name, rows in sorted(by.items())},
        'accuracy': accuracy(signals),
        'questions': questions(bets), 'recent_bets': recent,
    }


def main():
    report = build()
    out = data_dir() / 'report.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('messages', 'unparsed', 'unique_bets', 'settled_bets', 'audit')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
