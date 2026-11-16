# PR31 — fecho mínimo preparatório (2026-11-16)

Base auditada: `8eb6a9e8b19d98a710b942396965e5ef4a2776cf`.
Decisão: APROVAR COM CONDIÇÕES a preparação; execução real BLOQUEADA.
Não houve consultas desportivas, alteração de secrets, instalação, merge,
notificação Telegram, alteração científica ou criação de workflows.
PR29/30 não são modificadas. Este relatório complementa, não substitui, a auditoria anterior.

## Implementação

- `scangoal/recovery_remote.py`: backend Git remoto completo para o protocolo
  existente (head/read/propose/CAS). Cache bare isolada, fetch obrigatório, âncora
  fixa recebida de configuração auditada, referência única `refs/heads/bsd-trial-journal`.
  Mesmo TRIAL_ID `goal-roi-bsd-20261113-15-v1`, cap 5 e prior_debits do genesis.
  Não cria genesis/referência. Não usa force-push, artifacts, cache como autoridade,
  nem fallback local. Candidato obrigatoriamente filho direto do head esperado;
  push normal usa receive-pack e a transação atómica Git. Uma divergência recusa
  a escrita. Depois do push confirma novamente o head remoto.
- Timeout de escrita: nova leitura confirma ou declara incerteza; mesmo que encontre
  o candidato persistido, NÃO concede autorização de envio. O slot fica queimado,
  nunca há retry de push/pedido, nem reembolso. Perda do remoto não usa FETCH_HEAD antigo.
- `GitHubAttempts`: HTTPS autenticado exclusivamente para api.github.com, endpoint
  `/repos/nunovinhas-creator/goal-roi/actions/runs/{id}/attempts/{attempt}`.
  Sem redirects/retries; credential só em header, erros genéricos. Valida id,
  run_attempt, repositórios de origem/destino, workflow collect.yml, main, estado
  completed e conclusion. Persiste campos normalizados de prova; não grava token
  ou corpo arbitrário. `abandon` obtém a identidade da reserva, não de JSON do utilizador.
  A função de baixo nível InlineSlots.abandon continua uma primitiva de replay/teste;
  um futuro executor só deve chamá-la através deste leitor autenticado.
- `scangoal/recovery_tail.py`: PreparedTail.attach liga o run do patch separado ao
  NormalQuotaTracker, InlineSlots, PreparedBSD e motor original. Zero operações Git
  antes da recolha normal. Callback só depois do estado normal guardado. Falhas são
  contidas; reutilizar um tracker fechado não interrompe recolha normal.
  Receipt começa com débitos zero: desconta conservadoramente débitos anteriores
  novamente, podendo exigir até mais cinco pedidos de margem; nunca sobrestima saldo.
- Só uma página por callback/run; datas/IDs limitados, paginação reaproveitada,
  respostas incertas nunca reenviadas, resultados apenas fora do checkout em
  prepared-fixture-trial. Só OfflineWire exato é aceite; sem CLI live, sem HTTP BSD.
- Retoma: estado do motor é reconstruído das respostas duráveis, com timestamp UTC
  ORIGINAL, fetch sem live=True e o motor aprovado intacto. Todas as datas necessárias
  à identidade permanecem na reconciliação. Uma página abandonada não fornece
  eventos nem prova de completude. Evidência permanece synthetic_wire.

## Testado efetivamente

Servidor Git bare separado e dois clientes/cache independentes via `file://`,
com processos reais git-fetch/git-push/git-receive-pack, não mock de CAS. Último slot:
exatamente um vencedor. Servidor de ensaio configurado com receive.denyDeletes e
receive.denyNonFastForwards; rejeição de apagar/recuar verificada. Não é teste de
hosting GitHub, TLS, autenticação Git remota ou ruleset de produção. Nenhuma referência
GitHub de orçamento foi criada. A proteção administrativa ainda não está comprovada.

13 testes novos em test_recovery_tail.py, além dos 20 testes inline anteriores:
competição pelo último slot; timeout de escrita com/sem confirmação; perda de remoto;
falha entre resposta e persistência; abandono autenticado simulado e provas falsificadas;
retoma noutro processo com resposta NÃO vazia e decisão aceite; quarentena estável;
callback depois do save normal; ausência de margem; rejeição de transporte arbitrário.
Os testes anteriores cobrem falha após reserva antes do envio, expiração durante CAS,
sexta tentativa, erro429 e ausência de reembolso. Não se contam testes importados duas vezes.

Na retoma, todos os ficheiros raw/meta/engine-state são comparados byte a byte,
assim como as decisões e o número de reservas. O teste isolado tinha inicialmente
reutilizado o executor fechado e detetou que isso podia falhar a recolha; corrigido
para executar só a recolha normal nesse caso. Logs finais acompanham este relatório.
Não existe prova de recuperação real ou liquidação.

## Evidência operacional lida, sem ações no coletor

Leitura autenticada via conector GitHub da tentativa real 1 da run 43921443044:
collect.yml, main, repository nunovinhas-creator/goal-roi, completed/success.
O novo leitor Python usa o mesmo endpoint/contrato, mas foi testado com HTTPS simulado;
a credencial de runtime não foi lida nem exercitada. Nenhuma reserva real foi abandonada.

Job 133489523878: 17:44:05Z–17:52:56Z (531s). BSD às 17:44:14Z (0s registados),
report 16s, fast reads 497s, commits/encadeamento adicionais. Restavam 891s no ponto BSD;
os passos posteriores consumiram 522s nesta amostra. Não prova um limite máximo,
nem que esta execução fez uma consulta BSD normal (um skip também conclui com sucesso).
Fonte: API `/actions/runs/43921443044/jobs`, sem leitura de logs/segredos.

Limites configurados: leitor até 220+10s; fast reads 540+10s; job900s.
Só estes dois limites já somam780s, deixando120s para setup, HL, BSD normal, LLM,
report, commits, encadeamento e qualquer ensaio. Nem todos os restantes têm limite
end-to-end demonstrado. Executor exige deadline do início REAL do job e bound
posterior >=550s mais100s de margem de ensaio. Estes números são um gate preparatório,
NÃO uma prova de que operações Git/replay cabem em100s. Sem um bound revisto e
instrumentação do deadline, omitir ambos => skip. Rever deadline novamente no ponto
imediatamente anterior ao envio e limitar o tempo global do tail é requisito de ativação.
Não reduzir fast reads ou trabalho normal para obter margem.

Rulesets do repositório: API devolveu []. Leitura de proteção clássica da referência
bsd-trial-journal devolveu403 Resource not accessible by integration. Não inferimos
inexistência nem existência de proteção. Não há ferramenta/permissão administrativa
usada para instalar rulesets. Este bloqueio é concreto.

Consumidores encontrados no código revisto:
1. results_bsd.request_json, pelo único passo BSD de collect.yml: chave BSD_API_KEY.
   Inclui schedule/dispatch e encadeamento, mesmo scangoal-write.
2. recovery.LiveFetch/CLI genérico: também BSD_API_KEY, potencial consumidor manual
   fora desse grupo. NÃO é invocado pelo executor preparado.
3. PreparedBSD e testes: OfflineWire, zero conta/API real.
Nenhuma prova de que outras máquinas, repositórios ou clientes não partilham a conta.
Nomes iguais de secrets não provam identidade da conta. Exige inventário/atestado de
controlo da conta, sem revelar a chave; não basta listar runs ou observar uma fila vazia.

## P1 que ainda bloqueiam o primeiro pedido

1. **Hosting/âncora/proteção**: provisionar UMA referência e âncora protegidas contra
   eliminação/recriação/recuo e escrita por executores não autorizados; confirmar
   permissões de runtime, testar CAS/negações no hosting de ensaio equivalente.
   Reconciliar débitos de PR29/30/31 e fixar SHA/genesis, não aceitar número manual
   como prova de zero nem permitir novo genesis com mais cinco créditos.
2. **Conta e saldo**: comprovar exclusividade de TODOS os consumidores da conta;
   obter headers reais da última resposta BSD normal, válidos após persistência,
   >=100+orçamento restante (ou reserva maior). Sem consulta extra de saldo.
3. **Tempo e snapshot**: prazo de job autenticado, limites globais de tail/Git e
   bound conservador dos passos posteriores. Nova revalidação dos dez IDs sobre
   snapshot pós-normal com base SHA e hashes; não usar os dados antigos da auditoria
   como snapshot atual nem permitir novos IDs/datas. PreparedTail recebe seleção
   validada mas não prova sozinho a origem/frescura dessa seleção.
4. **Ativação auditada**: ponte HTTP BSD continua deliberadamente inexistente;
   instalar só mediante nova autorização a ligação mínima do patch ao executor,
   incluindo gate de conta, âncora e snapshot, namespace real separado das fixtures,
   prova de run terminal pelo leitor autenticado e prazo antes/depois da reserva.
   Um estado terminal do runner não garante por si só que um servidor BSD remoto
   deixou de processar um pedido. Se isso continuar incerto, não prosseguir o ensaio.

Sem P0 identificado nesta preparação inativa. P2: desconto conservador adicional
pode causar skips; parser de headers restrito; uma reserva incerta perde capacidade.
Não classificamos P1 como resolvido por um teste simulado ter passado.

## Caminho mínimo restante / autorização futura

1. Aprovar/provisionar e comprovar apenas a referência de orçamento, com reconciliação
   e proteção. Sem novo workflow, serviço pago ou lease que bloqueie o coletor.
2. Confirmar conta exclusiva, snapshot atualizado e margem. Se não demonstráveis,
   o callback fica ausente/skip e a recolha mantém o comportamento atual.
3. Auditar a pequena ligação HTTP e instalação do patch; pedir autorização expressa
   para esse SHA e os até cinco slots do MESMO ensaio, não cinco créditos adicionais.
4. Na execução elegível: recolha normal/save → último header → snapshot → gates →
   reserva confirmada → UMA página BSD → bytes/hash/UTC duráveis → replay/staging.
   Interromper perante erro/incerteza; abandono autenticado mantém débito e nunca
   repete a consulta. Remover/desativar só o callback permite reversão sem tocar no
   coletor, no histórico nem na referência de auditoria (que deve ser preservada).

Tudo preparado usa Python stdlib/Git e recursos existentes. Não foram criados serviços
ou contas pagos; proteção disponível no plano/permissões do hosting exige verificação.

Fontes oficiais consultadas:
- https://git-scm.com/docs/git-push (fast-forward, receive-pack e proteção servidor)
- https://docs.github.com/en/enterprise-cloud@latest/rest/actions/workflow-runs#get-a-workflow-run-attempt

## Continuação sobre `5888959` (2026-11-16)

Auditado `5888959`; todo o trabalho herdado mantém-se (backend Git, cinco slots,
abandono autenticado, PreparedTail, replay cronológico, testes de concorrência).
Correções mínimas, só defeitos reais:

- **P1-E — orçamento do tail imposto, não estimado.** Antes: margem de 100 s
  verificada uma vez, seguida de até ~13 operações Git (10 s cada) + BSD 20 s, o que
  podia exceder o `timeout-minutes` e cancelar "Start the next collection" (quebra da
  cadeia). Agora: `TAIL_BUDGET_S=150` exigidos além do bound posterior (>=550 s);
  cada operação Git remota é cortada ao fim do tail (`not_after`); nenhuma reserva
  autoriza envio depois de `tail_end-80 s` (BSD 20 s + 4 ops Git de conclusão +
  replay + persistência). Reserva confirmada tarde = slot queimado, sem envio.
- **P1-E — prazo real do job.** `job_deadline_from_env()` lê
  `GOAL_ROI_JOB_STARTED_UTC` (início do job +900 s −30 s de set-up); ausente/inválido
  => skip. Requer a linha proposta em `collect-yml-proposal.patch` (NÃO instalada).
- **P1-A — âncora fixada em código revisto.** O remoto GitHub exige
  `anchor == PROVISIONED_ANCHOR` (hoje `None` => bloqueado). Nenhum input de runtime
  escolhe a âncora; recriar genesis não dá novos slots. `file://` é apenas o servidor
  de teste e não pode servir de journal de produção numa futura ativação.
- **P1-A — credencial Git do runtime.** A cache bare isolada não herda a credencial
  do `actions/checkout`. O token passa por `GIT_CONFIG_*` no ambiente (nunca argv
  nem config gravada); remoto GitHub sem token => bloqueado.
- **P1-C — concorrência visível nos cabeçalhos normais.** Entre duas respostas
  normais da mesma execução, `r` tem de descer exatamente 1 por pedido e no mesmo dia
  UTC; qualquer outro valor => sem recibo => skip. Sem pedidos extra. Deteta só
  consumo durante o passo BSD; NÃO substitui a prova de exclusividade da conta.

6 testes novos (582 total). Nenhum pedido BSD/Highlightly, nenhuma ref criada,
workflow, secrets, Telegram e certificação intactos.

### Ainda pendentes (intervenção humana)

1. Criar `refs/heads/bsd-trial-journal` com genesis revisto (prior_debits reconciliado
   de PR29/30/31) e ruleset: bloquear eliminação e non-fast-forward; restringir
   escrita. Leitura da proteção devolveu 403 a esta integração: requer admin.
   Depois, fixar o SHA em `PROVISIONED_ANCHOR` num commit revisto.
2. Atestado de uso exclusivo da conta BSD (todos os consumidores da chave).
3. Bound posterior revisto: LLM, report, notify e commits não têm timeout de passo;
   só fast reads (550 s) é limitado. Sem bound revisto, `downstream_bound` fica ausente
   => skip. Não reduzir passos normais para ganhar margem.
4. Instalar `collect-yml-proposal.patch` e `collector-tail-proposal.patch`.
5. Ponte HTTP BSD real + namespace real separado de `synthetic_wire`: nova auditoria.

## Revisão para autorização final (sobre `be199e4`)

- O limite de 150 s passa a ser global: watchdog `SIGALRM` à volta de todo o tail
  (Git local, replay, persistência e conclusão), além dos cortes já existentes.
- Teste de neutralidade: com os patches instalados e sem callback, o coletor produz
  estado e ficheiro byte-a-byte iguais aos atuais.
- 4 testes novos (586 no total); os logs `full-tests.log` e `specific-tests.log`
  foram regenerados sobre este código.
- Procedimento administrativo exato, declaração de exclusividade, margem empírica,
  riscos dos patches e procedimento da primeira consulta: `AUTHORIZATION.md`.
