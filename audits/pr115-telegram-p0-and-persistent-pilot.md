# PR 115 Diagnóstico P0 e plano de piloto de recolha persistente

## Comportamento da draft

O reader grava as mensagens, o controlo da janela e o recibo da leitura principal antes de tentar o backfill. Uma revogação no backfill preserva o cursor pendente e termina com código 4. O watch mantém a espera da janela e termina com código 3 perante uma revogação. Ambos consultam a exceção interna do Telethon 1.45.0 antes e depois de desconectar. Uma falha de encerramento normal conserva a exceção primária da operação. O cancelamento do chamador propaga-se. Uma revogação observada em segundo plano continua a produzir o diagnóstico próprio nestes casos.

O resultado do passo `authorization=lost` descreve a perda de autorização observada naquela execução. É independente do `last_run_utc` persistido e da conclusão agregada. A ausência do resultado indica que o processo não registou uma perda definitiva. O encerramento pode terminar antes da emissão perante um kill abrupto.

No workflow, o wrapper do reader só tolera o código 4 com o respetivo resultado de autorização. Os outros códigos de falha continuam a chegar ao job. O watch continua tolerado. O resumo e a anotação identificam a degradação sem induzir um restart para um incidente exclusivamente de autorização. A publicação e o despacho conservam as condições e a ordem existentes. O observador de frescura e o mecanismo de continuidade mantêm a lógica atual. Podem agir sobre uma interrupção efetiva da cadeia segundo os critérios anteriores.

O passo de aviso existente, após o despacho, também aceita a indicação de perda de autorização. A síntese antes do despacho permite distinguir a falha mesmo quando o aviso está indisponível. Não é necessário um passo final adicional que faça falhar o job. O estado do passo e a anotação do resumo já comunicam a degradação.

O aviso de autorização ignora a janela habitual dos primeiros dez minutos da hora. A reserva separada em `data/notify_auth_health.json` limita as tentativas a uma por hora deslizante. O push fast-forward da reserva precede o envio. Um checkout desatualizado, uma transação pendente ou uma publicação indisponível não autoriza um envio. Uma reserva confirmada em Git não é um recibo de entrega. Uma resposta do bot perdida ou uma falha após a reserva pode deixar o utilizador sem aviso nessa hora. O resumo continua a registar a degradação. Esta opção usa o mesmo canal e mecanismo de deduplicação já presentes e preserva a proteção de publicação.

## Matriz de validação offline

| Fase | Cobertura e resultado esperado |
|---|---|
| Ligação e resolução | As classes RPC definitivas preservam a instância e registam a degradação. Um erro genérico de autorização não é revogação. |
| Leitura principal | A falha primária mantém a identidade com erro secundário de encerramento ou revogação assíncrona. Um erro transitório ou desconhecido não abre um incidente de autorização por si só. |
| Backfill | A revogação direta ou revelada por cancelamento interno da biblioteca devolve erro de autorização. Um erro transitório ou desconhecido mantém a página pendente. Os recibos e o histórico gravado são preservados. |
| Leituras rápidas | Uma revogação na primeira ou na última ronda impede que leituras anteriores deem código 0. Mantém-se a cadência e uma só ligação no cenário auditado. |
| Encerramento | Revogação direta no watch e revogação em segundo plano no reader e no watch dão saída não zero. A causa secundária é registada sem imprimir a mensagem RPC. |
| Cancelamento | O cancelamento externo durante a leitura ou o encerramento propaga-se. O diagnóstico secundário de autorização permanece visível. |
| Revogação assíncrona | Observação antes da leitura, após a última leitura e durante a desconexão. O teste do transporte usa a versão fixada de Telethon. |
| Continuidade e alertas | O wrapper é executado com códigos simulados. Um resumo degradado é produzido com agregado verde. Três checkouts com um remoto Git local provam uma única tentativa de envio para uma hora. |
| C01 e armazenamento | Suites do protocolo, histórico append-only, diário prospetivo, publicação, cadência e regressões de browser. Nenhum ficheiro científico ou dado histórico figura no diff. |

Com Python 3.11, dependências de `requirements.txt` e Chromium preparado offline, executar `python -m unittest discover -s tests -v`. Para a auditoria focada, usar `python -m unittest discover -s tests -p test_telegram_diagnostics.py -v` e `python -m unittest discover -s tests -p test_git_persistence.py -k ChainContinuityTests -v`. A suite JavaScript usa `node --test --test-isolation=none ops/continuity/watchdog.test.mjs tests/v6_core.test.cjs tests/v6_markets.test.cjs`.

## Plano documental para o piloto de 14 dias

**Decisão de alojamento.** Usar um anfitrião Linux que já funcione continuamente e tenha recursos disponíveis sem custo marginal adicional. Se não estiver disponível, usar uma única VM e um volume OCI Always Free elegíveis na região de origem, numa conta exclusivamente gratuita, após verificar quotas de disco, tráfego e capacidade. A recuperação de recursos inativos pela Oracle é um risco de indisponibilidade a contabilizar. Se nenhuma opção suportar 24/7 dentro de custo zero, não iniciar o piloto. [OCI Always Free](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)

1. **Antes do dia 1.** Ensaiar offline com um diário temporário e um relógio simulado. Injetar perda de rede, reinício do anfitrião, repetição de páginas, edições, cancelamento, revogação e FloodWait. Confirmar um único escritor, retoma do diário sem alterar o prefixo e respeito pelo prazo imposto.
2. **Preparação futura com autorização própria.** Criar uma sessão Telegram exclusiva do piloto numa preparação pontual. O serviço Linux seria supervisionado localmente, com uma instância e um lock no mesmo volume. A preparação pode ter verificação interativa pontual. A operação de 24 horas por dia não depende do telemóvel pessoal ligado. Uma revogação termina a instância e requer intervenção manual. O supervisor só reinicia falhas transitórias elegíveis e respeita FloodWait.
3. **Isolamento.** Manter a sessão apenas no anfitrião, com acesso restrito ao utilizador do serviço. Usar um diário append-only num volume do piloto com recibos UTC, cópia local e quota definida antes do arranque. Recolher sem escrever na origem. O serviço não recebe credenciais de escrita no GitHub e não alimenta publicação, alertas operacionais, resultados, C01 ou diário prospetivo.
4. **Dias 1–14.** Registar um heartbeat por minuto e, por leitura, disponibilidade, número e motivo de reconexões, classe e código RPC, FloodWait, lacunas, duplicados e atraso de receção. Comparar com a recolha GitHub por intervalos UTC e versões simultaneamente observáveis, usando apenas evidência publicada em leitura. Indicar o tamanho da amostra, p50 e p95 de atraso, interrupções de cada origem e diferenças de sessão, frequência e alojamento. Contabilizar interrupções do próprio anfitrião.
5. **Aceitação.** Exigir 14 dias com pelo menos 99% dos minutos esperados cobertos pelo heartbeat, custo adicional zero, zero sessões simultâneas no piloto, zero violações de FloodWait, zero reescritas do diário e zero escritas fora do perímetro. Para avançar com avaliação adicional de cobertura, exigir que a comparação com GitHub não revele menos versões observáveis no subconjunto comparável e que as discrepâncias tenham explicação documentada. Quaisquer lacunas ficam explícitas.
6. **Paragem imediata e rollback.** Parar à primeira cobrança, indicação de recurso pago, revogação, perda do diário ou perda de isolamento. Parar o serviço, revogar apenas a sessão do piloto e preservar as evidências autorizadas. Se tiver sido usada uma VM, remover apenas os recursos do piloto após conservar a evidência. A cadeia GitHub fica fora do rollback.

Um piloto de 14 dias avalia viabilidade e estabilidade observada. Para certificação estrutural, manter pelo menos 24 dias de observação na via observacional ou demonstrar a causa e a respetiva eliminação. A ausência de recorrência durante o piloto não identifica a causa da invalidação.

## Rollback da draft

Reverter o commit desta correção no ramo da PR 115 repõe o diagnóstico e as condições anteriores. O módulo isolado da PR 114 permanece congelado. Não foi provisionado alojamento, ativado um aviso ou alterada a produção durante esta validação.
