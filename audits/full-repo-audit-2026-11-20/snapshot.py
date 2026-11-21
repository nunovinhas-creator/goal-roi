"""Read the pinned checkout and print only aggregate audit evidence."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scangoal import (brain, certification, chronology, match_identity, notify, opportunity_forward,
                     opportunity_ranking, report)
from scripts.opportunity_forward_audit import PROTOCOL, sourced_results

REF = '9db4f20c614a645a4949f88562d5054ec53b8181'


def read(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))


def history_counts(path):
    raw = subprocess.check_output(['git', 'log', '--first-parent', '--format=%H', '--numstat',
                                   REF, '--', path], text=True)
    stats = [s.split('\t') for s in raw.splitlines() if '\t' in s]
    return {'commits_with_text_diff': len(stats),
            'commits_with_removed_lines': sum(x[1].isdigit() and int(x[1]) > 0 for x in stats)}


def audit():
    subprocess.run(['git', 'diff', '--quiet', REF, '--', '.github', 'data', 'docs', 'requirements.txt',
                    'scangoal', 'scripts', 'ops', 'tests', str(PROTOCOL.relative_to(ROOT))], check=True)
    data_path = ROOT / 'data'
    rows = [json.loads(line) for line in (data_path / 'messages.jsonl').read_text().splitlines() if line.strip()]
    raw = read('data/report.json')
    registry = read('data/hypotheses.json')
    state = read('data/notify_state.json')
    now = chronology.parse_utc(raw['generated_at_utc'])
    log, bad = notify.parse_log((data_path / 'notify_log.jsonl').read_bytes())
    deliveries = Counter(r['key'] for r in log if r['event'] == 'delivered')
    log_counts = Counter(r['event'] for r in log)

    evidence = {}
    messages, ft, marks, notes, _ = report.load(result_observations=evidence)
    versions = sourced_results(evidence, data_path)
    activation = state.get('migrations', {}).get('polymarket_over25_v1', {}).get('at_utc')
    rule = read(str(PROTOCOL.relative_to(ROOT)))
    forward = opportunity_forward.evaluate(messages, versions, rule, activation_utc=activation, now=now)
    ranking = opportunity_ranking.build(messages, versions, rule, activation_utc=activation, now=now)
    rank = ranking['ranking'][0] if ranking['ranking'] else {}
    common = ('forward_games', 'settled', 'ft_confirmed', 'wins', 'losses', 'pending', 'quarantined',
              'timeouts_counted_as_losses', 'roi_advertised', 'roi_odds_minus_2pct')

    computed = report.build(now=now)
    original = raw['questions']['Q7 Todas as apostas (referência)']
    replayed = computed['questions']['Q7 Todas as apostas (referência)']
    status = raw['brain'].get('validation') or {}
    providers = {}
    for family, path in (('highlightly', 'data/results_hl.json'), ('bsd', 'data/results_bsd.json')):
        provider = read(path)
        results, archived = provider['results'], provider.get('ft_versions', {})
        providers[family] = {'current_results': len(results), 'fixtures_with_versions': len(archived),
                             'current_only': len(results.keys() - archived.keys()),
                             'fixtures_with_score_changes': sum(
                                 len({tuple(observation['ft']) for observation in observations}) > 1
                                 for observations in archived.values()),
                             'last_request_count': provider.get('stats', {}).get('requests')}

    legacy, research = report.LAST['signals'], report.LAST['research_signals']
    left = {b['message_id']: b for b in legacy}
    right = {b['message_id']: b for b in research}
    shared = left.keys() & right.keys()
    transitions = Counter(f'{left[k]["w"]} -> {right[k]["w"]}' for k in shared
                          if left[k]['w'] != right[k]['w'])
    chains = read('data/collection_windows.json')['intervals']
    written_report = data_path / 'report.json'
    return {
        'ref': REF,
        'report_time_utc': raw['generated_at_utc'],
        'ledger': {
            'versions': len(rows),
            'distinct_messages': len({r['message_id'] for r in rows}),
            'distinct_messages_with_edits': sum(n > 1 for n in Counter(r['message_id'] for r in rows).values()),
            'duplicate_message_and_text_hash': len(rows) - len({(r['message_id'], r['sha256']) for r in rows}),
            'hash_mismatches': sum(r['sha256'] != hashlib.sha256(r['text'].encode()).hexdigest() for r in rows),
            'rows_with_button_metadata': sum(bool(r.get('buttons')) for r in rows),
            'rows_whose_edit_time_is_after_read_stamp': sum(
                chronology.parse_utc(r['edit_date_utc']) > chronology.parse_utc(r['collected_at_utc'])
                for r in rows if chronology.parse_utc(r.get('edit_date_utc')) and chronology.parse_utc(r.get('collected_at_utc'))),
            'first_parent': history_counts('data/messages.jsonl'),
            'collection_windows': dict(Counter(entry['status'] for entry in chains)),
        },
        'historical_q7': {key: original[key] for key in ('bets', 'settled', 'matches', 'wins', 'pushes', 'units', 'roi')},
        'historical_q7_reproduced': original == replayed,
        'groups_reproduced': raw['groups'] == computed['groups'],
        'data_and_docs_same_bytes': written_report.read_bytes() == (ROOT / 'docs/report.json').read_bytes(),
        'report_sha256': hashlib.sha256(written_report.read_bytes()).hexdigest(),
        'report_bytes': written_report.stat().st_size,
        'official_quality': {k: raw['audit'].get(k) for k in (
            'edits_changing_signal', 'bot_mark_disagreements', 'deleted_by_bot', 'ended_after_90',
            'hl_vs_bsd_disagreements', 'hl_vs_bot_disagreements', 'bsd_vs_bot_disagreements')},
        'providers': providers,
        'research_comparison': {
            'eligible_legacy_rows': len(left), 'eligible_research_rows': len(right), 'shared_message_ids': len(shared),
            'different_settlements_shared': dict(sorted(transitions.items())),
            'research_quarantined': sum(b.get('result_quarantined') is True for b in right.values()),
            'brain_published_counts': raw['brain']['counts'],
            'brain_published_eligible': raw['brain']['eligibility'],
            'brain_registry': len(registry), 'registry_source': dict(Counter(r['source'] for r in registry.values())),
            'validation_state': status.get('state'), 'certification_file_present': (data_path / certification.FILE).exists(),
            'certified_count': len(status.get('certified') or []),
            'llm_evaluation': raw['brain'].get('llm_evaluation', {}).get('verdict'),
            'llm_telemetry_last_run_utc': read('data/llm_state.json').get('last_run_utc'),
        },
        'polymarket_forward_at_report_time': {k: forward[k] for k in common} | {'status': forward['status']},
        'polymarket_ranking_matches_forward': all(forward.get(k) == rank.get(k) for k in common) if rank else None,
        'polymarket_rank': rank.get('evidence_status'),
        'polymarket_rank_baseline_overlap': ranking.get('overlap'),
        'notification': {
            'malformed_log_lines': bad, 'events': dict(log_counts),
            'duplicate_delivered_keys': sum(n > 1 for n in deliveries.values()),
            'state_at_end': dict(Counter(r['state'] for r in state.get('deliveries', {}).values())),
            'first_parent': history_counts('data/notify_log.jsonl'),
        },
    }


if __name__ == '__main__':
    print(json.dumps(audit(), ensure_ascii=False, sort_keys=True, indent=2))
