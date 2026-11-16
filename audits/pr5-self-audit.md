# PR 5/5 — Telegram e apresentação: autoauditoria

## Estado atual (depois da 3.ª auditoria independente, revisão #5921609334)

- **Não integrado.** PR #28 em Draft; o merge exige autorização expressa do proprietário.
- **Workflow:** no ramo da PR, `.github/workflows/collect.yml` tem exatamente duas linhas novas, `NOTIFY_DURABLE: git`, nos
  passos "Telegram notices" e "Fast reads" (autorização da revisão #5921136533). Nenhuma outra alteração a workflows; o `main`
  não foi alterado.
- **Barreira P0 ativa no ramo:** cada intenção é publicada no GitHub antes de contactar o Telegram. A garantia "no máximo uma
  vez" entre runners cobre os avisos desses dois passos. **Não cobre `--failure`**, o aviso de recolha falhada, que fica fora
  da barreira com o comportamento anterior.
- **Reversão:** retirar as duas linhas reabre o defeito P0 e não é uma reversão segura (runbook).
- **Fora do âmbito, nada mudou:** nenhuma mensagem real enviada, nenhuma alteração a `main`, dados de produção ou secrets; o
  coletor não foi interrompido; a certificação continua `not_activated`; as regras científicas das PR 3 e 4 estão intactas.
- **Histórico das rondas, mantido tal como foi escrito e datado por SHA:**
  - 1.ª ronda (`4b92b5b`): secções 1 a 6;
  - 2.ª ronda (`72107cf`): secção 0;
  - 3.ª ronda (`183b384`): secção 00;
  - fecho da 3.ª auditoria: secção 000.
  
  Onde uma passagem antiga já não descreve o estado atual, leva uma nota "[superado]".

- Base: `main` `60ba009` (contém o merge da PR 4 `5710113`), integrado por *fast-forward*, sem force-push.
- Ramo: `claude/pr5-telegram-presentation-catr19` (o ambiente de desenvolvimento só permite publicar neste nome; o
  autorizado era `claude/pr5-telegram-presentation`).
- Plano: `audits/pr5-telegram-plan.md`. Runbook: `audits/pr5-cutover-runbook.md`. Evidência: `audits/pr5-evidence/`.

## 00. Terceira ronda — autorização #5921136533 (barreira ligada no ramo e P2-R)

| Ponto | O que foi feito | Prova |
|---|---|---|
| Workflow | Só duas linhas: `NOTIFY_DURABLE: git` no `env` de "Telegram notices" e de "Fast reads". Nada mais mudou (`git diff origin/main -- .github` = 2 linhas `+`). | `WorkflowActivation` (3 testes); M54, M55 |
| Pré-requisitos | `GITHUB_REF_NAME` do Actions (`main` nas recolhas); `permissions: contents: write`; escritor único (`scangoal-write`, `cancel-in-progress: false`); `watch.refresh()` → `notify.run()` sem argumentos (lê o `NOTIFY_DURABLE` do passo). | `WorkflowActivation` |
| Nenhum envio antes do push | No instante do envio, o remoto já tem a intenção do mesmo texto (`sha256`), também com o emissor real e pelo caminho das leituras rápidas. | `BarrierOrdering` (2); M44, M47 |
| Produção | `post_cutover_check.pr5_intent_published_before_outcome`: commit da intenção anterior ao do resultado. Falha sem a barreira. | U15 (2, git real); M56 |
| P2-R | `--init-state` escreve `{"installation": {"method": "init-state", …}}`. Um estado só é aceite se tiver essa marca ou um histórico de produção (`sent_total`, `watch_sent`, `statuses` e a marca da PR 1). `{}` ou equivalente, com ou sem registo, dá `StateRegressed`: zero envios e nada escrito. `--migrate-format` também recusa. Estados PR 1, PR 2, PR 3 e de produção continuam válidos. | `P2R_ResetStateWithoutLog` (6, incluindo `{}` + log ausente ou vazio + relatório real com avisos históricos → zero envios); M49–M53 |
| Testes antigos | Dois testes de `test_notify_migration` corriam `notify.run` sobre um estado anterior à PR 1 sem marcas, que hoje é recusado. Passam a usar o mesmo estado com a marca da PR 1; a asserção sobre a migração persistida antes do envio passou a ser a da PR 5. | — |
| Resultados (código `987b016`, `main` `61cdb04`) | **433 testes OK** em Python 3.11 e 3.13. **56/56 mutações** apanhadas (`mutation_check.json`; M49–M56 novas, incluindo duas sobre o próprio workflow e uma sobre `post_cutover_check`). Ensaio `ok: true` (local e com barreira). Reconstituição das 24 h até `61cdb04`: 33 avisos contra 36 reais, 23/23 jogos no mesmo commit, estados iguais em 146/146 commits depois do corte PR 3, diferenças explicadas como antes. | `cutover-rehearsal.json`, `replay-24h.md` |
| Ensaio de corte | Nova fase `durable_*`: remoto *bare* com os dados congelados, runner limpo, duas execuções com a barreira e o passo de commit do workflow. Resultado: migração dentro da execução, zero mensagens, zero commits de intenção, contadores e marcas iguais, `post_cutover_check` verde. | `cutover-rehearsal.json` |

## 0. Segunda ronda — correções da auditoria independente (revisão #5920565094)

Ramo atualizado com o `main` `4af4c95` por merge (sem force-push; o `main` só trouxe commits de dados).

| Ponto | Correção | Testes | Mutações |
|---|---|---|---|
| **P0** persistência entre runners | Barreira `GitPublisher`: commit e push da intenção (só `notify_state.json` e `notify_log.jsonl`) **antes** de contactar o Telegram; se a publicação falhar, a intenção é retirada (`withdrawn`), nada é enviado e o aviso fica pendente. [superado na 3.ª ronda: ligada no workflow do ramo, ver secção 00] **Inativa** até `NOTIFY_DURABLE=git` no workflow, que NÃO foi alterado: desenho, diff proposto e riscos em `audits/pr5-p0-durability-design.md`, para autorização separada. Até lá, a garantia declarada é "no máximo uma vez dentro do mesmo runner". | `tests/test_notify_durability.py` (12, git real: remoto *bare* e um clone limpo por runner; inclui a reprodução do defeito sem barreira, morte em 6 pontos, push recusado, push aceite mas dado como falhado, conflito, leituras rápidas, queda durante a retirada) | M44–M48 |
| **P1** duplicação após promoção | A identidade de um aviso de jogo é a **aposta** (jogo, mercado, seleção), sem o nível indício/alerta; a proteção das chaves antigas (mesmo jogo e seleção em 3 dias) vale para todos os níveis. A certificação da hipótese continua a ser um acontecimento separado ("✅ Tática validada"). | `P1_PromotionDoesNotRepeatTheBet` (3: indício → reforço com outra hora → certificada → mesma aposta: só o aviso de estado; seleção nova depois da promoção: alerta de atualização só com ela; indício enviado pelo código antigo → alerta da mesma aposta: nada) | M38, M39 |
| **P2** perda de estado | `notify_state.json` ausente → `StateMissing`, nada enviado, nada criado (`NOTIFY_STATE_MISSING`; a recolha continua). Estado vazio, anterior à PR 5 ou com `sent_total` abaixo do da migração ao lado de um registo → `StateRegressed`. Instalação nova só com `--init-state`, que recusa se houver estado ou registo. A pré-visualização segue a mesma regra. | `P2_StateLoss` (7) | M41–M43 |
| **Contrato** seleção nova num jogo avisado | Cada aposta é avisada no máximo uma vez; as apostas novas de um jogo numa recolha saem juntas. Uma seleção genuinamente nova num jogo já avisado sai como **atualização**: título "A seguir (nova seleção num jogo já avisado)" ou "🟢 ALERTA (nova seleção num jogo já avisado)", só com a aposta nova e a linha "Apostas já avisadas neste jogo: N (não repetidas)". O reforço dessa aposta nova também não se repete. Documentado no README e em `notify.game_notices`. | U4 (alargado), P1 | M40 |

Resultados desta ronda, sobre o código `740f664`:
- **421 testes OK** em Python 3.11 e 3.13 (343 anteriores + 66 em `test_telegram_presentation.py` + 12 em
  `test_notify_durability.py`);
- **48/48 mutações apanhadas** (`mutation_check.json`; M09/M10 reescritas para o código novo, M38–M48 novas);
- ensaio de migração sobre `main` `4af4c95` `ok: true` (zero mensagens, `sent_total` 312, `watch_sent` 117, três marcas iguais);
- reconstituição das 24 h até `4af4c95`: 34 avisos contra 37 reais, nenhum aviso real perdido, diferenças explicadas
  (`replay-24h.md`).

## 1. Objetivos obrigatórios → implementação → prova

| # | Objetivo | Onde | Prova |
|---|---|---|---|
| 1 | No máximo uma tentativa por acontecimento, registo persistente e auditável | `notify.deliver`, `reconcile`, `FileStore`; `data/notify_log.jsonl` | U1, U2, C1–C11, `LogAndStateRecovery`, `X_LegacyAndHistory`; M01–M08 |
| 2 | Pendente / entregue com confirmação / falha confirmada / incerta | `send` (classificação), estados dos registos | `X_Sender`, U1, U2, U19 |
| 3 | Incertas nunca reenviadas nem contadas | `reconcile` (in_flight → incerta), filtro por chave | U2, C*, M02, M03, M05 |
| 4 | Reforços e seleções do mesmo jogo numa mensagem | `game_notices`, `item_key` | U3, U4; M09 |
| 5 | Equivalentes (Jaccard ≥ 0,85) só na apresentação, compatíveis, sem cadeias transitivas | `groups_of` → `equivalence.groups` (PR 4) | U5, U6 (cadeia A~B~C, incompatíveis), M11, M12 |
| 6 | Sem falsas mudanças por relatórios degradados ou ausências | `check_health`, `explicit_status`, `change_verified` | U7, U9, M13–M16 |
| 7 | Anti-oscilação 24 h sem esconder perdas de certificação ou alterações críticas | `status_notices` | U8 (4 testes), M17–M20 |
| 8 | Estado científico no topo; jogos, ROI, probabilidade, evidência nova, aposta | `labels`, `evidence_lines`, `game_notice`, `status_notice` | `X_Presentation`, U16, M34 |
| 9 | Verificação estrutural; "não certificada" permitida; nunca positivo sem certificado da hipótese | `certificate`, `scientific_label`, `guard_claims` | U10 (10 falsificações), U11–U13, M21–M27 |
| 10 | Preservar `sent_total`, `watch_sent`, três marcas, histórico, referências | migração aditiva; `watch_sent` sem truncatura | U14 (estados reais), U15, `X_LegacyAndHistory`, M29, M30 |
| 11 | Migração silenciosa `telegram_format_v2` sem reenvio | `migrate_format`, `--migrate-format` | U14, ensaio de corte, M29 |
| 12 | `--preview` offline, sem escrita, sem credenciais | `preview`, `no_network`, `MemoryStore` | U17 (3 testes), M31, M32 |

## 2. Desenho da entrega (no máximo uma vez)

Por aviso: (1) plano gravado com registos `pending` (estado atómico, depois registo); (2) intenção: efeitos do aviso
(o que fica marcado como avisado) + `in_flight` gravados no estado, depois a linha `intent` no registo (com `sha256` e efeitos);
(3) envio; (4) resultado (`delivered` só com `ok: true`; `failed` para 4xx, `ok: false`, DNS ou ligação recusada; `uncertain`
para 5xx, tempo esgotado, ligação cortada, resposta ilegível ou qualquer outra exceção) gravado no estado (com `sent_total`) e
depois no registo. Uma falha ou incerteza pára os envios dessa execução.

Recuperação (`reconcile`, no início de cada execução): `in_flight` → `uncertain` (nunca reenviado); resultados que só o
registo conhece são repostos no estado com os efeitos e contadores; eventos que só o estado conhece são acrescentados ao
registo (`reconstructed: true`). Linha cortada no registo: isolada com uma quebra de linha, nunca reescrita.

Quedas ensaiadas (C1–C11, `CrashPoints`): antes de persistir a intenção (`planned`, `before_intent`), depois de a persistir
(`after_intent_state`, `after_intent`), durante o envio (`during_send`), depois da resposta positiva (`after_response`), durante a
atualização do registo (`during_record_save`, `after_record_state`, `after_record`) e durante as escritas do registo
(`during_pending_log`, `during_intent_log`). Em todas: nada transmitido duas vezes; `sent_total` = entregas confirmadas;
estado e registo consistentes; registo só cresce. Falha entre `notify_state.json` e `notify_log.jsonl` em ambos os sentidos:
`after_intent_state`, `after_record_state` e `test_restored_older_state_is_repaired_from_the_log`.

## 3. Decisões e desvios ao plano (a validar na auditoria)

1. **U1**: o plano dizia "3.º–5.º uma vez"; o objetivo 1 diz "no máximo uma tentativa". Segui o objetivo: uma falha
   confirmada é terminal e nunca é repetida (o 3.º não volta a sair; o 4.º e o 5.º saem uma vez na execução seguinte).
2. `--migrate-statuses` ficou exatamente como estava (os scripts dos cortes PR 1–3 dependem dele). A migração PR 5 tem o seu
   comando, `--migrate-format`, e corre também dentro da primeira execução, antes de qualquer envio.
3. `brain.py`: alteração **aditiva** (bloco `presentation` e `match_id` nos sinais em aberto). Prova: relatório completo
   refeito com o código do `main` e da PR sobre os mesmos dados — idêntico exceto os campos novos e dois campos que dependem do
   relógio real (`agenda.generated_utc`, `days_to_conclusion`); `make_fixtures.py` confirma `status_list`, `watch` e
   `validation` iguais aos de produção no commit congelado.
4. ROI "com −2 %": por jogo, com a aposta comprometida de cada jogo (`committed_bet`, a regra da certificação) e
   `bet_profit` (perda de odd `ODDS_LOSS`). Só apresentação.
5. "Lista de estados vazia" só conta como degradação quando o relatório não explica, hipótese a hipótese, o regresso a
   observação.
6. Novo (fora do plano): ressincronização silenciosa depois de uma reversão e nova integração (`format_resyncs`), para não
   repetir mudanças já anunciadas pelo código antigo.
7. Chave de mudança de estado inclui o instante do anúncio anterior da hipótese: sem isso, uma certificação perdida e
   recuperada sem mudança no cérebro teria a mesma chave e a recuperação ficaria calada (defeito encontrado nos testes).
8. Negação: só formas fechadas ("não", "sem", "nunca", "nunca foi", "não está", "deixou de estar", "nenhuma tática"). A primeira
   versão (janela de 3 palavras) deixava passar "sem dúvida nenhuma: tática muito forte"; corrigido e testado.
9. Uma tática anunciada como validada que desaparece do relatório sem certificado é anunciada como perda (nunca escondida);
   se o certificado continuar válido, a ausência é "desconhecida".
10. `pr1_cutover.kind` ignora as linhas "Estado científico" do topo (os scripts dos cortes antigos continuam a classificar).

## 4. Testes existentes alterados (39 linhas removidas no total), todos por mudança de comportamento pedida

| Teste | Antes | Agora | Porquê |
|---|---|---|---|
| `test_scangoal.Notify` (2) | `startswith(...)`; 2 alertas para 2 sinais do mesmo jogo/seleção; ausência = "voltou a observação" | rótulo no topo; **1** alerta "(2 sinais)"; relatório com `presentation` | objetivos 4, 6, 8, 9 |
| `test_notify_migration` (4) | ausência = regresso; "forte" só com a lista `certified`; erro do sender = reenvio da saudação | `presentation` explícita; certificado da própria hipótese; erro = incerta, nunca reenviada | objetivos 3, 6, 9 |
| `test_chronological_validity` (2) | idem; chaves de migração sem a PR 5 | + afirmação de que a ausência não gera aviso; marca `telegram_format_v2` | 6, 11 |
| `test_independence_stability` (3) | 4 avisos para 4 hipóteses redundantes; contagem PR 1 via notificador | **1** aviso (Jaccard 1); contagem PR 1 calculada no próprio teste (diferenças da referência) | 5 |
| `test_pr2_cutover`, `test_pr3_cutover` (2+2) | contrafactual "sem migração" e "código antigo" contados pelo notificador atual | contrafactual calculado no teste (`raw_transitions`), e o notificador PR 5 envia 0 nesses cenários | 6 |

Nenhum teste foi apagado ou desativado. Fixture nova `tests/pr5_fixtures.py` (o bloco `presentation` que um relatório real tem).

## 5. Resultados

- Bateria completa: **398 testes OK** em Python 3.11 e 3.13 (343 anteriores + 55 novos em `test_telegram_presentation.py`).
- Mutação (`mutation_check.py`): **37/37** mutações apanhadas (`mutation_check.json`).
- Ensaio de corte (`scripts/rehearse_pr5_cutover.py`, `cutover-rehearsal.json`) sobre `main` `60ba009`: `ok: true` —
  migração `NOTIFY_FORMAT_MIGRATED` e depois `UNCHANGED` byte a byte; zero mensagens em duas execuções e na pré-visualização;
  `sent_total` 312 e `watch_sent` 117 iguais; três marcas iguais; `statuses == status_list`; registo vazio; `post_cutover_check`
  (com as 5 verificações PR 5) aprovado; só `notify_state.json` e `report.json` mudam.
- Reconstituição das últimas 24 h (`replay-24h.md`): 34 mensagens contra 37 reais; nenhum aviso real perdido; os 24 jogos no
  mesmo commit; diferenças explicadas uma a uma (todas antes do corte PR 3, regras PR 2 em produção).
- Pré-visualização: `preview-60ba009.txt` (zero avisos, estado intacto) e `preview-60ba009-formats.txt` (resumo das 21:05 e
  mudanças reais em formato PR 5).

## 6. Riscos residuais

Ver a tabela do runbook. Os principais: "no máximo uma vez" perde raramente um aviso (falha ou incerteza, contadas no
resumo); a persistência real é o commit do workflow (um runner que morra antes de qualquer commit perde a intenção — não
evitável sem mexer em workflows, fora de âmbito) [superado na 3.ª ronda: a barreira publica a intenção antes do envio; fica
só o risco de um push aceite cuja resposta se perde, que pode perder um aviso, nunca duplicá-lo]; nomes de equipas com
palavras de afirmação bloqueiam o aviso desse jogo (fail-closed); o aviso de recolha falhada (`--failure`) mantém o
comportamento anterior e fica fora da garantia "no máximo uma vez".
