# Liquidação asiática na camada de previsão: defeito da v3.0, correção na v3.1

**Defeito confirmado.** O protocolo `temporal-risk-set-v3` (v3.0, `1726e67e…`) foi substituído por `temporal-risk-set-v3.1` (`b26e9e14…`). O retorno económico não muda.

## O que estava errado

`parser.settle` liquida linhas asiáticas em unidades de (odd − 1):

| Resultado | `payoff` |
| --- | --- |
| Ganha | `1` |
| Perde | `−1` |
| Devolve | `0` |
| Meia vitória | `+0,5` |
| Meia derrota | `−0,5` |

O lucro em `Replay.label` (`payoff × (odd − 1)` se positivo, senão `payoff`) e o retorno anunciado em `layers.values` estavam corretos. Na camada `prediction`, porém, a v3.0 calculava `ganhou = 1 se payoff > 0 senão 0` e o resíduo `ganhou − 1/odd`. Cada meia liquidação contava como **vitória ou derrota completa**.

Numa linha de quarto, metade do stake é decidida e a outra metade é devolvida (ou decidida noutra linha). Com preço justo, `1/odd` não é a probabilidade do evento binário «ganhou», pelo que o resíduo binário deixa de ter média zero.

## Correção (v3.1)

Por decisão, o stake ganho é `max(0, payoff)` e o stake decisivo é `|payoff|`. O resíduo passa a ser:

`stake ganho − stake decisivo / odd`

Esta quantidade é igual a `lucro / odd`. Tem média exatamente zero com preço justo, para qualquer linha asiática (inteira, meia ou de quarto), e coincide com a v3.0 quando `payoff ∈ {−1, 0, +1}`. As devoluções continuam fora da previsão (stake decisivo nulo) e dentro do retorno. Os limites por FT em falta continuam a ser perda total e vitória total.

## Viés da v3.0 com preço justo

`half_settlement.py` usa golos totais com distribuição de Poisson (média 2,6) e a odd justa de cada contrato. A tabela mostra o resíduo esperado por decisão decisiva:

| Linha | P(meia liquidação) | Odd justa (over / under) | v3.0 over | v3.0 under | v3.1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1,75 | 25,1% | 1,44 / 3,27 | +0,038 | −0,038 | 0 |
| 2,25 | 25,1% | 1,82 / 2,23 | −0,069 | +0,069 | 0 |
| 2,75 | 21,8% | 2,39 / 1,72 | +0,063 | −0,063 | 0 |
| 3,25 | 21,8% | 3,38 / 1,42 | −0,032 | +0,032 | 0 |
| 2,5 e 3,0 | 0% | — | 0 | 0 | 0 |

O viés chega a ±7 pp por decisão. É da ordem dos efeitos que a v3 procura detetar (4–12 pp). O sinal depende da linha e da direção, por isso uma comparação caso-comparador com contrato idêntico tende a cancelá-lo no par. Ainda assim, enviesa as médias por braço e qualquer agregação entre contratos.

## Observações afetadas

| Conjunto | Decisões | Meias liquidações |
| --- | ---: | ---: |
| Snapshot real, âncoras com cotação | 1.816 (1.527 liquidadas) | **47** (45 × `+0,5`, 2 × `−0,5`); 46 ScanGoal LIVE em linhas de quarto e 1 Stake 2,25 |
| Snapshot real, casos v3 (54 hipóteses) | 1.851 | 0 |
| Snapshot real, decisões em pares v3 | 26 | 0 |
| Mundos sintéticos v3 (nula, +20 pp) | 22.712 / 22.673 âncoras | 0 |
| Banco v1/v2, semente 83 | 13.116 âncoras | 0 |
| Simulação do núcleo (`power_v3.py`) | só `±1` e `0` | 0 |

Todos os resultados publicados com a v3.0 são, por isso, numericamente idênticos aos da v3.1. Os ficheiros em `results/` foram regenerados com os hashes da v3.1.

## Versão e rastreabilidade

- `PROTOCOL_V3_0` fica byte a byte como publicado e o teste fixa o seu hash. A v3.1 difere apenas em `name`, `revision`, `supersedes` e em `layers.prediction` / `layers.asian_half_settlements`. Um teste verifica exatamente este conjunto de diferenças.
- O identificador `tr3:` liga a regra ao hash do protocolo, por isso a mesma regra tem outro identificador na v3.1.
- Nenhum plano foi alguma vez registado na v3.0. Se existisse, continuaria legível como histórico, `evaluate` devolvê-lo-ia como `superseded_protocol_v3_0_half_settlement_defect` sem o avaliar, e não seria possível registar novos planos v3.0.
- `quarantine.py` bloqueia alegações confirmatórias da v3.0, com sucessor v3.1, tal como faz para a v1 e a v2.

## Testes (`tests/test_discovery_v3.py::AsianHalfSettlement`)

- `parser` e `event_audit.payoff` dão vitória, derrota, devolução, meia vitória e meia derrota em linhas 2,0 / 2,25 / 2,5 / 2,75, over e under.
- O resíduo v3.1 vale exatamente metade do binário nas meias liquidações, as devoluções ficam fora da previsão e o retorno económico é idêntico ao da v3.0.
- Com preço justo, o resíduo v3.1 esperado é 0 (12 casas decimais) em 2,25 / 2,75 / 3,75. O binário da v3.0 desvia-se mais de 0,01.

Reprodução:

```sh
PYTHONPATH=. python audits/temporal-discovery-v3/half_settlement.py --as-of 2026-11-24T08:22:00Z > audits/temporal-discovery-v3/results/half_settlement.json
python -m unittest discover -s tests -p test_discovery_v3.py -v
```

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
