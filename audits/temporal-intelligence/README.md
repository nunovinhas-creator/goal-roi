# PR A · Auditoria exploratória de eventos temporais

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

## Reprodução

O relatório usa os 7.156 registos de versões em `data/messages.jsonl` do commit `82198c0af45270c6028e58255c21a46c55b19aa1`, com corte em **24 de novembro de 2026, 08:22:00 UTC**. O histórico registado neste snapshot começa em 12 de novembro. O limiar da análise de volume é **10.000 USD anunciados por alerta**, tanto em Stake como na Polymarket. É uma escolha exploratória ajustável.

A partir da branch da PR, extraia os quatro ficheiros do snapshot e execute o módulo. O JSON identifica o SHA‑256 de cada entrada. A auditoria integral inclui os originais, as edições, os episódios, os motivos de exclusão, as cotações comparadas e as provas de FT disponíveis no corte.

```sh
mkdir -p /tmp/goal-roi-temporal-snapshot
git archive 82198c0af45270c6028e58255c21a46c55b19aa1 \
  data/messages.jsonl data/results_hl.json data/results_bsd.json data/details_hl.json \
  | tar -x -C /tmp/goal-roi-temporal-snapshot

python -m scangoal.event_audit --data-dir /tmp/goal-roi-temporal-snapshot/data \
  --as-of 2026-11-24T08:22:00Z --volume-min-usd 10000 --format summary \
  > /tmp/goal-roi-temporal-summary.json

python -m scangoal.event_audit --data-dir /tmp/goal-roi-temporal-snapshot/data \
  --as-of 2026-11-24T08:22:00Z --volume-min-usd 10000 --format audit \
  > /tmp/goal-roi-temporal-audit.json

python -m scangoal.event_audit --data-dir /tmp/goal-roi-temporal-snapshot/data \
  --as-of 2026-11-24T08:22:00Z --format llm-context --limit 30
```

O SHA‑256 de `messages.jsonl` é `ac2914df9e42d27f61d41629aa1b6571131b5db661589e7e0c2fc9c4b393de8b`. A pesquisa abrange toda a recolha preservada nesse ficheiro. Mensagens anteriores à recolha, apagadas antes da primeira leitura ou registadas apenas numa versão editada podem faltar.

## Reconstrução e relógios

- Uma versão entra no corte quando o texto bate com o hash do diário, a emissão é coerente com o recibo e o texto já podia ter sido lido. Um recibo de página concluída dá a hora de disponibilidade. Para carimbos legados de início de recolha, o relógio de disponibilidade inclui a margem de cinco minutos do repositório. A primeira leitura registada e o limite conservador ficam em campos distintos.
- Cada sinal usa o primeiro corpo comprovado do seu ID. O inventário guarda versões editadas com ID, hashes, linha do diário, emissão, edição e leitura. Um original encontrado com FT, uma mensagem vista pela primeira vez já editada, um lote Betfair e cópias textuais identificadas têm tratamento explícito. As edições podem fornecer prova de FT da mesma partida.
- A identidade da partida segue as ligações explícitas e a resolução cronológica existente. IDs alternativos podem unir-se com prova exata de equipas, competição e início. Ligações ambíguas e IDs reutilizados geram exclusões, com o instante da colisão a limitar as decisões que dela dependem.
- As sequências incluem todos os pares ordenados por emissão estritamente crescente na mesma partida com direção, mercado, período e linha identificáveis. A hora de observação do par é o máximo das duas disponibilidades. Um intervalo negativo entre recibos indica que o primeiro sinal emitido foi lido depois do segundo. Pares com a mesma emissão ficam contabilizados à parte. O fim da janela operacional, o conflito de fase ou de identidade e a prova de FT já disponível bloqueiam a decisão. O relatório usa `observed_utc` para todos os episódios e `decision_utc` para os admitidos. Estes instantes representam a primeira decisão reconstruível no replay.
- A seleção de referência nas sequências é a do último alerta emitido. As tabelas de acerto e ROI usam as decisões admitidas e as ocorrências podem repetir o mesmo jogo. Frações asiáticas positivas e negativas contam como acerto e erro, respetivamente. A taxa exclui devoluções. O ROI usa uma unidade hipotética ao preço pontual anunciado, desde a odd mínima de 1,10 já usada no repositório, e inclui devoluções no denominador. As frações asiáticas ponderam o lucro e a perda. O tamanho do subconjunto com preço é mostrado junto ao ROI.
- Um FT confirmado requer observação válida do marcador aos 90 minutos. Marcadores contraditórios, mercados que exigem informação adicional e casos sem FT ao fim de três dias após a observação do episódio ficam indeterminados. Os casos sem FT dentro desse prazo ficam pendentes. A confirmação do FT é contabilizada à parte da liquidação da seleção. Para handicap LIVE, parciais, cantos e outros mercados sem prova de liquidação suficiente, a seleção fica em aberto na análise económica, mesmo que exista FT. Os resultados são rótulos do corte da auditoria.
- A odd é comparada com a última emissão anterior da mesma seleção, fonte, mercado, período e linha que já estava disponível no instante do alerta estudado. A estabilidade requer dois preços pontuais iguais. Intervalos completamente separados provam o sentido do movimento. Intervalos sobrepostos ficam sem classificação. A auditoria mostra separadamente cotações emitidas e vistas depois, bem como cotações anteriores lidas com atraso. Uma cotação emitida depois do alerta e lida no mesmo lote pode contribuir para o episódio da própria cotação posterior.

## Sequências encontradas

Os resultados confirmados, pendentes e indeterminados abaixo incluem toda a ocorrência reconstruída, mesmo quando a janela impediu a decisão. Acerto e ROI consideram a seleção do segundo sinal. A coluna `n` indica quantas decisões têm preço pontual e liquidação utilizáveis. Os totais de jogos por linha podem sobrepor-se.

| Padrão | Jogos | Ocorrências | Decisões | Confirmadas / pendentes / indeterminadas | Acerto | ROI teórico (n) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Under → Over | 99 | 768 | 460 | 569 / 3 / 196 | 65,40% | +13,30% (281) |
| Over → Under | 80 | 520 | 333 | 469 / 1 / 50 | 40,73% | −8,44% (182) |
| Over → Over | 122 | 1.496 | 660 | 1.031 / 0 / 465 | 70,00% | +18,45% (371) |
| Under → Under | 77 | 344 | 242 | 323 / 2 / 19 | 35,71% | −17,10% (154) |
| Repetição exata, mesma direção | 81 | 450 | 272 | 436 / 2 / 12 | 55,09% | +4,22% (140) |
| Inversões na mesma linha, período e mercado | 64 | 379 | 262 | 372 / 2 / 5 | 53,94% | +25,04% (115) |
| Todos os pares | 195 | 3.128 | 1.695 | 2.392 / 6 / 730 | 56,64% | +6,49% (988) |

Foram observados **1.288 pares com inversão de direção em 129 jogos**. Destes, 793 admitiram uma decisão em 105 jogos. Entre as 262 inversões na mesma linha, período e mercado que admitiram decisão, 139 vão de Under para Over e 123 de Over para Under. Entre fontes distintas, houve 105 confirmações do mesmo contrato e direção, 407 sinais na mesma direção com linha diferente e 408 divergências de direção no mesmo mercado e período. Outros 612 pares entre fontes distintas tinham mercado ou período diferente. Em 34 inversões decidíveis, em 12 jogos, as duas seleções tiveram liquidação positiva. Uma inversão de direção pode preservar a compatibilidade matemática entre as seleções.

A distribuição cronológica dos pares foi de 897 pré → pré, 1.353 pré → após o início anunciado, 837 após → após e 41 LIVE → LIVE sem início anunciado demonstrado a tempo. O JSON `summary` discrimina cada padrão por mercado normalizado e literal, fonte, período, correspondência de linha e fase. Em `audit`, os pares incluem o intervalo de emissão e de disponibilidade em segundos.

Dos originais reconstruídos, 123 têm exclusão de identidade ou de lote, incluindo 77 com identidade incompleta, 24 com independência por provar, 12 de lotes Betfair, nove com ID reutilizado e um sem ID. Há também 40 primeiras versões com edição ou FT e 2.800 versões editadas no corte. Vinte originais com direção carecem de linha, período ou mercado exato. Quarenta pares têm emissão empatada. Nas sequências, os motivos mais frequentes para excluir a decisão foram conflito com a fase anunciada, janela expirada e janela da primeira parte sem prova. Há exclusões múltiplas no mesmo episódio. O caso com FT já conhecido pode ser localizado em `episodes[].exclusion_reasons` através de `ft_already_known`.

## Odds e volume

Há **1.142 ocorrências de grande volume em 253 jogos**. Entre as 828 decisões admitidas, 216 tinham um movimento comprovado no momento da decisão. Os 612 casos admissíveis sem movimento comprovável incluem seleções sem outra cotação compatível e intervalos de preço sobrepostos. O relatório contém o mesmo inventário de movimentos para as restantes cotações, incluindo Betfair.

| Movimento conhecido até à decisão | Jogos | Ocorrências | Decisões | Confirmadas / pendentes / indeterminadas | Acerto | ROI teórico (n) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Desceu | 50 | 106 | 72 | 80 / 1 / 25 | 39,22% | −29,81% (32) |
| Estável | 60 | 102 | 79 | 88 / 2 / 12 | 36,76% | −26,22% (68) |
| Subiu | 43 | 97 | 65 | 77 / 0 / 20 | 55,10% | −3,36% (28) |
| Por provar | 240 | 837 | 612 | 598 / 17 / 222 | 49,67% | −12,69% (220) |
| Total | 253 | 1.142 | 828 | 843 / 20 / 279 | 47,81% | −16,16% (348) |

Nas cotações emitidas e observadas posteriormente, houve 121 descidas, 31 casos estáveis, 133 subidas e 857 sem movimento comprovável. São rótulos retrospetivos. O relatório fornece o instante em que cada mudança ficou conhecida e indica se já havia prova de FT. Neste corte, nenhuma leitura atrasada de uma cotação anterior provou um movimento que faltava no instante do alerta de grande volume.

O volume disponível é o valor anunciado no ScanGoal. Para comparar com a 1xBet, faltam cotações repetidas da mesma seleção, mercado, linha e período, de uma fonte autorizada ou de registos manuais documentados, junto ao instante de decisão. Também faltam horários de validade ou suspensão, evidência de preço disponível ao montante pretendido e dados de taxas, limites ou rejeições. Dois alertas dão uma comparação de amostras, sem trajetória contínua de mercado. `ExternalQuote` fixa o formato de uma futura observação com fonte, proveniência, hora da cotação e hora da leitura.

## Casos auditáveis

Os números a seguir aos IDs, entre parênteses, são linhas do diário fixado no commit da reprodução. Pesquise o ID do episódio no JSON `audit` para obter os hashes completos, a primeira leitura, o limite de disponibilidade, os dados do jogo e as observações de FT.

| Episódio | Sinais originais | Decisão UTC | Resultado e prova |
| --- | --- | --- | --- |
| `sequence:32516:32605` · St. Louis City SC–Los Angeles | Stake Under 3 → ScanGoal Over 3, separados por 5h45m16s | 24 nov 02:44:15.572496 | FT 3–2. Originais 32516 (6933) e 32605 (7127). Edição 32516 (7138). Over a 1,83 ganhou. |
| `sequence:32583:32584` · Internacional–Vitória BA | ScanGoal Over 5 → Under 5, separados por 11 s | 23 nov 23:54:57.324309 | FT 2–5. Originais 32583 (7069) e 32584 (7070). Edição 32545 (7078). Over ganhou e o último alerta Under perdeu. |
| `sequence:32157:32233` · Chicago–Charlotte FC | Polymarket Under 3,5 → Over 3,5, separados por 2h34m09s | 22 nov 22:46:14 | FT 1–2. Originais 32157 (6458) e 32233 (6573). Edição 32233 (6633). Over perdeu. |
| `sequence:30144:30276` · Stoke–Birmingham | Polymarket Under 2,5 → Over 2,5 | 21 nov 10:33:54 | Pendente. Originais 30144 (3542) e 30276 (3683). |
| `sequence:32251:32256` · Deportivo Lara–Aragua | ScanGoal Over 4,5 → Under 5,5 | 22 nov 23:39:22 | FT 4–1. Originais 32251 (6595) e 32256 (6607). Edições 32251 (6616) e 32256 (6621). Ambas as seleções ganharam. |
| `quote:32556` · Internacional–Vitória BA | Polymarket Under 2,5, 20.000 USD, desceu de 1,92–1,96 para 1,89–1,90 | 23 nov 21:56:17.943431 | Cotações 32553 (6984) e 32556 (6990). Reverteu para 1,92–1,96 na 32560, observada às 22:04:45.732969. FT 2–5 na edição 32545 (7078). |
| `quote:32609` · Feyenoord–Porto | Polymarket AH2(−0,5), 23.000 USD, estável a 2,27 | 24 nov 03:15:28.575590 | Cotações 32606 (7128) e 32609 (7132). Pendente no corte. |
| `quote:32441` · Brentford–Everton | Stake empate, 13.000 USD, subiu de 3,45 para 3,50 | 23 nov 19:25:05.765607 | Cotações 32350 (6750) e 32441 (6845). FT 4–0 na edição 32109 (7007). |
| `quote:32626` · Daejeon Citizen–Beijing Guoan | Polymarket AH2(+0,5), 36.000 USD a 2,63, sem preço anterior comparável | 24 nov 06:15:51.740680 | Original 32626 (7155). Pendente no corte. |

A busca pelos três relatos localizou **180 decisões em 29 jogos** com inversão no mesmo mercado, período e linha em que a seleção Over ganhou, considerando Over em qualquer posição da sequência. Os candidatos mais recentes em jogos distintos incluem St. Louis City SC–Los Angeles, Internacional–Vitória BA e Brentford–Everton (`sequence:32446:32456`, FT 4–0). Sem datas, equipas ou IDs associados aos relatos, a correspondência desses três jogos com os casos do utilizador fica indeterminada. A lista mais ampla de inversões em que o Over ganhou está no campo `inversion_with_winning_over_episode_ids` do resumo.

## Investigação posterior e contrato experimental

O recorte mais direto para uma futura hipótese é a inversão no mesmo mercado, período e linha, estratificada pelo sentido, minuto, marcador, intervalo entre alertas e combinação de fontes. O segundo recorte é a relação entre volume e movimento de preços comparáveis antes da decisão, com controlo pelo preço inicial e pela fase. Os dados exibem seleção pela disponibilidade de cotações, repetição de partidas e mistura de mercados. Os ROIs da tabela são descritivos, com odds anunciadas, sem custos de execução. Uma avaliação futura precisaria de um protocolo registado antes de uma janela nova, com uma regra de dependência por partida e cobertura de cotações construída para esse fim.

A PR B pode consumir `goal_roi_experimental_temporal_context_v1` através de `llm_context` ou do JSON da CLI. Cada exemplo tem um ID estável, um instante de decisão, no máximo duas observações com hashes e um conjunto fixo de campos. O exportador emite até 100 episódios e limita campos textuais. A sua entrada é um replay com `as_of` explícito. A auditoria mantém FT, edições e movimentos conhecidos mais tarde em estruturas próprias. Hipóteses sequenciais podem ser expressas como predicados externos sobre estes campos e testadas por replay, mantendo o cérebro atual como consumidor independente das suas próprias regras. A PR A é invocada explicitamente pela CLI e funciona em leitura local.
