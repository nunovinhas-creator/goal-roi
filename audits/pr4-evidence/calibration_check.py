"""PR 4 (protocolo v4, P1-E): erro de tipo I do e-processo sob nulas adversariais, fronteira A-sel e poder.

Uso (raiz do repositório): python3.11 audits/pr4-evidence/calibration_check.py [saída.json]
Simulação pura (sem dados de produção, sem rede), sementes fixas: reprodutível byte a byte. Usa a função de produção
`llm_eval.eprocess` (a mesma que a avaliação), com o diagnóstico bootstrap-t reduzido a 9 réplicas (não entra no veredicto).

A garantia é um teorema (§6-A do protocolo): sob H0, P(alguma vez K >= 20) <= 0,05. Esta simulação não a "prova"; verifica que
a implementação respeita as hipóteses do teorema (X >= −1, λ ∈ [0, 1/2] previsível) e que, em cenários nulos adversariais, a
fração de rejeições a qualquer momento fica abaixo de 0,05 (o limite superior do intervalo de Monte Carlo a 95 % é mostrado).
Mostra também a fronteira declarada A-sel (composição do dia dependente dos resultados desse dia), onde H0 deixa de valer.

Cenários nulos (mesma esperança de lucro por jogo nos dois lados; 5 % de resultados em falta; 120 dias, rejeição a qualquer momento):
- mistura igual, efeito de dia forte;
- LLM em cotações curtas e controlos em cotações muito longas (o caso que derrubou o v3), e o inverso;
- poucos jogos por dia;
- dependência forte dentro do dia (o mesmo resultado para todos os jogos do dia de um lado);
- seleção adaptativa entre dias (número de jogos LLM decidido pelos resultados do dia anterior).
Código de saída: 0 = todas as hipóteses do teorema respeitadas e todas as nulas com rejeição <= 0,05; 1 = não.
"""
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scangoal import llm_eval  # noqa: E402

T0 = datetime(2026, 12, 1, tzinfo=timezone.utc)
MIX, SHORT, VERY_LONG = (1.5, 1.8, 2.0, 2.5, 3.5, 5.0), (1.4, 1.6, 1.8), (6.0, 8.0, 10.0)


def world(rng, days, odds_l, odds_c, n_l, n_c, day_sd, edge=0.0, clustered=False, adaptive=False, missing=0.05):
    test, ctrl, last = {}, {}, 0.0
    for d in range(days):
        day, shift = (T0 + timedelta(days=d)).date().isoformat(), rng.gauss(0, day_sd)
        nl = (2 if last > 0 else 15) if adaptive else max(1, int(rng.expovariate(1 / n_l)))
        total = 0.0
        for side, n, out, odds, e in (('L', nl, test, odds_l, edge), ('C', max(1, int(rng.expovariate(1 / n_c))), ctrl, odds_c, 0.0)):
            common = rng.random()
            for k in range(n):
                o = rng.choice(odds)
                top = o * 0.98 - 1
                u = common if clustered else rng.random()
                v = top if u < min(0.97, max(0.01, (1 + e + 2 * shift) / o)) else -1.0
                if rng.random() < missing:
                    v = None
                out['%s%d-%d' % (side, d, k)] = (day, v, T0 + timedelta(days=d, hours=12), top)
                total += (v or 0.0) * (1 if side == 'L' else -1)
        last = total
    return [(1.0, test)], [(1.0, ctrl)]


def _binom_cdf(k, n, p):
    """P(X <= k) para X ~ Binomial(n, p), exata (inteiros grandes e floats; n até alguns milhares)."""
    if p <= 0:
        return 1.0
    if p >= 1:
        return 1.0 if k >= n else 0.0
    return min(1.0, sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1)))


def _solve(f, target, lo=0.0, hi=1.0):
    """f decrescente em p; p com f(p) = target, por bisseção (precisão 1e-9)."""
    for _ in range(80):
        mid = (lo + hi) / 2
        if f(mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def clopper_pearson(k, n, conf=0.95):
    """Intervalo exato de Clopper-Pearson para k sucessos em n: [lo, hi] bilateral. Com k = 0, lo = 0 e hi = 1 − (α/2)^(1/n)
    (o intervalo de Wald dá [0, 0], o que é errado: zero rejeições observadas não provam uma probabilidade nula)."""
    a = (1 - conf) / 2
    lo = 0.0 if k == 0 else _solve(lambda p: _binom_cdf(k - 1, n, p), 1 - a)
    hi = 1.0 if k == n else _solve(lambda p: _binom_cdf(k, n, p), a)
    return round(lo, 6), round(hi, 6)


def upper_one_sided(k, n, conf=0.95):
    """Limite superior exato unilateral (Clopper-Pearson): P(X <= k | p) = 1 − conf."""
    return 1.0 if k == n else round(_solve(lambda p: _binom_cdf(k, n, p), 1 - conf), 6)


def rate(hits, sims):
    lo, hi = clopper_pearson(hits, sims)
    return {'rejection': round(hits / sims, 4), 'rejections': hits, 'sims': sims, 'ci95_exact': [lo, hi],
            'upper95_one_sided_exact': upper_one_sided(hits, sims)}


def main(argv):
    llm_eval.BOOT_REPLICATES = 9
    out = {'alpha': llm_eval.ALPHA, 'threshold': 1 / llm_eval.ALPHA, 'lambda_start': llm_eval.LAMBDA_START,
           'lambda_cap': llm_eval.LAMBDA_CAP, 'null': [], 'a_sel_boundary': None, 'power': [],
           'meaning': 'anytime rejection = max over closed days of K >= 20; null = equal expected profit per game on both sides'}
    ok = True
    nulls = [('mistura igual, efeito de dia forte', MIX, MIX, 8, 12, 0.20, {}),
             ('LLM curtas, controlos muito longas', SHORT, VERY_LONG, 10, 15, 0.10, {}),
             ('LLM muito longas, controlos curtas', VERY_LONG, SHORT, 10, 15, 0.10, {}),
             ('poucos jogos por dia, cotações opostas', SHORT, VERY_LONG, 3, 4, 0.10, {}),
             ('dependência total dentro do dia', MIX, MIX, 8, 12, 0.10, {'clustered': True}),
             ('seleção adaptativa entre dias', SHORT, VERY_LONG, 8, 12, 0.10, {'adaptive': True})]
    days, sims = 120, 1000
    clock = T0 + timedelta(days=days + 10)
    for i, (name, ol, oc, nl, nc, sd, kw) in enumerate(nulls):
        rng, hits, theorem = random.Random(500 + i), 0, True
        for _ in range(sims):
            res = llm_eval.eprocess(*world(rng, days, ol, oc, nl, nc, sd, **kw), clock, None)
            theorem = theorem and all(row['x'] >= -1 and 0 <= row['lambda'] <= 0.5 for row in res['path'])
            hits += res['k_sup'] >= 20
        row = dict(rate(hits, sims), scenario=name, days=days, theorem_conditions_hold=theorem)
        ok = ok and theorem and row['rejection'] <= llm_eval.ALPHA
        out['null'].append(row)
    rng, hits, sims_b = random.Random(900), 0, 400
    for _ in range(sims_b):
        test, ctrl = {}, {}
        for d in range(120):
            day = (T0 + timedelta(days=d)).date().isoformat()
            for k in range(10):                                   # LLM pára no primeiro ganho do dia (viola A-sel)
                win = rng.random() < 0.49
                test['L%d-%d' % (d, k)] = (day, 0.96 if win else -1.0, T0, 0.96)
                if win:
                    break
            for k in range(10):
                ctrl['C%d-%d' % (d, k)] = (day, 0.96 if rng.random() < 0.49 else -1.0, T0, 0.96)
        hits += llm_eval.eprocess([(1.0, test)], [(1.0, ctrl)], clock, None)['k_sup'] >= 20
    out['a_sel_boundary'] = dict(rate(hits, sims_b), scenario='composição do dia dependente dos resultados desse dia (viola A-sel)',
                                 meaning='H0 does not hold here although each bet is fair: this is why A-sel is declared')
    for days_p in (90, 180, 365):
        for edge in (0.10, 0.20):
            rng, hits, sims_p = random.Random(1000 + days_p + int(edge * 100)), 0, 300
            clock_p = T0 + timedelta(days=days_p + 10)
            for _ in range(sims_p):
                hits += llm_eval.eprocess(*world(rng, days_p, MIX, MIX, 8, 12, 0.10, edge=edge), clock_p, None)['k_sup'] >= 20
            out['power'].append(dict(rate(hits, sims_p), scenario='mistura igual, efeito +%d %%' % round(edge * 100), days=days_p))
    out['ok'] = ok
    text = json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + '\n'
    if argv:
        Path(argv[0]).write_text(text, encoding='utf-8')
    print(text)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
