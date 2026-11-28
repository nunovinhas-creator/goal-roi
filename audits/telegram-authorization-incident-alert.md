# Aviso por incidente de autorização Telegram

## Integração e ativação

Esta correção parte de `fcaadaaa2e8343a49dda2f457aed6e650a940574` da PR #116. Conserva a deteção, a identidade do incidente, a prova de recuperação autenticada e o transporte já validados. O reader e o watch entregam o evento no ponto de deteção quando o mecanismo está ligado. A publicação do relatório e o despacho não são pré-condições para o envio do aviso.

A variável de repositório `GOAL_ROI_TELEGRAM_AUTH_ALERTS` tem de conter exatamente `on`. A comparação na shell e em Python é sensível a maiúsculas. Ausência, vazio, `off` e qualquer outro valor deixam o mecanismo desligado. Neste modo o workflow não cria a pasta do worker nem arranca o processo. O reader e o watch mantêm o diagnóstico e não emitem eventos. O aviso genérico de falha do despacho continua independente.

## Preparação autorizada das referências

A referência operacional `refs/heads/goal-roi-telegram-auth-alert-state` e a testemunha `refs/heads/goal-roi-telegram-auth-alert-witness` têm de existir e apontar para o mesmo commit. Não há inicialização automática. A leitura falha se faltar uma referência, houver divergência, Git estiver inacessível ou o JSON falhar a validação. A reclamação de tentativa exige um push atómico confirmado para as duas referências. O commit tem como pai o tip observado. Os leases impedem a recriação ou substituição fora da posição observada durante uma corrida.

Preparar as referências apenas numa intervenção autorizada, com o mecanismo desligado e sem workers em execução:

1. Confirmar o repositório, as duas referências exatas e a inexistência de um estado operacional anterior. Uma referência ausente após inicialização exige recuperação, mesmo que a outra também esteja ausente.
2. Num Git isolado, construir um commit inicial sem pai, com um único ficheiro `incident.json` contendo `{"active":null,"incidents":{},"recovered_at":null,"version":1}`. O operador designado publica explicitamente o mesmo commit nas duas referências, num push atómico condicionado à inexistência de ambas. Guardar o SHA inicial e a evidência de autorização.
3. Aplicar a proposta de [ruleset](../ops/authorization-alert/ruleset.json) às referências exatas. A regra impede criação posterior, eliminação e recuo. As atualizações fast-forward ficam disponíveis ao token do workflow com `contents: write`, sujeito às restantes regras aplicáveis. A proposta não requer bypass do workflow, PR, checks ou assinatura adicional para estes commits gerados. Confirmar que regras herdadas não alteram estas condições e testar a capacidade real de push atómico com lease na referência de ensaio antes de ligar o mecanismo. O Git bare local confirmou a política equivalente. A instalação e as permissões reais ficaram por verificar. [Regras do GitHub](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
4. Excluir literalmente as duas referências de todas as limpezas de branches, incluindo políticas baseadas em idade, ausência de PR ou ausência de checkout. A [lista de exclusões](../ops/authorization-alert/branch-cleanup-exclusions.txt) é o contrato para futuras ferramentas. Não foi encontrada uma limpeza automática de referências remotas no projeto. Validar o modo de simulação antes de introduzir um limpador.
5. Conservar uma cópia verificada dos objetos e do SHA do último tip confirmado fora do conjunto sujeito a limpeza. Guardar o tip que inclui a reclamação, mesmo se o resultado da entrega ainda for incerto.

### Recuperar sem repetir um aviso

Desligar o mecanismo antes de qualquer reparação e preservar a testemunha protegida. Restaurar apenas a referência afetada para o tip exato confirmado pela referência saudável e pela evidência operacional. Verificar conteúdo e ancestralidade e reaplicar a proteção antes de voltar a permitir execução. Se o último tip não for demonstrável, manter desligado e reconciliar tentativas manualmente. A cópia desatualizada da referência operacional diverge da testemunha e um checkout novo recusa enviar. O teste local apaga a referência, restaura uma cópia anterior à tentativa e depois restaura o tip exato. A tentativa original permanece única.

A proteção depende da integridade de pelo menos um dos dois tips e das regras do servidor. Um administrador que ultrapasse as proteções e recue simultaneamente ambas as referências para o mesmo commit anterior torna esse estado indistinguível de um estado antigo legítimo sem uma âncora externa. O procedimento exclui recuperar ambas a partir de uma cópia desatualizada. Se o estado mais recente de ambas tiver sido perdido, a reativação exige reconciliação e autorização próprias.

## Estados apresentados

O passo final publica um resumo local mesmo com o workflow agregado verde. Os estados degradados também produzem anotações. O ficheiro transitório conserva resultados degradados observados durante o job, inclusive se o incidente recuperar antes do resumo.

| Estado | Significado operacional |
|---|---|
| Mecanismo desligado | A opção ativa está ausente ou é diferente de `on`. |
| Mecanismo saudável sem incidente | O worker consultou as referências e não encontrou incidente ativo. Um canal sem configuração produz uma advertência própria. |
| Incidente detetado | Existe um incidente ativo, com aviso pendente. |
| Reserva pendente | O estado Git foi reservado. Ainda falta reclamar a tentativa. |
| Tentativa em curso | Este worker reclamou a tentativa e está a tratar a entrega. |
| Entrega confirmada | O Bot API devolveu uma confirmação positiva. |
| Entrega rejeitada | O transporte demonstrou rejeição ou ausência de envio. |
| Entrega incerta | A resposta não permite determinar se a mensagem foi entregue. |
| Tentativa anterior abandonada sem confirmação | Nova execução encontrou uma reclamação sem desfecho que não lhe pertence. O estado continua a impedir o reenvio. |
| Estado indisponível | Referências ausentes, divergentes, inválidas ou inacessíveis, ou escrita do estado falhada. |
| Worker interrompido ou com trabalho por concluir | Saída prematura, ausência de snapshot ou trabalho ainda pendente quando o job recolhe o resultado. |

Se a publicação do relatório falhar, o aviso pode avançar pela sua referência própria. Quando a reserva ou a reclamação não chega ao armazenamento durável, o worker não envia. Uma falha posterior a reclamar uma tentativa mantém a deduplicação. Se o resultado já foi conhecido localmente e falhou a sua publicação, o worker pode concluir apenas essa escrita enquanto estiver vivo. Uma entrega incerta nunca é reenviada automaticamente.

## Drenagem e continuidade

A fila pode esvaziar enquanto correm os passos já existentes. Depois do despacho, o resumo sinaliza a paragem cooperativa, lê o snapshot e termina. Retirei a espera adicional de 65 s. O passo final tem zero segundos de espera deliberada, sem `sleep`, ciclo de espera ou subprocesso de Git. Um worker parado ou pendente gera advertência sem transformar esta degradação num `failure`. O timeout global mantém-se em 18 minutos e o observador mantém as condições existentes. A opção de leitura imediata do snapshot preserva a libertação do grupo de concorrência. Uma deteção junto ao fim do job ainda pode ficar por concluir antes de o runner ser limpo, o que tem de ser exercitado no ensaio autorizado.

## Validação reproduzível

```sh
python -m unittest discover -s tests -p test_authorization_alerts.py -v
python -m unittest discover -s tests -v
node --test --test-isolation=none ops/continuity/watchdog.test.mjs tests/v6_core.test.cjs tests/v6_markets.test.cjs
actionlint -shellcheck= .github/workflows/collect.yml .github/workflows/continuity.yml
PYTHONPATH=. python audits/reproduce-telegram-authorization-alert.py
```

Os testes de Git escrevem apenas em remotos bare temporários. A fixture prova a ausência de auto-inicialização, a rejeição de JSON inválido, o bloqueio de um restauro desatualizado visto de um checkout frio, a aceitação de avanços atómicos e a rejeição de eliminação e recuo sob uma regra local equivalente. Os testes de workflow exercitam o opt-in estrito, o diagnóstico com a variável desligada, a visibilidade dos estados e a ausência de espera perante um worker bloqueado.

## Plano do ensaio controlado dos cinco minutos

O ensaio fica por executar e exige autorização expressa para SHA, repositório de ensaio, runner, referência, janela e destinatário. Primeiro provar, em Git local, todos os casos de perda, restauração e concorrência. No ensaio autorizado, usar um workflow manual isolado num repositório de teste, com o mesmo tipo de runner e a política equivalente, duas referências isoladas e pré-inicializadas e o token desse workflow. Encaminhar a constante das referências apenas pelo harness de ensaio. Confirmar o destino antes da execução e a recusa de eliminação e recuo pelo mesmo perfil de limpeza previsto para o caso real.

Injetar uma deteção sintética e usar bot e chat exclusivos de teste, sem cliente MTProto nem sessão de produção. Prefixar a mensagem com `[TESTE AUTORIZAÇÃO TELEGRAM — SEM INCIDENTE REAL]` e um identificador de correlação. Medir desde t0 até handoff, reserva confirmada, tentativa, confirmação API e observação no chat. Exercitar o reader, as quatro rondas do watch, a última janela antes da libertação do job, concorrência, reinício, recuperação, perda de resposta e falha de publicação. Para falhas induzidas ambíguas, usar transporte sintético.

Aceitar entrega no prazo apenas quando cada caso elegível for observado no chat até 300 s após t0, com uma mensagem por incidente e uma nova identidade só depois de recuperação comprovada. Os casos rejeitados, incertos, omitidos ou fora de prazo ficam explícitos. Parar perante destino real, mensagem sem marca, custo, perda de estado ou duplicação. Terminar o ensaio mantendo o mecanismo operacional desligado. O prazo e a sobrevivência entre passos do runner alojado continuam por certificar.

## Rollback

Manter ou repor a variável em `off` numa futura intervenção autorizada e reverter o commit da correção caso se pretenda retirar o código. Um rollback para um SHA que arranque o worker sem opção exigiria desativar essa via primeiro. Preservar ambas as referências e o último tip confirmado. Nesta intervenção não foram criadas nem protegidas referências reais, nem executado o ensaio.
