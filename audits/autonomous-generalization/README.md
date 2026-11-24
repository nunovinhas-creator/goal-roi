# PR H · Descoberta autónoma e generalização

**GO para investigação offline. NO-GO para generalização sintética e confirmação prospetiva.**

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

O sucessor experimental lê a fita de observações tipadas da v3.2 e propõe regras temporais sem rótulos, resultados, hipóteses introduzidas pelo utilizador ou fontes externas. O ensaio conservou a PR #88 e os 31 ficheiros do intérprete congelado. A decisão distingue a recuperação de um padrão sintético da capacidade de o transferir e da eventual validade científica com dados reais. [Resultados reproduzíveis](report.json)

## Auditoria de causas

| Limite da PR G | Evidência |
| --- | --- |
| Confusão entre descida e subida e entre períodos | A regra escolhida no diagnóstico histórico tem quatro passos com predicados de fonte e marcador e três ligações adjacentes de igualdade da linha. A igualdade da linha é compatível com preços em sentidos opostos e com períodos diferentes. Não há relação de variação de odds nem teste de período nessa regra. A primitiva de preço da v3.2 já consegue verificar fonte, seleção e período. |
| Terminal certo com caminho incompleto | Os 47 candidatos na reprodução histórica têm dois a quatro passos. O terminal correto da regra selecionada pode ser precedido apenas pelas fontes e marcadores requeridos. Os eventos de golo e a publicação do marcador podem ficar fora da testemunha. |
| Relações não adjacentes raramente sobrevivem em conjunto | O ramo tipado enumera pares de posições, mas a projeção emite isoladamente uma característica ou uma relação sobre a estrutura. Os 24 candidatos tipados do diagnóstico têm quatro passos e no máximo um predicado ou uma relação adicional. A shortlist desse ramo privilegia suporte e comprimento. |
| Perda de passos e distinções pelo ramo legado | A base de trajetórias usa sufixos recentes em três filtros de mercado. Um evento intercalado pode deslocar um passo anterior. A expansão legada acrescenta uma condição de cada vez. A deduplicação compara conjuntos de jogos com limiar de sobreposição de 80%, pelo que caminhos distintos no mesmo jogo competem entre si. |
| Seleção histórica pouco sensível à testemunha | O diagnóstico G ordenava primeiro pelo terminal exato. A avaliação H regista todas as deteções e escolhe no desenvolvimento primeiro pela recuperação do caminho e depois pela precisão. O mesmo critério avalia os dois motores no novo banco. |

A execução voltou a obter **6 de 18 deteções corretas, 0 caminhos completos e 0 de 47 candidatos transferíveis** nos bancos originais da PR G. Esses bancos continuam a servir exclusivamente de diagnóstico. [Código da PR G](../offline-observation/README.md) · [Seleção histórica](report.json)

## Arquitetura experimental

- **Observações.** `receipt-trajectory-v1` usa o prefixo conhecido na receção de uma cotação elegível. Eventos com o mesmo instante de emissão são alternativas para a mesma posição. Um evento entregue depois de uma decisão fica fora dessa decisão. A comparação de preços e volumes exige a identidade exata do contrato. A variação de preço usa pontos explícitos e relógios de emissão distintos.
- **Estrutura e expansão.** `trajectory-recurrence-v1` enumera trajetórias de 2 a 8 passos, incluindo variantes com eventos intercalados. Mede a recorrência em jogos e dias distintos. Propõe conjunções a partir de campos tipados e relações entre quaisquer posições. Para razões e deltas usa a referência neutra da unidade, e para valores contínuos usa limiares retirados das observações de desenvolvimento.
- **Pesquisa.** O feixe hierárquico intersecta conjuntos de testemunhas e mantém uma alternativa composta quando o suporte permite. O ranking combina recorrência, raridade condicional e dimensão da trajetória. Até 24 propostas são devolvidas por treino. A deduplicação elimina características e regras com testemunhas equivalentes dentro da amostra. Essa equivalência empírica pode perder distinções relevantes fora da amostra.
- **Refutação.** Uma variante inverte uma cláusula conhecida sem alterar o terminal. O relatório detalhado inclui as regras, variantes e impressões digitais dos contraexemplos observados no desenvolvimento. Predicados sem dados ficam indetermináveis. A ausência exige cobertura.
- **Limites.** O algoritmo fixa 1,8 milhões de operações de mineração por treino, 384 âncoras, 12 lotes por trajetória, 8 variantes por comprimento e largura 12 no feixe. A contagem inclui observações, variantes, avaliação de características e expansão. Uma execução que atinge o limite de trabalho é assinalada como incompleta. A pesquisa dentro dos limites de largura e recência continua aproximada.

### Defeitos reproduzidos e tratados

1. **Volumes contraditórios.** O adaptador mascara os campos contraditórios e impede que o intervalo afetado prove ausência. Contagens com unidades contraditórias ficam também indeterminadas. O preço continua observável quando coincide. A máscara entra no instante do recibo contraditório e os testes demonstram que a decisão anterior é preservada.
2. **Aborto com golos.** O teste reproduz `invalid_literal` na entrada congelada da v3.2. O sucessor investiga apenas eventos tipados, sem converter golos para a estrutura legada que exigia `signal_type`. O teste verifica candidatos que mantêm o golo na trajetória.

## Avaliação independente

O commit `85c3c7f` fixou o código e os critérios antes da criação dos bancos. O avaliador separado em `banks.py` e `run.py` foi fixado em `c888ba2`. O minerador recebe apenas a fita de desenvolvimento. O avaliador detém as etiquetas, seleciona pelas métricas de desenvolvimento e sela os IDs antes de construir os dados de seguimento. O mesmo matcher experimental e as mesmas decisões por cotação avaliam as propostas do sucessor, da PR G e, se disponível, da LLM. A reprodução histórica também executa a avaliação original da PR G. [Bloqueio prévio](freeze.json)

Há três mecanismos sintéticos, duas sementes de desenvolvimento e três de seguimento por mecanismo. Cada banco de seguimento contém 9 trajetórias-alvo e 18 contraexemplos distribuídos por seis mecanismos. Foram usados jogos, datas, preços e volumes novos, deslocamento no regime de preços e eventos distractores. Uma deteção válida exige o contrato e o instante certos. A recuperação completa também exige cada evento da trajetória etiquetada, por ordem.

| Cenário | Motor | Precisão no terminal | Recuperação do terminal | Recuperação do caminho |
| --- | --- | ---: | ---: | ---: |
| Inversão pré-jogo e volume acumulado | PR G | 54/144 | 54/54 | 54/54 |
| Inversão pré-jogo e volume acumulado | PR H | 16/40 | 16/54 | 16/54 |
| Inversão ao vivo com golo do empate | PR G | 4/13 | 4/54 | 0/54 |
| Inversão ao vivo com golo do empate | PR H | 44/87 | 44/54 | 44/54 |
| Pressão de volume e subida da cotação ao vivo | PR G | 14/40 | 14/54 | 0/54 |
| Pressão de volume e subida da cotação ao vivo | PR H | 51/105 | 51/54 | 51/54 |

Os denominadores da tabela abrangem seis avaliações por mecanismo. As duas regras pré-selecionadas em sementes de desenvolvimento diferentes são aplicadas aos mesmos três bancos de seguimento. O sucessor recuperou **111/162 caminhos**, face a **54/162** da PR G, mas o ganho agregado concentra-se nos cenários ao vivo. O candidato pré-jogo de uma semente de desenvolvimento perdeu todos os terminais em dois bancos e acertou só um no terceiro. As regras escolhidas mudaram entre sementes nos três cenários.

- **Diversidade.** O sucessor devolveu 24 candidatos por treino, com trajetórias de 3 a 8 passos, até 14 predicados e ligações que saltam até seis eventos. As regras usam, entre outros, deltas de odds, variação do volume acumulado, razão de volume e mudanças da diferença de golos. A PR G devolveu de 40 a 51 candidatos nestes mesmos treinos.
- **Contraexemplos.** O sucessor não detetou 84/108, 65/108 e 54/108 dos jogos de controlo nos três cenários, respetivamente. Todos esses jogos incluem decisões indetermináveis. São abstenções face à falta de cobertura sintética contínua. Ainda ocorreram falsos positivos como subidas terminais pré-jogo, marcadores contraditórios, alertas fracos e períodos incompatíveis em alertas.
- **Invariância.** Permutar linhas, mudar IDs, repetir recibos, acrescentar campos de resultado inertes e adicionar eventos para lá do corte preservou todos os conjuntos de propostas do sucessor e todas as medições das regras escolhidas. A estabilidade perante ruído de transporte coexistiu com variação entre sementes de desenvolvimento.
- **Consumo.** A mineração usou entre 1.323.852 e 1.799.995 operações. Os dois treinos do cenário do empate esgotaram o orçamento, enquanto todas as avaliações das regras escolhidas terminaram. Uma execução local mediu 8,22 s para as seis chamadas iniciais ao sucessor e 2,84 s para as seis chamadas ao motor anterior. O processo completo atingiu cerca de 44 MiB de memória residente. As unidades de trabalho internas dos motores têm definições diferentes. `--profile` repete a medição na máquina de execução.
- **LLM.** A máquina não tinha um executável local compatível e um modelo GGUF disponíveis. A comparação ficou sem propostas e métricas de inferência. O percurso opcional local submete as propostas ao mesmo validador tipado e ao mesmo avaliador.

Os gates congelados exigiam pelo menos **90% de precisão**, **80% de recuperação do terminal** e **75% de recuperação da trajetória** em todos os bancos, invariância e pesquisa concluída. **Nenhuma proposta do sucessor ou da PR G** atingiu os três mínimos em todos os bancos de seguimento do seu cenário. O algoritmo permanece selado. Fica reservada uma avaliação posterior com mecanismos, jogos e sementes próprios, a gerar pelo avaliador depois de selar uma eventual revisão. Continuam por demonstrar a transferência para novos mecanismos e dados reais, a continuidade das fontes, a potência prospetiva e qualquer rentabilidade.

## Reprodução offline

Na raiz do repositório:

```sh
PYTHONPATH=. python audits/autonomous-generalization/run.py --deterministic-only > /tmp/goal-roi-h.json
cmp /tmp/goal-roi-h.json audits/autonomous-generalization/report.json
PYTHONPATH=. python audits/autonomous-generalization/run.py --details --profile > /tmp/goal-roi-h-details.json
PYTHONPATH=. python audits/autonomous-generalization/investigate.py --deterministic-only > /tmp/goal-roi-h-journal.json
python -m unittest discover -s tests -p 'test_*generalization*.py' -v
node --test ops/continuity/watchdog.test.mjs
```

A saída detalhada inclui os candidatos e os resultados por semente e família de contraexemplos. `investigate.py` deduz o corte do último recibo completo verificável em `data/messages.jsonl` e gera propostas exploratórias em leitura. No teste com o ficheiro local, analisou 78 âncoras e apresentou duas propostas parciais ao chegar ao limite. Os testes verificam hashes, reprodução, conflitos tardios, períodos e contratos incompatíveis, eventos simultâneos, limites de pesquisa e isolamento da rede. O percurso experimental fica fora das entradas operacionais.

O `cmp` reproduz o relatório integralmente no CPython 3.12 e 3.13. O CPython 3.11 usa uma soma em vírgula flutuante diferente e desempata algumas expansões internas noutra ordem. Os sete contadores de trabalho que variam nesse ambiente estão registados para a comparação exata na CI. As regras escolhidas, as métricas dos bancos e o veredito coincidem. O algoritmo selado foi preservado. [Contadores do CPython 3.11](runtime-counters.json)
