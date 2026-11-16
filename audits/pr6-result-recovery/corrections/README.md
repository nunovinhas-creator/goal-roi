# Correções após auditoria independente — PR #29

Parecer atualizado: **APROVAR COM CONDIÇÕES para revisão técnica em Draft; ativação de produção continua bloqueada.**

Revisão corrigida: `bf1eb33adcf8541ec464123f218b204295e6e3d2`. Este relatório e os logs referem-se às correções posteriores dessa revisão. Não foi atualizado o snapshot de produção nem consumida quota HL/BSD. A base de dados do ensaio é a mesma do diagnóstico inicial, não o main vivo no momento desta correção.

## P1-A — Primeira observação de resultado final específico

Confirmado: a versão anterior usava `versions[0]`, que podia ser a observação de um jogo em curso. Além disso, qualquer mudança no evento gerava conflito, incluindo a progressão normal live → finished.

A correção identifica o resultado por ID do evento, equipas normalizadas, competição, início e marcador regulamentar validado. `matched_at_utc` é a primeira hora comprovada em que esse resultado final específico foi observado. Estados live e marcadores provisórios nunca qualificam. Sem carimbo final válido, o resultado não é aceite. Uma atualização de metadados com o mesmo resultado conserva a hora inicial; resultados finais diferentes ou retirada de resultado ficam em quarentena.

A observação inicial não é alterada. Todas as transições de payload são acrescentadas, incluindo o regresso a um payload antigo (por exemplo live → final → live). Só observações consecutivas idênticas são deduplicadas. O histórico de decisões preserva o valor antigo se um estado legado tiver o timestamp de aceitação corrigido.

`adversarial-proof.json` regista uma sequência **sintética**: T1 live sem final; T2 final 2–1 aceite com hora T2; T3 final 3–1, ambas as observações finais conservadas, aceitação removida e conflito persistente. Nenhum resultado 2–1 é conhecido em T1.

## P1-B — Retenção e revogação independentemente da janela

Confirmado: `accepted = {}` reconstruído só com alvos atuais perdia resultados ao sair da janela.

A correção conserva os resultados aceites e guarda `target_history` com as restrições de identidade/tempo. A janela limita o planeamento de pedidos, não a retenção de resultados. A reconciliação continua a verificar os alvos guardados e as linhagens dos eventos aceites, mesmo sem alvos atuais. Estados antigos sem `target_history` conservam as aceitações verificáveis e são revalidados pelas suas evidências existentes, sem inventar vínculos de identidade.

`decision_history` conserva cada decisão de aceitação, correção de proveniência e quarentena, sem duplicar decisões idênticas. Novos motivos de conflito acumulam-se sem apagar os anteriores. Um marcador corrigido, resultado retirado, identidade incompatível ou conflito entre fontes remove a aceitação e conserva a decisão anterior. A expiração, uma página em atualização ou a ausência de um evento numa lista não são, isoladamente, prova de invalidação.

O teste do dia 31 confirma que o jogo saiu da janela de 30 dias, grava o estado, inicia **outro processo Python**, retoma sem pedidos e preserva aceitação, observações, decisões e consumo. Há testes separados de correção/retirada após expiração, migração de estado legado e reexecução idempotente.

## P1-C — Filtro BSD e identidade da consulta paginada

Contrato oficial consultado em 16/11/2026: [BSD — List matches](https://sports.bzzoiro.com/docs/football/events/). A lista documenta `status`, incluindo `finished`, filtros de data e paginação `limit`/`offset` com máximo 200. O coletor original `results_bsd.py` já usa `status=finished` e permanece byte a byte inalterado.

`LiveFetch` da recuperação voltou a enviar `status=finished`. O teste intercepta o transporte e verifica a URL efetiva: `date_from`, `date_to`, `status=finished`, `limit=200`, `offset=200`, além da passagem da chave sintética pelo argumento do transporte.

Offsets de uma consulta sem filtro não são compatíveis com os da consulta filtrada. Páginas legadas sem a marca `query_filter=status=finished` são arquivadas em `page_history`; o cursor filtrado começa em zero, sem apagar observações, decisões, conflitos, tentativas registadas ou pedidos reservados. A migração pode exigir nova leitura inicial; não reaproveita falsamente a contagem de linhas antiga como pesquisa completa.

Impacto **apenas em fixture**: 400 eventos live + 1 terminado exigiriam 3 páginas de 200 sem filtro; com filtro, a resposta simulada devolve o evento terminado numa página, um pedido reservado e deteção do 2–1. Não se afirma poupança real. O filtro reduz o conjunto pesquisado; não permite concluir que um evento omitido foi cancelado ou que a fonte não o cobre. A validação local de estado e marcador mantém-se obrigatória.

## Testes executados

| Verificação | Resultado |
|---|---:|
| Novos testes adversariais desta revisão | 14 passaram |
| Total de testes específicos da recuperação | 53 passaram |
| Suíte completa `python scripts/test_offline.py` | 486 passaram |
| Pedidos reais HL/BSD / Telegram | 0 / 0 |
| Recuperações reais adicionais demonstradas | 0 |

Os 53 testes já estão incluídos nos 486; não são somados ao total. Logs completos: `recovery-tests.log` e `regression-tests.log`. A regressão bloqueia sockets; os testes de transporte usam mocks e chaves sintéticas. As mensagens NOTIFY/HL/BSD dos testes existentes são de simulações, não envios/consultas reais.

Cobertura nova: T1/T2/T3; live com marcador provisório; metadados alterados sem mudar FT; hora final ausente; dia 31 e reinício de processo; correção após expiração; migração de proveniência legada; retorno ao payload live anterior; acumulação de conflitos após expiração; identidade posteriormente incompatível; pesquisa parcial sem evidência invalidante; parâmetros efetivos BSD; redução de páginas em fixture; migração do cursor antigo e preservação do orçamento.

## Ensaio real e integridade

Reexecutado `scripts/recovery_rehearsal.py` com o relógio reproduzível de 16/11/2026 15:40 UTC. Usou uma cópia descartável do snapshot inicial. `rehearsal/integrity.json` verifica todos os ficheiros originais e copiados antes/depois. O censo gerado era byte a byte idêntico ao `../pending.json` e não é duplicado nesta pasta.

| Indicador | Antes | Depois |
|---|---:|---:|
| Apostas únicas | 602 | 602 |
| Liquidadas | 467 | 467 |
| Pendentes | 135 | 135 |
| Jogos distintos pendentes | 97 | 97 |
| Novos resultados reais comprovados | 0 | 0 |

`rehearsal/trial.json` contém a evidência real; `adversarial-proof.json` contém exclusivamente fixtures. Ganho de liquidação comprovado: **0 pontos percentuais**. Não há declaração de eficácia operacional.

`scope.json` comprova identidade dos dados, módulos científicos, Telegram, site e workflows face ao head anterior da PR. O código de execução alterado é exclusivamente `scangoal/recovery.py`; os outros ficheiros alterados são testes e documentação/evidências da recuperação. As reservas por dia/execução, reserva mínima, proteção 429, alocação explícita e staging isolado continuam em vigor e têm testes de regressão. A migração do cursor não devolve quota já consumida.

## Limitações e decisão

- **P1-A/B/C corrigidos e cobertos por testes offline.** Quarentenas persistentes não são desbloqueadas automaticamente; conflitos antigos sem prova suficiente continuam a exigir revisão.
- **Ativação bloqueada:** falta eficácia real demonstrada, integração cronológica revista e coordenação de quota com os produtores existentes. Não houve backfill real, alteração de secrets, merge ou modificação de produção. Todos os resultados permanecem isolados em staging.
- A retenção após 30 dias não constitui monitorização infinita de correções: fora da janela não são criados pedidos. Só nova evidência efetivamente observada permite detetar uma correção/retirada posterior. O filtro BSD não descobre por si estados não terminados; ausência numa lista filtrada não invalida nem confirma um resultado.
- A migração recupera apenas a evidência que existe. Sem observação final comprovada ou com identidade ambígua, falha de forma conservadora; não reconstrói horas históricas.
- Histórico de alvos, observações, páginas arquivadas e decisões cresce no diretório de staging; mantém-se a necessidade de retenção/arquivo operacional, sem apagar o histórico de auditoria.

**APROVAR COM CONDIÇÕES para continuar a revisão técnica em Draft. Não autorizar ativação de produção com base apenas nestes testes.**
