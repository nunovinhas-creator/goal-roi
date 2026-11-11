"""Relatório ScanGoal: ROI por grupo com regras fixas. Só contas; sem LLM, sem rede.

Regra: 1 unidade na seleção indicada, à odd do PRIMEIRO sinal de cada (jogo, tipo, mercado, seleção).
Sinal = primeira versão da mensagem; resultado = FT em qualquer versão de qualquer mensagem do mesmo jogo.
"""
import collections, json, math, sys
from pathlib import Path


from .parser import MIN_ODDS, parse, settle  # noqa: E402
from .store import data_dir  # noqa: E402

HAIRCUTS = (0.0, 0.02, 0.05)


SIGNAL_PART = lambda text: text.split('\n🏁 FT')[0].strip()


def load():
    first, ft = {}, {}
    load.edits_changing_signal = 0
    path = data_dir() / 'messages.jsonl'
    if not path.exists():
        return [], ft
    for line in path.open(encoding='utf-8'):
        if not line.strip():
            continue
        r = json.loads(line)
        p = parse(r['text'])
        if p.get('ft') and p.get('match_id'):
            ft[p['match_id']] = p['ft']
        if r['message_id'] in first and SIGNAL_PART(r['text']) != first[r['message_id']]['signal_text']:
            load.edits_changing_signal += 1  # o bot alterou o sinal (odd/seleção) depois de publicado
        if r['message_id'] not in first:  # ficheiro append-only: a primeira linha é a versão original
            first[r['message_id']] = dict(p, message_id=r['message_id'], date_utc=r['date_utc'],
                                          had_ft_at_first_sight='ft' in p, signal_text=SIGNAL_PART(r['text']))
    return [first[k] for k in sorted(first)], ft


def bets(msgs):
    out = {}
    for m in msgs:
        if not m.get('bet') or m['odds'] < MIN_ODDS or m['had_ft_at_first_sight']:
            continue  # mensagem vista pela 1.ª vez já com resultado: a hora do sinal não está provada
        k = (m['match_id'], m['type'], m['market'], m['selection'])
        if k not in out:
            out[k] = dict(m, n_signals=0, max_mult=1)
        out[k]['n_signals'] += 1
        out[k]['max_mult'] = max(out[k]['max_mult'], m.get('mult', 1))
    return list(out.values())


def group(b):
    if b['type'] == 'BETFAIR':
        return 'BETFAIR/%s/%s' % (b['phase'], 'mkt>=10K' if b['mkt_volume'] >= 10000 else 'mkt<10K')
    return b['type']


def summarise(rows):
    res = {}
    for hc in HAIRCUTS:
        pl = [(r['w'] * (r['odds'] * (1 - hc) - 1) if r['w'] > 0 else r['w']) for r in rows]
        n = len(pl)
        mean = sum(pl) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in pl) / (n - 1)) if n > 1 else float('nan')
        half = 1.96 * sd / math.sqrt(n) if n > 1 else float('nan')
        res['odd-%d%%' % round(hc * 100)] = {'roi': round(mean, 4), 'ci95': [round(mean - half, 4), round(mean + half, 4)] if n > 1 else None}
    return res


def main():
    msgs, ft = load()
    all_bets = bets(msgs)
    by = collections.defaultdict(lambda: {'bets': 0, 'settled': []})
    for b in all_bets:
        g = by[group(b)]
        g['bets'] += 1
        if b['match_id'] in ft:
            w = settle(b['market'], b['selection'], ft[b['match_id']])
            if w is not None:
                g['settled'].append({'w': w, 'odds': b['odds']})
    report = {'messages': len(msgs), 'types': collections.Counter(str(m.get('type')) for m in msgs),
              'unparsed': sum(1 for m in msgs if m.get('type') is None or m.get('error')),
              'excluded_first_seen_with_result': sum(1 for m in msgs if m.get('bet') and m['had_ft_at_first_sight']),
              'edited_versions_changing_signal': load.edits_changing_signal,
              'unique_bets': len(all_bets), 'groups': {}}
    for name, g in sorted(by.items()):
        n = len(g['settled'])
        report['groups'][name] = {'bets': g['bets'], 'settled': n, 'wins': sum(1 for r in g['settled'] if r['w'] > 0),
                                  'roi': summarise(g['settled']) if n else None,
                                  'note': 'amostra insuficiente (<300 liquidadas): sem conclusão' if n < 300 else 'ok'}
    out = data_dir() / 'report.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=1, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
