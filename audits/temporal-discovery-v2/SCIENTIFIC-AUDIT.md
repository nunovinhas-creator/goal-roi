# Auditoria científica adversarial da PR #84

**GO técnico para integração experimental. NO-GO para usar o protocolo v2 como validação confirmatória ou para executar apostas.**

A auditoria partiu de `d1db6fb2aa8213a9521ba40c7836d927343749b7`. A PR #83 continua a ser a dependência direta e mantém o HEAD `0798e69b27b6d131735737c3a7c2f33a501f8720`. É o primeiro pai do commit auditado e ainda não está em `main`. A PR #84 permanece em draft. O código desta revisão só altera o minerador experimental. O replay, DSL, protocolo e inferência v2 mantêm-se byte a byte, tal como a v1.

## Defeitos e correções

| Prioridade | Reprodução | Tratamento |
| --- | --- | --- |
| P0 | Exercitados os cortes de receção, mercados, identidade do jogo, FT e registo. | Nenhum P0 reproduzido no âmbito dos testes. |
| P1 · validade do teste | Em 80 dias emparelhados, 60 diferenças zero e 20 positivas dão mediana 0, IC [0; 0] e valor-p nominal 2⁻²⁰. O teste congelado condiciona em dias com diferença diferente de zero. Pode rejeitar para uma nula de mediana zero. Correlação entre dias também invalida a calibração binomial. | Acrescentados o contraexemplo end-to-end, a simulação e o NO-GO confirmatório. Uma correção inferencial exige outro protocolo e publicação independente antes da janela. |
| P1 · validade do banco | Potência com regras-oráculo e desempenho das regras realmente descobertas eram ensaios distintos. Igualar a probabilidade do alvo escondido de cada jogo não iguala o rendimento dos contratos reservados. O gerador LIVE pode dar FT inferior ao marcador observado e só emite âncoras LIVE nos jogos plantados. | O itinerário rastreia alvo, âncora, seleção, observabilidade, previsão e veredito separadamente. O benchmark passa a chamar ao ensaio antigo «alvos igualizados». A calibração usa um banco económico separado com a nula especificada no contrato da decisão. O gerador original conserva-se para reproduzir o benchmark histórico. |
| P2 · cobertura do minerador | A reversão multiorigem estava entre as projeções geradas e desaparecia antes da lista final. Os três cortes percorrem todas as âncoras. Desativar só a remoção de redundância deixa-a de fora. | Separação genérica de projeções em que duas dimensões variam, e projeção de continuidade de linha para trajetórias curtas observadas no mesmo mercado e período. A regra projetada exige `same_line`. A identidade de preço e a comparação por contrato continuam no replay. A revisão `joint-line-projection-1` fica no relatório e no registo de descoberta. |

## Estrutura, previsão e inferência

A recuperação estrutural compara conjuntos de jogos a Jaccard ≥ 0,60. A ablação retira os antecedentes plantados, conservando FT, cotações terminais e ruído. Uma queda após ablação demonstra dependência de algum antecedente nesse banco. Os campos `exact_terminal` e `exact_witness` medem a correspondência da oportunidade e dos eventos com o caminho plantado. Assim distinguem uma regra parcial de uma reconstrução do mecanismo.

A avaliação preditiva treina uma taxa de vitória binária regularizada para cada contrato e braço, com dados de descoberta e dez pseudo-observações à probabilidade implícita. Pontua as mesmas âncoras de caso posteriores por Brier. Mostra também o comparador aprendido no braço de controlo e a probabilidade bruta 1/odd. Quando faltam controlos de treino no contrato, o comparador encolhe para a probabilidade implícita. O Brier mede previsão na população selecionada. Uma diferença pode refletir composição. A seleção de candidatos ignora esses resultados.

A inferência analisa uma decisão observável por jogo, comparadores em sobreposição e FT no prazo. O JSON inclui regra, jogos e âncoras elegíveis, alocação, motivos de exclusão, preço/FT, hora e marcador de entrada, pesos efetivos, diferenças covariáveis, p nominal, Holm e veredito oficial. Com `--decision-rows` inclui IDs, timestamps, recibos por evento, alvo do gerador e liquidação. O efeito injetado é +30 pp de probabilidade de vitória na seleção alvo do gerador. A auditoria converte a distribuição do FT em rendimento à seleção e odd efetivamente observadas na decisão. Recompõe o contraste diário com efeito e com alvos igualizados. A diferença entre os dois mede a parcela injetada naquele desenho.

## Percurso dos seis padrões

A tabela sintetiza três sementes e 90 dias independentes com o minerador revisto. Os resultados originais continuam reproduzíveis. «Controlos efetivos» é a soma dos tamanhos efetivos de controlo por bloco.

| Padrão | Decisão | Sobreposição e efeito | Veredito |
| --- | --- | --- | --- |
| Reversão multiorigem | O minerador revisto encontra o percurso fonte × direção com linha estável. O terminal coincide em 53/55, 83/86 e 54/58 dos jogos recuperados no ensaio com seguimento. A regra omite o limite de tempo do oráculo. | 64–80 dias, 161–205 controlos efetivos. Efeito esperado incremental de +37 a +40 pp de retorno líquido por unidade na decisão. Igualar os alvos deixou −5 a −9 pp de composição. | Nenhuma retenção das selecionadas ou do oráculo. Holm ≥ 0,55 nas selecionadas. |
| Atraso não adjacente | Jaccard 0,985–1,00. A regra mais estrita quase elimina o comparador. | 1–69 dias e 1–142 controlos efetivos. O oráculo conserva 46–49 dias, 67–74 controlos efetivos e efeito esperado incremental de +63 a +70 pp. | Oráculo retido nas sementes 84 e 85. Nenhuma selecionada retida. |
| Contexto e preço LIVE | Terminal recuperado em todos os jogos. Apenas o processo plantado fornecia a oportunidade LIVE. O FT contradizia o marcador visível em 29–43 casos por semente. | 134–161 casos e zero controlos. Esses FT inviabilizam usar o cenário como calibração LIVE. | Sem contraste nem teste válido. Propostas ficaram fora do seguimento normal. |
| Oscilação de oito eventos | Jaccard 1,00. Na revisão, terminal certo em 0/79, 36/80 e 0/93 jogos. No original, zero em todas. Numa semente original a reserva mudou para a direção oposta em todos os casos plantados. | Efeito incremental esperado da regra selecionada muda de sinal (−42 a +42 pp). O oráculo tem 25–35 dias e 32–43 controlos efetivos. | Duas sementes do oráculo falham tamanho efetivo. A outra falha o teste. Nenhuma retenção. |
| Odd sobe e depois desce | Selecionadas acertam no terminal. O preço pontual ou uma trajetória específica quase determinam a inclusão nos casos. O oráculo admite sequências acidentais. | 2–6 dias, 2–6 controlos efetivos nas selecionadas. O oráculo chega a 64–66 dias com Jaccard 0,45–0,56. | Nenhuma retenção. A regra estrita carece de suporte e a ampla sofre diluição. |
| Confirmação tardia | Jaccard 1,00. Na semente 83, a selecionada entra antes da confirmação final em todos os jogos recuperados. | 16–32 dias e 16–43 controlos efetivos nas selecionadas. O oráculo tem 36–43 dias e contraste de composição com alvos igualizados de −15 a −19 pp. | Nenhuma retenção. Relógio, composição e cobertura limitam a interpretação. |

O padrão enganador perde o efeito futuro no seu alvo. O oráculo nunca sobreviveu a Holm. Nenhuma das regras selecionadas mais próximas dos seis padrões foi retida com os dois mineradores. A regra-oráculo do atraso não adjacente foi a única retida, em duas sementes. As famílias completas e os vereditos por hipótese estão no JSON. Rótulos, ablações e oráculos só são usados na auditoria.

No ensaio LIVE independente, as cotações terminais e os marcadores foram iguais nos grupos elegíveis, a presença do antecedente foi sorteada no gerador e o FT conservou os golos já observados. A regra-oráculo obteve 88–89 dias com sobreposição e 336–367 controlos efetivos. O efeito positivo de +12 pp foi retido em 1/3 sementes, com uma hipótese registada. A nula e o efeito negativo não foram retidos. Isto testa o replay inteiro com um comparador fisicamente plausível. A simulação abaixo cobre muito mais repetições.

## Alocação, relógios e confundimento

- O SHA-256 da identidade canónica preserva a alocação em reexecuções. Cada jogo entra uma vez. A alocação separa jogos, e os casos ainda precisam de adquirir a exposição. Os controlos são reservados na primeira avaliação determinável negativa. Renomear uma fixture pode trocar o braço. O hash não atribui a exposição.
- Um controlo que depois exibe o padrão conserva a decisão inicial. Isto tem uma interpretação descritiva válida no tempo da reserva e pode diluir a diferença. A auditoria conta controlos que mais tarde exibem o padrão, mede o atraso relativo dos pares e compara a distribuição de preços, relógios e contexto antes e depois da ponderação. O bloco dia × contrato não sincroniza os relógios das decisões.
- Inserções posteriores não antecipam um fecho. Os testes cobrem recibos tardios, reserva prévia do controlo e orçamento truncado. Um movimento de preço exige identidade de jogo, fonte, mercado, período, linha e seleção. As propostas podem relacionar fontes diferentes como eventos.
- Preços em falta contam nas reservas e não recebem preço inventado. FT ausente conserva uma banda de perda e ganho no denominador ponderado. Ausência de controlo é perda de suporte. A convergência no estado «rejected» ao fecho não prova ausência de efeito. Usar os campos `withheld`, `coverage`, `insufficiency` e `official.reason`.

## Descoberta multiorigem e generalização

| Banco | Original | Projeção com continuidade | Jaccard da reversão |
| --- | --- | --- | --- |
| Referência, 83/84/85 | 5/6 em cada | 6/6 em cada | 0,009 / 0,095 / 0,008 → 0,816 / 0,913 / 0,733 |
| Holdout anterior | 4/4 nas duas sementes | 4/4 nas duas sementes | — |
| Novo holdout, 4619/97751 | 2/4 e 3/4 | 3/4 e 4/4 | O exercício independente de troca de papéis chega a 1,00 nas duas sementes |

O novo banco foi escrito depois de congelar o minerador no blob `42e0ca947648f38b167957be260f99122303903c`. Usa 600 jogos por semente, novas fontes nos motivos, ruído LIVE, períodos diferentes e falsificadores com as mesmas marginais e a mesma âncora. Perdeu a mudança de período na semente 4619. A precisão por candidato na referência foi 55,6%/46,3%/46,3% na original e 50,0%/44,4%/44,4% na revisão, com 54 propostas e até 236 mil operações de geração. A precisão no novo holdout continua baixa (13,0% e 20,4%). Remover a deduplicação aumenta a precisão aparente por repetição de candidatos e deixa a reversão de fora. Nenhuma semente de referência encontrou estruturalmente o motivo enganador acima do limiar na revisão.

Antes da seleção, as melhores assinaturas originais para a reversão eram substitutas de volume, com Jaccard de 0,939/0,944/0,873 após replay e posições 9/7/7 na ordenação de base. A separação das famílias conjuntas, sem a projeção de linha, também não recuperou a reversão. O relatório de pesquisa mostra a ordem de base, apoio amostral e potencial sobreposição com a lista final.

## Potência, intervalos e falsos positivos

A simulação usa 400 repetições por cenário, até 90 dias, resultados binários à odd indicada, custos congelados na referência e cenários de sensibilidade separados. O JSON inclui intervalos de Monte Carlo das proporções, mediana populacional do contraste diário por enumeração e cobertura do intervalo para a mediana e para o efeito médio injetado.

| Cenário | Retenção v2 nominal a 2,5% | Holm, família de 7 |
| --- | ---: | ---: |
| Nula independente, 2.160 jogos | 0,5% | 0% |
| Efeito −8 pp de vitória, 2.160 jogos | 0% | 0% |
| Efeito +4 pp, 2.160 jogos | 35,0% | 8,8% |
| Efeito +8 pp, 1.080 / 2.160 / 4.320 jogos em 90 dias | 59,5% / 89,3% / 99,0% | 30,0% / 67,0% / 93,5% |
| Efeito +8 pp, metade das cotações em falta | 50,5% | 25,3% |
| Efeito +8 pp, 5% / 20% de FT em falta | 25,5% / 0% | 7,3% / 0% |
| Nula com dependência em blocos de 3 / 9 dias | 6,8% / 19,0% | 4,3% / 15,5% |
| Efeito da sequência nulo, vantagem de composição não medida de 12 pp | 99,8% | 98,0% |
| Nula de mediana com dias empatados assimétricos | 76,8% | 76,8% |

Holm avalia a hipótese focal na presença de seis nulas independentes simuladas. A vantagem de composição é um efeito observacional real. É um falso positivo para a interpretação de valor incremental da sequência. Sob dependência de três dias, agregar pelo bloco independente reduz a retenção nominal de 6,8% a 2,0% quando o efeito é nulo, com perda de potência. No contraexemplo dos empates, contar zeros como falhas dá 0% de rejeições. Na censura seletiva de derrotas, usar só FT completos reteve 77,5% sob a nula; as bandas v2 reduziram para 3,3% nominal. Mudar a odd comum ou o custo simétrico altera a dimensão económica. O cenário de odds afastadas sem suporte fica inelegível.

A referência de 4.320 jogos para +8 pp e cerca de 94% de potência aplica-se a 24 casos e 24 controlos comparáveis por dia, 90 dias independentes e FT completos. A janela de oráculos tem 2.160 jogos repartidos por linhas, fontes e fases. O suporte por hipótese é muito menor.

O intervalo de ordem cobriu a mediana populacional em 100% das repetições da nula independente e em 84,8% / 62,0% com blocos dependentes de 3 / 9 dias. Na presença de um efeito de +8 pp, cobriu a mediana em 100% e o efeito médio injetado em 70,8% no cenário de 2.160 jogos. Estimar a diferença média exige um intervalo construído para esse alvo. A auditoria não estabelece uma estrutura de dependência para o histórico real.

## Propostas para um protocolo posterior

1. Pré-registar previsão e retorno incremental em separado. Para cada fecho, construir o conjunto de risco com cotações observadas na mesma fase e no mesmo relógio relativo ao início ou ao minuto LIVE. Escolher um controlo determinável nesse instante e reservar cada jogo uma vez. Usar FT que respeite o marcador, remoção de antecedentes e controlos negativos com as mesmas marginais.
2. Definir o desfecho primário entre diferença média em unidades líquidas e mediana diária. Especificar como tratar empates e dimensionar para dias ou blocos independentes plausíveis. Medir o efeito mínimo, a perda de cobertura e a sensibilidade a confundimento, incluindo dependência por fonte e competição.
3. Definir a família de hipóteses, correção e janela antecipadamente, assinar o plano com hash e recibo independente e começar com zero resultados confirmatórios. Um novo comparador, preço, relógio ou teste exige outro identificador. O retorno com preços anunciados conserva a classificação teórica.

Estas alternativas são ensaios de auditoria. A execução normal continua a usar o protocolo v2 congelado quando selecionado.

## Reprodução e integridade

~~~sh
PYTHONPATH=. python audits/temporal-discovery-v2/scientific_audit.py --generator original > /tmp/pr84-original.json
PYTHONPATH=. python audits/temporal-discovery-v2/scientific_audit.py > /tmp/pr84-revised.json
PYTHONPATH=. python audits/temporal-discovery-v2/scientific_audit.py --seed 83 --decision-rows > /tmp/pr84-proof.json
PYTHONPATH=. python audits/temporal-discovery-v2/scientific_audit.py --calibrated-live > /tmp/pr84-live.json
PYTHONPATH=. python audits/temporal-discovery-v2/search_audit.py > /tmp/pr84-search.json
PYTHONPATH=. python audits/temporal-discovery-v2/power_audit.py --repetitions 400 > /tmp/pr84-power.json
python -m unittest discover -s tests -v
node --test ops/continuity/watchdog.test.mjs
git diff --name-only d1db6fb2aa8213a9521ba40c7836d927343749b7..HEAD
~~~

Os dados sintéticos usam recibos de publicação simulados. Os hashes de registo, provas e vereditos são inspecionáveis. Os testes incluem idempotência, troca de protocolo, prefixo histórico, mercado e seleção incompatíveis, controlo reservado antes de futura exposição e o contraexemplo estatístico dos empates. A revisão não publica registos reais e não altera collector, Telegram, dados, workflows ou certificação.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
