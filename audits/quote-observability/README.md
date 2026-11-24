# PR E · Fase 0 · Observabilidade offline

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

A fase 0 transforma o jornal existente em evidência tipada para futuros motores de descoberta autónoma. O diagnóstico volta a gerar as hipóteses pelo minerador determinístico da v2 e usa o emparelhador e a cascata congelados da v3.1. A base desta PR é o commit `64015624b7d82f3d049929db0003f0570a9972de` da PR #85. [Especificação](../temporal-discovery-v3/PR-E-SPEC.md)

## Reprodução

Na raiz, com o snapshot de `data/` desse commit:

```sh
PYTHONPATH=. python audits/quote-observability/diagnose.py --verify-frozen --summary > /tmp/quote-observability.json
cmp /tmp/quote-observability.json audits/quote-observability/snapshot.json
```

Sem `--summary`, o comando devolve o inventário de todos os contratos e as contagens de chegadas por hora UTC. Para outro snapshot, indicar `--as-of` com fuso explícito e omitir `--verify-frozen`. O comando lê os ficheiros disponíveis e gera as hipóteses sem introdução manual. O JSON versionado contém agregados, hashes de entrada, do protocolo e do snapshot, e identificadores de contrato e hipótese. Os rótulos livres de mercado e seleção ficam de fora do relatório público. [Resultados](snapshot.json)

## Cobertura medida

Corte de 24 de novembro de 2026 às 08:22 UTC. SHA-256 da reprodução v3.1 `1f2bf65d751dc735d2a0688c700e7ff24ad9b41474e87f7768efc38c9e102944`.

| Medida | Observado |
| --- | ---: |
| Alertas originais com observação | 3.654 |
| Contratos exatos em todos os alertas originais | 448 |
| Contratos exatos nas âncoras da auditoria congelada | 203 |
| Preços explícitos recuperados onde a auditoria tinha preço indeterminado | 348 |
| Versões ligadas a jogos com cronologia de golos | 1.326 |
| Eventos distintos de golo reportados por jogo, minuto e marcador | 1.464 |
| Versões ligadas a jogos com maior bilhete e contagem | 4.091 |
| Versões ligadas a jogos com volume acumulado | 2.167 |
| Âncoras com liquidação sem suporte | 209 |
| Decisões de caso em 54 hipóteses, 505 únicas | 1.851 |
| Pares estritos históricos com ambos os braços liquidados | 13 |
| Identidades de par distintas nesses 13 emparelhamentos | 5 |
| Pares na janela de recibos completos | 0 |

Os 348 preços recuperados vêm de 271 alertas Betfair e 77 Polymarket. São 326 pontos e 22 intervalos adicionais no inventário de texto. Uma linha de cotação pode ser legível com mercado ou identidade do jogo ainda por provar. Os intervalos continuam a ter ambos os limites, incluindo 11 faixas Polymarket que foram publicadas com extremos iguais. Quando o texto e o parser não sustentam um preço fica `indeterminate`. Os 13 pares históricos e a cascata foram reproduzidos com o protocolo original. As categorias da cascata são uma atribuição retrospetiva e algumas consultam o snapshot além do instante do caso. O campo `v3_waterfall_stage_basis` assinala essa limitação.

Nas 3.654 observações, 2.242 alertas já tinham maior bilhete e contagem na versão inicial, e 1.196 já tinham volume acumulado. Das 403 versões com recibo completo e ligação ao jogo, 90 tinham cronologia de golos, 313 tinham contagem e maior bilhete e 128 tinham acumulado. As contagens distinguem apostas de negócios. Os totais são agregados publicados por desfecho e jogo e podem reaparecer em revisões. Uma cronologia esparsa ou revista descreve o que o bot reportou com o respetivo recibo.

Entre 1.807 âncoras com contrato exato, há 1.316 combinações contrato–hora, 207 com mais de um jogo. Em 502 instantes de referência havia outro jogo com o mesmo contrato e cotação emitida na última hora. Em 224 instantes havia um início a ±45 minutos e em 80 os dois preços eram pontos explícitos, sem conflito visível e com odd pelo menos igual ao mínimo. A medição original contava 82. A versão auditada conserva a cotação mais recente por emissão e trata conflitos no instante em que se tornam disponíveis. O preço do caso tinha idade mediana de 363 segundos. A mediana do comparador efetivamente usado foi de 969 segundos. As métricas de densidade agregam chegadas e precedência. A correspondência LIVE, a reserva única e a ausência do antecedente são avaliadas pela cascata v3.1.

## Consumo por motores de descoberta

`scangoal.observability.read_timeline` devolve `QuoteObservation` e `ContextObservation`. O histórico de um jogo aceita instante, início de janela e filtro por recibo completo. `context_at` devolve o último estado de publicação determinável por mensagem, incluindo os campos presentes nessa versão. O histórico conserva as revisões observadas, o que permite descobrir relações entre mudanças, atrasos e sequências. As observações de marcador distinguem cabeçalho do alerta, golos reportados e linha de FT.

O relógio de conhecimento é o fim da página com recibo comprovado ou o limite superior de cinco minutos sobre o carimbo antigo. A cada leitura correspondem o hash do texto e do registo, o identificador da mensagem, a emissão e a publicação da versão. Um motor pode juntar sequências pela disponibilidade, filtrar por contrato e comparar os limites de preço, as medidas em USD e o marcador reportado. O minuto desportivo descreve o evento, enquanto o recibo determina quando pode entrar numa hipótese. FT é uma categoria própria e a seleção de antecedentes deve respeitar a sua finalidade e o instante de decisão.

`contract_key(event)` usa a definição exata da v3.1, incluindo fonte, fase, mercado nativo, período, linha e seleção. A tabela de equivalências serve para agrupar estatísticas entre fontes. A resolução de identidade é conservadora e as exclusões ficam no diagnóstico. Campos ausentes permanecem desconhecidos. Revisões que contradizem a identidade original ou mudam de fonte ficam sem ligação ao jogo. Uma revisão já disponível substitui o estado anterior mesmo na consulta de recibos completos. O estado atual fica indeterminado até existir um recibo completo da revisão.

## Potência

A calculadora repete o calendário medido de pares estritos de cada hipótese e família, incluindo dias vazios, a partir do primeiro dia UTC com recibo completo. Conserva preços anunciados, calendário de início, competições, dados de resultado em falta e devoluções medidas. Nos filtros de planeamento, os dois braços exigem preço pontual explícito, identidade verificada e ausência de novo alerta no histórico do controlo entre a sua cotação e o instante do caso. O efeito simulado acrescenta +4, +8 ou +12 pontos percentuais à probabilidade de vitória da parte decisiva do caso, com probabilidades de base dadas pela cotação. A simulação assume resultados independentes condicionais à configuração e usa o núcleo de `power_v3.py`, a inferência congelada e o limiar de duas camadas para uma única hipótese na primeira vaga. O horizonte e as repetições são configuráveis com `--days` e `--repetitions`. O primeiro horizonte testado com estimativa de pelo menos 80% é apresentado juntamente com a retenção e os intervalos de Wilson. Linhas de quarto exigem um modelo de resultados compatível com a liquidação. A calculadora assinala-as como não estimáveis, tal como efeitos que implicariam uma probabilidade acima de um nas odds observadas.

Há dois dias de exposição com recibos completos e zero pares comparáveis nessa janela. Repetir esse calendário resulta deterministicamente em zero pares a 90, 180 e 365 dias. A potência prospetiva a uma cadência futura é indeterminada, com zero retenção apenas no cenário de repetição observado. A estimativa de tempo para chegar a 60 pares e oito semanas de início fica em aberto. A amostra histórica de 13 dias teve no máximo um par por hipótese. A repetição desses emparelhamentos entre hipóteses e a cobertura contrafactual do relatório v3 ficam fora das entradas da potência.

## Verificação e eventual fase 1

O workflow existente de testes descobre `tests/test_quote_observability.py` com dados sintéticos. Os testes exercitam fontes, períodos, linhas asiáticas, valores incoerentes, moedas, recibos tardios, revisões em conflito, truncamento de evidência, potência e reprodução do comando. A verificação integral usa os dados congelados só localmente, pelo comando acima.

Uma eventual fase 1 requer autorização separada e PR própria. Começaria pela revisão dos termos e das restrições geográficas da fonte pública. Depois, a observação seria limitada a jogos e pares com início a ±45 minutos em total 2,5 e handicap ±0,5 pré-jogo, a cada 15 minutos entre T−6 h e o início. Usaria endpoints públicos de leitura, limites de pedidos e duração, um interruptor e um arquivo separado append-only com recibo e hash por resposta. Os testes verificariam zero ordens, ausência de segredos e notificações e identidade determinística. O resultado seria avaliado por pares comparáveis por dia e contrato, idade, identidade resolvida e cobertura de preço pontual com profundidade. Uma avaliação inferencial futura exige protocolo próprio e pré-registo.

[Auditoria adversarial da PR #86](AUDIT-PR86.md) · [Desenho para eventual fase 1](PHASE-1-DESIGN.md)
