# P2 — Observação automática e prospetiva de preços Polymarket

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

O código desta PR está **desativado** e o workflow de produção (`.github/workflows/collect.yml`) **não é alterado**. A
integração no workflow é um patch separado, `ops/polymarket-p2/activation.patch`, para uma PR de ativação própria. Não
há apostas, ordens, POST nem credenciais. Alertas, regras congeladas (#55, #76), auditoria #77, certificação e
`data/messages.jsonl` ficam intactos.

## 1. Arquitetura

### O que a cadeia existente impõe

| Facto (código atual) | Consequência para o observador |
|---|---|
| `publication.publish` faz `git add data` e, num replay com o remoto avançado, exige árvore limpa (`status --porcelain`) | Escrever em `data/` **durante** uma publicação pode degradar a transação. O observador nunca escreve em `data/` enquanto corre. |
| `reader` e `watch` carimbam `collected_at_utc` quando a página fica completa (`stamp_receipt`); o `watch` é um só passo de até 540 s | Um passo sequencial depois do `watch` chegaria minutos tarde. Para cumprir 120 s, a observação tem de correr **em paralelo**. |
| A cadeia é auto-lançada (`Start the next collection`); o agendamento do GitHub não dispara neste repositório | Um erro no workflow pára a cadeia. Mudar o workflow é o único caminho com impacto possível na produção. |
| Os testes do projeto fixam invariantes: os passos tolerados (`test_silent_failures`), o aviso seguido de `Commit data` e um simulador de condições fechado (`test_git_persistence`) | Qualquer passo novo exige alterar esses invariantes, o que é uma decisão de ativação e não de integração. |

### Escolha: seguidor desacoplado + ingestão sequencial

```
Resume ─► [Start observer] ─► Read ─► fontes ─► Report ─► Publish ─► Notify ─► Commit data ─► Fast reads ─► [Stop + ingest] ─► Commit fast reads
               │  (segundo plano, só lê data/messages.jsonl)                                                  ▲
               └─► Gamma/CLOB (GET) ─► $RUNNER_TEMP/goal-roi-polymarket/<chave>.json ─────────────────────────┘
```

- `scangoal/polymarket_observer.py follow` verifica `data/messages.jsonl` de 5 em 5 s e lê **só os bytes acrescentados**
  (cursor incremental, linha a linha). Se o prefixo mudar ou o ficheiro encolher, relê tudo, mas sem repetir mensagens.
  Considera apenas mensagens **novas desde o arranque** e lidas depois dele: o histórico, mesmo que reapareça mais
  tarde (checkout, rebase), nunca é observado. Quando aparece
  um sinal elegível, chama o observador da P1 (`polymarket_shadow.observe`) e escreve um ficheiro por observação, de
  forma atómica, num spool **fora do repositório**.
- `ingest` corre num passo sequencial imediatamente antes de `Commit data from the fast reads`. Acrescenta as
  observações a `data/polymarket_observations.jsonl` e a transação Git existente publica-as junto com o resto. É o
  mesmo padrão que `notify` e `watch` já seguem: escrevem em `data/` entre publicações, nunca durante uma.
- Elegibilidade: a mesma do aviso PR #55, sem os cortes de quota e valor. A 1.ª versão é construída só a partir da
  1.ª linha da mensagem (`first_signal`), sem chamar `report.load`. Um teste verifica a equivalência campo a campo com
  `report.load` sobre o diário real completo. Critérios:
  `bet=True`, `had_ft/edited_at_first_sight=False`, `burst=False`, `eligibility.eligible`, âmbito Polymarket pré-jogo
  Asian Total Over 2.5 regulamentar e kickoff comprovado. A identidade, o contrato, o token, as regras, o livro e a taxa
  usam os critérios da P1.

### Alternativas rejeitadas

| Alternativa | Motivo da rejeição |
|---|---|
| Chamar o observador dentro de `reader`/`watch` | Acopla a leitura do Telegram a uma API externa: o tempo e as exceções da Polymarket entrariam no orçamento de 220/540 s e no caminho dos avisos. |
| Passo sequencial depois da leitura | O passo do `watch` dura até 540 s, por isso os sinais das leituras rápidas seriam observados minutos depois, fora dos 120 s. |
| Workflow separado agendado | O agendamento não dispara neste repositório. No grupo de concorrência `scangoal-write`, uma execução nova **substitui** a que está à espera e poderia cancelar a cadeia. Fora desse grupo, seria um segundo escritor em `main`. |
| Job paralelo no mesmo workflow | Corre noutro runner, sem ver `data/`. Para publicar teria de ser um segundo escritor ou um artefacto extra. |
| Seguidor a escrever diretamente em `data/` | Pode sujar a árvore durante um replay da publicação e degradar a transação. |
| Alterar `collect.yml` nesta PR | Exige mudar os invariantes do projeto, e o `actionlint` e o comportamento real no runner não foram verificados aqui. Fica para a PR de ativação. |

## 2. Código

| Ficheiro | Função |
|---|---|
| `scangoal/polymarket_observer.py` | `follow`, `ingest`, `stop`, a classificação, o orçamento e o disjuntor. Sai **sempre** com código 0 e exige `GOAL_ROI_POLYMARKET_SHADOW=on`. |
| `scangoal/polymarket_shadow.py` | Duas alterações pequenas: um `timeout` configurável no `http_get`; e a pesquisa tenta primeiro "casa vs fora" e depois "casa fora" (no máximo 2 pesquisas por estado; a identidade continua exata). |
| `scripts/polymarket_p2_audit.py` | Relatório prospetivo, só leitura, com `--as-of` e `--since`. Sem ativação verificável nem `--since`: `since_required` (código 2), nunca "zero elegíveis". |
| `scripts/measure_polymarket_p2.py` | Medição de CPU, memória, latência e disco com cópias do diário real (1×, 4×, 30×), num diretório temporário. |
| `scripts/rehearse_polymarket_p2.py` | Ensaio ponta a ponta com rede real e mensagem **sintética**, num diretório temporário. |
| `ops/polymarket-p2/activation.patch` | Os dois passos do workflow (condicionados, `continue-on-error`, `timeout-minutes: 1`) e a atualização explícita dos dois invariantes. |

### Registo (`data/polymarket_observations.jsonl`, só acrescenta)

Chave única `message_id:sha256 da primeira versão`. Cada registo guarda:
- a mensagem: `message_id`, `version_sha256`, `signal_sha256`, `match_id`, `first_read_utc`, `signal_sent_utc`, `kickoff_utc` e `signal_odds`;
- o contrato: `condition_id`, `token_over` e `market_slug`;
- a cotação: `quote` (melhor ask e tamanho, bid, profundidade, `fills` para 10/50/100/500 USDC com VWAP e odds líquidas), `source_utc` e `received_utc`;
- a taxa: `fee`, com taxa, expoente e fonte `clob /clob-markets fd (taker only)`;
- o resultado: `status` e `reason`;
- a frescura, com **duas provas distintas**: `received_within_window` (resposta nos 120 s, antes do início) e
  `fresh_book_proven` (o livro tinha mudado há no máximo 300 s quando foi recebido);
  `evidence_for_signal = status contemporaneous E fresh_book_proven`;
- `responses`: URL, `sha256`, bytes e horas de cada resposta;
- os campos fixos `execution_proven=false`, `orders_sent=0` e `credentials_used=false`.

| Estado | Significado |
|---|---|
| `contemporaneous` | Resposta ≤ 120 s depois da primeira leitura e antes do início, **e** livro alterado há ≤ 300 s: prova de preço fresco |
| `stale_book` | Resposta dentro da janela, mas o livro não mudava há mais de 300 s. Conta como resposta na auditoria, **nunca** como preço fresco nem na comparação económica. |
| `outside_window` | `detected_late`, `current_only`, `deadline_exceeded`, `after_kickoff` ou `first_read_unproven` (os casos detetados tarde ou já depois do início não fazem pedidos) |
| `contract_unavailable` | Sem contrato O/U 2.5, mercado suspenso ou sem asks |
| `identity_unproven` | Encontro, contrato ou token sem prova exata |
| `technical_failure` | Rede, HTTP, timeout, disjuntor, limite atingido ou erro interno |

O `ingest` nunca reescreve linhas. Um diário com uma linha inválida ou um final truncado é recusado (o spool fica, nada
se perde) e a escrita é atómica (ficheiro temporário + `fsync` + `rename`). Registos adulterados (`orders_sent≠0`,
`execution_proven=true`, chave incoerente, `evidence_for_signal` sem `contemporaneous` + `fresh_book_proven`) vão para
`rejected/`. O relatório não confia nesse campo: recalcula a prova a partir da hora de leitura do sinal, da hora de
receção, do início do jogo e da idade do livro.

### Execuções (`data/polymarket_observer_runs.jsonl`, só acrescenta)

Cada execução do seguidor regista um evento `start` e um evento `stop`, **mesmo sem observações**. O `stop` inclui o
motivo, as passagens, as observações, os pedidos, o disjuntor, as leituras incrementais e completas, o CPU do processo
e o pico de memória (`VmHWM`). Estes eventos são a data de ativação verificável do relatório e mostram quando o
observador estava a correr. Uma execução sem `stop` (processo morto) conta, no máximo, até 870 s depois do início.

## 3. Testes (`tests/test_polymarket_observer.py`, 60)

| Pedido | Teste |
|---|---|
| Falha total da Polymarket | `test_total_polymarket_failure_opens_circuit`: 2 falhas abrem o disjuntor, depois **0 pedidos** |
| Timeout | `test_timeout_is_a_technical_failure`, `test_http_get_passes_the_bounded_timeout` |
| Rate limiting / 403 / 5xx | `test_rate_limit_and_server_errors_open_immediately` (429, 403, 500, 503: 1 pedido e disjuntor aberto) |
| Reinício do runner | `test_runner_restart_never_requeries` |
| Sinal repetido / leitura rápida seguida de recolha completa | `test_repeated_signal_and_fast_then_full_read` |
| Edição posterior | `test_later_edit_is_not_a_new_signal` (a chave usa a 1.ª versão) |
| Depois do início | `test_after_kickoff_no_network` |
| Depois de 120 s | `test_signal_detected_after_120s_is_recorded_without_network`, `test_quote_received_after_120s_is_outside_window`, `test_deadline_stops_requests_mid_observation` |
| Sem contrato / mercado suspenso | `test_missing_contract_and_suspended_market` |
| Livro desatualizado | `test_stale_book` |
| Escrita interrompida | `test_interrupted_journal_write_leaves_previous_bytes`, `test_partial_spool_file_is_ignored`, `test_torn_journal_is_never_rewritten` |
| Publicação Git em conflito | `GitPublication`: escritor concorrente noutro ficheiro, conflito no próprio diário (falha fechada, bundle preservado, remoto intacto) e o seguidor nunca suja o checkout |
| Erro de rede sem impacto nos alertas | `ProductionIsolation`: nenhum módulo da cadeia importa o observador; com o patch aplicado, o simulador do projeto dá **o mesmo resultado em todos os cenários**, incluindo os passos do observador a falhar |
| Repetição sem duplicar | `test_replayed_execution_does_not_duplicate_evidence` |
| Consulta histórica sem dados futuros | `test_historical_cut_ignores_future_data` |
| Livro desatualizado ≠ preço fresco | `test_stale_book_is_a_response_not_a_fresh_price`, `test_fresh_boundary_is_inclusive_at_300s`, `test_tampered_records_cannot_claim_a_fresh_price` (9 registos adulterados: estado trocado, livro velho ou do futuro, outra leitura, depois do início ou dos 120 s, sem contrato, só o sinalizador) |
| Ativação sem observações | `test_since_required_without_verifiable_activation`, `test_successive_runs_without_observations_keep_the_denominator`, `test_total_failure_keeps_failed_signals_in_the_denominator`, `test_run_killed_without_stop_is_bounded` |
| Leitura incremental | `test_first_signal_matches_report_load_on_the_real_journal`, `test_first_sight_flags_match_report_load`, `test_tick_never_calls_report_load`, `test_cursor_reads_only_the_appended_bytes`, `test_incomplete_line_waits_for_completion`, `test_rewrite_or_shrink_rescans_without_reemitting`, `test_history_injected_later_is_never_observed` |
| Recolha normal com o observador sempre a falhar | `ContinuityUnderSystematicFailure`: processo real com o proxy morto; 5 sinais escritos em < 10 s, `report.load` correto, `messages.jsonl` intacto, nada escrito em `data/`, código de saída 0 |

Prova de mutação: são apanhadas 9 alterações ao observador e 3 ao patch na 1.ª ronda, e 12 alterações às correções
da 2.ª ronda. Estas incluem: `stale_book` contado como fresco, confiar no sinalizador, limite de 300 s exclusivo,
`since` inventado, ausência do observador escondida, execução morta sem limite, cursor sem verificação do prefixo,
histórico admitido e `first_signal` divergente. Uma mutação é equivalente: retirar `burst` do filtro, porque
`eligibility.eligible` volta a excluí-lo.

## 4. Sem regressões

- `collect.yml` fica idêntico a `main` (testado).
- Com o patch aplicado a cópias, os testes de continuidade do projeto (`Steps`, `Telegram`, `ChainContinuityTests`)
  passam. Com o simulador do projeto, em todos os cenários (nenhuma falha, cada passo existente a falhar, cada passo do
  observador a falhar), os passos executados e ignorados e a conclusão do job são iguais aos do workflow atual.
- Suite completa: ver secção 7.

## 5. Limites

| Recurso | Limite |
|---|---|
| Latência medida (ensaios reais) | 6,2–6,4 s entre a primeira leitura e a receção do livro (sondagem de 5 s + ~1 s de rede). Teto: 120 s; prazo de 30 s por observação. |
| Duração | Seguidor ≤ 840 s (`timeout` 870 s + 10 s); passos ≤ 1 min (arranque instantâneo; stop + ingest ≤ 25 s) |
| Concorrência | Um processo, sequencial; uma observação de cada vez |
| Pedidos | ≤ 6 GET por observação, ≤ 12 observações e ≤ 80 pedidos por execução, timeout de 8 s por pedido |
| Disjuntor | 1.º 403/429/5xx, ou 2 falhas técnicas seguidas → sem rede até ao fim da execução |
| Rede | ~330 KB por observação (o evento "More Markets" tem ~280 KB); cada pesquisa custa ~0,6 MB. Pior caso ≈ 30 MB por execução. |
| CPU/memória | Ver a tabela "Custo do seguidor" abaixo: ~0,2–0,3 ms por leitura rápida e ~1–2 ms por sinal novo, a qualquer dimensão; pico de 26–34 MB |
| Armazenamento | ~4,7 KB por registo; ~9 sinais elegíveis/dia → ~45 KB/dia (~16 MB/ano) |

### Custo do seguidor (`measure.json`, `python -m scripts.measure_polymarket_p2`)

Cópias do diário real (6 905 linhas, 4,4 MB) repetidas 1×, 4× e 30× (30× ≈ um ano a este ritmo), sem rede:

| Escala | Anterior: `first_versions` + `report.load` por alteração | Novo: arranque (uma vez) | Novo: leitura rápida | Novo: sinal novo | Processo real: CPU em 20 s / pico de memória | Atraso escrita → registo |
|---|---|---:|---:|---:|---|---|
| 1× (4 MB) | 54 + 356 ms | 35 ms | 0,20 ms | 1,4 ms | 0,05 s / 26 MB | 3,9–4,9 s |
| 4× (17 MB) | 169 + 1 462 ms | 167 ms | 0,21 ms | 1,2 ms | 0,18 s / 27 MB | 3,7–4,7 s |
| 30× (131 MB) | 1 326 + 12 501 ms | 1 187 ms | 0,30 ms | 1,3 ms | 1,29 s / 34 MB | 1,9–3,2 s |

O custo por alteração deixou de crescer com o diário. O atraso é dominado pela sondagem de 5 s. Durante a medição
apareceram e foram corrigidos dois problemas:
- o primeiro cursor lia o ficheiro inteiro para memória (pico de 733 MB a 30×); agora lê linha a linha;
- `ru_maxrss` herda o pico do processo pai em Linux; o evento `stop` usa agora `VmHWM`.

O registo de uma falha técnica tem ~1,2 KB e o de uma observação com livro ~4,7 KB.

## 6. Ativação e reversão

1. PR de ativação: `git apply ops/polymarket-p2/activation.patch`. A CI tem de ficar verde (inclui os invariantes
   atualizados). A revisão deve aceitar explicitamente a alteração dos dois invariantes.
2. Merge da PR de ativação, por decisão do responsável. O observador continua desligado.
3. Ligar: variável de repositório `GOAL_ROI_POLYMARKET_SHADOW=on` (Settings → Secrets and variables → Actions →
   Variables). Sem código.
4. Supervisionar 3 execuções:
   - duração do job sem aumento > 30 s;
   - `PUBLICATION_COMPLETED` normal e avisos normais;
   - linhas `POLYMARKET_SHADOW_*` no passo final;
   - `data/polymarket_observations.jsonl` criado após o primeiro sinal elegível.
5. Verificar no runner, porque não foi verificado aqui:
   - o processo em segundo plano sobrevive entre passos;
   - o Cloudflare da Polymarket aceita os IPs do GitHub;
   - `actionlint` sem erros.

**Reversão imediata:** apagar a variável ou pôr `off`. Na execução seguinte os dois passos ficam `skipped` e nada mais
muda. Para remover por completo: `git apply -R` do patch numa PR. O diário existente fica (só acrescenta) e é
ignorado pela produção.

Relatório: `python -m scripts.polymarket_p2_audit [--as-of ...] [--since ...]`.
- **Início do período:** `--since` explícito; senão o 1.º evento `start` em `data/polymarket_observer_runs.jsonl`
  (anterior ao corte). Sem nenhum dos dois: `since_required`, `eligible_signals=null` e código 2. Nunca se apresenta
  "zero elegíveis" por falta de evidência.
- **Denominador:** **todos** os sinais elegíveis lidos no período, observados ou não, e também os jogos sem prova. Os
  não observados dividem-se em "observador a correr" e "observador ausente".
- **Métricas distintas:** `responses_within_120s` (inclui `stale_book`), `fresh_price_within_120s` (só prova de preço
  fresco) e `stale_book_within_120s`. A profundidade e a comparação odd do bot ↔ odd líquida usam **só** preço fresco
  (`excluded_not_fresh` conta os excluídos).
- ROI líquido comprovado: `null` (sem execução nem liquidação).

## 7. Evidência e CI

- Ensaios reais, com mensagens **sintéticas** no formato do bot para jogos reais (não são sinais do ScanGoal; as odds
  da mensagem foram escolhidas à mão, por isso a comparação odd do bot ↔ odd líquida não tem significado):

  | Jogo | Estado | Atraso | Idade do livro | Ask | Odd líquida 10 / 500 USDC |
  |---|---|---:|---:|---|---|
  | Arsenal FC — BV Borussia 09 Dortmund | `contemporaneous`, preço fresco | 6,4 s | 18 s | 0,61 × 10 943 | 1,6080 / 1,6080 |
  | Real Madrid CF — PSV | `contemporaneous`, preço fresco | 6,2 s | 2 s | 0,84 × 1 585 | 1,1810 / 1,1810 |
  | Feyenoord — Porto | `identity_unproven` | — | — | — | — |

  "Feyenoord" ≠ "Feyenoord Rotterdam" e não há alias: falha fechada, como deve ser.
  Ficheiros: `rehearsal-*.json`, gerados com o código atual, com os eventos de execução e a auditoria de cada ensaio
  (data de ativação tirada do evento `start`). O `git status` do repositório não mudou durante os ensaios.
- Cobertura de identidade nos 61 encontros atuais (`coverage-identity.json`, sem preços): 35 contratos verificados,
  25 sem identidade e 1 indisponível (rede). Os nomes do bot e os da Polymarket continuam a ser o maior limite da
  cobertura.
- CI: ver a descrição da PR.

## 8. Veredito

| Dimensão | Veredito |
|---|---|
| Integração do código, desativada, sem tocar na produção | **GO** |
| Ativação no workflow | **GO condicionado**: PR de ativação + variável + 3 execuções supervisionadas |
| Integrar o observador no workflow nesta PR | **NO-GO** (por desenho: decisão separada) |
| Científico | **NO-GO**: ainda não há dados prospetivos (`not_activated`). Uma cotação contemporânea não é uma aposta executável (`restricted=true`, atraso de taker de 1 s, deslizamento). |

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.** A issue #56 continua aberta.
