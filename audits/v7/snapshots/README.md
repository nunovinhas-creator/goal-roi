# Snapshots V7 — correções isoladas da PR #98

Protótipo offline. **Não altera a PR #98, não ativa snapshots e não é chamado por nenhum workflow.** O builder Node da PR #98 é usado só em leitura de um checkout separado (33b469e4) e o relatório vem do corte e3a0760.

## Defeitos e correção

| Defeito reproduzido na PR #98 (`storage/pr98_adversarial.py`, reproduzido de novo) | Correção no protótipo | Prova |
|---|---|---|
| Corpo corrompido mantém-se após build `unchanged` | Antes de `unchanged`, os corpos vivos são verificados por SHA-256; a mesma revisão é reescrita (`repaired`) | `corrupted_same_revision: repaired` |
| Interrupção entre renames deixa manifesto antigo com corpos de revisões diferentes | Duas ranhuras com nomes estáveis (`a/`, `b/`): a revisão nova é escrita completa e verificada na ranhura inativa; só depois o ponteiro é trocado por rename atómico com fsync | 7 pontos de interrupção + SIGKILL: 0 revisões misturadas |
| Entrada prospetiva alterada depois do builder é publicada com digest obsoleto | Relatório, entrada prospetiva e protocolo copiados e fixados em conjunto; digests verificados antes/depois; o ponteiro regista os três | `prospective_change_rejected: true`; ponteiro coincide com a entrada viva |
| Cliente aceita corpo pelo comprimento sem `crypto.subtle` | `client_integrity.mjs`: só SHA-256 (subtle ou implementação pura), tudo-ou-nada, recuo para a revisão anterior | adulteração de igual comprimento recusada nos dois lados |

O ponteiro guarda a revisão anterior verificada (`previous`), íntegra na outra ranhura: há sempre uma versão anterior íntegra para recuo. Os nomes estáveis mantêm os deltas Git por caminho (cada caminho muda de duas em duas revisões).

## Desempenho

`perf_p95.cjs`, perfil da PR #98 (CPU 4×, 1,6 Mbit/s, 150 ms, 412×915), 20 repetições após 1 aquecimento, servidor local com ETag e gzip, relatório de 3 995 973 B.

| Ensaio | Primeiro conteúdo p50 / p95 | Completo p50 / p95 |
|---|---:|---:|
| Corte atual, anfitrião livre | 1,214 / 1,248 s | 1,756 / 1,786 s |
| Corte atual, 4 núcleos ocupados | 1,219 / 1,246 s | 1,760 / 1,794 s |

Os 2,150 / 2,777 s registados pelo Codex (3 amostras, sem aquecimento) **não se reproduzem** neste anfitrião. A meta de 2 s é cumprida em laboratório, com margem de ~0,21 s no p95 completo. Os seis snapshots ocupam ~72 KB comprimidos; a margem cobre ~+60% de crescimento comprimido. Falta prova em HTTPS/CDN de staging (exige autorização).

A publicação com o builder real demora 0,21–0,26 s por revisão.

## Reproduzir

```sh
export PYTHONDONTWRITEBYTECODE=1
python3 -m pytest -q tests/test_v7_snapshot_publisher.py
node --test tests/test_v7_client_integrity.mjs
# checkouts separados: PR98=<worktree 33b469e4>, CUT=<worktree e3a0760>
(cd "$PR98" && PYTHONPATH=. python3 "$OLDPWD/audits/v7/storage/pr98_adversarial.py" --pr . --report-root "$CUT")
python3 audits/v7/snapshots/real_run.py --pr "$PR98" --report-root "$CUT"
cp "$CUT/docs/report.json" "$PR98_COPY/docs/report.json"; cp "$CUT/data/prospective_report.json" "$PR98_COPY/data/"
RUNS=20 node audits/v7/snapshots/perf_p95.cjs "$PR98_COPY"
```

## Vereditos

| Item | Veredito |
|---|---|
| Protótipo de publicação (ranhuras + ponteiro + fixação conjunta) | **GO** para revisão offline |
| Cliente só por SHA-256 | **GO** para revisão offline |
| Meta de 2 s em laboratório | **GO** (p95 1,79 s), condicionado a staging HTTPS/CDN |
| Ativação de snapshots / alteração da PR #98 | **NO-GO** nesta fase (requer autorização própria, staging e fim do período experimental do Brain V5) |

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
