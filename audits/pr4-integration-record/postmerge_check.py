"""Verificação pós-merge da PR 4 (só leitura): uma recolha `after` face ao commit de merge `before`."""
import json
import subprocess
import sys

ROOT = '/home/user/goal-roi'
BASELINE = {
    'Betfair | Dia = dia de semana': 'abandonada',
    'Betfair | Reforço = sem reforço': 'abandonada',
    'Jogo aberto | Marcador no momento = diferença de 2 ou mais golos': 'promissora',
    'Jogo aberto | Marcador no momento = diferença de 2 ou mais golos & Outros sinais no mesmo jogo = jogo com um só tipo de sinal': 'promissora',
    'Jogo aberto | Marcador no momento = diferença de 2 ou mais golos & Sinal anterior de outro tipo no mesmo jogo = não': 'promissora',
    'Jogo aberto | Ritmo indicado pelo bot = menos de 90': 'promissora',
    'Polymarket | Faixa de odd = odd 1,50 a 2,00': 'abandonada',
}


def show(ref, path):
    return json.loads(subprocess.run(['git', '-C', ROOT, 'show', '%s:%s' % (ref, path)], capture_output=True, check=True).stdout)


before, after = sys.argv[1], sys.argv[2]
pc = subprocess.run([sys.executable, 'scripts/post_cutover_check.py', before, after], cwd=ROOT, capture_output=True, text=True)
rb, ra = show(before, 'data/report.json'), show(after, 'data/report.json')
sb, sa = show(before, 'data/notify_state.json'), show(after, 'data/notify_state.json')
pages = show(after, 'docs/report.json')
le = ra['brain'].get('llm_evaluation') or {}
out = {
    'before': before, 'after': after,
    'post_cutover_check_exit': pc.returncode, 'post_cutover_check_tail': pc.stdout.strip().split('\n')[-12:],
    'report_generated_at_utc': ra.get('generated_at_utc'),
    'llm_evaluation': le, 'llm_evaluation_ok': le.get('protocol') == 'llm_incremental_v5' and le.get('verdict') == 'não verificada',
    'page_report_has_llm_evaluation_v5': (pages['brain'].get('llm_evaluation') or {}).get('protocol') == 'llm_incremental_v5',
    'status_list_equals_baseline_7': ra['brain']['status_list'] == BASELINE,
    'status_list_changes': {k: (rb['brain']['status_list'].get(k), v) for k, v in ra['brain']['status_list'].items() if rb['brain']['status_list'].get(k) != v},
    'validation_state': ra['brain']['validation']['state'], 'certified': ra['brain']['validation'].get('certified'),
    'track_is_descriptive': ra['brain']['track'].get('criterion') is None and 'llm_as_expected' not in ra['brain']['track'],
    'telegram': {'sent_total_before': sb.get('sent_total'), 'sent_total_after': sa.get('sent_total'),
                 'watch_sent_before': len(sb.get('watch_sent') or []), 'watch_sent_after': len(sa.get('watch_sent') or []),
                 'markers_unchanged': sb.get('migrations') == sa.get('migrations'),
                 'statuses_unchanged': sb.get('statuses') == sa.get('statuses'),
                 'statuses_equal_report': sa.get('statuses') == ra['brain']['status_list'],
                 'new_watch_keys': sorted(set(sa.get('watch_sent') or []) - set(sb.get('watch_sent') or []))},
}
llm_state = show(after, 'data/llm_state.json')
out['llm_state'] = {k: llm_state.get(k) for k in ('last_run_utc', 'last_outcome', 'last_mode', 'last_accepted', 'last_rejected',
                                                  'rejected_equivalent', 'last_rejected_equivalent', 'neurons_today')}
print(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True))
