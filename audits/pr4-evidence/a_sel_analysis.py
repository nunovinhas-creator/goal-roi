"""PR 4 (auditoria #5917252470, ponto 2): a suposição A-sel é respeitada pelo próprio sistema?

Uso (raiz de um clone completo): python3.11 audits/pr4-evidence/a_sel_analysis.py <commit de main> [saída.json]
Só leitura e sem rede; sementes fixas.

A-sel (§6-A do protocolo v4): a entrada de um jogo num dia não depende dos resultados de outros jogos desse dia.
O sistema tem um mecanismo próprio que a pode violar: o modelo de linguagem propõe hipóteses de hora a hora, vendo os resultados
já conhecidos (incluindo os do próprio dia), e uma hipótese registada a meio do dia d (e os seus controlos) contribui com jogos
desse mesmo dia d. Se a criação dessas hipóteses depender dos resultados da manhã, a composição do dia depende de resultados do
dia.

1. Dados reais: em que fração dos jogos LLM admitidos (janela pós-registo, que é a única com dados) a hipótese que os admite nasceu
   no próprio dia UTC da decisão do jogo? E quantos dias têm pelo menos uma hipótese nascida nesse dia a contribuir?
2. Simulação do mecanismo com o e-processo de produção, sob apostas individualmente justas: (a) hipóteses novas nascem a meio do
   dia independentemente dos resultados (A-sel respeitada); (b) nascem sobretudo depois de uma manhã má (A-sel violada pelo
   sistema); (c) como (b), mas com a regra "uma hipótese só contribui a partir do dia UTC seguinte ao seu nascimento".
"""
import io
import json
import math
import os
import random
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def clopper_pearson(k, n, conf=0.95):
    from calibration_check import clopper_pearson as cp   # noqa: E402  (mesma função das outras evidências)
    return cp(k, n, conf)


def real_data(ref):
    from scangoal import brain, chronology, insights, llm_eval
    from scangoal.eligibility import eligible
    archive = subprocess.run(['git', '-C', str(ROOT), 'archive', '--format=tar', ref, 'data'], capture_output=True, check=True).stdout
    when = chronology.parse_utc(subprocess.run(['git', '-C', str(ROOT), 'show', '-s', '--format=%cI', ref], capture_output=True, text=True).stdout.strip())
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(tmp, filter='data')
        os.environ['SCANGOAL_DATA_DIR'] = str(Path(tmp) / 'data')
        from scangoal import report
        report.build(now=when)
        signals = [b for b in report.LAST['signals'] if eligible(b)]
        registry = brain.load_registry(Path(tmp) / 'data' / 'hypotheses.json')
    first, problems = llm_eval.registry_history(ROOT, ref)
    born = llm_eval.births(registry, first, not problems)
    multi, earlier = brain.multi_matches(signals), brain.earlier_index(signals)
    have = [(b, insights.family(b), frozenset(brain.atoms(b, multi, earlier))) for b in signals]
    games, same_day, days, days_with_same_day, hours = 0, 0, set(), set(), []
    for hid, h in sorted(registry.items()):
        if h.get('source') != 'llm' or born.get(hid) is None:
            continue
        conds = {tuple(c) for c in h['conditions']}
        match = [b for b, f, at in have if f == h['family'] and conds <= at]
        for g, (day, _, t, _) in llm_eval.admitted(match, born[hid]).items():
            games += 1
            days.add(day)
            if day == born[hid].date().isoformat():
                same_day += 1
                days_with_same_day.add(day)
                hours.append((t - born[hid]).total_seconds() / 3600)
    hours.sort()
    births_by_hour = {}
    for hid, h in registry.items():
        if h.get('source') == 'llm' and born.get(hid) is not None:
            births_by_hour[born[hid].hour] = births_by_hour.get(born[hid].hour, 0) + 1
    return {'ref': ref, 'llm_hypotheses_with_birth': sum(1 for hid, h in registry.items() if h.get('source') == 'llm' and born.get(hid)),
            'admitted_llm_games_post_registration': games, 'games_on_the_birth_day': same_day,
            'share_on_the_birth_day': round(same_day / games, 4) if games else None,
            'days_with_llm_games': len(days), 'days_with_a_same_day_hypothesis': len(days_with_same_day),
            'hours_after_birth_median': round(hours[len(hours) // 2], 2) if hours else None,
            'llm_births_by_utc_hour': dict(sorted(births_by_hour.items()))}


def simulate(rng, days, mode):
    """Apostas individualmente justas (odd 2, P(ganhar) = 0,49 dos dois lados). Manhã: 6 jogos LLM de hipóteses antigas e 10
    de controlo; tarde: 6 jogos de controlo e, se nascer uma hipótese nova nesse dia, 6 jogos LLM dela."""
    T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)
    test, ctrl, pending = {}, {}, 0
    for d in range(days):
        day = (T0 + timedelta(days=d)).date().isoformat()
        bet = lambda: 0.96 if rng.random() < 0.49 else -1.0
        morning = [bet() for _ in range(6)]
        for k, v in enumerate(morning):
            test['m%d-%d' % (d, k)] = (day, v, T0, 0.96)
        for k in range(16):
            ctrl['c%d-%d' % (d, k)] = (day, bet(), T0, 0.96)
        if mode == 'independent':
            born_today = rng.random() < 0.5
        else:
            born_today = (sum(morning) < 0 and rng.random() < 0.9) or rng.random() < 0.1
        if mode == 'next_day_rule':
            if pending:
                for k in range(6):
                    test['a%d-%d' % (d, k)] = (day, bet(), T0, 0.96)
            pending = born_today
        elif born_today:
            for k in range(6):
                test['a%d-%d' % (d, k)] = (day, bet(), T0, 0.96)
    return [(1.0, test)], [(1.0, ctrl)]


def main(argv):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from scangoal import llm_eval
    out = {'real_data': real_data(argv[0]), 'simulation': []}
    llm_eval.BOOT_REPLICATES = 9
    T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)
    for mode, label in (('independent', 'nascimentos a meio do dia independentes dos resultados (A-sel respeitada)'),
                        ('after_bad_morning', 'nascimentos sobretudo depois de uma manhã má (A-sel violada pelo sistema)'),
                        ('next_day_rule', 'como o anterior, mas a hipótese só contribui a partir do dia seguinte')):
        for days in (120, 365):
            rng, sims, hits = random.Random(7 + days + len(mode)), 400, 0
            for _ in range(sims):
                hits += llm_eval.eprocess(*simulate(rng, days, mode), T0 + timedelta(days=days + 10), None)['k_sup'] >= 20
            lo, hi = clopper_pearson(hits, sims)
            out['simulation'].append({'mode': mode, 'scenario': label, 'days': days, 'sims': sims, 'rejection': round(hits / sims, 4),
                                      'ci95_exact': [lo, hi]})
    text = json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + '\n'
    if len(argv) > 1:
        Path(argv[1]).write_text(text, encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
