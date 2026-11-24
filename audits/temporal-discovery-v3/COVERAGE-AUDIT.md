# Auditoria de cobertura: porque é que o snapshot real quase não gera pares v3

**Diagnóstico offline e só de leitura. Os critérios da v3.1 não foram relaxados.** Os contrafactuais servem para planeamento (PR E). São limites superiores, nunca evidência.

Snapshot: corte em 2026-11-24 08:22 UTC, os mesmos dados do HEAD `a532d9f`. Reprodução:

```sh
PYTHONPATH=. python audits/temporal-discovery-v3/coverage_audit.py --as-of 2026-11-24T08:22:00Z > audits/temporal-discovery-v3/results/coverage.json
```

Verificação de consistência: a cascata reencontra os números do protocolo. São 1.851 fechos de caso somados nas 54 hipóteses (505 decisões únicas); 827 sem odd pontual (806 intervalares e 21 indeterminadas); 13 pares estritos, todos com FT nos dois braços.

## 1. Onde se perde cada caso

Cada caso é atribuído à **primeira** etapa em que deixa de existir um comparador admissível, pela ordem dos filtros da v3.1.

| Classe | Etapa | Somado (1.851) | Casos únicos (505) |
| --- | --- | ---: | ---: |
| Preço | Cotação do caso intervalar | 806 | 226 |
| Preço | Cotação do caso indeterminada | 21 | 9 |
| Mercado/seleção | Contrato exato nunca cotado noutro jogo | 57 | 15 |
| Relógio | Nenhum jogo com o mesmo contrato no mesmo relógio (±45 min / LIVE ±5 min com o mesmo marcador) | 407 | 115 |
| Disponibilidade | O jogo compatível ainda não tinha sido observado no instante | 130 | 30 |
| Disponibilidade (frescura) | Última cotação do jogo compatível com mais de 30 min | 346 | 82 |
| Preço | Comparador sem odd pontual | 49 | 15 |
| Preço | Comparador fora do calibre de 0,15 em log-odd | 9 | 6 |
| Reserva | Todos os comparadores admissíveis já reservados | 11 | 3 |
| Correspondência | Passo terminal falha ou antecedente presente/indeterminado | 2 | 0 |
| — | **Emparelhado** | **13** | **4** |

Não houve falhas de identidade de contrato nos casos: 1.807 das 1.816 âncoras têm contrato exato. As perdas de identidade e de relógio acontecem **antes** de haver âncoras (ver secção 4). A disponibilidade de resultados não bloqueou nenhum dos 13 pares.

**Leitura.** O preço é a primeira barreira (46% dos casos únicos). A segunda é estrutural: a coincidência no tempo, que soma relógio, disponibilidade e frescura (45%). O mesmo contrato raramente é cotado para dois jogos ao mesmo tempo. Mesmo que todas as odds fossem pontuais, a maioria dos casos esbarraria no relógio ou na frescura.

## 2. Preços: fontes, formas e largura

| Fonte | Eventos | Pontuais | Intervalares | Indeterminados | Sem preço pontual |
| --- | ---: | ---: | ---: | ---: | ---: |
| Polymarket | 1.284 | 636 | 571 | 77 | **50,5%** |
| Betfair | 594 | 323 | 0 | 271 | **45,6%** |
| ScanGoal | 818 | 689 | 0 | 129 | 15,8% |
| Stake | 958 | 821 | 137 | 0 | 14,3% |

- Nas âncoras de decisão (1.816), as intervalares são **Polymarket 417/909 (46%)** e Stake 40/238 (17%). A Polymarket é a principal fonte de falta de preço pontual. Na Betfair, a falta vem de mensagens sem odd; nas âncoras sobram só 65 Betfair pontuais.
- Os intervalos são faixas de execução observadas pelo bot (`@ 2.15–2.16`), não preços desconhecidos. A largura mediana é de 2,0% na Polymarket e 2,4% na Stake. A largura é ≤ 1% em 74 + 17 eventos e ≤ 2% em 274 + 58.
- 30 âncoras Polymarket ficaram indeterminadas porque o mesmo contrato teve preços diferentes no mesmo segundo.
- 2 eventos pontuais Betfair estão abaixo da odd mínima (1,10).

**Contrafactual (não protocolar).** Se a cotação intervalar do caso fosse aceite, desbloquear-se-iam 28 casos únicos com largura ≤ 1% e 110 com largura ≤ 2%. Esses casos ficariam ainda sujeitos às barreiras de relógio e frescura. Usar limites de intervalo exigiria um protocolo novo e pré-registado (por exemplo, retorno com o limite desfavorável e calibre aplicado aos dois extremos). Isso **não** foi feito.

## 3. Mercados e contratos com maior cobertura

Há 203 contratos exatos distintos; 101 (50%) aparecem num único jogo. Jogos por contrato: mediana 2, p75 7, p90 18.

| Contrato (fonte · fase · mercado · linha · seleção) | Jogos | Cotações | Pontuais |
| --- | ---: | ---: | ---: |
| ScanGoal · LIVE · total asiático · 3 · over | 119 | 120 | 120 |
| ScanGoal · LIVE · total asiático · 4 · over | 85 | 85 | 85 |
| ScanGoal · LIVE · total asiático · 0,75 · over | 58 | 58 | 58 |
| Polymarket · pré · total · 2,5 · over | 55 | 90 | 45 |
| Polymarket · pré · handicap · −0,5 · AH1 | 49 | 98 | 37 |
| Polymarket · pré · handicap · +0,5 · AH2 | 44 | 113 | 67 |
| Polymarket · pré · handicap · −0,5 · AH2 | 36 | 69 | 37 |
| Polymarket · pré · total · 3,5 · over | 35 | 51 | 26 |

| Mercado (fonte · fase · mercado) | Jogos | Cotações | Pontuais |
| --- | ---: | ---: | ---: |
| ScanGoal · LIVE · total asiático | 568 | 599 | 599 |
| Polymarket · pré · total asiático | 117 | 271 | 143 |
| Polymarket · pré · handicap | 112 | 419 | 213 |
| Polymarket · LIVE · handicap | 59 | 152 | 93 |
| Stake · pré · 1X2 | 46 | 71 | 47 |
| Polymarket · LIVE · total | 44 | 63 | 40 |
| Betfair · pré · 1X2 | 43 | 65 | 65 |
| Stake · pré · total asiático | 42 | 69 | 63 |

O mercado mais denso é o total asiático LIVE da ScanGoal. Tem cobertura larga e preços pontuais, mas está espalhado por muitas linhas e minutos, e o comparador LIVE exige o mesmo marcador. No pré-jogo, Polymarket total 2,5 e handicap ±0,5 são os contratos com mais jogos.

## 4. Relógio, identidade e disponibilidade antes das âncoras

| Etapa | Contagem |
| --- | ---: |
| Episódios de cotação | 3.143 |
| Com relógio de decisão provado | 1.816 |
| **Relógio**: janela LIVE não provada 557, sinal fora da janela de decisão 298, fase em conflito com o início 218, janela LIVE expirada 118, 1.ª parte não provada/expirada 68, 2.ª parte 1 | 1.260 |
| **Mercado/período** não provado | 50 |
| **Identidade**: colisão de identificador | 29 |
| **Resultados**: FT já conhecido | 14 |
| Exclusões de entrada: **identidade** (identidade incompleta 77, match_id reutilizado 9, em falta 1) | 87 |
| Exclusões de entrada: **fonte** (independência não provada 24, rajada Betfair 12) | 36 |
| Exclusões de entrada: **disponibilidade** (primeira leitura já com FT 39, editada na primeira leitura 1) | 40 |

**Recibos.** Só 229 dos 3.654 eventos (130 das 1.816 âncoras) têm recibo `completed_page`, e todos a partir de 2026-11-23. O resto é `legacy_upper_bound`: recolha retroativa com primeira leitura até horas depois (p75 80 min, p90 168 min). A janela confirmatória v3.1 só aceita `completed_page`, portanto **todo o histórico anterior a 23/11 é inutilizável para confirmação**. A recolha atual já produz recibos completos.

## 5. Tempo entre observações comparáveis

| Medida (cotações pontuais, contrato exato) | n | p25 | Mediana | p75 | p90 | ≤ 30 min |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Nova cotação do mesmo contrato no mesmo jogo | 162 | 6 min | 40 min | 159 min | 525 min | — |
| Cotação anterior mais próxima noutro jogo, mesmo contrato | 1.148 | 42 min | 174 min | 1.054 min | 2.450 min | 240 (21%) |
| Idem, com início a ±45 min | 171 | 13 min | 51 min | 215 min | 607 min | 58 (34%) |

Só 162 cotações voltam a observar o mesmo contrato no mesmo jogo. O bot emite alertas, não séries de preços, e o comparador do mesmo instante quase nunca está fresco.

## 6. Quantos pares haveria com observações adicionais do mesmo contrato

Contrafactual (limite superior, não protocolar): o caso bloqueado na disponibilidade, na frescura ou no preço do comparador teria par se o mesmo contrato do jogo compatível tivesse sido observado de novo no instante. Para isso, a odd mais próxima conhecida tem de estar dentro do calibre, o jogo não pode estar reservado e tem de haver FT. A correspondência no instante não é verificada.

| Etapa que bloqueou | Casos (somado) |
| --- | ---: |
| Frescura (última cotação com mais de 30 min) | 87 |
| Ainda não observado no instante | 20 |
| Comparador sem odd pontual | 6 |
| Fora do calibre | 2 |
| Correspondência | 1 |
| **Total** | **116** (contra 13 atuais) |

Mesmo neste cenário otimista, nenhuma das 54 hipóteses chegaria aos 60 pares exigidos para inferência. Com 13 dias de histórico, a cobertura densa tem de vir de observação **futura** e regular dos contratos, não de reprocessamento.

## 7. Resultados

Nas 1.816 âncoras há 1.527 liquidadas, 209 com liquidação não suportada (Polymarket 144, Stake 60, ScanGoal 5: mercados ou seleções que o parser não liquida pelo FT), 42 com FT em falta e 38 pendentes. Os 1.212 jogos identificados têm resultados de origem Telegram (2.634 linhas FT e 21 notas). Há 47 meias liquidações (ver `HALF-SETTLEMENT.md`).

## 8. O que o collector já recebe e não é aproveitado

Medido no texto de `data/messages.jsonl` já guardado (4.411 mensagens; 7.169 linhas; 2.757 mensagens com várias versões preservadas):

| Informação já guardada | Ocorrências | Aproveitamento atual |
| --- | ---: | --- |
| Faixa de execução `@ a–b` | 708 eventos intervalares | Guardada como `odds=[a,b]`. A v3 só usa pontuais. |
| Cronologia de golos (`⚽ 58' 1 - 1`) em versões editadas | 3.884 linhas em 1.379 textos | Não convertida em eventos de marcador com hora. Poderia validar marcadores LIVE e coerência FT. |
| Linha FT no texto editado | 2.791 textos | Usada como resultado `telegram`. |
| Maior bilhete (`biggest $…`) | 2.761 textos | Não extraído. Só `n_bets` está em `context`. |
| Volume acumulado no resultado (`Σ … overall`, `Total on … this match`) | 1.612 textos | Não extraído. `volume_usd` guarda o volume do alerta. |
| Ligação para a página do jogo | 4.179 textos | Não usada. Consultá-la seria recolha externa (ver secção 9). |
| `receipt_version` (recibos completos) | 462 linhas | Usado desde 23/11. |

## 9. O que exige uma fonte externa autorizada

- **Séries de preços regulares do mesmo contrato** em vários jogos em simultâneo. O bot não as emite. A única fonte compatível com as restrições (sem pagamento, sem credenciais, sem apostas) é a **API pública de leitura da Polymarket**, já usada pelo observador existente. O observador tem ativação controlada e registou até agora 5 execuções e 1 observação (`identity_not_found`), com 0 ordens e sem credenciais. Alargar o seu uso exige autorização separada.
- **Betfair Exchange**: exige conta e chave de aplicação (credenciais). Excluída.
- **Stake e 1xBet**: sem API pública autorizada. *Scraping* proibido e odds manuais excluídas.
- **Odd executável, profundidade e latência**: só uma fonte de livro de ordens (Polymarket CLOB pública) as aproxima. A execução real continua não demonstrada.
- **Resultados independentes do Telegram**: exigiriam um fornecedor externo. Os gratuitos exigem normalmente chave de registo e ficam fora do âmbito.

## 10. Melhorias possíveis sem alterar a recolha

1. **Usar só dados com recibo completo daqui em diante.** É o que a janela confirmatória v3.1 já faz. O ganho vem do tempo, não de reprocessar.
2. **Extrair a cronologia de golos** das versões editadas, para obter o marcador LIVE com hora e verificar a coerência FT/marcador. É um parser offline novo.
3. **Extrair o maior bilhete e o volume acumulado** como variáveis do vocabulário de antecedentes. Isto não altera o preço.
4. **Pré-registar um protocolo v3.2 opcional com preços intervalares** (limite desfavorável no retorno e calibre nos dois extremos), avaliado primeiro só em sintético. Alcance máximo: 110 casos únicos com largura ≤ 2%.
5. **Restringir a família de hipóteses aos contratos densos** (Polymarket pré-jogo total 2,5 e handicap ±0,5; ScanGoal LIVE total), para concentrar o suporte. É uma decisão de desenho a pré-registar.
6. **Repartir as 209 liquidações não suportadas** por mercado e seleção, para alargar o parser de liquidação onde a regra for inequívoca.

Nada disto, sozinho, aproxima as hipóteses dos 60 pares: o fator limitante é a observação simultânea do mesmo contrato. A especificação da PR E está em `PR-E-SPEC.md`.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
