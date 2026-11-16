# PR30 — Infraestrutura manual e isolada para ensaio BSD

Data: 16/11/2026. Âmbito: preparação e validação offline; **nenhum workflow novo foi instalado em main ou executado; nenhum pedido desportivo foi feito**.

## Parecer e limite da entrega

**APROVAR COM CONDIÇÕES a preparação em Draft. REJEITAR a ativação real nesta revisão.**

Este é um workflow de **pré-verificação bloqueante**, com um harness de simulação offline do motor, não um ensaio real pronto a desbloquear por checkbox. O transporte real está deliberadamente ausente. Não existe `--live` nem override por inputs. Resolver as condições abaixo exigirá revisão e autorização específicas; a autorização anterior de cinco pedidos não autoriza instalar workflows, modificar o coletor ou realizar uma consulta adicional de quota.

A auditoria técnica foi realizada nesta intervenção sobre o código e evidências locais. Não se apresenta como aprovação obtida de um segundo revisor. A revisão independente externa e a autorização de ativação continuam pendentes.

## Base e ficheiros

Branch separada: `codex/recovery-trial-infrastructure`, descendente exata de `247deee40e5e6ebdb107808b66adb96622074238`. A PR é empilhada sobre `codex/pr6-result-recovery`, para mostrar exclusivamente a infraestrutura nova e conservar o motor auditado intacto. Isto não modifica a branch ou a PR29. **Não fazer merge da PR30 na PR29.** Um eventual destino main terá de ser revisto quando houver autorização e o motor estiver disponível nesse destino.

Adições exclusivamente:

- `.github/workflows/recovery-trial.yml`: YAML em sintaxe JSON (válida em YAML), analisável nos testes com a biblioteca padrão; só `workflow_dispatch`.
- `scangoal/recovery_trial.py`: pré-verificação, cópia isolada, seleção fixa e simulação com transporte de fixtures exclusivamente.
- `tests/test_recovery_trial.py`: 25 testes novos de segurança.
- `audits/pr30-trial-infrastructure/README.md`: esta auditoria e runbook.
- `audits/pr30-trial-infrastructure/full-tests.log`: regressão offline completa.
- `audits/pr30-trial-infrastructure/safety-tests.log`: testes novos isolados.
- `audits/pr30-trial-infrastructure/preflight.json`: execução local bloqueada, sem rede.
- `audits/pr30-trial-infrastructure/integrity.json`: hashes e comparação byte a byte de todos os ficheiros originais do SHA auditado.

## Auditoria do coletor e quotas existentes

`collect.yml` injeta `secrets.BSD_API_KEY` apenas no ambiente do passo BSD. `results_bsd.py` envia a chave no cabeçalho Authorization, não em argumentos. O transporte existente só devolve o JSON: não conserva cabeçalhos RateLimit. Não foram lidos valores de secrets.

O coletor tem agendamento aos minutos 3,13,23,33,43,53 e `workflow_dispatch`. A última etapa encadeia outra execução por `gh workflow run collect.yml --ref main`. O grupo global `scangoal-write`, com `cancel-in-progress: false`, protege a escrita dos produtores que o usam. **Não garante uma fila sem perda:** uma execução pendente pode substituir outra pendente. Partilhar esse grupo com o ensaio pode cancelar uma recolha pendente ou fazer o ensaio ser substituído, e ocupar esse grupo atrasaria o coletor inteiro. O fluxo manual `force_results` também pode retirar o intervalo mínimo. Um snapshot sem runs ativas não é uma exclusão durante os próximos pedidos.

Limites atuais BSD: `MAX_REQUESTS=12` por execução, intervalo mínimo normal de 50 minutos, janela histórica de 3 dias, página de 200. O estado antigo regista a última execução e o seu número de pedidos; não é um contador diário global nem um saldo da conta. O motor PR29 reserva antes da consulta, faz checkpoint atómico e tem limite diário por fornecedor, reserva e controlo de paginação. Estes contadores só protegem o seu próprio estado: não coordenam outros consumidores. Além disso, o limite diário isolado reinicia no dia UTC seguinte, razão para o harness testar também um débito vitalício por tentativa para este ensaio único.

O workflow `tests.yml` existente responde a PR e push em main; não tem dispatch. Não foi modificado. A PR29 permanece no SHA auditado cujo marcador `[skip ci]` omitiu CI. O novo commit não utiliza esse marcador; o workflow normal de testes pode ser acionado pela nova PR, sem executar o workflow de ensaio.

## Confirmação de saldo: evidência oficial versus conta não observada

Fontes oficiais consultadas em 16/11/2026, **só páginas de documentação, nunca endpoints da API**:

1. https://sports.bzzoiro.com/pricing/ — plano gratuito Football de 7 500 pedidos/dia, reset UTC; painel apresenta os valores por desporto.
2. https://sports.bzzoiro.com/docs/conventions/ — cabeçalhos `RateLimit-Policy` e `RateLimit`. O âmbito football, `r` restante e `t` até reset são documentados. Não há cabeçalhos legados X-RateLimit. A documentação recomenda parser de HTTP Structured Fields; não foi introduzida uma regex como parser de produção.
3. https://sports.bzzoiro.com/docs/ — autenticação Token, paginação limit/offset e status.
4. https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency — substituição de pending e cancel-in-progress.
5. https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow — workflow_dispatch e presença na branch predefinida, acesso de escrita para dispatch manual.

Não foi encontrado nestas páginas um endpoint de quota documentado como gratuito/isento. Isto não prova que tal endpoint não exista. Não o sondar. Os cabeçalhos de uma resposta de dados exigem o pedido respetivo; uma consulta nova para obter saldo é um custo separado, **não incluído implicitamente nos cinco pedidos**. Reutilizar cabeçalhos já obtidos por um consumidor autorizado não faz novo pedido, mas o transporte atual descarta-os.

**Método proposto sem novo pedido API:** o titular obtém no painel autenticado o saldo football atual, a hora UTC, a identidade não secreta da conta e a evidência original verificável, sem copiar token/cookies. A documentação confirma que o painel mostra saldo; a conta do utilizador e o custo interno da página não foram observados. Por isso, esta revisão **não aceita automaticamente uma captura nem um inteiro como saldo comprovado**. Deve ser confirmado que o painel não efetua uma consulta desportiva faturável, verificada a correspondência com a conta da chave existente e contabilizado todo o consumo posterior. Não é possível inferir isso da quota nominal ou dos quatro pedidos da última recolha.

Antes de executar, saldo utilizável >= reserva recalculada + orçamento restante. Para começar com os cinco pedidos: >=105 e reserva >=100. Outros consumidores podem exigir reserva superior. Prova antiga, de outro dia UTC, de outro âmbito/conta, sem totalização do consumo intermédio, ou consulta paga, bloqueia. Cabeçalhos ausentes não provam saldo gratuito disponível; o objetivo é não contratar um plano pago.

## Coordenação conservadora, sem efeito no coletor

O novo workflow **não usa scangoal-write nem qualquer grupo partilhado**, não lista/cancela/desativa/dispara runs e não altera o coletor. Tem apenas `contents: read`. Não possui mecanismo que possa bloquear, substituir ou cancelar uma run do coletor. Sem transporte real, mesmo dois preflights concorrentes não consomem quota nem escrevem em produção.

**Alternativa conservadora atual: falhar antes do primeiro pedido.** Não se certifica uma janela só por listar runs, por esperar 50 minutos ou por fornecer `collector_idle=true`. Para uma ativação futura é necessário provar uma janela exclusiva para toda a conta BSD ou aprovar um mecanismo cooperativo atómico em todos os consumidores. Um lock só no harness não basta. Um eventual lock apenas no passo BSD do coletor é uma possibilidade a estudar sob autorização de alteração da produção; não foi implementado nem considerado aprovado. Se puder atrasar/impedir consultas do coletor, não satisfaz por si o requisito e não desbloqueia o ensaio. Consumidores externos desconhecidos mantêm o bloqueio.

## Isolamento, orçamento e proveniência

O CLI faz apenas pré-verificação. Compara byte a byte todos os módulos originais de scangoal com os objetos Git do SHA auditado; alterações posteriores bloqueiam. Exige histórico Git disponível (`fetch-depth: 0`). A execução local comprovou cópia dos dados para fora do checkout, seleção dos dez IDs e nenhuma exclusão no snapshot auditado. Seleciona apenas apostas ainda sem FT e sem liquidação no snapshot. Um futuro ensaio requer uma cópia fresca e imutável de main e novo cruzamento desses dez IDs; o snapshot congelado PR29 não prova que continuem pendentes em produção no dia do ensaio.

Diretórios de saída dentro ou acima do checkout são rejeitados. Os inputs são copiados e identificados por hashes; symlinks são rejeitados. A preparação em diretório existente exige a mesma fotografia e verifica a cópia; não recopia silenciosamente dados novos sobre estado antigo. O report publicado inclui apenas metadados sanitizados, sem mensagens brutas, chave ou valor da conta.

O harness simulado recebe apenas um objeto exato `FixtureTransport` com dados, nunca callbacks de rede, e não tem transporte real. Aplica os dez IDs do plano, as três datas e a seleção do motor. Se um alvo precisar de um dia adjacente fora de 13–15/11, exclui o alvo; não reduz a janela para fingir unicidade.

A simulação usa `run_batch`, `max_requests=1` por iteração, max_passes=1, HL=0, BSD=5, limit=200, offset inicial zero. Cada tentativa é reservada e persistida antes do transporte. O comprimento vitalício de attempts impede mais de cinco, incluindo falhas, reinício e mudança de dia UTC. Flock exclusivo impede dois processos locais no mesmo diretório. Uma resposta inválida, 429 ou erro termina todo o ensaio simulado e deixa halted persistente. Interrupção abrupta preserva a reserva; no máximo pode perder a resposta, nunca devolver crédito. Alteração de alvos/aliases/evidência histórica bloqueia a retoma.

Os ficheiros `fixture-trial/response-NN.json` guardam parâmetros com status=finished, hash de bytes JSON canónicos, hora UTC e payload. **Não são bytes HTTP originais nem resultados reais**. O estado conserva decisões, observações e quarentena do motor. A simulação tem relógio explícito de fixture; o futuro transporte real terá de conservar bytes originais seguros, hash, parâmetros e hora efetiva pós-leitura, sem relógio fixo ou chave no artifact. Não foi demonstrada essa captura real nesta revisão.

**Persistência entre runners Actions não está resolvida:** filesystem temporário, caches e artifacts opcionais/deletáveis não fornecem um débito global inviolável. Não foram usados como falsa garantia de orçamento. O workflow não aceita retoma live; reporta `cross_run_budget_not_proven`. O limite entre processos/UTC foi comprovado só para o mesmo estado local preservado. Apagar esse estado e iniciar noutro diretório não é um mecanismo autorizado de retoma. Um futuro registo durável único, auditável e com criação/retoma atómicas terá de ser aprovado antes de habilitar rede.

## Segurança do workflow e ativação manual

A única entrada é `workflow_dispatch`, sem inputs que desbloqueiem rede. As ações estão fixadas por SHA; checkout não persiste credenciais. A chave existente é injetada em env só no passo de pré-verificação e apenas se testa a sua presença; não se revela nem usa para API nesta revisão. O CLI não imprime exceções de fornecedores nem escreve secrets. Não há pip install, serviços adicionais, notificações, imports de notify, commit ou push. Só `preflight.json` é selecionado para artifact; não se envia a cópia de mensagens, o ambiente ou um diretório inteiro. Artifacts expiram em 14 dias, sem serem usados como ledger de quota.

Não instalar nem disparar este workflow nesta tarefa. Uma PR em Draft não é, por si só, uma barreira de segurança. A instalação na branch predefinida e dispatch futuro requerem autorização do utilizador e permissões GitHub adequadas; a chave terá de estar acessível segundo a política do repositório/ambiente, sem mostrar o seu valor. Só código e ref revistos podem receber o secret. Nenhum environment ou secret novo foi criado.

Procedimento futuro antes de qualquer live:

1. Revisão independente dos ficheiros e SHA final; PR29 sem alteração, Draft e sem merge.
2. Provar método não consumidor para saldo e obter prova atual da conta football >= reserva+5, com todos os consumidores contabilizados.
3. Provar exclusão durante a janela inteira sem interromper, substituir ou atrasar injustificadamente o coletor; incerteza bloqueia.
4. Aprovar registo durável do débito único entre runs e procedimento de retoma; perda do estado bloqueia.
5. Rever/autorizar implementação do transporte real estritamente BSD sem redirects/retries, captura segura, teto global e validade de todas as provas a cada pedido. Esta implementação ainda não existe.
6. Revalidar snapshot fresco de main e dez IDs ainda pendentes, código exato e staging externo; não executar CLI genérico de recuperação.
7. Autorizar separadamente a instalação manual e a execução. Confirmar que a inclusão em main foi aprovada, que o executor tem write para dispatch e que a ref é revista. Nenhum evento automático deve executar o ensaio.
8. Depois de um ensaio autorizado, parar e entregar respostas reais e relatório para auditoria, sem integrar resultados nem autorizar outros pedidos.

## Interrupção e reversão

Atualmente um preflight termina deliberadamente com exit 2 e lista os bloqueios; este resultado não é CI falhado do código, é recusa de execução real. O workflow de testes é separado. Cancelar só uma eventual run do próprio preflight não afeta o coletor porque não partilha concorrência. Esta tarefa não disparou qualquer run.

Para a simulação, preservar state.json/lock/evidências e não reiniciar contadores. 429 ou inválido deixa halted. Não apagar nem editar estado para obter mais chamadas. Não há publicação, integração nem dados a reverter. A reversão da proposta é fechar a PR30, preservando evidência; nenhuma alteração à produção precisa de rollback. Não apagar runs, históricos ou artifacts de auditoria como procedimento normal.

## Evidência e testes

- `python scripts/test_offline.py`: **511 testes, OK**, 26.892 s; sockets do processo principal proibidos. Inclui os 486 testes anteriores, 53 do motor PR29 e 25 novos. Não somar os 53 outra vez.
- `python -m unittest discover -s tests -p test_recovery_trial.py -v`: **25 testes, OK**.
- CLI local com sockets bloqueados: exit 2 esperado, **10 IDs selecionados**, 0 pedidos BSD, 0 HL, 0 integrações, originals_unchanged=true.
- Todas as respostas usadas nos testes são fixtures. Mensagens como NOTIFY_OK/HL_OK/LLM_OK no log completo são dos testes antigos com transportes simulados, não comunicações reais.
- O teste T1 live / T2 2–1 / T3 3–1 comprova primeira observação final em T2, preservação das três observações e quarentena do resultado corrigido.
- Testes negativos de chave, saldo desconhecido/<105, coletor ativo/incerto, transporte arbitrário, saída em produção, alvo/data fora da autorização e alteração de bindings comprovam zero chamadas simuladas adicionais.
- Testes de falha provam reserva pré-transporte, ausência de texto sensível de exceção, paragem de todos os dias após 429/inválido, retoma e crash sem restituição. Cinco páginas simuladas esgotam orçamento; processo novo no dia UTC seguinte não consegue sexta chamada.
- A primeira execução integrada detetou uma falha no próprio teste de ausência de import de notify: a suíte antiga já tinha importado notify. Foi corrigido para verificar o harness num interpretador limpo. Não houve regressão do produto; o log entregue é a execução final aprovada.
- `integrity.json`: todos os ficheiros originais do commit auditado permanecem iguais, incluindo produção, Telegram, certificação, resultados históricos e workflows existentes. Apenas ficheiros novos. O motor não foi modificado.

## Riscos remanescentes e decisão

| Nível | Risco | Decisão |
|---|---|---|
| P0 | Publicar evidência sintética ou executar em produção | Sem caminho de rede/publicação nesta revisão; namespace explícito; não observado nos testes. Não equivale a provar eficácia real. |
| P1 | Saldo atual da conta não comprovado | Bloqueia live. Painel/cabeçalhos documentados não são medição desta conta. |
| P1 | Corrida com coletor/consumidores externos | Bloqueia live. Não usar grupo partilhado nem snapshot idle como prova. |
| P1 | Débito entre runners e perda de estado | Bloqueia live. Simulação retoma só com o mesmo estado; artifacts não resolvem atomicidade. |
| P1 | Transporte real/captura de respostas e timestamps não implementados neste harness | Bloqueia live e qualquer alegação de recuperação real. Exige revisão específica. |
| P1 | Snapshot auditado antigo face a main | Revalidar snapshot fresco antes do ensaio; não declarar pendências atuais com este snapshot. |
| P2 | Artifact sanitizado expira, sem arquivo durável permanente | Prazo 14 dias é só conveniência de revisão, não persistência científica ou ledger. |
| P2 | API e workflows podem mudar antes da ativação | Revalidar contrato, permissões, refs e código exato; mudanças não autorizam rede. |

**Não há risco de cancelar ou atrasar o coletor por partilha de concorrência neste desenho, porque não se entra no seu grupo nem se atua sobre runs. O custo BSD/HL desta implementação é zero. A proposta ainda não permite o ensaio real. Nenhum P1 em aberto é convertido em condição meramente informativa: todos bloqueiam rede.**
