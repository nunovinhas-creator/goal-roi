"""P2 — auditoria prospetiva das observações Polymarket (só leitura; nunca escreve em data/ nem no repositório).

Uso (raiz do repositório):
    python -m scripts.polymarket_p2_audit                       # agora
    python -m scripts.polymarket_p2_audit --as-of 2026-12-01T00:00:00Z
    python -m scripts.polymarket_p2_audit --since 2026-11-24T00:00:00Z --out /tmp/p2.json

Denominador: TODOS os sinais elegíveis cuja primeira leitura cai em [since, as_of], com ou sem observação. `since` vem de
--since ou do primeiro início de execução registado (data/polymarket_observer_runs.jsonl); sem nenhum dos dois o
relatório devolve `since_required` (código 2) e nunca "zero elegíveis". Preço fresco = resposta nos 120 s E livro alterado
há no máximo 300 s; `stale_book` conta como resposta, nunca como preço. Sem dados de execução e liquidação não há ROI
líquido comprovado: o relatório não o calcula.
"""
import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from scangoal import chronology, polymarket_observer as po
from scangoal.eligibility import game_identity
from scangoal.store import data_dir
from scripts.polymarket_shadow import safe_output, write_output

VERDICT = 'RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.'
STAKES = ('10', '50', '100', '500')
UNRECORDED_STOP = timedelta(seconds=po.LIFETIME_SECONDS + 30)   # execução sem fim registado: limite máximo do processo


def parse(value):
    return chronology.parse_utc(value)


def received_at(record):
    return parse((record.get('quote') or {}).get('received_utc'))


def records_as_of(rows, as_of):
    """Só observações já recebidas no corte (sem dados futuros). A tentativa conta pela sua hora."""
    out = {}
    for row in rows:
        seen = received_at(row) or parse(row.get('attempted_utc'))
        if seen is None or seen > as_of:
            continue
        out.setdefault(row['key'], row)
    return out


def within_window(record, m):
    """Resposta recebida nos 120 s depois da 1.ª leitura do sinal e antes do início (recalculado, não confiado)."""
    received, read, kickoff = received_at(record), parse(m.get('collected_at_utc')), parse(m.get('kickoff_utc'))
    return (received is not None and read is not None and kickoff is not None
            and record.get('first_read_utc') == m.get('collected_at_utc')
            and timedelta(0) <= received - read <= po.WINDOW and received < kickoff)


def fresh_price(record, m):
    """Prova de preço fresco: resposta na janela E livro alterado há no máximo FRESH_BOOK_MAX_AGE. stale_book nunca."""
    age = record.get('source_age_seconds')
    return (record.get('status') == 'contemporaneous' and within_window(record, m)
            and bool(record.get('condition_id')) and bool(record.get('quote'))
            and isinstance(age, (int, float)) and not isinstance(age, bool)
            and -2 <= age <= po.FRESH_BOOK_MAX_AGE.total_seconds())


def depth(record, stake):
    fills = (record.get('quote') or {}).get('fills') or []
    return any(f.get('stake_usdc') == stake and f.get('filled') is True for f in fills)


def net_odds(record, stake='10'):
    for f in (record.get('quote') or {}).get('fills') or []:
        if f.get('stake_usdc') == stake and f.get('filled') and f.get('net_decimal_odds'):
            return Decimal(f['net_decimal_odds'])
    return None


def run_intervals(runs, as_of):
    """[(início, fim, fim_registado)] das execuções do observador conhecidas no corte."""
    starts, stops = {}, {}
    for event in runs:
        at = parse(event.get('at_utc'))
        if at is None or at > as_of:
            continue
        (starts if event.get('event') == 'start' else stops)[event.get('run_id')] = (at, event)
    out = []
    for run_id, (at, _) in starts.items():
        stop = stops.get(run_id)
        end = stop[0] if stop else min(at + UNRECORDED_STOP, as_of)
        out.append((at, end, stop is not None, stop[1] if stop else None))
    return sorted(out, key=lambda item: item[0])


def resolve_since(since, intervals, records):
    if since is not None:
        return since, 'explicit'
    if intervals:
        return intervals[0][0], 'observer_runs'
    starts = [parse((r.get('run') or {}).get('started_utc')) for r in records.values()]
    starts = [s for s in starts if s is not None]
    if starts:
        return min(starts), 'observation_records'
    return None, None


def audit(messages, first, rows, *, as_of, since=None, runs=()):
    base = {'schema': 'goal-roi/polymarket-p2-audit/v2', 'as_of_utc': po.iso(as_of), 'scientific_claim': 'none',
            'execution_proven': False, 'roi_net_proven': None, 'verdict': VERDICT,
            'fresh_book_max_age_seconds': po.FRESH_BOOK_MAX_AGE.total_seconds()}
    records = records_as_of(rows, as_of)
    intervals = run_intervals(runs, as_of)
    since, source = resolve_since(since, intervals, records)
    if since is None:
        # Sem execução registada nem --since, o período é desconhecido: nunca responder "zero sinais elegíveis".
        return dict(base, status='since_required', since_utc=None, eligible_signals=None,
                    note='no verifiable activation in the cut: pass --since explicitly')
    eligible = []
    for m in messages:
        mid = m.get('message_id')
        read = parse(m.get('collected_at_utc'))
        if mid not in first or read is None or not (since <= read <= as_of) or not po.eligible_signal(m):
            continue
        eligible.append((m, '%d:%s' % (mid, first[mid][0])))
    statuses = {s: 0 for s in po.STATUSES}
    unobserved = {'observer_running': 0, 'observer_absent': 0}
    games, games_fresh = set(), set()
    depth_counts = {s: 0 for s in STAKES}
    comparisons, responses_within, fresh, stale_within = [], 0, 0, 0
    for m, key in eligible:
        record = records.get(key)
        games.add(game_identity(m))
        if record is None:
            read = parse(m.get('collected_at_utc'))
            running = any(start <= read <= end for start, end, _, _ in intervals)
            unobserved['observer_running' if running else 'observer_absent'] += 1
            continue
        statuses[record['status']] += 1
        within = within_window(record, m)
        responses_within += within
        if record['status'] == 'stale_book' and within:
            stale_within += 1
        if not fresh_price(record, m):
            continue
        fresh += 1
        games_fresh.add(game_identity(m))
        for stake in STAKES:
            depth_counts[stake] += depth(record, stake)
        net = net_odds(record)
        if net is not None and isinstance(m.get('odds'), (int, float)):
            comparisons.append(float(net) / float(m['odds']))
    total = len(eligible)
    pct = lambda part, whole: round(100 * part / whole, 2) if whole else None
    stops = [stop for _, _, recorded, stop in intervals if recorded]
    return dict(
        base, status='activated' if intervals or source == 'observation_records' else 'explicit_period_no_observer_runs',
        since_utc=po.iso(since), since_source=source,
        eligible_signals=total, eligible_games=len(games),
        contract_identified=sum(1 for _, key in eligible if (records.get(key) or {}).get('condition_id')),
        responses_within_120s=responses_within,
        fresh_price_within_120s=fresh,
        stale_book_within_120s=stale_within,
        without_observation=sum(unobserved.values()),
        without_observation_by_observer=unobserved,
        statuses=dict(statuses, not_observed=sum(unobserved.values())),
        depth_filled_fresh={s + '_usdc': depth_counts[s] for s in STAKES},
        coverage_fresh_price_pct=pct(fresh, total),
        coverage_responses_within_120s_pct=pct(responses_within, total),
        coverage_games_fresh_pct=pct(len(games_fresh), len(games)),
        games_without_fresh_proof=len(games) - len(games_fresh),
        observer={'runs': len(intervals), 'runs_without_recorded_stop': sum(1 for i in intervals if not i[2]),
                  'runs_with_zero_observations': sum(1 for stop in stops if stop.get('observations') == 0),
                  'requests': sum(stop.get('requests') or 0 for stop in stops)},
        bot_vs_net_odds_10_usdc={
            'n': len(comparisons),
            'median_ratio_net_over_bot': round(statistics.median(comparisons), 4) if comparisons else None,
            'net_at_least_bot': sum(1 for r in comparisons if r >= 1),
            'excluded_not_fresh': responses_within - fresh,
            'note': 'descriptive only; fresh book proof required; a contemporaneous quote is not an executed bet',
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
    _, runs = po.journal_rows(directory / po.RUNS_JOURNAL)
    result = audit(messages, first, rows, as_of=as_of, since=since, runs=runs)
    text = json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + '\n'
    if out:
        write_output(out, text)
    else:
        sys.stdout.write(text)
    return 2 if result['status'] == 'since_required' else 0


if __name__ == '__main__':
    sys.exit(main())
