# PR F · Sucessor científico v3.2

**GO técnico para investigação offline. NO-GO para confirmação com os dados existentes, ativação da fase 1 ou execução.**

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

A PR F assenta na PR #86, commit `ce7629a11aefe0b655893ffab5390427d25fc1de`. A definição `temporal-risk-set-v3.2` tem SHA-256 `28f5e47f23e052e55d4a75481cdc23a0b9b1611e3e441933038ffaa01ed9c8ad`, fixado em teste. O plano congela também o intérprete, a lista de hipóteses, o corte e o hash da descoberta. A v3.1 continua consultável por `registry --version 3` e reproduzível pelo código arquivado. A CLI bloqueia novos registos e devolve apenas histórico em `evaluate --version 3`. Os ficheiros dos motores e protocolos congelados conservam os seus bytes. [Auditoria da PR #86](../quote-observability/AUDIT-PR86.md)

## Estado no instante da decisão

Os dados entram pela disponibilidade do recibo. O motor consolida primeiro todo o lote com carimbo `t` e associa os jogos por equipas ordenadas, competição e início exato, com prova disponível. Uma contradição de identidade bloqueia o jogo a partir do recibo em que ficou conhecida. Duplicados partilham uma impressão digital sem o ID da mensagem, o número da linha ou o hash físico desse registo. Para cada contrato, usa-se a última emissão disponível. Um intervalo explícito, inclusive com dois limites iguais, mantém a sua forma. Preços contraditórios na mesma emissão ficam indeterminados. Uma alteração conhecida de marcador invalida uma cotação LIVE que represente o marcador anterior.

O terminal da sequência é a oportunidade em `t`. Conserva os atributos da cotação utilizável, os limites de frescura e o relógio físico para velocidades de preço. O prefixo inclui todos os eventos observáveis emitidos ou publicados antes de `t` dentro da janela, inclusive depois dessa cotação. A própria cotação reutilizada como terminal fica fora do prefixo. Dois eventos com emissão idêntica não formam uma sequência estritamente ordenada. Podem, porém, estar simultaneamente disponíveis para a comparação entre jogos.

| Estado do antecedente | Condição |
| --- | --- |
| Presente | Há testemunhas observadas para todos os passos e relações. |
| Comprovadamente ausente | A pesquisa termina sem testemunha nem caminho irresolúvel e há prova de cobertura contínua de toda a janela. |
| Indeterminável | Falta cobertura ou informação relevante, há ambiguidade no estado ou a pesquisa ficou incompleta. |

Os segmentos de cobertura são específicos do jogo e das categorias de evidência, vêm de um adaptador de confiança e requerem disponibilidade até `t`. Contagens no prefixo e persistência entre duas observações também exigem cobertura. A prova refere-se ao universo observável fixado pelo protocolo. Os recibos da página Telegram e o diário da fase 0 documentam observações individuais. A adaptação offline dos ficheiros existentes não fornece prova de continuidade e a CLI não aceita cobertura declarada à mão.

Todos os casos de um lote são identificados antes de formar o conjunto de candidatos a controlo. Cada jogo só pode ser reservado uma vez por hipótese. Comparam-se o contrato nativo exato, o relógio e a odd. A alocação usa competição, distância, idade da cotação e desempate por SHA derivado da identidade semântica. A ordem de alocação dos casos é canónica e o relatório interno conserva as arestas admissíveis do lote antes dessa alocação. A falta de orçamento descarta as decisões daquele lote. [Definição versionada](../../scangoal/discovery/v32/protocol.py)

## Descoberta e seguimento prospetivo

O minerador mantém as projeções determinísticas da v2 e gera trajetórias adicionais a partir dos eventos reconhecidos. O vocabulário tipado permite conjunções e relações não adjacentes sobre alertas, odds, montantes, golos e marcadores, incluindo contextos de revisões que apenas passam a existir quando recebidos. A LLM local opcional pode propor sequências novas dentro da gramática geral e sugerir uma refutação. O teste usa uma resposta capturada que combina golo, volume acumulado, marcador e preço. Não houve avaliação de desempenho de um modelo real.

Os resultados ficam fora do estado e da geração de hipóteses. O ranking para registo usa apenas suporte emparelhado observável. O registo é append-only com domínio RFC 3161 próprio. O testemunho independente é anterior ao início da janela, que começa na primeira meia-noite UTC seguinte ao testemunho e ao corte de descoberta. A avaliação exclui jogos conhecidos no corte de descoberta e usa janelas de antecedentes inteiramente contidas no período prospetivo. Os resultados só são abertos para avaliação após a janela e o prazo de liquidação. Um checkpoint externo da cadeia é necessário para detetar truncagem do registo. Os limiares, a correção de multiplicidade e as camadas de previsão e retorno anunciado da v3.1 são reutilizados. A calibração dessas camadas após a nova seleção ainda exige avaliação independente com cobertura comprovada.

## Reavaliação histórica

Corte de 24 de novembro de 2026 às 08:22 UTC. O replay arquivado reproduziu 54 hipóteses, 1.851 casos e 13 emparelhamentos, correspondentes a cinco identidades de par. Mantendo os jogos dos pares originais e reconstituindo as oportunidades em `t`:

| Classificação | Emparelhamentos | Identidades de par |
| --- | ---: | ---: |
| Exclusão justificada por antecedente já presente no controlo. A testemunha foi emitida depois da cotação antiga. | 3 | 1 |
| Antecedente indeterminável por falta de prova de cobertura. | 10 | 4 |
| Pares admissíveis para confirmação v3.2. | 0 | 0 |

Ambos os braços dos treze emparelhamentos tinham recibos antigos. A tabela caracteriza o histórico usado no desenvolvimento. A CLI v3.2 gera 57 hipóteses exploratórias no mesmo corte, com 24 projeções de trajetórias tipadas e 33 do minerador anterior. Nenhuma cumpre o mínimo para registo. [Saída histórica](historical.json)

## Banco adversarial e reprodução

O banco usa jogos e relógios sintéticos próprios, cinco sementes e 20 cenários de falsificação. Cinco cenários admitem um par e quinze recusam-no. Cada cenário é reconstruído com ordens, IDs, linhas, hashes de prova e duplicados variados. O banco exige invariância das arestas e da reserva e volta a avançar o corte para verificar que recibos posteriores não antecipam decisões. O conjunto de testes também cobre o diário realista com provas sintéticas, conflituosidade LIVE, grafo de dois casos simultâneos e um controlo, orçamento parcial, vocabulário ampliado, ausência de cobertura e fecho do plano prospetivo. [Resultados do banco](bank.json)

```sh
PYTHONPATH=. python audits/temporal-discovery-v32/historical.py --verify-frozen > /tmp/v32-historical.json
cmp /tmp/v32-historical.json audits/temporal-discovery-v32/historical.json
PYTHONPATH=. python audits/temporal-discovery-v32/bank.py > /tmp/v32-bank.json
cmp /tmp/v32-bank.json audits/temporal-discovery-v32/bank.json
python -m scangoal.discovery discover --version 3.2 --as-of 2026-11-24T08:22:00Z
python -m unittest discover -s tests -p test_discovery_v32.py -v
node --test ops/continuity/watchdog.test.mjs
git diff --check ce7629a..HEAD
```

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
