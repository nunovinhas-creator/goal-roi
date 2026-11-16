# PR 5/5 — Procedimento de integração (NÃO executado; exige autorização expressa)

A PR 5 muda só a forma como os avisos Telegram são decididos, entregues e apresentados. Não muda regras de estado,
certificação (continua `not_activated`), avaliação LLM, leitor do ScanGoal nem secrets.

**Workflow:** `.github/workflows/collect.yml` tem **exatamente duas linhas novas**, `NOTIFY_DURABLE: git`, no `env` dos passos
"Telegram notices through the user's bot" e "Fast reads every 2 minutes until the next collection". Foram autorizadas na
revisão #5921136533 e não há mais nenhuma alteração a workflows.

**Âmbito da garantia "no máximo uma vez":** cobre os avisos dos passos "Telegram notices" e "Fast reads" com
`NOTIFY_DURABLE=git`, entre runners. **Não cobre o aviso de recolha falhada (`--failure`)**: esse passo fica fora da barreira,
sem chave de acontecimento, sem registo e sem `sent_total`, e mantém o comportamento anterior (no máximo um por hora).

O que muda nos dados de produção, sempre por acréscimo:
- `data/notify_state.json` ganha campos novos (`announced_statuses`, `announced_changes`, `notified_items`, `notice_games`,
  `deliveries`, `delivery_counts`, `summary_counts`, `oscillations`, `health`, `format_last_run_utc`, `format_resyncs`) e a marca
  `migrations.telegram_format_v2`. Nenhum campo existente é removido, renomeado ou reiniciado; `sent_total` e `watch_sent`
  continuam a crescer; as três marcas anteriores ficam byte a byte iguais.
- `data/notify_log.jsonl` (novo, só a crescer) aparece no primeiro aviso tentado depois da integração. Entra nos commits de
  intenção da barreira e no `git add data` que o workflow já fazia.
- `data/report.json` e `docs/report.json` ganham `brain.presentation` e `match_id` nos sinais em aberto (aditivos; a página não
  os lê).

## Estado atual (depois das revisões #5920565094, #5921136533 e #5921609334)

- P0: a barreira de publicação (`NOTIFY_DURABLE=git`) está no código e, por autorização da revisão #5921136533, **ligada no
  workflow desta PR** (duas linhas, passos "Telegram notices" e "Fast reads"). Entra em funcionamento com o merge, sem janela
  com a barreira desligada. Desenho e validação: `audits/pr5-p0-durability-design.md`.
- P2-R: um `{}` (ou um estado sem histórico) nunca é uma instalação nova, mesmo sem registo de entregas. Só `--init-state`
  escreve a marca `installation`; um estado de produção tem de ter `sent_total`, `watch_sent`, `statuses` e a marca da PR 1.
- P1: a identidade de um aviso de jogo é a aposta (jogo, mercado, seleção), sem o nível; indício → alerta da mesma aposta não
  repete a aposta.
- P2: estado ausente ou regredido → recusa, nada enviado (`NOTIFY_STATE_MISSING`, `StateRegressed`). Em produção, a correção
  é repor `data/notify_state.json` a partir do último commit do `main` (`git checkout origin/main -- data/notify_state.json`),
  nunca `--init-state`.

## Pré-condições

1. Auditoria independente aprovada sobre o SHA final da PR (resultado em `audits/pr5-independent-audit.md`).
2. Autorização expressa do proprietário para o merge.
3. CI verde nesse SHA.

## Fase A — preflight (só leitura, no checkout da PR, com o histórico completo)

1. `git fetch origin main` e congelar o SHA M do `main`.
2. `git diff --stat M...<SHA da PR> -- data docs/report.json docs/bets.csv` tem de estar vazio, e
   `git diff M...<SHA da PR> -- .github` só pode conter as duas linhas `+          NOTIFY_DURABLE: git`.
3. `python3.11 -m unittest discover -s tests` — tudo a passar.
4. `python3.11 audits/pr5-evidence/mutation_check.py` — todas as mutações apanhadas.
5. `python3.11 scripts/rehearse_pr5_cutover.py M /tmp/pr5-rehearsal.json` — `ok: true`:
   zero mensagens nas duas primeiras execuções e na pré-visualização, `sent_total` e `watch_sent` iguais, três marcas iguais,
   `statuses == status_list`, nenhuma linha no registo, `post_cutover_check` (com as verificações PR 5) aprovado.
6. Pré-visualização dos dados de M (sem rede, sem credenciais, sem escrita):
   `SCANGOAL_DATA_DIR=<cópia de data/ de M> python3.11 -m scangoal.notify --preview --out /tmp/pr5-preview.txt`
   (com o relatório de M gerado pelo código antigo não há `presentation`: zero avisos de estado é o resultado esperado).

Último ensaio: `main` `4af4c95`, ver `audits/pr5-evidence/cutover-rehearsal.json`.

## Fase B — merge

- Merge com **merge commit** (sem *squash* nem *rebase*), como nas PR anteriores.
- **A recolha não precisa de parar.** O grupo de concorrência `scangoal-write` garante uma só cadeia de execuções: uma
  execução iniciada antes do merge corre e grava com o código antigo (formato antigo, que o código novo lê); a primeira
  execução com o código novo faz, dentro da própria execução e antes de qualquer envio, a migração silenciosa
  `telegram_format_v2` (a mesma função que `--migrate-format`), e só depois decide avisos. Ensaiado em `U14` e no ensaio de corte.
- Alternativa, se o proprietário preferir a migração antes da primeira execução: com a recolha parada, `python3.11 -m
  scangoal.notify --migrate-format` num checkout do `main` já com o merge, commit de `data/notify_state.json` e só depois retomar.
  Exige que a referência de regras do relatório já esteja migrada (recusa caso contrário) e é idempotente.
- Não ativar a certificação. Não alterar `fetch-depth`. Não enviar mensagens Telegram de teste.
- O merge leva as duas linhas `NOTIFY_DURABLE: git`. Cada aviso passa a gerar um commit `notify: intent … [skip ci]` antes do
  envio.

## Fase C — verificação (só leitura, nas duas recolhas seguintes)

1. `python3.11 scripts/post_cutover_check.py <merge> <commit da 1.ª recolha seguinte>` e depois entre a 1.ª e a 2.ª. Tem de
   passar, incluindo as verificações PR 5:
   - `pr5_format_marker_stable`: a marca aparece e nunca muda;
   - `pr5_log_append_only`: o registo só cresce;
   - `pr5_at_most_one_attempt`: nenhuma chave com duas intenções ou dois resultados;
   - `pr5_sent_total_matches_log`: `sent_total` = valor na migração + entregas confirmadas no registo;
   - `pr5_nothing_in_flight`: nenhuma entrega a meio;
   - `pr5_intent_published_before_outcome`: cada entrega tem a intenção num commit anterior ao do resultado (barreira ativa).
   Os registos dos passos mostram `NOTIFY_DURABILITY git`.
2. `migrations.telegram_format_v2.sent_total` é igual ao `sent_total` do último commit antes do merge; `announced_statuses`
   é igual ao `statuses` desse commit.
3. O proprietário confirma no Telegram que a primeira mensagem real depois do merge é um acontecimento novo e que começa por
   `Estado científico: NÃO CERTIFICADA — certificação desligada.` (ou `nenhuma tática certificada`).
4. Se `post_cutover_check` falhar: parar (desligar o workflow na interface do GitHub não exige alterar ficheiros), guardar os
   dois commits e investigar antes de qualquer outra ação. `pr5_nothing_in_flight` falso indica uma queda a meio de um envio:
   a execução seguinte marca-a como incerta e não a reenvia.

## Reversão

- `git revert -m 1 <merge>`.
- **Nunca** restaurar `data/notify_state.json` nem `data/notify_log.jsonl` de uma cópia anterior: manter sempre os mais
  recentes. O código antigo ignora os campos novos e continua a manter `statuses`, `watch_sent`, `sent_total`, `summary_day`
  e `operational_regime`; o registo fica parado (o código antigo não o escreve).
- **Retirar só as duas linhas `NOTIFY_DURABLE: git` NÃO é uma reversão segura.** Reabre o defeito P0 tal como os testes o
  reproduzem (`test_without_the_barrier_a_dead_runner_causes_a_duplicate`): um runner que envie e morra, ou cujo push final
  falhe, deixa o runner seguinte reenviar a mesma mensagem. Só serve como medida de emergência, aceitando expressamente a perda
  da garantia entre runners. Nesse caso `pr5_intent_published_before_outcome` passa a falhar. A reversão completa
  (`git revert -m 1 <merge>`) também volta ao comportamento anterior à PR 5, que não tinha esta garantia.
- Nova integração depois de uma reversão: a primeira execução com o código novo deteta que outro escritor correu
  (`last_run_utc` diferente de `format_last_run_utc`) e passa a referência anunciada para a do código antigo, em silêncio e
  com registo em `format_resyncs`. Os sinais avisados pelo código antigo ficam protegidos pelas chaves de `watch_sent`.
  Nesse período `pr5_sent_total_matches_log` deixa de valer (o código antigo conta envios sem registo): esperado e explicado.

## Riscos residuais (aceites ou a decidir)

| Risco | Situação |
|---|---|
| "No máximo uma vez" perde um aviso | Uma falha confirmada (p. ex. HTTP 429) ou uma entrega incerta nunca é repetida. Conta no resumo diário. Decisão do proprietário: preferir perder raramente a repetir. |
| Execução incompleta entre commits | Um commit pode conter várias execuções do notificador (leituras rápidas). Sem efeito na entrega; limita a reconstituição a nível de commit. |
| Degradação longa | Uma degradação estável há 24 h passa a referência; mudanças ficam atrasadas até 24 h, nunca descartadas, e o resumo mostra-a. |
| Regra "mesmo jogo em 3 dias" | Um aviso no formato antigo protege reforços com outra hora de sinal pela dupla (jogo, seleção) durante 3 dias. Dois jogos diferentes com as mesmas equipas e seleção em 3 dias não teriam o segundo indício. |
| Nome de equipa com "validada", "certificado", "forte"… | O aviso desse jogo é bloqueado (fail-closed) e contado em "bloqueados". |
| Crescimento | `deliveries` ≈ 250 bytes por aviso no estado e ≈ 300 bytes por aviso no registo; rever acima de 5 MB. |
| `--failure` | **Fora da barreira**: o aviso de recolha falhada mantém o comportamento anterior (no máximo um por hora, sem registo, sem chave de acontecimento). |
| Barreira e GitHub | A garantia entre runners pressupõe que o GitHub não perde nem reverte commits de intenção já aceites (nunca fazer force-push ao `main`). Um push aceite com a resposta perdida pode perder um aviso (fica incerto), nunca duplicá-lo. |
| Estado ausente | O notificador pára de enviar até o estado ser reposto (fail-closed). Visível nos registos (`NOTIFY_STATE_MISSING`) e por `post_cutover_check` (ficheiro ilegível). |
