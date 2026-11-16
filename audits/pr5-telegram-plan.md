# PR 5/5 — Telegram e apresentação (plano técnico, NÃO implementado)

Estado: plano para aprovação. Nada foi implementado, nenhuma mensagem foi enviada e `data/notify_state.json` não foi tocado.
Depende da PR 4 (módulo `scangoal/equivalence.py`). Certificação: continua `not_activated`.

## 0. Âmbito

Tornar os avisos fiáveis e informativos: um aviso por acontecimento real, agrupado por hipóteses equivalentes, com jogos
independentes, ROI, probabilidade, evidência nova e estado científico; nunca "validada" sem certificado ativo e verificado;
nenhuma perda de histórico nem de contadores.

Fora de âmbito: regras de estado, certificação, avaliação LLM (PR 4), leitor do ScanGoal (`reader.py`, `watch.py` só leitura),
`data/messages.jsonl`, workflows, secrets.

## 1. Diagnóstico (estado atual, com referências)

Fotografia: `sent_total = 307`, `watch_sent = 115`, 3 marcas de migração (PR 1, PR 2, PR 3), 4 promissoras de referência.

| # | Constatação | Onde | Consequência |
|---|---|---|---|
| E1 | O estado só é gravado depois de **todos** os envios. Se o envio k falhar, os k−1 já entregues voltam a sair na recolha seguinte; `sent_total` não os conta. | `notify.run` (`notify.py:264-268`) | Repetições e contador abaixo do real. |
| E2 | Chave do indício/alerta = `date_utc|match|selection`. Cada reforço (×2/×4/…) do mesmo jogo é outro sinal com outra hora. | `notify.py:182` | O mesmo jogo/aposta pode sair várias vezes; o README promete "uma só vez por jogo". |
| E3 | Um aviso por hipótese que muda de estado. | `notify.py:205-219` | Hoje, 3 das 4 promissoras são a mesma condição (47/42/41 jogos, Jaccard 0,87–0,89): três avisos para um só facto. |
| E4 | Hipótese ausente do `status_list` = "voltou a em observação". Ausência por relatório degradado (fonte de resultados em falha, nascimento não verificável, verificação de histórico indisponível) gera uma falsa mudança, e depois outra falsa no regresso. | `notify.py:214-218` | Falsas mudanças de estado (ida e volta). |
| E5 | `watch_sent` é truncado aos 400 mais recentes. | `notify.py:201` | O histórico de entregas não é integral. |
| E6 | Os textos misturam acerto por sinais e jogos; não mostram ROI, evidência nova desde o último aviso nem o estado científico. | `notify.alert`, indício | Leitura enganadora da força da evidência. |
| E7 | O resumo diário escreve "N fortes, N confirmadas" a partir de `brain.counts`, não do certificado. | `notify.py:242` | Hoje dá 0, mas não é fail-closed se as contagens e a certificação divergirem. |

Garantias que já existem e se mantêm: `certified_ids` e `eligible_statuses` são fail-closed (método, `state = active`,
`ledger_valid is True`); migração silenciosa por chave de regras; gravação atómica; `watch.py` e `notify` correm em
sequência no mesmo job (escritor único pelo grupo de concorrência).

## 2. Desenho

### 2.1 Entrega idempotente (E1, E5)

- Cada aviso tem uma chave determinística do **acontecimento** (não do texto): p. ex. `alert|<match_id>|<market>|<selection>`,
  `status|<grupo>|<de>→<para>|<jogo da confirmação>`, `summary|<dia>`.
- Antes de cada envio grava-se `in_flight = chave` (atómico); depois do `ok` grava-se a entrega e incrementa-se `sent_total`
  (atómico). Uma chave que ficou `in_flight` por queda do processo **não é reenviada** (no máximo uma vez) e conta em
  `uncertain_deliveries`. Falha antes do envio: a chave fica pendente e só sai se ainda for válida (alerta ainda apostável).
- Registo de entregas só a crescer: `data/notify_log.jsonl` (chave, tipo, hora, `sha256` do texto; sem token, sem chat id).
- `watch_sent` mantém-se com o significado atual (compatibilidade com `post_cutover_check`), mas deixa de ser truncado;
  a deduplicação nova usa o registo.

### 2.2 Consolidação (E2, E3)

- Indícios e alertas: um aviso por **jogo** (`match_id`); várias seleções do mesmo jogo saem na mesma mensagem; reforços
  posteriores do mesmo (jogo, mercado, seleção) nunca geram novo aviso.
- Hipóteses: agrupadas por `equivalence.groups` (PR 4; presença em jogos, nunca resultados). Um aviso por grupo e por
  mudança: representante + "e mais N equivalentes (partilham ≥ 85 % dos jogos)".
- Limite por recolha (`MAX_WATCH_PER_RUN = 5`) passa a contar jogos, não sinais.

### 2.3 Mudanças de estado sem falsos positivos (E4)

Uma mudança só é anunciada se **todas** se verificarem:
1. Relatório saudável: `evidence_scope` e `status_rules` iguais aos da referência; `eligible_statuses` válido; nenhuma
   queda anómala do universo elegível (limiar fixado nos testes); verificação de histórico no mesmo estado da anterior.
2. A hipótese está **presente** no relatório com o novo estado; ausência = "desconhecido", nunca "voltou a em observação"
   (só se anuncia o regresso a observação quando o relatório o diz explicitamente para essa hipótese).
3. `pending_status` é nulo e a mudança consta de `status_changes` com o jogo que a confirmou (histerese da PR 2).
4. Não é a reversão de uma mudança anunciada nas últimas 24 h; nesse caso sai no resumo diário como "oscilou".

Separação de campos: `statuses` continua a ser a referência do relatório (exigido por `post_cutover_check`:
`statuses_follow_report`); o que foi anunciado passa a `announced_statuses` (novo).

### 2.4 Conteúdo (E6, E7)

Cada aviso de hipótese/alerta mostra, nesta ordem (o estado científico fica no topo para nunca ser cortado nos 3900 caracteres):
- **Estado científico:** "não certificada — certificação desligada" / "não certificada" / "certificada (método
  `eprocess_online_alpha_v1`, registo verificado, entrada n.º j)". Gerado por uma só função, `scientific_label(report, hid)`.
- Jogos independentes (`games`), não sinais; ROI por jogo à odd anunciada e com −2 %;
- probabilidade de estar acima do necessário e com −2 % de perda de odd, vantagem com intervalo de 90 %;
- **evidência nova:** jogos prospetivos desde o registo e desde o último aviso desse grupo;
- jogo, seleção, odd, hora de Lisboa, odd mínima (como hoje).
O resumo diário passa a "validadas (certificadas): N" a partir de `certified_ids`, e "fortes/confirmadas" deixa de aparecer sem certificado.

### 2.5 Nunca "validada" sem certificado (guarda dupla)

1. Estrutural: só `scientific_label` e o ramo de alerta (que já exige `certified_ids`) podem produzir as palavras
   "validada", "certificada", "confirmada"; textos vindos de dados (razão da LLM, notas) não são mostrados nos avisos.
2. Barreira final `guard_claims(texts, report)`: se algum texto contiver essas palavras (qualquer flexão, com ou sem acentos)
   e não houver certificação ativa e verificada para o identificador citado, o aviso é **bloqueado** (não enviado,
   contador `blocked_claims`), fail-closed.

### 2.6 Migração silenciosa e histórico

- Chave de migração nova `telegram_format_v2` (mesmo mecanismo da PR 1–3): na primeira execução semeia `announced_statuses`
  com os estados atuais e marca como entregues os jogos já avisados (convertendo as chaves antigas), sem enviar nada.
- Nenhum campo existente é removido, renomeado ou reiniciado: `hello`, `sent_total`, `watch_sent`, `statuses`, `migrations`
  (as três marcas), `regime`, `operational_regime`, `summary_day`. `sent_total` só cresce.
- `data/messages.jsonl` (histórico lido do ScanGoal) não é tocado.

### 2.7 Pré-visualização sem envio

`python -m scangoal.notify --preview [--state <ficheiro>]`: ignora `TG_BOT_TOKEN`/`TG_CHAT_ID`, trabalha numa cópia do
estado, escreve os textos para o ecrã ou ficheiro, nunca grava estado, nunca abre ligações. É a única forma de "ver" mensagens
nesta PR; a pré-visualização sobre dados de `main` congelados fica em `audits/pr5-evidence/preview-<sha>.txt`.

## 3. Testes adversariais (offline; `sender` injetado; `socket` bloqueado)

| ID | Cenário | Resultado exigido |
|---|---|---|
| U1 | Falha no 3.º de 5 envios; nova execução | 1.º e 2.º nunca reenviados; 3.º–5.º uma vez; `sent_total` exato |
| U2 | Queda entre envio e gravação (`in_flight`) | Não reenviado; `uncertain_deliveries` = 1 |
| U3 | Sinal ×1, ×2, ×4, ×8 do mesmo jogo/seleção em recolhas seguidas | Um único aviso |
| U4 | Duas seleções no mesmo jogo | Uma mensagem com ambas |
| U5 | 3 hipóteses equivalentes passam a promissora | Uma mensagem (representante + 2) |
| U6 | 3 hipóteses não equivalentes | Três mensagens |
| U7 | Relatório degradado (hipótese ausente, `status_list` vazio, fonte de resultados em falha) | Zero mudanças anunciadas; referência anunciada intacta |
| U8 | A→B→A em 3 recolhas dentro de 24 h | No máximo um aviso; oscilação no resumo |
| U9 | `pending_status` não nulo | Nenhum aviso |
| U10 | Certificado forjado: `state=active` com `ledger_valid=False`; método diferente; `certified` com id sem confirmação; histórico não verificado | Nunca "validada"; tática tratada como promissora |
| U11 | `status=forte` sem certificado | Nunca "validada" nem "forte" no texto |
| U12 | Razão/nota da LLM com "tática validada e certificada" | Não aparece; `guard_claims` não é necessário mas é testado à parte |
| U13 | `guard_claims` com "VALIDADA", "validádas", "certificado", "confirmadíssima" sem certificado | Bloqueado |
| U14 | Estados reais das marcas PR 1, PR 2, PR 3 e de produção (copiados do histórico git) + relatório atual | Migração silenciosa: zero mensagens; todos os campos e contadores preservados |
| U15 | `post_cutover_check` em antes/depois simulado | Continua a passar |
| U16 | Mensagem com 40 jogos/hipóteses | ≤ 3900 caracteres; estado científico presente |
| U17 | `--preview` com token e chat definidos no ambiente | Nenhuma ligação; estado byte a byte igual |
| U18 | Resumo às 20:59 e 21:00 UTC, mudança de dia | Comportamento atual mantido (um por dia) |
| U19 | Alerta fora de prazo (jogo começado há > 30 min) | Não sai e não fica pendente |
| U20 | `notify_log.jsonl` | Só cresce; nenhuma linha com token ou chat id |

Verificação por mutação (`audits/pr5-evidence/mutation_check.py`): remover `in_flight`, trocar a chave por jogo pela antiga,
desligar o agrupamento, tratar ausência como observação, saltar `guard_claims` — cada mutação tem de partir um teste.

## 4. Critérios de aceitação

1. U1–U20 e toda a bateria existente passam em Python 3.11; todas as mutações apanhadas.
2. Ensaio de corte sobre uma cópia de `data/` de `main` congelado: **zero** mensagens na primeira execução, `sent_total`
   e `watch_sent` iguais ou maiores, as três marcas de migração intactas, `statuses == status_list`.
3. Pré-visualização das mensagens que o código produziria nas 24 h anteriores (reconstituídas a partir do histórico git),
   comparada com o que foi realmente enviado: menos ou igual número de avisos, nenhum aviso real perdido (diferenças explicadas
   uma a uma em `audits/pr5-evidence/`).
4. Nenhum texto contém "validada/certificada/confirmada" com a certificação desligada.
5. Nenhuma mensagem real enviada durante o desenvolvimento e a auditoria; nenhuma alteração a workflows ou secrets.
6. README atualizado (secção "Avisos pelo Telegram").

## 5. Riscos

| Risco | Mitigação |
|---|---|
| Migração reenvia avisos antigos | Semeadura silenciosa (`telegram_format_v2`) + ensaio U14 sobre estados reais |
| "No máximo uma vez" perde um alerta após queda | Raro (queda entre envio e gravação); contado em `uncertain_deliveries`, visível no resumo |
| Consolidação esconde uma hipótese distinta | Agrupamento só por pertença em jogos (≥ 85 %), representante e contagem sempre mostrados; U6 |
| Supressão de falsas mudanças atrasa uma mudança real | Só atrasa até ao relatório saudável seguinte; nunca a descarta |
| `notify_log.jsonl` cresce | ~150 bytes por aviso (~300 avisos até hoje); revisto se passar 5 MB |
| Rollback com estado novo | O código antigo ignora campos novos; nunca restaurar um estado antigo sobre um que já teve entregas (regra da PR 1) |

## 6. Estratégia de integração (separada)

1. **Desenvolvimento:** só depois do merge da PR 4 (depende de `equivalence.py`). Código, testes, mutação, ensaio de corte,
   pré-visualização. Sem envios.
2. **Auditoria independente** (outra sessão, só leitura): repete U1–U20, escreve ataques próprios, refaz o ensaio de corte e a
   pré-visualização sobre o mesmo SHA. Resultado em `audits/pr5-independent-audit.md`.
3. **Integração** (autorização expressa): runbook `audits/pr5-cutover-runbook.md` no modelo da PR 3 — cópia de segurança de
   `data/notify_state.json`, merge sobre SHA congelado, migração silenciosa offline (`--migrate-statuses` estendido) antes da
   primeira execução, verificação só de leitura das duas recolhas seguintes (`post_cutover_check` + verificações PR 5:
   `sent_total` monotónico, marcas intactas, zero envios retroativos). O proprietário confirma no Telegram que a primeira
   mensagem real é um acontecimento novo.
   Reversão: `git revert`; manter o `notify_state.json` mais recente (nunca o da cópia, se já houve entregas).

## 7. Entregáveis

`scangoal/notify.py` (entrega idempotente, consolidação, filtros, `scientific_label`, `guard_claims`, `--preview`),
`tests/test_telegram_presentation.py`, `scripts/rehearse_pr5_cutover.py`, verificações PR 5 em `scripts/post_cutover_check.py`,
`audits/pr5-cutover-runbook.md`, `audits/pr5-evidence/`, README.

## 8. Decisões pedidas ao proprietário

1. Entrega "no máximo uma vez" (prefere perder raramente a repetir) em vez de "pelo menos uma vez"? Recomendado: no máximo uma vez.
2. Janela anti-oscilação de 24 h. Aceitar ou ajustar.
3. Ramo da PR 5: autorizar um segundo ramo de desenvolvimento ou reutilizar este depois do merge da PR 4.
