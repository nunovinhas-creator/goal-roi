# PR 1 — Migração silenciosa e integração futura

**Preparação apenas. Nada foi integrado ou executado em produção.** A PR #24 continua Draft. A revisão #5907607348 aprovou tecnicamente `5078921e9cb8b3d88524fe1a5a1913326a07bda7` com reserva sobre avisos retroativos; este aditamento resolve essa reserva para nova revisão independente.

## Mecanismo e persistência

`notify.migrate_status_reference()` estabelece uma única referência a partir de `brain.status_list` apenas quando `brain.evidence_scope` é `eligible_bets_only`. A ausência de uma hipótese na lista significa que regressou a observação; estados explícitos de observação também são aceites. Um relatório sem scope válido ou sem lista não migra nem substitui a referência atual.

O marcador `migrations.eligible_statuses_v1` guarda a data, a data do relatório, a referência anterior completa e a nova referência inicial. `statuses` passa a ser uma cópia independente da lista elegível. Uma segunda execução não redefine essa referência: alterações reais posteriores continuam a ser comparadas e notificadas. A referência inicial fica arquivada e não acompanha as alterações futuras.

A função de migração não envia mensagens. Não altera `watch_sent`, `sent_total`, `hello`, `summary_day`, `last_run_utc`, `regime`, `operational_regime` ou campos de configuração. O modo normal continua a processar sinais, resumos e regimes como antes; a migração não é um interruptor geral de silêncio. Assim, pode haver um sinal legítimo ou um resumo diário no mesmo ciclo, mas não 25 transições históricas de classificação.

`run()` persiste a migração antes de qualquer chamada ao emissor. As gravações usam ficheiro temporário, flush/fsync e substituição atómica no mesmo diretório. Se a gravação falhar, nenhum envio começa. Se um envio posterior falhar, a referência migrada já está persistida e o histórico de entrega anterior não foi consumido. Um estado existente com JSON inválido causa erro antes de enviar, em vez de reiniciar o histórico vazio.

Mantém-se o pressuposto existente de **um único escritor**: a substituição atómica não é um bloqueio entre processos. Continua também a limitação preexistente de poder repetir mensagens legítimas já enviadas se uma execução falhar antes de persistir o lote final; esta PR não redesenha a entrega Telegram.

## Comparação e testes

Antes da alteração: **103 testes passaram**. Depois: **112 testes passaram**, todos via `python scripts/test_offline.py`, incluindo a regressão JavaScript existente.

| Cenário | Notificador aprovado anterior | Com migração |
|---|---:|---:|
| 33 promissoras → 8; 25 regressam a observação | 25 avisos retroativos | 0 |
| Repetir o relatório após migração | — | 0 |
| Quatro mudanças genuínas posteriores | — | 4 |
| Repetir essas quatro mudanças | — | 0 |

Demonstração reproduzível: `python scripts/audit_notify_migration.py`. O JSON `pr1-notify-migration.json` regista os resultados, preservação dos campos e hashes de todos os dados. Só usa funções locais; nunca chama `send()`.

As nove regressões cobrem: 33→8 e idempotência; observação explícita/abandono no corte; promoção/abandono/observação/hipótese nova posteriores; sinais e resumo diário; scope ausente ou antigo; persistência em disco sem mudar contadores; falha do emissor e repetição; falha de substituição atómica; estado corrompido.

Não foram modificados cérebro, LLM, critérios estatísticos ou dados históricos. Os resultados científicos já documentados (8 promissoras; ROI walk-forward −0,23%) não foram recalculados nem reinterpretados nesta alteração exclusivamente operacional. Não há nova evidência de capacidade preditiva.

## Procedimento futuro — apenas após revisão e autorização expressa

1. Suspender temporariamente os escritores de produção e a cadeia de recolha/watch/reagendamento. Garantir que não resta execução com checkout antigo e impedir novos arranques durante o corte. Isto é uma ação futura do operador, não realizada nesta tarefa. Não mudar secrets.
2. Guardar uma cópia integral do `notify_state.json`, do relatório e do commit de produção fora dos ficheiros controlados pela recolha. Confirmar JSON válido, `watch_sent`, `sent_total` e última execução. Registar o SHA revisto/aprovado.
3. Resolver eventuais conflitos com `main` sem substituir dados atuais pelos snapshots antigos da branch. Se a integração exigir mudanças de código, voltar a testar e rever o resultado. Só então integrar a revisão autorizada; esta tarefa não autoriza merge ou publicação Pages.
4. Num checkout da revisão integrada e com os dados atuais, gerar um relatório local com `report.build(register=False)`, sem CSV e sem executar coletores, LLM ou notificações. Guardá-lo em `data/report.json`. Confirmar `brain.evidence_scope=eligible_bets_only` e `brain.status_list` válido. Os números atuais podem diferir do snapshot histórico 33→8.
5. Antes de qualquer envio, executar exclusivamente:

   ```bash
   python -m scangoal.notify --migrate-statuses
   ```

   Este modo não lê credenciais nem invoca emissor/coletores. Exige um estado existente válido; não deve ser usado para criar um histórico vazio. Esperado: `NOTIFY_STATUS_MIGRATED` (ou `NOTIFY_STATUS_UNCHANGED` se já preparado). Um erro termina com código 1: não retomar notificações.
6. Verificar que `statuses` coincide com o relatório e que `previous_statuses` preserva a referência antiga. Confirmar que histórico, deduplicação e contadores não mudaram. Repetir o comando: deve mostrar `NOTIFY_STATUS_UNCHANGED`, mantendo o ficheiro byte a byte igual. Persistir o **estado atualizado** no mecanismo de armazenamento usado pelo próximo checkout, antes de retomar a cadeia; não basta deixá-lo num checkout descartável.
7. Retomar uma única cadeia usando o código e o estado atualizados. O primeiro ciclo normal pode enviar sinais novos/resumo legítimos; não deve enviar transições históricas. O caminho automático em `run()` é uma proteção adicional, mas a preparação explícita permite inspecionar o corte antes dos envios.

A primeira referência define a fronteira da migração. Mudanças já presentes nesse relatório não podem ser distinguidas de reclassificações históricas e ficam silenciosas por definição. A pausa dos escritores torna esse corte explícito. Mudanças posteriores à referência são notificadas normalmente.

## Reversão futura

- Parar primeiro todos os escritores e envios; copiar o estado **mais recente** antes de qualquer reversão. Reverter o código por um commit específico, preservando os dados de produção. Não fazer reset de `data/` nem restaurar todo o `notify_state.json` antigo: isso perderia deduplicação e poderia reenviar mensagens.
- Se apenas se reverter este aditamento para `5078921`, manter `statuses` elegível atual, `watch_sent`, contadores, resumo e marcador. A migração já feita não precisa de ser desfeita; o código anterior ignora o marcador.
- Se se reverter toda a PR 1 para código exploratório, gerar primeiro o relatório compatível com esse código, sem emissores. Arquivar os estados atuais, estabelecer silenciosamente `statuses` a partir desse relatório e arquivar/remover o **marcador ativo** `eligible_statuses_v1`, preservando o seu conteúdo num registo de reversão. Preservar todos os restantes campos de entrega. Isto permite uma nova migração silenciosa quando se voltar a instalar a PR 1. Não usar a referência antiga arquivada como se refletisse acontecimentos ocorridos entretanto.
- Persistir a referência e só depois retomar um escritor. Um relatório antigo recebido pelo código novo não migra nem sobrescreve a referência elegível; essa proteção não existe no código antigo revertido, daí ser indispensável preparar a referência antes de reativar envios.

A reversão também requer autorização explícita e verificação pelo operador. Nenhuma ação deste procedimento foi executada em produção.

## Aditamento (15/11/2026) — automatização dos passos 4–6

Os passos 4–6 acima foram automatizados, sem alterar o mecanismo revisto, em `scripts/pr1_cutover.py`: guardas contra snapshots desatualizados e marcador já presente, rede bloqueada, migração repetida com verificação byte a byte, simulação sem emissor e restauro automático em falha. O ensaio sobre os dados reais (`audits/pr1-cutover-rehearsal.json`), o risco medido de uma execução antiga depois do corte e o procedimento detalhado estão em `audits/pr1-cutover-runbook.md`. Nada disto foi executado em produção.
