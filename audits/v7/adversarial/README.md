# Auditoria adversarial independente da PR #103 — snapshots

Âmbito: `audits/v7/snapshots/publisher.py` e `client_integrity.mjs` no HEAD `1b06e7d6` da PR #103. Foco: concorrência entre leitores e publicadores, reutilização das duas ranhuras, cache CDN e recuperação da revisão anterior. O protótipo não foi alterado. Ensaios offline; sem rede, fornecedores nem produção.

## Resultado

Os quatro defeitos da PR #98 continuam corrigidos (11 testes existentes passam). A garantia anunciada "há sempre uma versão anterior íntegra" **não se verifica**. Com a cadência real de publicação, duas ranhuras são insuficientes atrás de uma CDN com `max-age=600`.

| # | Constatação | Gravidade | Prova |
|---|---|---|---|
| A1 | A nova revisão é escrita na ranhura que o ponteiro ainda anuncia como `previous`. Durante a publicação, e para sempre se esta for interrompida, `previous` aponta para corpos já reescritos. | Alta | `test_a1_…` (xfail: `corpo não coincide com o manifesto: summary`) |
| A2 | A ranhura de destino é escolhida pelo ponteiro, não pela integridade. Se `current` estiver corrompida, a publicação reescreve a única revisão íntegra. Uma interrupção deixa zero revisões legíveis. | Alta | `test_a2_…` (xfail: `read_verified` devolve `None`) |
| A3 | Leitor com o ponteiro obtido antes de duas publicações (A-B-A) não lê nem `current` nem `previous`. `previous` morre sempre primeiro (à 1.ª publicação), por isso nunca serve de recuo para leituras atrasadas. | Média | `test_a3_…` (xfail), `test_a3b_…` (limite exato) |
| A4 | Proteção implícita: o ponteiro é uma linha única, por isso o `rebase --onto` de `scangoal/publication.py` entra em conflito e aborta quando dois escritores publicam da mesma base. Se o ponteiro passar a JSON indentado, a fusão pode misturar ranhuras sem conflito. | Latente | `test_a4_…` (passa; fixa a invariante) |
| A5 | O layout `a/`, `b/` e `.lock` não cabe na lista de caminhos publicáveis da PR #98 (`SNAPSHOT_FILE`). O `.lock` ficaria na pasta servida pelo Pages. O porte exige alterar `publication.py`. | Média (porte) | `test_a5_…` (xfail) |
| A6 | O recuo para `previous` devolve dados de outras entradas. O chamador tem de comparar `inputs`/`report_generated_at_utc` e assinalar o atraso. | Baixa | `test_a6_…` (passa; documenta o contrato) |
| C1 | O cliente pede `entry.path` tal como vem do manifesto: aceita URL absoluta ou `../`. | Baixa (endurecimento) | `test_v7_adversarial_client.mjs` C1 (todo) |
| C2 | `loadVerified` não volta a pedir o ponteiro quando ambas as revisões falham. Com um ponteiro atrasado, recorre ao relatório completo (~4 MB). | Média | C2 (todo) |
| C3 | Corpo de outra revisão no mesmo caminho é sempre recusado (tudo-ou-nada). | — | C3 (passa) |
| D1 | `flock` só serializa processos no mesmo anfitrião. Entre runners a serialização vem do grupo `scangoal-write` e do conflito de rebase (A4); o coletor externo (commits a cada 1–4 min) não partilha o lock. | Informativa | análise de `collect.yml`, `continuity.yml`, `publication.py` |
| D2 | Com `?v=<sha16>`, um ponteiro atrasado pede uma chave nova; a CDN obtém da origem os bytes da revisão mais recente e guarda-os sob a chave antiga durante o TTL. A integridade mantém-se (SHA-256), mas a chave fica envenenada até expirar. | Média (desempenho) | modelo abaixo; requer confirmação em staging |

## Cache CDN × ranhuras (`cdn_slot_sim.py`)

**Limite superior.** O staging HTTPS (PR #106, `ops/staging/`) mostrou que, com ponteiro e corpos na mesma cache, um ponteiro atrasado continua coerente: os corpos com `?v=` ficam em cache com ele. Os valores abaixo só se aplicam quando ponteiro e corpos estão em níveis ou nós de cache diferentes (cache do navegador, outro nó, despejo LRU). A frequência real só se mede numa CDN real.

Publicações: os 1 628 instantes em que `docs/report.json` mudou em `main` entre 19/11 e 26/11/2026 (`publicacoes-report-7d.txt`). Intervalo p10/p50/p90: 112/381/577 s. Em 26,6 % dos casos há duas publicações em menos de 600 s. Modelo: idade do ponteiro em cache uniforme em [0, 600] s (pior caso), corpos com chave nova. É um limite superior, porque o Pages cancela implantações sobrepostas.

| Limite entre publicações de snapshots | 2 ranhuras | 3 ranhuras | 4 ranhuras |
|---|---:|---:|---:|
| Sem limite (1 628 publicações) | 19,0 % relatório completo | 0,8 % | 0 % |
| ≥ 300 s (1 097) | 2,9 % | 0 % | 0 % |
| ≥ 600 s (790) | 0 % | 0 % | 0 % |

## Correções propostas (não aplicadas; exigem autorização e ramo próprio)

1. **Três ranhuras** com nomes estáveis: escrever sempre na ranhura que não é `current` nem `previous`. Corrige A1 e A2. Mantém os deltas Git (cada caminho muda de três em três revisões).
2. Escolher a ranhura de destino pela integridade verificada, nunca só pelo ponteiro (A2).
3. **Limitar a publicação de snapshots a ≥ TTL (600 s)**, por exemplo só na execução por cron. Com isso, 2 ranhuras chegam para leitores atrasados; com 3, há margem.
4. Cliente: validar `path` contra `^[ab]/dashboard-<nome>\.json$` (C1); se `current` e `previous` falharem, voltar a pedir o ponteiro uma vez com `cache: 'no-cache'` antes de recorrer ao relatório (C2).
5. Porte: acrescentar o layout à lista de `publication.py` e mover o `.lock` para fora de `docs/` (A5). Manter o ponteiro numa linha e acrescentar o teste A4 ao porte.
6. Staging HTTPS: confirmar se a CDN inclui a query string na chave e medir D2 (ver `ops/staging/`, PR de staging).

## Vereditos

| Item | Veredito |
|---|---|
| Correção dos quatro defeitos da PR #98 | Confirmada (offline) |
| "Versão anterior sempre íntegra" | **Refutada** (A1, A2) |
| Duas ranhuras atrás de CDN `max-age=600` com a cadência atual | **NO-GO** até medição em CDN real (limite superior 19 %; 0 % com cache única) |
| Ativação dos snapshots | **NO-GO** até 1–5 e prova em staging |

## Reproduzir

```sh
export PYTHONDONTWRITEBYTECODE=1
python3 -m pytest -q -rxX tests/test_v7_adversarial_103.py tests/test_v7_snapshot_publisher.py
node --test tests/test_v7_adversarial_client.mjs
python3 audits/v7/adversarial/cdn_slot_sim.py
```

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
