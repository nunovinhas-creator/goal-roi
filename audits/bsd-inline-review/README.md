# BSD: caminho mínimo para a primeira recuperação real

## Decisão

**Preferir uma página no fim do passo BSD existente. Não instalar a alternativa distribuída como pré-requisito deste ensaio único.** Manter apenas o débito durável de cinco tentativas e o armazenamento de evidência, reaproveitando o motor e o adaptador já preparados. Esta branch prepara esse desenho; não o instala nem permite rede real.

Branch de revisão: codex/bsd-inline-review, baseada exatamente em PR30/d59dac002952aa4acac8aa20d4607328a74b8e31. PR29 e PR30 não foram alteradas. Sem merge, force-push, workflow novo, dispatch, secrets modificados, Telegram, certificação ou consultas BSD/HL. Os194 ficheiros da base continuam byte a byte iguais; só adições. Um patch separado descreve as mudanças possíveis no coletor, sem as aplicar.

## O que foi implementado

- `scangoal/recovery_inline.py` (223 linhas): recibo estrito de quota da última resposta normal, tracker serial, cinco slots irreversíveis com CAS, abandono sem restituição, uma página por run e restauro de respostas a partir do registo durável.
- `tests/test_recovery_inline.py`:20 testes direcionados para estes riscos.
- `collector-tail-proposal.patch`: duas interfaces opcionais em results_bsd.py: observar os headers de uma resposta já pedida e executar um callback depois das consultas normais e do seu estado guardado. Compilado, aplicado apenas numa cópia temporária de teste e verificado por git apply --check. **Não aplicado ao ficheiro real nem instalado.**
- `main-revalidation.json`: revalidação real, exclusivamente por leitura do GitHub/dados guardados, dos dez jogos.
- Logs da regressão e testes específicos; `integrity.json`: comparação dos194 blobs da base e hashes dos ficheiros adicionados.

O patch é uma interface preparatória, não o executor final. Não acrescenta ativação por env, não cria runs e não passa o secret a outro passo. A montagem do executor, backend remoto e ponte HTTP revistos continua pendente. A ausência de ativação é intencional; a branch não anuncia o ensaio como operacional.

## Revalidação dos jogos em main

Snapshot observado e fixado: **e143268f459f93897a284e9c3c737ecec53eff79**. Usou-se o motor exato da PR29. Os cinco ficheiros de entrada foram obtidos do mesmo commit e conferidos por Git blob ID: messages.jsonl, details_hl.json, results_hl.json, results_bsd.json e team_aliases.json. Não há manual_results.json nesse tree. As mensagens brutas ficam só na cópia de trabalho, não na evidência pública desta revisão.

**10/10 continuam selecionáveis, cada um com uma aposta pendente e sem FT nesse snapshot.** Datas necessárias são apenas13,14,15/11; nenhum alvo exige dia adjacente fora da autorização. Isto prova pendências guardadas, não que os jogos terminaram efetivamente ou que a BSD os cobre.

| Data | Jogos |
|---|---|
| 13/11 | Vasteras–Brommapojkarna; Shooting Stars–Bendel Insurance |
| 14/11 | Lumezzane–Trento; Fukushima–Sagamihara; Athletic Bilbao B–Racing Ferrol; Villa Dalmine–Camioneros; Horsham–Torquay; Farnham–Ebbsfleet |
| 15/11 | Guidonia Montecelio–Ostiamare; Cittadella–Folgore Caratese |

Antes da execução será necessário atualizar a fotografia e, depois das consultas normais, excluir qualquer alvo que entretanto tenha FT. A cópia pós-consultas deve identificar o commit base de main **e os hashes dos dados locais depois da recolha normal**, porque estes podem ainda não estar commitados. Não chamar a uma cópia suja um checkout byte a byte do commit original. Só ler/copiar esses dados para staging; não devolver resultados de recuperação para data/ ou docs/.

## Ordem concreta proposta

1. Executar as consultas BSD normais com o comportamento e a chave existentes. A recolha de headers usa o mesmo request, sem consulta de saldo adicional.
2. Guardar normalmente results_bsd.json. O callback de recuperação só corre depois desse ponto. Se a recolha for omitida por intervalo mínimo, não houver resposta válida ou não houver quota documentada, saltar o ensaio sem forçar consultas.
3. Fechar a fase normal no tracker. Nova consulta normal invalida a prova anterior; erro posterior não permite recorrer a um saldo antigo. Não permitir interleaving entre witness e recuperação.
4. Ler o registo durável e os alvos atuais na cópia isolada. Verificar a âncora única, débitos herdados, fotografia e identidade dos dez jogos, revisão da exclusividade da conta, quota >=reserva+tentativas restantes e margem do job.
5. Se houver uma reserva antiga incerta, obter a prova autenticada da conclusão da tentativa anterior do coletor e abandoná-la definitivamente. Se não houver prova, saltar a recuperação e continuar o coletor.
6. Reservar um slot por CAS confirmado **antes** de enviar. Só uma página por run_id:run_attempt. Sem retries, sem substituição de um pedido que ficou incerto, sem novas runs.
7. Usar o adaptador preparado com status=finished e paginação controlada, max_requests=1 no motor, HL=0 e teto global5 no registo. Preservar raw/hash/UTC, resultado e decisão só em staging. Gravar a conclusão e os bytes de resposta no registo separado.
8. Parar depois dessa página e deixar o coletor continuar. No máximo5 tentativas no ensaio inteiro. Um primeiro resultado aceite deverá levar a revisão antes de decidir utilizar mais orçamento; não autoriza integração de produção.

Esta revisão usa apenas OfflineWire; network_bridge continua bloqueado. A ordem foi validada por injeção, não por execução real. Não foi criado executor live nem opção --live.

## Concorrência e recuperação de reservas

O grupo scangoal-write já serializa as execuções do coletor, incluindo dispatch manual. Não se acrescenta outro workflow ao grupo nem uma run de ensaio que dispute a posição pending. O callback é síncrono, no fim do mesmo passo, depois do último HTTP normal. **Dentro desse processo e do grupo existente não há consultas normais e de ensaio paralelas.** Isto não prova exclusão de scripts/consumidores externos ou de outro workflow que não use o grupo. A revisão da utilização exclusiva da conta é condição para ativar; exclusive_account_review_ref é uma referência a essa revisão, não uma prova criada por um teste.

Retira-se a lease global da conta usada no desenho distribuído. A única reserva pendente do novo protocolo pertence ao orçamento do ensaio. A recolha normal não consulta esse bloqueio, não espera por ele e não passa a depender da disponibilidade do servidor do ensaio. O callback captura falhas e volta ao fluxo normal; não desencadeia notificações de falha de workflow por erro do ensaio. Não se modifica o módulo Telegram.

Uma reserva abandonada **não é recuperada para gastar outra vez**. O débito é definitivo. O registo conserva o operation_id, dono run_id:run_attempt e parâmetros. O mesmo pedido (dia/offset) nunca é reemitido por outro slot. Se o request era offset0 e a resposta se perdeu, não se avança a data para offset200 nem se certifica cobertura: essa data fica por resolver/quarentena. Pode investigar-se outra data com outro slot após revisão, se todas as condições continuarem válidas.

O evento abandon exige um documento do GitHub para a tentativa exata: id, run_attempt, path collect.yml, head_branch main e status completed. Guarda-se o documento original, hash e campos interpretados. **A leitura autenticada desse documento e a confirmação de que o callback atual detém o grupo são obrigações do executor futuro; um JSON colado não autentica o GitHub.** Não se usa TTL nem simples listagem de runs para roubar um envio incerto. Se a tentativa antiga ainda está ativa ou é outra tentativa, o abandono é rejeitado. Mesmo depois de abandonada, uma conclusão tardia não pode restaurar crédito nem autorizar reenvio.

A comprovação de run terminal não é uma garantia universal de fim de processamento no servidor BSD depois de um timeout. Sem informação suficiente sobre um HTTP antigo, não inferir ausência de consultas externas/em voo. Nessa situação, continuar o coletor com as suas regras e suspender só o ensaio até revisão. Não se resolve essa incerteza inventando uma janela temporal.

## Orçamento e evidência durável

InlineSlots reaproveita o backend Git e o adaptador da PR30. Não cria servidor ou conta novos. O backend efetivamente testado é um Git bare local com update-ref expected_old atómico, acessível a processos distintos. O servidor remoto e a âncora protegida ainda **não foram instalados nem comprovados**; o plano remoto pode usar o beforeOid/force=false já preparado na PR30. O caso local não é apresentado como exclusão distribuída operacional.

O registo único contém no máximo cinco slots e não é reiniciado por dia UTC, processo ou runner. O primeiro CAS reserva o envio; um retry do mesmo operation_id devolve send_permitted=false. Perda da resposta ou crash que antecede o HTTP gasta o slot conservadoramente. Dois candidatos concorrentes para o mesmo head não podem ambos ganhar. Falta/corrupção do registo bloqueia o ensaio; não existe bootstrap automático.

A âncora mantém o mesmo TRIAL_ID. `prior_debits` e `predecessor_budget_review_ref` obrigam a contabilizar tentativas eventualmente gastas no desenho anterior; não se podem obter cinco extras criando outro protocolo. A verificação dessa linhagem e a desativação do caminho alternativo são requisitos de implantação: só um mecanismo pode servir este ensaio. Declarar manualmente zero não é prova de ausência de consumo anterior. Aqui nenhuma API desportiva foi chamada.

Ao concluir, os bytes raw e metadados ficam no registo append-only separado (base64 no protótipo Git local). Isto permite restaurar a evidência num novo processo/runner sem confiar no artifact. Uma implementação remota deve escolher blobs/árvore de staging para os corpos grandes, manter hashes e atualizar o head atomicamente. Payload/limites de API, confirmação após timeout e proteção da âncora precisam de revisão antes de produção; não se promete que o protótipo com corpos em mensagens de commit seja o formato remoto final.

Os testes demonstram restauro dos bytes num **novo interpretador Python**, depois de apagar a cópia local. A reconstrução das páginas/decisões do motor deverá preservar o observed_utc original de cada resposta, não tratar replay como nova observação. Esse encaixe do executor/estado do motor ainda precisa de revisão antes de ativar; a evidência bruta restaurada não é uma liquidação automaticamente aceite.

## Saldo sem pedido adicional

NormalQuotaTracker observa só a última resposta das consultas BSD normais. Usa a chave que o passo já utiliza; não guarda nem imprime o valor. Não há novo secret, CLI de chave ou alocação a outro job. O adaptador de ensaio terá de receber a mesma variável no mesmo processo, não uma chave introduzida à parte.

O parser aceita um subconjunto estrito dos headers documentados RateLimit-Policy e RateLimit: âmbito football único, q7500, w86400, r e t inteiros, sem duplicados/escopos adicionais. Compara t com a meia-noite UTC (tolerância60s). Não é um parser completo de Structured Fields: um formato legal mas não suportado faz saltar o ensaio, sem falhar a recolha. Não interpreta ausência de headers como saldo ou como autorização para pagar um plano ilimitado.

O recibo inclui hash do corpo normal, hash dos headers, receção UTC, dono da run, sequência normal e débitos de slots já observados. Deve ter <=60s no momento da reserva, mesmo dia UTC e nenhuma consulta normal intermédia. Depois do CAS, o relógio é conferido novamente: se a persistência tiver deixado expirar a prova, o slot é debitado mas não autoriza envio. O CAS desconta qualquer débito do ensaio posterior ao recibo, para não sobrestimar o saldo numa corrida. Primeiro envio exige >=105 se a reserva é100; depois exige reserva+orçamento ainda não debitado. Reserva superior exige mais saldo. Consumidores não contabilizados invalidam a condição de exclusividade; o código não assume que seguem o registo.

**A conta real continua sem saldo observado.** Capturar headers de uma resposta que seria feita de qualquer forma é o caminho mais curto para obter essa prova, sem gastar um dos cinco pedidos num diagnóstico. Não há endpoint inventado nem pedido API extra.

## Comparação quantificada

| Aspeto | PR30 distribuída proposta | Alternativa inline |
|---|---|---|
| Nova run/workflow de ensaio | 1 workflow manual preparado | 0 novos workflows/runs |
| Escritas do ensaio para cada consulta normal | Protocolo cooperativo prevê reserva/conclusão de cada consumidor | 0 escritas de trial por HTTP normal; captura em memória |
| Lease de conta que pode bloquear BSD normal | 1 no modelo cooperativo | 0; reserva afeta apenas o ensaio |
| Atualizações duráveis por tentativa de ensaio | Reserva+conclusão, além de quota/coordenação | 2 CAS: reserva e conclusão OU abandono |
| Teto de atualizações CAS para cinco slots | Dependente de todos os consumidores | Até10 para os slots, excluindo criação/leituras de objetos e âncora |
| Limite HTTP adicional por run | Lote poderia usar até5 timeouts | 1 página, timeout20s |
| Tempo HTTP máximo das cinco tentativas | Até100s | Até100s totais, repartidos por até5 ocasiões elegíveis |
| Saldo antes do primeiro envio | Captura externa/revisão+débitos globais | Última resposta normal, mesmo processo, sem pedido de diagnóstico |
| Código novo nesta alternativa | Não aplicável | 223 linhas; reutiliza transporte e Git da PR30, não elimina esses ficheiros do repo |
| Consumidores externos | Exigem prova/coordenação | Continuam a exigir prova de exclusividade; não resolvido por inline |

Os20s são apenas o timeout HTTP adicional, **não uma promessa de duração total**. O backend remoto deve ter deadlines para metadados e o executor deve verificar margem restante do job. collect.yml tem timeout15min, leitura inicial até220s e fast reads até540s, além dos restantes passos; uma página adicional pode aproximar o job do limite e atrasar o encadeamento. Sem margem demonstrada para HTTP+persistência+passos seguintes, saltar a recuperação. Não diminuir o trabalho normal para criar essa margem nem alterar o agendamento nesta revisão.

Os números de complexidade são contagens do desenho/código, não medições de desempenho real nem percentagens inventadas de redução de risco. Não há garantia de zero latência. O custo monetário da preparação é zero; a proposta usa GitHub já existente e a BSD gratuita, sujeitos aos limites da conta/Actions/API. Nenhuma opção paga ou dependência nova foi adicionada.

## Validação e estado dos bloqueios

O CI inicial detetou uma dependência indevida do teste num commit histórico ausente num checkout shallow. A verificação foi corrigida para comparar o Git blob hash com o manifesto auditado, sem alterar workflows ou reduzir a exigência de integridade.

563 testes da suíte completa aprovados (543 anteriores+20 específicos). A regressão inclui os bloqueios anteriores de chave/transporte. Os testes novos cobrem CAS concorrente, headers ausentes/stale, consulta normal intermédia, 104 insuficiente, reservas abandonadas, dono/attempt errados, sexto envio e dia UTC seguinte, ausência de TTL steal, caminho externo sem inventário, uma página por run, pedido incerto não reemitido, evidência restaurada noutro processo e hook depois do estado normal guardado. Falha do tail é contida. Todos os dados HTTP usados nos testes são sintéticos e não provam recuperação real.

Resolvidos na preparação: revalidação atual dos dez jogos; ponto exato para o callback; erro da quota/tail não faz falhar o coletor; parser/recibo da última resposta; slot queimado sem reenvio/reembolso; ausência de lease de conta no caminho normal; restauro durável de bytes comprovado no backend de teste. Não se confunde estas propriedades com implantação operacional.

**P1 ainda bloqueantes:** backend remoto/âncora única e migração de débitos; exclusividade real da conta fora do grupo; executor que autentique run-terminal e detenção do grupo; HTTP real sem redirects/retries ainda desativado; reconstrução do estado do motor com cronologia original; margem de tempo do job e evidência real de quota. O patch de interfaces não preenche sozinho estes pontos. Não há P0 conhecido demonstrado por esta revisão, sem alegar risco zero. P2: formato estrito de headers pode saltar um ensaio válido; resposta perdida pode consumir um dos cinco slots sem ganho; o snapshot deve voltar a ser atualizado antes do envio.

## Próximo passo mínimo, sujeito a autorização

Rever esta alternativa **em substituição do executor distribuído**, completar somente o pequeno executor do passo BSD e o backend remoto do registo dos cinco slots, rever a exclusividade da conta e o timeout do job. O executor deve usar os hooks do patch, o tracker e o adaptador existentes, congelar entradas em staging e não carregar notify. Depois de auditoria e autorização explícita de instalação/execução, habilitar no máximo uma página após uma resposta normal que comprove saldo. Não instalar agora; não fazer merge; não disparar workflows. Encontrar um FT só conta como recuperação adicional quando a identidade é confirmada e aceite em staging; produção continua com zero integrações.

Fontes oficiais: https://sports.bzzoiro.com/docs/conventions/ (quota, UTC, paginação); https://docs.github.com/en/graphql/reference/git (beforeOid, atomicidade e force=false); https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency (pending e serialização). As páginas foram consultadas sem chamar endpoints desportivos.
