"""PR 4 (protocolo v3, P1-D): calibração do teste sob ausência de utilidade incremental e efeito da paragem oportunista.

Uso (raiz do repositório): python3.11 audits/pr4-evidence/calibration_check.py [saída.json]
Simulação pura (sem dados de produção, sem rede), sementes fixas: reprodutível byte a byte.

1. Calibração: em cenários SEM utilidade incremental (os dois lados com a mesma esperança de lucro; efeito comum por dia;
   tamanhos diários desiguais; cotações até 6), mede a fração de p <= 0,05 do teste do v3 (`llm_eval.compare`, inversão de
   sinais por dia) e, como referência, do bootstrap por blocos de dia do v2 (reproduzido aqui sem alterações).
2. Paragem oportunista: sob a hipótese nula, a fração de séries em que ALGUM olhar (dias 14, 16, ..., 30) tem p <= 0,05,
   contra a fração em que o olhar único no horizonte (dia 30) o tem.
3. Poder sob um efeito positivo (+10 % na probabilidade de ganhar das apostas LLM), só para referência.
Código de saída: 0 = o teste do v3 fica dentro de α + 3 erros-padrão de Monte Carlo em todos os cenários nulos; 1 = não fica.
"""
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scangoal import llm_eval  # noqa: E402

ALPHA, B = 0.05, 1000
MIX = (1.5, 1.8, 2.0, 2.5, 3.5, 5.0)


def v2_bootstrap_p(test_units, ctrl_units, replicates=B, seed=llm_eval.SEED):
    """Bootstrap por blocos de dia do protocolo v2 (§6 do v2), reproduzido como referência."""
    tx, nt = llm_eval._by_day(test_units)
    cx, nc = llm_eval._by_day(ctrl_units)
    if not nt or not nc:
        return None
    days, rng, stats, zero = sorted(set(tx) | set(cx)), random.Random(seed), [], (0.0, 0)
    for _ in range(replicates):
        st = sn = cs = cn = 0
        for _ in days:
            day = days[rng.randrange(len(days))]
            a, b = tx.get(day, zero), cx.get(day, zero)
            st, sn, cs, cn = st + a[0], sn + a[1], cs + b[0], cn + b[1]
        if sn and cn:
            stats.append(st / sn - cs / cn)
    return (1 + sum(1 for x in stats if x <= 0)) / (1 + len(stats)) if stats else None


def simulate(rng, days, n_llm, n_ctrl, day_sd, odds_llm=MIX, odds_ctrl=MIX, edge=0.0):
    """Um conjunto de dados com `days` dias. Probabilidade de ganhar = (1 + edge + 2·efeito do dia) / odd: sem edge, os dois
    lados têm exatamente a mesma esperança de lucro, qualquer que seja a mistura de cotações."""
    test, ctrl = {}, {}
    for d in range(days):
        day, shift = '2026-12-%02d' % (d + 1), rng.gauss(0, day_sd)
        for side, mean_n, out, odds, e in (('L', n_llm, test, odds_llm, edge), ('C', n_ctrl, ctrl, odds_ctrl, 0.0)):
            for k in range(max(1, int(rng.expovariate(1 / mean_n)))):
                o = rng.choice(odds)
                win = rng.random() < min(0.97, max(0.01, (1 + e + 2 * shift) / o))
                out['%s%d-%d' % (side, d, k)] = (day, (o * 0.98 - 1) if win else -1.0, None)
    return test, ctrl


def upto(units, last):
    return {g: v for g, v in units.items() if v[0] <= last}


def main(argv):
    llm_eval.REPLICATES = B
    out = {'alpha': ALPHA, 'replicates_per_test': B, 'null_scenarios': [], 'optional_stopping': None, 'power': [],
           'meaning': 'null scenarios have no incremental utility: same expected profit on both sides'}
    nulls = [('14 dias, efeito de dia moderado', 14, 15, 15, 0.10, MIX, MIX, 2000, 1),
             ('21 dias, efeito de dia forte', 21, 10, 15, 0.20, MIX, MIX, 2000, 2),
             ('30 dias, efeito de dia forte', 30, 8, 12, 0.20, MIX, MIX, 2000, 3),
             ('21 dias, LLM em cotações longas, controlos curtas', 21, 10, 15, 0.10, (3.5, 5.0, 6.0), (1.4, 1.6, 1.8), 2000, 4),
             ('21 dias, LLM em cotações curtas, controlos longas', 21, 10, 15, 0.10, (1.4, 1.6, 1.8), (3.5, 5.0, 6.0), 2000, 5)]
    ok = True
    for name, days, nl, nc, sd, ol, oc, sims, seed in nulls:
        rng, v3, v2 = random.Random(seed), 0, 0
        for _ in range(sims):
            test, ctrl = simulate(rng, days, nl, nc, sd, ol, oc)
            v3 += llm_eval.compare([(1.0, test)], [(1.0, ctrl)])['p'] <= ALPHA
            p2 = v2_bootstrap_p([(1.0, test)], [(1.0, ctrl)])
            v2 += p2 is not None and p2 <= ALPHA
        se = math.sqrt(ALPHA * (1 - ALPHA) / sims)
        within = v3 / sims <= ALPHA + 3 * se
        ok = ok and within
        out['null_scenarios'].append({'scenario': name, 'sims': sims, 'v3_signflip_rejection': round(v3 / sims, 4),
                                      'v2_bootstrap_rejection': round(v2 / sims, 4), 'monte_carlo_se': round(se, 4),
                                      'v3_within_alpha_plus_3se': within})
    rng, sims, looks = random.Random(6), 600, list(range(14, 31, 2))
    single = naive = 0
    llm_eval.REPLICATES = 500
    for _ in range(sims):
        test, ctrl = simulate(rng, 30, 8, 12, 0.15)
        ps = [llm_eval.compare([(1.0, upto(test, '2026-12-%02d' % k))], [(1.0, upto(ctrl, '2026-12-%02d' % k))])['p'] for k in looks]
        single += ps[-1] <= ALPHA
        naive += any(p <= ALPHA for p in ps)
    out['optional_stopping'] = {'sims': sims, 'looks_days': looks, 'replicates_per_test': 500,
                                'single_look_at_horizon_rejection': round(single / sims, 4),
                                'stop_at_first_significant_look_rejection': round(naive / sims, 4)}
    llm_eval.REPLICATES = B
    for name, edge in (('efeito +10 %', 0.10), ('efeito +20 %', 0.20)):
        rng, sims, v3 = random.Random(7), 1000, 0
        for _ in range(sims):
            test, ctrl = simulate(rng, 30, 8, 12, 0.10, edge=edge)
            v3 += llm_eval.compare([(1.0, test)], [(1.0, ctrl)])['p'] <= ALPHA
        out['power'].append({'scenario': name, 'days': 30, 'sims': sims, 'v3_signflip_power': round(v3 / sims, 4)})
    out['ok'] = ok
    text = json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + '\n'
    if argv:
        Path(argv[0]).write_text(text, encoding='utf-8')
    print(text)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
