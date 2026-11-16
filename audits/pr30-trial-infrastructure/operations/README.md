# PR30 — Resolução dos bloqueios operacionais (16/11/2026)

## Decisão

**APROVAR COM CONDIÇÕES a implementação preparatória. Não está pronta para propor ativação real.** A exclusão global, o ledger remoto e o saldo efetivo da conta continuam sem comprovação operacional; todos impedem rede. Não se declarou uma recuperação real. Não foram efetuados pedidos BSD/HL, dispatch, instalação em main, notificações, alterações de secrets ou modificações na PR29.

Esta auditoria foi efetuada sobre a implementação desta intervenção. Não equivale a aprovação obtida de outro revisor independente; essa revisão externa permanece necessária. Os testes verificam invariantes do código e não certificam que consumidores reais cooperem nem que exista saldo real.

## A. Correções concretas

| Bloqueio | Implementação | Prova e limite |
|---|---|---|
| Exposição do secret | Removido BSD_API_KEY do workflow de pré-verificação; nenhum secrets.* no YAML | Testes verificam workflow inteiro. CLI não consulta chave, regista `bsd_key_check_deferred_to_authorized_execution` e continua com exit 2. A disponibilidade só pode ser verificada no passo de uma execução futura autorizada. |
| Orçamento não transacional | `recovery_trial_journal.py`: protocolo append-only, Git CAS, cap global=5, débito antes de envio, idempotência, lease e replay a partir de âncora | Teste contra Git bare real local, dois concorrentes e novo processo. Não é um servidor distribuído instalado. Plano GraphQL CAS preparado, não executado. |
| Quota sem proveniência | Envelope de captura com origem, conta, âmbito football, observação UTC, reset UTC, sequência de débitos, hash da captura e hash do envelope integral, revisão e vínculo à conta | Só hash de envelope previamente aprovado na âncora é aceite. Número adulterado é rejeitado. A veracidade da captura e vínculo à conta exigem revisão externa; não há saldo real observado. |
| Transporte ausente | `recovery_trial_transport.py`: contrato BSD, RequestSpec, corpo original, SHA-256, receção UTC real, headers whitelisted, validação de página/offset/totais, paragem 429/erro/redirect | Wire injetável exclusivamente data-only OfflineWire; nenhuma ponte de rede utilizável. O código preparado chama as funções do motor inalterado. Evidência marcada synthetic_wire. |
| Snapshot antigo | `prepare(... snapshot_checkout=..., snapshot_sha=...)` e opções secretless --snapshot-checkout/--snapshot-sha | Exige checkout na revisão exata, data tracked/limpa, cópia externa, hashes e binding da revisão na retoma. Só lê data, nunca importa código dessa origem. Não seleciona IDs novos nem reduz janelas de pesquisa. |
| Concorrência | Protocolo cooperativo com intenções do coletor e reserva exclusiva; proposta/patch de integração separados | Coletor ativo/pendente bloqueia trial no protocolo. Não foi instalado e não demonstra exclusão de consumidores reais. Consumidor não coordenado/ausência de revisão global bloqueia. |

O motor validado continua em `247deee40e5e6ebdb107808b66adb96622074238`. Nenhum dos seus módulos originais foi modificado. As alterações funcionais afetam apenas ficheiros da infraestrutura PR30 e módulos/testes novos.

## B. Concorrência: comparação e proposta separada

O coletor injeta a chave no passo results_bsd, tem intervalo normal de 50 minutos (pode ser anulado pelo dispatch force_results), limite de 12 pedidos e paginação de 200. `collect.yml` encadeia a próxima run. `scangoal-write` não permite uma fila adicional segura para o ensaio: pending pode substituir pending, mesmo sem cancelamento da run ativa. Listar runs não cria exclusão. O CLI genérico recovery também seria um consumidor não coordenado se alguém o ativasse; consumidores fora do repositório não são observáveis pelo GitHub.

| Alternativa | Simplicidade e custo | Corridas/fiabilidade | Impacto no coletor |
|---|---|---|---|
| Mesmo grupo scangoal-write | Simples, sem serviço | Pode substituir pending; não cobre externos | Rejeitada: pode cancelar ou atrasar recolhas |
| Reserva apenas de quota | Simples, Git existente | Limita débito, mas não impede duas consultas simultâneas | Não resolve exclusão |
| Lock cooperativo por conta + ledger Git CAS | Moderado, sem serviço pago novo | Garante um proprietário entre participantes; não cobre quem o ignora | Crash pode deixar lease sem libertação; não instalar sem procedimento de recuperação |
| Uma consulta de ensaio no próprio passo BSD, depois das consultas normais | Menor mudança de escalonamento; usa execução existente | Reduz coordenação entre workflows; ainda exige ledger e inventário da conta | Não cria/cancela runs nem muda grupo; acrescenta até um timeout de 20 s por ocasião, sujeito a aprovação |

**Proposta preferida para revisão futura:** captura dos headers de uma consulta BSD que o coletor já faria; depois das suas consultas normais, no máximo uma página de ensaio por ocasião aprovada, com reserva no ledger comum e snapshot isolado. Não criar uma run concorrente para disputar scangoal-write. Não instalar sem autorização e análise do acréscimo máximo de 20 s, do encadeamento e da conservação das evidências. Não há promessa de ausência de qualquer latência: essa latência é uma decisão de produção pendente.

O ficheiro `collector-coordination-proposal.patch` é um **patch de interface**, não uma solução integral já pronta a instalar. Adiciona ao request_json existente um coordenador opcional injetado, que envolve a chamada e recebe só timestamp/hash/headers de quota. Mantém o caminho atual quando não é fornecido coordenador, e não existe toggle por env que o instale automaticamente. Não inventa acesso a conta/chave nem um backend remoto. O executor/coordenador remoto e o registo obrigatório de todos os consumidores ainda precisam de implementação/revisão; o patch isolado **não coordena o coletor**. Foi compilado e passou `git apply --check`; não foi aplicado nem executado. O ficheiro real results_bsd.py e collect.yml continuam intactos.

O journal implementa prioridade: uma intenção registada de coletor pendente impede reserva de trial, mas permite a reserva do coletor; uma lease ativa impede outro envio. Não existe TTL com roubo automático: expirar um relógio não impede um HTTP antigo ainda em curso. Crash deixa reserva e lease preservadas e requer prova de término/reconciliação antes de libertar. Isto é seguro contra dupla reserva, mas pode impedir a próxima consulta BSD se instalado; **P1 de disponibilidade ainda pendente**, razão para não ativar nem aplicar o patch.

Uma lista de consumidores ou coverage_review_ref num teste não prova exclusão global. Antes de produção, o titular terá de comprovar todos os utilizadores da mesma conta/chave e adoção obrigatória do mecanismo ou uma janela exclusiva controlada. Não assumir adesão dos externos, não alterar secrets para forçar adesão e não ignorar consumidores desconhecidos.

## C. Ledger durável: implementado versus implantação

Não se usa cache/artifact como transação. O protocolo usa uma única cadeia Git com âncora imutável que identifica conta e TRIAL_ID=`goal-roi-bsd-20261113-15-v1`. A âncora deverá ser fixada em configuração revista/protegida; não pode ser escolhida livremente por cada runner. O backend desta revisão é OfflineGitStore, um servidor de objetos bare temporário **só dos testes**. `git update-ref ref new expected_old` faz o CAS real local; não é uma alegação de lock local distribuído.

Cada evento é um commit com um único pai; a leitura verifica o hash do objeto e reconstrói todas as transições até à âncora. Dois candidatos para o mesmo head: só um CAS vence. O perdedor não envia nem tenta novo débito automaticamente. O operation_id e o proprietário vinculam a reserva; replay devolve `send_permitted=false`, mesmo se não houver resposta. Crash entre reserva e HTTP gasta a tentativa conservadoramente. Nunca reenvia uma reserva incerta, nunca devolve crédito, nunca reinicia por novo dia UTC. Completar exige o proprietário da lease e hash de resposta; falhas/429 tornam o ensaio halted. Perda da ref, objetos, âncora ou evidência de resposta bloqueia, sem bootstrap automático. Bootstrap_fixture cria apenas bases sintéticas dos testes.

O plano remoto `remote_cas_plan` prepara uma mutation GitHub `updateRefs` com **refUpdates**, `beforeOid`, `afterOid` e `force=false`, exclusivamente para refs/heads/bsd-trial-journal. A documentação oficial confirma atomicidade/beforeOid. **Nenhuma mutation foi enviada, nenhuma ref de ledger foi criada.** O plano não é um backend remoto completo. Leituras verificadas, criação de commits, confirmação após resposta de escrita incerta, proteção de referência/âncora, autorização restrita e disponibilidade operacional continuam pendentes. Sem esses elementos, o preflight continua a indicar cross_run_budget_not_proven.

Mesmo no futuro, erros/indisponibilidade do GitHub não podem provocar fallback para contador local. Um timeout de CAS deve levar a leitura/reconciliação do commit exato, nunca a presumir que não houve reserva. A ausência de evidência não autoriza recomeço. Uma eliminação/reescrita administrativa da ref não é tolerada pelo desenho sem âncora e proteção operacional; não se promete resistência a um administrador que altere os controlos.

## D. Quota: prova auditável sem pedidos adicionais

Limite nominal documentado: 7 500/dia em football gratuito, reset à meia-noite UTC. Saldo real não deriva desse limite nem do número da última run. A documentação apresenta painel por desporto e os cabeçalhos RateLimit-Policy/RateLimit. Não foi encontrado endpoint documentado isento; não se fez sondagem nem pedido para obter quota.

Duas origens preparadas: captura sanitizada do painel da conta pelo titular, ou headers de uma resposta que o coletor já estava autorizado a receber. Uma consulta API extra só para saldo teria de ter custo e autorização separados; não entra implicitamente nos cinco. A disponibilidade do painel e o seu custo interno não foram verificados na conta. Uma captura não é automaticamente autêntica e não se deve guardar HTML/cookies/chave.

record_capture cria um envelope de evidência sem fazer rede. A validação exige origem pública BSD, âmbito football, identificação não secreta da conta, vínculo revisto à conta da chave, hora UTC, reset diário correto, sequência de consumo e revisão. A âncora aprova **o hash integral do envelope**, ligando número/metadados ao hash dos bytes da captura. Adulterar remaining mantendo o hash antigo falha. Um número avulso ou source=manual-number não é aceite. Este mecanismo torna a decisão auditável; o checksum não autentica o fornecedor nem substitui a revisão do documento original.

A prova só vale até 60 segundos e dentro do mesmo dia UTC. Todo o débito coordenado posterior é subtraído antes da reserva. A condição inicial é saldo >= reserva recalculada +5; depois aplica reserva+pedidos de ensaio ainda não debitados. Um pedido normal do coletor entre verificação e trial pode tornar 105 em 104 e bloquear o trial; não se omite esse consumo. Consumo desconhecido invalida o ensaio de forma persistente. A reserva mínima é100, mas requer avaliação de outros consumidores. Janela expirada, conta diferente ou consumo não totalizado bloqueiam. A aprovação de capturas novas/renovação no backend remoto não foi instalada; se a prova expirar não há execução adicional automática.

O adaptador guarda apenas os dois headers documentados, sem os interpretar por regex permissiva. Parser completo de HTTP Structured Fields e revisão da origem/vínculo continuam necessários numa integração que aprove capturas automaticamente. Nesta revisão um header recebido em fixture não é promovido automaticamente a saldo confiável.

## E. Transporte preparado e staging

PreparedBSD reutiliza ROOT e parse_page do código existente, e devolve o JSON BSD para run_batch/identity/reconcile inalterados. Não chama request_json: esse transporte segue o urlopen normal e descarta headers, incompatível com as exigências de redirects/captura. RequestSpec fixa status=finished, três datas, limit200, offset0 na primeira página, timeout20, retries0 e follow_redirects=false. A próxima página só pode continuar o offset derivado da anterior; total alterado, ID repetido/sobreposto ou página vazia prematura é erro estrutural. Não segue next/Location como URL de destino.

Somente OfflineWire com bytes estáticos pode ser injetado, verificado também no momento da chamada; callbacks arbitrários/substituição posterior são rejeitados. network_bridge lança sempre bloqueio; não há --live nem input de ativação. Esta revisão **não abre sockets HTTP**. O contrato de rede está preparado, mas a futura implementação do envio HTTP real continua dependente de auditoria/autorização e não foi demonstrada contra o fornecedor.

A reserva CAS precede cada send simulado. Sem chave no momento da execução não há reserva/envio. RequestSpec omite headers de repr; a chave só está no cabeçalho interno, não em URL/CLI/metadados. Exceções do transporte são substituídas por códigos sanitizados. Respostas que reflitam a credencial literalmente ou por escape JSON são recusadas antes da persistência. Sem retries após erro.

Captura os bytes exatos injetados, hash SHA-256 e UTC após receção, em diretório externo preparado-fixture-trial, com criação exclusiva de ficheiro; timestamp não é o relógio fixo antigo de fixtures. Metadados declaram synthetic_wire, source BSD, parâmetros públicos, hash e commit da reserva. Não são respostas reais. Falha de persistência mantém débito/lease, sem reenvio. Evidências anteriores completas ausentes/corrompidas impedem novo débito. O engine continua a aceitar apenas identidade determinística e colocar correções/conflitos em quarentena; os testes anteriores T1/T2/T3 continuam incluídos. Nenhuma liquidação de produção ocorre.

O snapshot pode ser atualizado a partir de checkout de dados em SHA específico. O código continua do motor auditado. Não se aceita dirty/untracked data nem troca de revisão sobre estado existente. Os dez IDs e três datas mantêm-se fixos; os já liquidados ou que requeiram dias fora do autorizado são excluídos. Atualizar entradas após uma reserva não permite novo orçamento: exige preservar a âncora e a evidência do ensaio, não criar outro TRIAL_ID. A ligação do hash do snapshot à âncora remota deve ser revista antes de produção; o backend remoto ainda não existe.

## F. Testes e integridade

Evidências desta revisão em operations/full-tests.log, safety-tests.log, preflight.json e integrity.json.

- Suíte completa: **543 testes aprovados**, incluindo 486 originais, 25 PR30 iniciais e 32 operacionais novos. Os 53 do motor estão nos486 e não se somam outra vez.
- Segurança PR30: **57 testes aprovados** (25+32).
- Testes operacionais incluem dois candidatos concorrentes sobre o mesmo Git ref, retomada noutro processo depois do débito, dia UTC seguinte, ref/objeto perdido/corrompido, âncora errada, consumidor não coordenado, coletor ativo e intenção pendente, saldo stale/adulterado, consumo intermédio, ausência de chave, 429, corpo inválido, sexto pedido, offset inicial errado, IDs sobrepostos, datas/fontes proibidas, saída em produção, injeção de rede e perda da resposta no runner seguinte.
- O descritor GraphQL foi conferido com o schema oficial: UpdateRefsInput.refUpdates, não um campo inventado updates. Nenhuma execução GraphQL do ledger foi usada como teste.
- A primeira execução isolada do teste de timestamp comparava microssegundos com matched_at_utc do motor, que conserva precisão de segundos. A asserção foi ajustada a essa precisão; o motor não foi alterado.
- Preflight real local, com sockets bloqueados e sem chave: dez IDs, zeroBSD/HL, zero integrações, originais intactos, exit2 esperado.
- Patch de interface: compila e `git apply --check` passa. Não foi aplicado a results_bsd.py nem instalado em main. Não se declara validação operacional do patch completo com fornecedor/servidor reais.
- Integridade: todos os177 ficheiros originais do SHA motor continuam byte a byte iguais. Certificação, Telegram, histórico, relatórios, collect.yml, tests.yml e store.py intactos. Apenas ficheiros da infraestrutura nova foram alterados/adicionados. A atividade autónoma do coletor em main não é atribuída a esta intervenção.
- Logs NOTIFY_OK/HL_OK etc. da regressão antiga provêm dos seus mocks; não são chamadas reais. O runner offline proíbe sockets no processo principal; subprocessos novos só usam Git local/simulação sem ponte de rede.

## G. Custos, riscos e próximos passos mínimos

Não foram criadas contas, serviços ou dependências Python adicionais. Código e testes usam biblioteca padrão e Git já disponível. A proposta remota usa o repositório GitHub existente, sem serviço contratado; está sujeita às permissões e limites de API/armazenamento/Actions do plano existente. Não foram ativadas opções pagas. BSD/HL consumidos nesta tarefa:0/0. Operações futuras de metadados no GitHub não são quota BSD, mas requerem autorização/permissões adequadas. O preflight mantém contents:read; não ganhou permissões de escrita para ledger.

| Nível | Estado | Consequência |
|---|---|---|
| P0 conhecido nesta revisão | Não identificado um caminho utilizável de rede/publicação; isto não é certificação de risco zero | Nenhuma eficácia real declarada; continuar a revisão externa |
| P1 secret na pré-verificação | **Resolvido no código publicado** | Secret removido; verificação de disponibilidade adiada e bloqueante |
| P1 concorrência global | **Parcial: protocolo e patch de interface preparados; não instalado/provado** | Bloqueia live, incluindo desconhecimento de externos e recuperação de lease abandonada |
| P1 orçamento global | **Parcial: transações Git locais reais e plano CAS remoto; servidor/âncora/proteções ausentes** | Bloqueia live entre runners reais |
| P1 quota real | **Parcial: envelope e validação preparados; sem captura real/conta autenticada** | Bloqueia live, inclusive prova stale ou débitos desconhecidos |
| P1 transporte | **Preparado por injeção, ponte de rede bloqueada e ainda não validada no fornecedor** | Exige nova revisão/autorização antes de implementação/ativação do envio real |
| P1 snapshot | **Atualização por SHA/seleção segura implementada; validação de main atual pendente na execução** | Não usar snapshot antigo como prova de pendências atuais |
| P2 operação | Expiração de prova60s, limites GitHub, revisão de capturas e durabilidade das respostas | Orçamento pode terminar conservadoramente com menos de5 tentativas; não aumentar limites |

Procedimento mínimo futuro: (1) rever o backend remoto completo com âncora fixa e proteção contra perda/recriação, sem force; (2) aprovar e instalar separadamente a coordenação de todos os consumidores e decidir o impacto máximo/recovery de crash; (3) comprovar saldo football real por captura existente revista e débitos totais; (4) congelar main num SHA e revalidar os dez jogos com o motor exato; (5) rever a ponte HTTP BSD sem redirects/retries e armazenamento durável de evidências, mantendo os limites; (6) obter autorização específica para instalação e execução; (7) só então injetar a chave no passo que fará os pedidos e executar no máximo o orçamento remanescente. Parar e entregar evidências após o ensaio, sem integrar produção. Nenhum destes passos foi autorizado automaticamente pelo sucesso dos testes.

Fontes primárias consultadas:

- https://docs.github.com/en/graphql/reference/git — updateRefs, RefUpdate.beforeOid, atomicidade, force=false e schema refUpdates.
- https://docs.github.com/en/rest/git/refs — atualização fast-forward e permissões.
- https://git-scm.com/docs/git-update-ref — transação com valor anterior esperado.
- https://sports.bzzoiro.com/docs/conventions/ — contrato de paginação/status e quota/RateLimit/reset UTC.
- https://sports.bzzoiro.com/pricing/ — plano gratuito e painel por desporto.
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency — semântica de pending.
