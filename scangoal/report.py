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


def load():
    """-> (mensagens na 1.ª versão, FT por jogo, auditoria)."""
    first, ft, audit = {}, {}, {'versions': 0, 'edits_changing_signal': 0, 'bot_mark_disagreements': 0, 'last_collected_at_utc': None}
    path = data_dir() / 'messages.jsonl'
    if not path.exists():
        return [], ft, audit
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        audit['versions'] += 1
        audit['last_collected_at_utc'] = max(audit['last_collected_at_utc'] or '', r.get('collected_at_utc') or '')
        p = parse(r['text'])
        if p.get('ft') and p.get('match_id'):
            ft[p['match_id']] = p['ft']
            if p.get('bet'):
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
    return [first[k] for k in sorted(first)], ft, audit


def lead_minutes(m):
    if not m.get('kickoff_utc'):
        return None
    ko = datetime.fromisoformat(m['kickoff_utc'])
    return round((ko - datetime.fromisoformat(m['date_utc'])).total_seconds() / 60)


def build_bets(msgs, ft):
    out = {}
    for m in msgs:
        if not m.get('bet') or m['odds'] < MIN_ODDS or m['had_ft_at_first_sight']:
            continue  # vista pela 1.ª vez já com resultado: a hora do sinal não está provada
        k = (m['match_id'], m['type'], m['market'], m['selection'])
        b = out.get(k)
        if b is None:
            b = out[k] = {f: m.get(f) for f in ('match_id', 'type', 'phase', 'market', 'selection', 'odds', 'teams', 'date_utc',
                                                 'kickoff_utc', 'minute', 'share', 'mkt_volume', 'message_id')}
            b.update(n_signals=0, max_mult=1, odds_at_x4=None, odds_last=m['odds'], lead_min=lead_minutes(m))
        b['n_signals'] += 1
        b['odds_last'] = m['odds']
        b['max_mult'] = max(b['max_mult'], m.get('mult', 1))
        if m.get('mult', 1) >= 4 and b['odds_at_x4'] is None:
            b['odds_at_x4'] = m['odds']
    bets = sorted(out.values(), key=lambda b: (b['date_utc'], b['message_id']))
    for b in bets:
        b['group'] = group(b)
        f = ft.get(b['match_id'])
        b['ft'] = f
        b['w'] = settle(b['market'], b['selection'], f, b['teams']) if f else None
    return bets


def group(b):
    if b['type'] == 'BETFAIR':
        return 'BETFAIR/%s/%s' % (b['phase'], 'mkt>=10K' if b['mkt_volume'] >= 10000 else 'mkt<10K')
    if b['type'] == 'STAKE_LOAD':
        return 'STAKE_LOAD/%s' % b['phase']
    return b['type']


def label(b):
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
    msgs, ft, audit = load()
    bets = build_bets(msgs, ft)
    by = collections.OrderedDict()
    for b in bets:
        by.setdefault(b['group'], []).append(b)
    unparsed = [m for m in msgs if m.get('type') is None or m.get('error')]
    recent = [{'date_utc': b['date_utc'], 'group': b['group'], 'match': ' — '.join(b['teams'] or []) or b['match_id'], 'selection': label(b), 'odds': b['odds'], 'ft': b['ft'], 'w': b['w'],
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
