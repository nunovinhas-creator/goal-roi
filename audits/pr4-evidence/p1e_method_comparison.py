"""PR 4, ponto P1-E (auditoria #5916502096): erro de tipo I sob cotações diferentes e diferenças diárias assimétricas.

Uso (raiz do repositório): python3.11 audits/pr4-evidence/p1e_method_comparison.py [saída.json]
Simulação pura (sem dados de produção, sem rede), sementes fixas: reprodutível byte a byte.

Hipótese nula que interessa: SEM utilidade incremental = a esperança do lucro por jogo é a mesma nos dois lados (LLM e
controlos), qualquer que seja a mistura de cotações. NÃO é simetria das diferenças diárias, que é o que a inversão de sinais
do v3 exige (daí a rejeição de 6,4 % com perfis de cotação opostos).

Métodos comparados (todos sobre dias emparelhados, unilateral, α = 0,05):
- M1 `signflip_v3`: inversão de sinais por dia (protocolo v3).
- M2 `bootstrap_t`: bootstrap studentizado por dias (assintoticamente de 2.ª ordem; sem garantia em amostra finita).
- M3 `betting_grid`: teste por apostas (e-processo). x_d = d_d / L_d, com L_d = perda máxima possível de d_d (fixada pelas cotações
  antes dos resultados), x_d >= -1. Riqueza K = média sobre λ ∈ {1/2, 1/4, 1/8, 1/16, 1/32} de Π_d (1 + λ x_d). Rejeita se K >= 1/α.
  Garantia (Ville): se, para cada dia, E[d_d | dias anteriores, cotações do dia] <= 0, então P(alguma vez K >= 1/α) <= α, para
  qualquer distribuição limitada, assimétrica ou não, e com olhares repetidos.
- M4 `betting_plugin`: como M3, mas λ_d previsível (só com os dias anteriores): λ_d = min(1/2, max(0, m/(v+m²))) com m, v a
  média e a variância dos x anteriores (λ = 1/8 sem passado). Mesma garantia de Ville.
"""
import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from calibration_check import clopper_pearson  # noqa: E402  (intervalo exato; Wald dava [0, 0] com zero rejeições)

ALPHA = 0.05
LAMBDAS = (0.5, 0.25, 0.125, 0.0625, 0.03125)     # = brain.EPROCESS_LAMBDAS (certificação da PR 3)
SHORT, MIX, LONG, VERY_LONG = (1.4, 1.6, 1.8), (1.5, 1.8, 2.0, 2.5, 3.5, 5.0), (3.5, 5.0, 6.0), (6.0, 8.0, 10.0)


def simulate_day(rng, n_llm, n_ctrl, day_sd, odds_llm, odds_ctrl, edge):
    """Um dia: listas de (lucro, lucro máximo) por jogo. P(ganhar) = (1 + edge + 2·efeito do dia) / odd -> sem edge, a esperança
    de lucro por jogo é igual nos dois lados (≈ −2 %), qualquer que seja a mistura de cotações."""
    shift = rng.gauss(0, day_sd)
    out = []
    for n, odds, e in ((n_llm, odds_llm, edge), (n_ctrl, odds_ctrl, 0.0)):
        side = []
        for _ in range(max(1, int(rng.expovariate(1 / n)))):
            o = rng.choice(odds)
            top = o * 0.98 - 1
            side.append((top if rng.random() < min(0.97, max(0.01, (1 + e + 2 * shift) / o)) else -1.0, top))
        out.append(side)
    return out


def day_stats(days):
    """-> [(d_d, peso w_d, L_d)] por dia emparelhado."""
    res = []
    for llm, ctrl in days:
        ml, mc = sum(p for p, _ in llm) / len(llm), sum(p for p, _ in ctrl) / len(ctrl)
        w = len(llm) * len(ctrl) / (len(llm) + len(ctrl))
        loss = 1 + sum(t for _, t in ctrl) / len(ctrl)     # d_d >= min(m_L) − max(m_C) = −1 − média dos máximos dos controlos
        res.append((ml - mc, w, loss))
    return res


def signflip(st, rng, b=1000):
    total = sum(w for _, w, _ in st)
    terms = [d * w / total for d, w, _ in st]
    obs, ge = sum(terms), 0
    for _ in range(b):
        bits = rng.getrandbits(len(terms))
        ge += sum(x if (bits >> i) & 1 else -x for i, x in enumerate(terms)) >= obs - 1e-12
    return (1 + ge) / (1 + b) <= ALPHA


def bootstrap_t(st, rng, b=999):
    n = len(st)
    if n < 3:
        return False

    def est(sample):
        tw = sum(w for _, w, _ in sample)
        m = sum(d * w for d, w, _ in sample) / tw
        z = [w * (d - m) / tw for d, w, _ in sample]
        se = math.sqrt(n / (n - 1) * sum(x * x for x in z))
        return m, se
    m, se = est(st)
    if se == 0:
        return False
    t_obs, ts = m / se, []
    for _ in range(b):
        sample = [st[rng.randrange(n)] for _ in range(n)]
        mb, sb = est(sample)
        if sb > 0:
            ts.append((mb - m) / sb)
    ts.sort()
    q = ts[min(len(ts) - 1, math.ceil(0.95 * len(ts)) - 1)]
    return t_obs > q


def betting_grid(st):
    logs = [0.0] * len(LAMBDAS)
    best = 0.0
    for d, _, loss in st:
        x = d / loss
        for j, lam in enumerate(LAMBDAS):
            logs[j] += math.log1p(lam * x)
        top = max(logs)
        best = max(best, top + math.log(sum(math.exp(v - top) for v in logs) / len(logs)))
    return best >= math.log(1 / ALPHA)


def betting_plugin(st):
    logk, xs = 0.0, []
    for d, _, loss in st:
        x = d / loss
        if xs:
            m = sum(xs) / len(xs)
            v = sum((y - m) ** 2 for y in xs) / len(xs)
            lam = min(0.5, max(0.0, m / (v + m * m))) if v + m * m > 0 else 0.125
        else:
            lam = 0.125
        logk += math.log1p(lam * x)
        xs.append(x)
        if logk >= math.log(1 / ALPHA):
            return True
    return False


METHODS = (('signflip_v3', signflip), ('bootstrap_t', bootstrap_t), ('betting_grid', None), ('betting_plugin', None))


def run(name, days, n_llm, n_ctrl, day_sd, odds_llm, odds_ctrl, edge, sims, seed):
    rng, mrng = random.Random(seed), random.Random(seed + 1000)
    hits = dict.fromkeys([m for m, _ in METHODS], 0)
    for _ in range(sims):
        st = day_stats([simulate_day(rng, n_llm, n_ctrl, day_sd, odds_llm, odds_ctrl, edge) for _ in range(days)])
        hits['signflip_v3'] += signflip(st, mrng)
        hits['bootstrap_t'] += bootstrap_t(st, mrng)
        hits['betting_grid'] += betting_grid(st)
        hits['betting_plugin'] += betting_plugin(st)
    row = {'scenario': name, 'days': days, 'sims': sims, 'edge': edge}
    for m, k in hits.items():
        row[m] = {'rejection': round(k / sims, 4), 'rejections': k, 'ci95_exact': list(clopper_pearson(k, sims))}   # Clopper-Pearson
    return row


def main(argv):
    nulls = [('mesma mistura, efeito de dia forte', 30, 8, 12, 0.20, MIX, MIX),
             ('LLM curtas, controlos longas', 30, 10, 15, 0.10, SHORT, LONG),
             ('LLM curtas, controlos muito longas', 30, 10, 15, 0.10, SHORT, VERY_LONG),
             ('LLM longas, controlos curtas', 30, 10, 15, 0.10, LONG, SHORT),
             ('LLM curtas, controlos muito longas, 14 dias', 14, 10, 15, 0.10, SHORT, VERY_LONG),
             ('LLM curtas, controlos muito longas, poucos jogos', 30, 3, 4, 0.10, SHORT, VERY_LONG)]
    out = {'alpha': ALPHA, 'null': [], 'power': [],
           'meaning': 'null = equal expected profit per game on both sides; rejection rates with 95 % Monte Carlo intervals'}
    for i, (name, days, nl, nc, sd, ol, oc) in enumerate(nulls):
        out['null'].append(run(name, days, nl, nc, sd, ol, oc, 0.0, 2000, 100 + i))
    for days in (30, 60, 90):
        for edge in (0.10, 0.20):
            out['power'].append(run('mistura igual, efeito +%d %%' % round(edge * 100), days, 8, 12, 0.10, MIX, MIX, edge, 500, 200 + days + round(edge * 100)))
    text = json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + '\n'
    if argv:
        Path(argv[0]).write_text(text, encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
