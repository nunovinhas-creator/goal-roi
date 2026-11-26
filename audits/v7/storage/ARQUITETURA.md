# Arquitetura de armazenamento V7 — consolidação com a história Git real

Continuação da [PR #102](https://github.com/nunovinhas-creator/goal-roi/pull/102). O protótipo do Codex provou o diário isolado (24 segmentos, 1 825 releases diários simulados). Este passo repete a prova com **todas as fontes e vistas, commit a commit, a partir dos blobs Git reais**, e acrescenta índice, retenção, exportação e restauração. Tudo offline, em pastas temporárias; custo adicional 0 €.

## O que foi construído

| Peça | Ficheiro | Função |
|---|---|---|
| Armazém imutável | `segment_store.py` (Codex, inalterado) | Objetos gzip endereçados por SHA-256, manifestos encadeados, HEAD/PREVIOUS atómicos |
| Arquivo V7 | `archive_v7.py` | Fontes (`data/*`) num armazém sem apagamento; vistas (`docs/report.json`, `data/report.json`) noutro, com retenção. Blocos definidos pelo conteúdo em fronteiras de linha; manifestos em árvore (um meta por ficheiro) |
| Índice de consulta | `archive_v7.py` (SQLite) | `revisions`, `files` (por SHA-256), `messages` (primeira revisão, nº de versões, último hash). Derivado: reconstruído do zero a partir do armazém e comparado |
| Retenção | `retain_views` | Mantém as últimas K revisões das vistas, regista lápides com SHA-256 do que saiu e remove só objetos não referenciados. Fontes nunca são apagadas |
| Exportação | `run` / `restore` | Repõe `data/` e `docs/report.json` nos caminhos originais, byte a byte |
| Restauração | `restore_drill.py` | Sete cenários de recuperação a partir de cópia independente |
| Pack por classe | `pack_split.py` | Peso diário de fontes vs vistas no Git |

## Resultados (corte do ramo, últimos commits de recolha)

| Ensaio | Resultado | Evidência |
|---|---|---|
| 120 commits consecutivos, fontes + vistas | **240 revisões byte-idênticas** ao Git; índice reconstruído idêntico; exportação final idêntica; 0 reescritas do prefixo do diário | [arquivo-v7-por-commit.json](arquivo-v7-por-commit.json) |
| Retenção (K = 24) | 96 revisões de vistas removidas com lápide, 5,7 MB libertados, 24 retidas verificadas; **120 revisões de fontes verificadas depois** | idem |
| Índice | 5 696 IDs, 9 088 versões; consulta pontual em 0,04 ms; 1,2 MB para 120 revisões | idem |
| Cadência ~12 h (1 em 320 commits) | 8 revisões byte-idênticas | [arquivo-v7-12h.json](arquivo-v7-12h.json) |
| Restauração | 7/7 cenários conformes (abaixo) | [resultados-restauracao.json](resultados-restauracao.json) |

### Custo por revisão e por dia

| Classe | Segmentado por commit | Git por commit (delta) | Segmentado a cada ~12 h | Pack Git autocontido de 24 h |
|---|---:|---:|---:|---:|
| Fontes `data/*` | 47,4 KB | 6,1 KB | 610 KB | **2,57 MB/dia** |
| Vistas (relatórios) | 66,4 KB | 58,5 KB | 317 KB | **38,1 MB/dia** |

Com ~640 commits de recolha por dia ([packs-24h-por-classe.json](packs-24h-por-classe.json)):

| Opção para as fontes | Por dia | 5 anos |
|---|---:|---:|
| Segmentado a cada commit | ~30 MB | ~55 GB |
| Git (deltas no repositório) | ~3,9 MB | ~7 GB |
| Pack/bundle diário autocontido | 2,57 MB | ~4,7 GB |
| **Segmentado selado a cada 12 h** | **~1,2 MB** | **~2,2 GB** |

**Correção ao protótipo anterior:** os 68 MiB a cinco anos cobriam só o diário num release por dia. Com todas as fontes, o arquivo segmentado **por commit é ~8× mais caro que os deltas Git**: o hypotheses.json (6 MB), o notify_state.json e o state.json (uma linha) mudam em vários blocos a cada recolha. O segmentado só compensa como **cópia selada periódica**, não como substituto do Git.

As vistas são o problema de crescimento: **38,1 de ~47,6 MB/dia** do pack diário (80%), sem valor histórico científico. Com retenção (24 recentes + 1 por dia, ~317 KB cada) ficam em ~0,6 GB a cinco anos, em vez de ~70 GB.

## Restauração (cópia independente, sem credenciais)

| Cenário | Resultado |
|---|---|
| Perda total do Git | 20 ficheiros restaurados do espelho, idênticos à árvore Git da revisão 12 |
| Bloco corrompido no primário | Detetado por SHA-256 e reparado a partir do espelho verificado |
| HEAD ilegível | Leitura recua para PREVIOUS (revisão 11); HEAD reposto do espelho |
| Espelho sem um objeto | Restauração **recusada**, destino não criado |
| Espelho atrasado 3 revisões | Devolve a revisão 9, declara 3 revisões em falta (RPO), conteúdo idêntico à revisão declarada |
| Restauração interrompida | Destino ausente, sem temporários; repetição idêntica |
| Bundle Git incremental | Verifica com a base; **não verifica num repositório vazio** — a cadeia de bundles tem de ser mantida completa |

## Compatibilidade

A exportação repõe os mesmos caminhos e bytes (`data/prospective_report.json`, `data/prospective_ledger.jsonl`, `docs/report.json`, …). Brain (leitura dos caminhos prospetivos e sincronização Termux), Android/Pages e V6.1 continuam a ler as rotas atuais; nada muda para eles enquanto a fonte publicada for a mesma. Qualquer mudança de rota exige validação de cada consumidor e autorização própria.

## Arquitetura recomendada

1. **Fontes:** Git continua canónico (deltas eficientes, commits atómicos).
2. **Cópia independente:** arquivo segmentado selado a cada 12 h (~1,2 MB/dia), verificável por ficheiro sem Git, guardado em disco existente fora do GitHub. Bundle Git diário como segunda via, mantendo a cadeia completa.
3. **Vistas:** sair da história Git por commit para um armazém com retenção (ou artefacto Pages). É a única alavanca grande de crescimento. **Mudança de produção: só com autorização, depois do período experimental do Brain V5.**
4. **Índice:** SQLite derivado e descartável; nunca fonte de verdade.

## Vereditos

| Item | Veredito |
|---|---|
| Preservação integral (240 revisões reais byte a byte) | **GO** |
| Recuperação verificável (7 cenários) | **GO** offline |
| Índice derivado e reconstruível | **GO** |
| Retenção das vistas | **GO** offline; fontes nunca retidas |
| Segmentado por commit como arquivo principal | **NO-GO** (~8× o custo dos deltas Git) |
| Segmentado selado a cada 12 h como cópia independente | **GO** para piloto offline |
| Retirar as vistas do Git / mudar rotas em produção | **NO-GO** nesta fase (requer autorização e validação de Brain, Android e V6.1) |
| Custos adicionais | 0 € (disco existente; nenhum serviço contratado) |

## Reproduzir

```sh
export PYTHONDONTWRITEBYTECODE=1
git fetch --deepen=600 origin main        # história suficiente num clone superficial
python3 -m pytest -q tests/test_v7_archive.py
python3 audits/v7/storage/archive_v7.py --repo . --commits 120 --keep-views 24
python3 audits/v7/storage/archive_v7.py --repo . --commits 4 --stride 320 --keep-views 2
python3 audits/v7/storage/restore_drill.py --repo . --commits 12
python3 audits/v7/storage/pack_split.py .
```

Os commits exatos de cada ensaio estão em `first_commit`/`last_commit` dos JSON.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
