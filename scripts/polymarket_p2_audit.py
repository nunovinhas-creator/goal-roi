"""P2 — auditoria prospetiva das observações Polymarket (só leitura; nunca escreve em data/ nem no repositório).

Uso (raiz do repositório):
    python -m scripts.polymarket_p2_audit                       # agora
    python -m scripts.polymarket_p2_audit --as-of 2026-12-01T00:00:00Z
    python -m scripts.polymarket_p2_audit --since 2026-11-24T00:00:00Z --out /tmp/p2.json

Denominador: TODOS os sinais elegíveis cuja primeira leitura cai em [since, as_of], com ou sem observação. Sem dados de
execução e liquidação não há ROI líquido comprovado: o relatório não o calcula.
"""
import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from decimal import Decimal

from scangoal import chronology, polymarket_observer as po
from scangoal.eligibility import game_identity
from scangoal.store import data_dir
from scripts.polymarket_shadow import safe_output, write_output

VERDICT = 'RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.'
STAKES = ('10', '50', '100', '500')
WITHIN = ('contemporaneous', 'stale_book')


def parse(value):
    return chronology.parse_utc(value)


def records_as_of(rows, as_of):
    """Só observações já recebidas no corte (sem dados futuros). A tentativa conta pela sua hora."""
    out = {}
    for row in rows:
        seen = parse(((row.get('quote') or {}).get('received_utc'))) or parse(row.get('attempted_utc'))
        if seen is None or seen > as_of:
            continue
        out.setdefault(row['key'], row)
    return out


def depth(record, stake):
    fills = (record.get('quote') or {}).get('fills') or []
    return any(f.get('stake_usdc') == stake and f.get('filled') is True for f in fills)


def net_odds(record, stake='10'):
    for f in (record.get('quote') or {}).get('fills') or []:
        if f.get('stake_usdc') == stake and f.get('filled') and f.get('net_decimal_odds'):
            return Decimal(f['net_decimal_odds'])
    return None


def audit(messages, first, rows, *, as_of, since=None):
    if since is None:
        starts = [parse((r.get('run') or {}).get('started_utc')) for r in rows]
        starts = [s for s in starts if s is not None and s <= as_of]
        since = min(starts) if starts else None
    base = {'schema': 'goal-roi/polymarket-p2-audit/v1', 'as_of_utc': po.iso(as_of),
            'since_utc': po.iso(since) if since else None, 'scientific_claim': 'none',
            'execution_proven': False, 'roi_net_proven': None, 'verdict': VERDICT}
    if since is None:
        return dict(base, status='not_activated', eligible_signals=0)
    records = records_as_of(rows, as_of)
    eligible = []
    for m in messages:
        mid = m.get('message_id')
        read = parse(m.get('collected_at_utc'))
        if mid not in first or read is None or not (since <= read <= as_of) or not po.eligible_signal(m):
            continue
        eligible.append((m, '%d:%s' % (mid, first[mid][0])))
    statuses = {s: 0 for s in po.STATUSES}
    statuses['not_observed'] = 0
    games, games_proven = set(), set()
    depth_counts = {s: 0 for s in STAKES}
    comparisons = []
    for m, key in eligible:
        record = records.get(key)
        games.add(game_identity(m))
        if record is None:
            statuses['not_observed'] += 1
            continue
        statuses[record['status']] += 1
        if record['status'] in WITHIN and record.get('evidence_for_signal'):
            games_proven.add(game_identity(m))
            for stake in STAKES:
                depth_counts[stake] += depth(record, stake)
            net = net_odds(record)
            if net is not None and isinstance(m.get('odds'), (int, float)):
                comparisons.append(float(net) / float(m['odds']))
    total = len(eligible)
    within = statuses['contemporaneous'] + statuses['stale_book']
    identified = sum(1 for _, key in eligible if (records.get(key) or {}).get('condition_id'))
    return dict(
        base, status='activated', eligible_signals=total, eligible_games=len(games),
        contract_identified=identified, observed_within_120s=within,
        observed_within_120s_fresh_book=statuses['contemporaneous'], without_observation=statuses['not_observed'],
        statuses=statuses,
        depth_filled_within_120s={s + '_usdc': depth_counts[s] for s in STAKES},
        coverage_signals_pct=round(100 * within / total, 2) if total else None,
        coverage_games_pct=round(100 * len(games_proven) / len(games), 2) if games else None,
        games_without_proof=len(games) - len(games_proven),
        bot_vs_net_odds_10_usdc={
            'n': len(comparisons),
            'median_ratio_net_over_bot': round(statistics.median(comparisons), 4) if comparisons else None,
            'net_at_least_bot': sum(1 for r in comparisons if r >= 1),
            'note': 'descriptive only; a contemporaneous quote is not an executed bet',
        })


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--as-of')
    parser.add_argument('--since')
    parser.add_argument('--out')
    args = parser.parse_args(argv)
    out = safe_output(args.out) if args.out else None
    as_of = parse(args.as_of) if args.as_of else datetime.now(timezone.utc)
    since = parse(args.since) if args.since else None
    if as_of is None or (args.since and since is None):
        parser.error('times need an explicit UTC offset')
    from scangoal import report
    messages, *_ = report.load(result_observations={}, as_of=as_of)
    directory = data_dir()
    first = po.first_versions(directory / 'messages.jsonl')
    _, rows = po.journal_rows(directory / po.JOURNAL)
    text = json.dumps(audit(messages, first, rows, as_of=as_of, since=since), ensure_ascii=False, indent=1,
                      sort_keys=True) + '\n'
    if out:
        write_output(out, text)
    else:
        sys.stdout.write(text)


if __name__ == '__main__':
    main()
