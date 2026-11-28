# Aviso por incidente de autorização Telegram

## Âmbito

Esta draft parte de `496354b2f391d0b9cf287a1008e030d39228a4ae`, o SHA certificado da PR #115. O reader e o watch conservam a classificação, as exceções e os resultados da auditoria anterior. Acrescentam uma entrega local do evento ao processo de aviso no ponto de deteção. Nas rondas do watch, a entrega ocorre antes da atualização final e da espera de cadência. Na perda durante backfill ou desconexão, o mesmo diagnóstico emite o evento. O processo usa o canal Bot API existente, independente da sessão de leitura.

O aviso pode começar enquanto decorrem a janela do watch, a publicação e o despacho. O passo final apenas recolhe o resultado do processo, após o despacho. Em condições normais termina assim que esvazia a fila. Em caso de atraso, pode acrescentar até 65 s à libertação do grupo de concorrência do workflow. Esta espera dá ao processo uma oportunidade de terminar quando a deteção ocorre na última ronda. O timeout global ou um cancelamento continuam a poder terminar o processo primeiro.

## Identidade e sequência

O processo persiste um identificador aleatório por incidente numa referência Git operacional própria, `goal-roi-telegram-auth-alert-state`, que contém apenas metadados. Cada transição acrescenta um commit à referência observada. Um push com lease explícito impede dois processos de reclamar a mesma tentativa. O checkout, o índice, a publicação científica e a referência `main` ficam fora destas operações. A referência de estado só é criada por uma futura execução autorizada que observe um incidente.

| Estado | Evidência e ação |
|---|---|
| Deteção | Hora UTC da observação e classe RPC segura. Novas observações durante o incidente conservam a identidade. |
| Reserva | Registo publicado na referência. Qualquer processo pode prosseguir de uma reserva ainda sem tentativa. |
| Tentativa | Uma segunda transição durável reclama a tentativa antes de chamar o bot. Só o autor da transição confirmada pode chamar o transporte. |
| Confirmada | A operação Bot API devolveu confirmação positiva. O estado guarda a hora de início da tentativa e a conclusão. |
| Rejeitada | O transporte demonstrou que a mensagem não foi entregue. O incidente fica aberto e exige intervenção manual ou recuperação. |
| Incerta | O bot pode ter recebido a mensagem. A tentativa permanece consumida. Uma reclamação de tentativa sem resultado durável é tratada como incerta para efeitos de reenvio. |

Uma leitura principal ou rápida concluída, iniciada depois de obter o identificador do incidente que pretende fechar, fornece a prova de recuperação por um pedido autenticado já existente. O encerramento só é emitido após concluir o diagnóstico da sessão sem nova revogação. A comparação do identificador impede que uma prova antiga encerre um incidente novo. Eventos de perda anteriores ao último encerramento são descartados pela hora da observação. A ordenação de eventos atrasados entre hosts depende de relógios UTC suficientemente sincronizados. As execuções normais de recolha mantêm a exclusão do workflow. O erro recuperável de um endpoint, a substituição de credenciais por si só e uma leitura iniciada antes de conhecer o incidente deixam o estado aberto.

O alerta de falha do despacho continua a usar o mecanismo genérico e a respetiva reserva. Pode ser enviado mesmo que o incidente de autorização já tenha tido uma tentativa, sujeito às proteções de publicação do aviso genérico.

## Falhas e fronteira dos cinco minutos

- **Publicação do relatório.** A referência de incidentes é independente da transação de publicação. Um erro do relatório ou uma transação pendente não é uma condição de bloqueio do processo de autorização.
- **Publicação da reserva.** Se Git estiver indisponível, o processo regista a falha e volta a tentar publicar enquanto houver margem no job. A chamada ao bot exige confirmação da reclamação da tentativa. Se o runner desaparecer antes de guardar a deteção, uma nova observação terá de criar a evidência durável.
- **Bot indisponível.** Sem configuração do canal, o incidente permanece elegível para uma execução posterior. Uma rejeição de um envio efetivamente tentado fica registada, com resolução manual. O processo conserva uma única tentativa automática por incidente.
- **Confirmação perdida ou cancelamento.** Depois da reclamação da tentativa, uma falha ambígua permanece incerta, sem reenvio automático. Se já houve confirmação do bot mas falhou a sua publicação, o processo repete apenas a escrita do resultado enquanto estiver vivo. Uma paragem abrupta pode deixar a reclamação sem resultado definitivo. O canal não tem uma chave de idempotência que permita garantir uma entrega exatamente uma vez.
- **Prazo.** O teste local mede a passagem imediata do evento e a tentativa sem depender de watch, relatório ou despacho. O prazo de cinco minutos em produção depende da disponibilidade da referência Git, do bot e de tempo restante no runner. As permissões reais da referência, a latência externa e a entrega ao utilizador ficaram fora da validação offline. Em particular, indisponibilidade simultânea do Git e do bot torna impossível garantir o prazo com a infraestrutura existente.

## Testes e reprodução

Com as dependências de `requirements.txt` e telemetria de rede externa desativada:

```sh
python -m unittest discover -s tests -p test_authorization_alerts.py -v
python -m unittest discover -s tests -v
node --test --test-isolation=none ops/continuity/watchdog.test.mjs tests/v6_core.test.cjs tests/v6_markets.test.cjs
PYTHONPATH=. python audits/reproduce-telegram-authorization-alert.py
actionlint -shellcheck= .github/workflows/collect.yml .github/workflows/continuity.yml
```

Os testes focados cobrem deteção no reader, nas quatro rondas, no backfill e na desconexão. Exercitam 48 h, recuperação seguida de nova revogação, falta de reserva, falhas de publicação e envio, confirmação perdida, cancelamento, processos concorrentes com clones de um Git bare e falha simultânea do despacho. O teste temporal usa relógio virtual para localizar a deteção do watch em 120, 240, 360 e 480 s desde o início da janela. A medição de parede do ensaio independente usa Git bare local, dois processos e um remetente sintético, com tempos relativos à primeira deteção. O ensaio local mediu 0 ms na deteção, 550,4 ms até à reserva confirmada, 578,2 ms até à chamada e 578,2 ms até à confirmação sintética, à precisão apresentada. As suites completas preservam os testes de publicação, histórico, diário prospetivo e C01.

## Rollback

Fechar a draft, ou reverter o commit deste aviso numa intervenção autorizada, repõe a versão certificada da PR #115. Se o mecanismo vier a ser usado no futuro, preservar a referência de incidentes durante rollback e eventual reativação, porque nela estão as tentativas já consumidas. Esta intervenção não provisionou essa referência nem iniciou o piloto persistente.
