# PR I · Generalização robusta e estabilidade

**GO para investigação offline. NO-GO para generalização independente e confirmação prospetiva.**

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

O sucessor devolve uma carteira de hipóteses tipadas sem rótulos, resultados ou configuração manual. O trabalho partiu da PR #89 em `3a040f4`. O algoritmo, os parâmetros, os limites por cenário e os hashes da base ficaram selados em `6ca1b1c`. O avaliador com as quatro novas famílias foi criado depois e registado em `302eb7f`, antes do primeiro ensaio. [Bloqueio prévio](freeze.json) · [Resultados completos](report.json)

## Diagnóstico da perda pré-jogo

| Etapa | Evidência reproduzida |
| --- | --- |
| Geração | Dos nove caminhos etiquetados em cada treino conhecido, H enumerou seis e sete. O limite de oito variantes favorecia combinações recentes em ordem lexicográfica. A inclusão de janelas interiores e de ambos os extremos elevou a amostra para nove e nove com o mesmo limite de variantes. |
| Preservação dos passos | A estrutura de seis passos da regra da PR G reapareceu como alternativa de I. Em H, o ramo pré-jogo tinha o comprimento correto, mas as propostas com a estrutura-alvo acrescentavam condições. A proposta estrutural puramente tipada com esses seis passos ficou fora da carteira nas duas sementes. |
| Deduplicação de características | H guardava uma coluna por máscara de positivos e uma opção por intersecção da expansão. Valores absolutos, relações entre odds e disponibilidade das medições podiam coincidir na amostra. A contagem de colunas colapsadas foi 2.073 e 2.016 nos dois treinos pré-jogo. Um teste construído separadamente produz a mesma cobertura para um limiar de odds e uma descida, e verifica que I mantém ambos. |
| Distribuição do orçamento | Os dois treinos pré-jogo de H terminaram dentro do teto, pelo que o esgotamento global não explica essa perda. H consumiu 1.323.852 e 1.413.210 operações. O cenário conhecido do empate esgotou o teto nas duas sementes. I separa pré-jogo, LIVE e faixas de comprimentos, cacheia valores de testemunhas e reserva uma proposta estrutural antes de refinar. |
| Seleção e estabilidade | A escolha H da segunda semente exigia odds inferiores a 2,29 numa posição e uma descida em relação à abertura. Após a mudança do regime de preços, acertou um caminho no primeiro banco conhecido e zero nos outros dois. Na primeira semente, a escolha acertou 5, 3 e 7, e deixou passar subidas terminais e volumes incompatíveis. |
| Falsos positivos | Na inversão pré-jogo conhecida, a regra estrutural da PR G recuperou 54/54 caminhos com 54 terminais corretos em 144 deteções. A disponibilidade do caminho regressou aos 54/54 com I e a seleção assistida reproduziu a mesma precisão de 37,5%. A primeira escolha automática de I recuperou zero caminhos completos nesse recorte. |

Nos bancos conhecidos, a carteira I continha um candidato capaz de recuperar todos os caminhos em cada avaliação, nas três famílias. A escolha assistida em desenvolvimento recuperou 54/54 por família. Na primeira escolha automática, os números foram **0/54 pré-jogo**, **34/54 no empate** e **0/54 na pressão ao vivo**. O controlo de disponibilidade face a H passou. Os resultados conhecidos são diagnóstico de desenvolvimento e continuam fora da validação independente. A disponibilidade foi medida retrospetivamente como o melhor candidato de cada banco. [Diagnóstico executável](history.py)

## Alterações e orçamento

- **Trajetórias.** O amostrador alterna decisões entre fases e jogos. Conserva janelas com omissões interiores, extremos temporais e alternativas simultâneas. Só usa eventos disponíveis na receção da cotação. Uma cotação sobre a qual não há oportunidade elegível fica fora da decisão.
- **Pesquisa.** Mantém o teto de **1.800.000 operações**, as oito variantes por comprimento e as 24 propostas. Reserva trabalho por fase e por faixas de 2–3, 4–5 e 6–8 passos. Divide trabalho dentro de cada faixa pelo tamanho das testemunhas e pela complexidade dos pares de posições. Emite estruturas recorrentes e refina até sete por faixa. A busca abre ligações entre posições distantes antes de expandir condições de igualdade e níveis absolutos. Os caminhos e as conjunções mantêm a linguagem tipada da v3.2 e as guardas de identidade da PR H.
- **Diversidade e contraste.** A carteira intercala versões estruturais, relativas e mistas e alterna formas distintas dentro de cada grupo. A expansão agrega conjunções canónicas idênticas. Predicados com a mesma cobertura observada continuam candidatos quando exprimem relações diferentes. A largura do feixe limita a ocupação por um perfil de comportamento e mantém diferentes estruturas. As contradições observadas noutros jogos pontuam separadamente de informação indeterminável.
- **Interrupção.** O resultado expõe o limite, consumo, rubricas por fase e faixa, motivos de interrupção e envelope de aproximação. A cauda de 12 lotes, o feixe de largura 8 e as cinco expansões limitam a pesquisa mesmo numa execução que completa o orçamento atribuído. Uma fase densa não consome o trabalho reservado a outra.
- **Estabilidade numérica.** A ordenação usa inteiros, com contagens distintas por jogo, testemunhas e dias, e desempate canónico. Os limiares calculados usam quantização decimal explícita. Na PR H, o Python 3.11 usava uma implementação diferente de soma em vírgula flutuante, com sete diferenças nos contadores históricos. A reprodução de I exige igualdade integral do relatório, incluindo contadores e hashes de ordenação e testemunha. [Contadores históricos da PR H](../autonomous-generalization/runtime-counters.json)

Na leitura do `data/messages.jsonl` já recolhido, o comando experimental devolveu 24 propostas, consumiu 1.460.210 operações e identificou esgotamento em várias estruturas locais. Estes resultados estão assinalados como parciais. As propostas e o estado do orçamento ficam em stdout. O comando deduz o corte do último recibo concluído verificável. [Comando do arquivo local](investigate.py)

## Avaliação independente

O avaliador retém as etiquetas. A primeira regra da ordenação sem rótulos é fixada para cada motor e semente antes de criar os bancos inéditos. O diagnóstico assistido escolhe uma regra em desenvolvimento com base na menor margem relativa aos três critérios, penalizando falsos positivos no desempate. As regras assistidas e o melhor candidato observado depois da avaliação são publicados como diagnósticos separados. Foram usadas duas sementes de desenvolvimento e três de avaliação por cenário, com 10 trajetórias-alvo e 18 contraexemplos por banco, horários, atrasos, intervalos de preço e ruído novos.

**Um cenário é não avaliável.** No teste de confirmação de marcador, os 10 terminais positivos de cada banco tinham três golos visíveis numa linha Over 2,5. A guarda congelada já considera essa linha resolvida no instante da cotação. Nenhuma das 30 decisões positivas distintas era elegível. O banco foi conservado e a classificação do defeito foi acrescentada ao avaliador depois da primeira execução. A tabela exclui esse cenário de ambas as colunas. O algoritmo, os dados do banco e os limiares mantiveram os hashes iniciais. O primeiro relatório bruto teve SHA-256 `6dcb7ad6fdf68e13e40351b4d49a0f223f44cce5db35d1d264464f979f7a6055`.

| Cenário avaliável | Precisão H / I | Recuperação dos terminais H / I | Caminhos exatos da primeira escolha H / I | Melhor caminho disponível H / I |
| --- | ---: | ---: | ---: | ---: |
| Queda do maior bilhete pré-jogo | 43/76 · 30/58 | 43/60 · 30/60 | 43/60 · 0/60 | 52/60 · 60/60 |
| Intervalo seguido de preço pontual pré-jogo | 60/141 · 32/73 | 60/60 · 32/60 | 60/60 · 0/60 | 60/60 · 60/60 |
| Mudança de âmbito ao vivo | 60/134 · 24/56 | 60/60 · 24/60 | 48/60 · 0/60 | 60/60 · 60/60 |

Cada linha reúne seis avaliações, aplicando a cada um dos três bancos a escolha das duas sementes de desenvolvimento. O melhor caminho disponível permite um candidato diferente por avaliação. Nenhum candidato de I passou simultaneamente os três limiares em todos os bancos de uma família. A escolha assistida de I recuperou 44/60, 60/60 e 48/60 caminhos nestas famílias. A primeira escolha de I favoreceu trajetórias de sete ou oito passos nas três famílias avaliáveis, incluindo passos acessórios. A identidade das primeiras regras também mudou entre sementes nessas famílias.

- **Rejeição.** Das 324 avaliações de terminais de contraexemplos nas três famílias avaliáveis, I declarou 164 ausentes sob cobertura sintética, indicou 59 como indetermináveis e detetou 101. H declarou 50 ausentes, 62 indetermináveis, 24 inelegíveis pelo predicado terminal e detetou 188. A rejeição adicional coexistiu com perda de terminais e de testemunhas. O relatório preserva as causas por mecanismo e distingue ausência, inelegibilidade e indeterminação.
- **Robustez ao transporte.** As permutações, duplicações, IDs alterados, campos de resultado inertes e eventos posteriores ao corte mantiveram as ordenações completas de I e as medições das regras escolhidas.
- **Custo.** Nos seis treinos dos cenários avaliáveis, I usou entre 609.163 e 676.431 operações. O perfil local mediu 6,35 s nas oito chamadas iniciais a I, 10,63 s nas oito chamadas a H e cerca de 41 MiB de memória residente no processo completo. O relatório mede as operações de avaliação por regra. As unidades internas de H e I contam trabalho por métodos diferentes.

A autorização de generalização requer **90% de precisão**, **80% de recuperação do terminal** e **75% de recuperação do caminho** em cada cenário e cada semente avaliável, com invariância e pesquisa concluída. Nenhuma família foi aprovada. A regra de proteção das famílias conhecidas preservou a disponibilidade de testemunhas e não compensou a falha de escolha independente. Fica reservado um banco posterior com outra semente e outros mecanismos para uma eventual revisão selada. A estabilidade do seletor e a transferência para condições independentes continuam por demonstrar.

## Reprodução

Na raiz do repositório:

```sh
PYTHONPATH=. python audits/robust-generalization/history.py
PYTHONPATH=. python audits/robust-generalization/evaluate.py > /tmp/goal-roi-i.json
cmp /tmp/goal-roi-i.json audits/robust-generalization/report.json
PYTHONPATH=. python audits/robust-generalization/evaluate.py --details --profile > /tmp/goal-roi-i-details.json
PYTHONPATH=. python audits/robust-generalization/investigate.py > /tmp/goal-roi-i-journal.json
python -m unittest discover -s tests -p 'test_robust*.py' -v
node --test ops/continuity/watchdog.test.mjs
```

Os testes cobrem separação temporal, recibos simultâneos, caminhos interiores, alternativas semanticamente diferentes com a mesma cobertura, reservas por família, interrupções, classificação dos terminais inelegíveis, isolamento da rede e ordem da avaliação.
