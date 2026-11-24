# Auditoria independente pré-merge · PR #81

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

## Veredito técnico

**NO-GO no HEAD original `df4d31d961376195d0a87e20cff87bc39057952b`.** Um FT podia ser atribuído ao jogo errado, um FT já disponível num alias podia ser ignorado e algumas cotações podiam combinar preço e mercado de linhas diferentes. A janela LIVE também admitia decisões sem prova de que o mercado continuaria aberto durante a margem operacional.

**GO técnico para a revisão corrigida como componente experimental offline, sujeito à CI verde nesse SHA.** As regressões abaixo reproduzem os defeitos e verificam as correções na branch existente. O veredito científico é independente. O histórico e os resultados originais ficam no snapshot e no [relatório da PR A](https://github.com/nunovinhas-creator/goal-roi/blob/df4d31d961376195d0a87e20cff87bc39057952b/audits/temporal-intelligence/README.md).

## Defeitos, reproduções e correções

Não foi identificado P0. As reproduções são testes do motor experimental. Os testes existentes continuam a cobrir edições, recibos, publicações posteriores, janelas pré-jogo e colisões de identidade.

| Gravidade | Reprodução | Comportamento corrigido |
| --- | --- | --- |
| P1 | `test_later_live_ft_with_reused_id_cannot_settle_previous_fixture`. Uma primeira leitura LIVE com FT 7–0, no dia seguinte, usa as mesmas equipas e ID, sem início explícito. No HEAD original liquidava um jogo anterior. | Exigir que a janela de início compatível do original se sobreponha à do ID ligado. A edição continua a fornecer FT quando a identidade e o relógio são coerentes. |
| P1 | `test_ft_note_on_an_exact_known_alias_excludes_later_canonical_decision`. A nota de FT aponta para o alias exato já conhecido. O jogo canónico ainda admitia uma decisão LIVE posterior. | Transferir a nota quando o alias estava comprovado no instante, com início idêntico e sem colisão já observada. Preservar a ligação e o hash originais. |
| P1 | `test_stake_market_and_price_must_come_from_same_unambiguous_row`. Uma mensagem contém duas linhas 📊. O parser lia a seleção na segunda e a extração experimental usava a odd da primeira. | Exigir uma única linha de cotação com mercado, seleção e preço coerentes. Validar também o campo integral do volume anunciado. |
| P1 | `test_late_live_and_missing_market_clock_do_not_prove_open_window` e `test_live_totals_already_decided_by_the_score_are_excluded`. Uma decisão aos 86′ passava com uma margem operacional de 15 minutos. Alertas LIVE sem minuto ou com Over já ultrapassado no marcador também podiam passar. | Aplicar o fecho do período, incluindo partes e totais parciais, com a margem já usada pelo motor. Conservar o alerta na auditoria quando a janela ou o estado do mercado impedem uma decisão. |
| P2 | `test_stake_price_history_does_not_merge_native_market_labels` e `test_simultaneous_conflicting_quotes_cannot_be_ordered_by_read_clock`. Rótulos iguais depois de normalização ou duas emissões empatadas com odds incompatíveis podiam provar uma falsa direção de movimento. | Exigir também o mercado nativo normalizado na chave do preço. Se a última emissão conhecida é ambígua, classificar o movimento como indeterminado. |
| P2 | `test_partial_period_cannot_override_conflicting_second_half_scope` e `test_unspecified_whole_goal_total_has_no_asian_push_imputed`. Um período parcial substituía um rótulo de segunda parte. Um total sem regra asiática comprovada podia receber uma devolução asiática imputada. | Conservar a ambiguidade temporal. Separar as linhas inteiras ou fracionárias não binárias de totais com regras de liquidação por provar. |

Para as 2.809 decisões corrigidas, o relógio coincidiu com o máximo dos recibos exigidos. As referências de preço já estavam visíveis e tinham a mesma chave de seleção. Comparei oito cortes entre 12 e 23 de novembro com os episódios observados até esses mesmos instantes no replay final. O inventário elegível e as características da decisão coincidiram em todos os cortes. Um Under → Over nunca pode entrar antes de o segundo alerta ter sido observado. Se o Under foi observado mais tarde, o relógio espera também por essa observação. Os recibos legados mantêm a margem de cinco minutos, como no relatório original.

O inventário continua com 3.128 pares. As decisões de sequência passam de 1.695 para 993. A maioria das novas exclusões vem de relógios LIVE ausentes ou de mercados demasiado perto do fecho. Por exemplo, `sequence:31916:32088` ficou sem decisão. O último alerta, LIVE aos 76′, foi emitido às 18:36:30 e ficou observável às 18:43:29 UTC em 22 de novembro. A janela com a margem operacional termina depois do fecho regulamentar do mercado. Os marcadores FT dos episódios comuns ao snapshot ficaram iguais após as correções.

## Política de escolha e comparadores

Selecionei a primeira decisão admissível por fixture canónico e por regra. A ordenação fixa usa disponibilidade UTC, emissão e ID numérico da âncora, depois emissão e ID do antecedente. A primeira decisão reserva o jogo mesmo quando fica sem FT ou sem preço pontual. A auditoria adicional também conta uma vez cada alerta-âncora. Os números de subgrupos podem sobrepor-se porque cada regra tem a sua própria seleção.

Para cada grupo de inversão, o comparador é um alerta admissível na direção da âncora, sem alerta oposto do mesmo mercado e período já emitido e observado nesse jogo. Têm de coincidir fonte, tipo de sinal, mercado nativo, período, linha, fase, classe da odd anunciada e tipo de preço pontual ou intervalo. Em pré-jogo exige-se classe de tempo até ao início. Em LIVE exige-se classe de minuto e o mesmo marcador. Os casos são processados cronologicamente e recebem o comparador mais próximo já conhecido nas 72 horas anteriores, de um jogo distinto ainda livre e fora do conjunto dos casos dessa regra. Incidências futuras podem determinar a reserva de jogos no conjunto retrospetivo de casos. O protocolo proposto fecha a classificação de cada jogo no seu primeiro alerta elegível.

Os desfechos visíveis no corte são usados apenas depois da seleção. O ROI é teórico à odd pontual anunciada. A amostra de ROI exige FT utilizável, preço e decisão admissível. As devoluções contam no denominador. A análise distingue acerto, erro, meia liquidação, devolução, falta de FT, pergunta de liquidação indeterminada e casos sem preço pontual. O JSON inclui cada seleção e cada par comparador para inspeção.

## Efeito da repetição

| Cohorte | Decisões sem deduplicar | Jogos | ROI por par (n com preço) | ROI por alerta-âncora (n) | ROI por jogo (n) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sequências, HEAD original | 1.695 | 154 | +6,49% (988) | −2,04% (279) | −1,93% (111) |
| Sequências, corrigido | 993 | 124 | +11,58% (569) | −1,66% (203) | −6,79% (88) |
| Inversões, HEAD original | 793 | 105 | +4,76% (463) | −1,79% (194) | −16,05% (74) |
| Inversões, corrigido | 489 | 90 | +9,78% (265) | −2,13% (136) | −19,66% (61) |

Os cinco jogos mais repetidos representam 651 decisões de sequência no HEAD original, 38,4% das 1.695. Um único jogo representa 190 decisões. Para inversões são 306 decisões nos cinco jogos e até 91 num jogo. A escolha fixa reduz tanto a repetição do mesmo alerta como o peso das partidas com muitos alertas. Na revisão corrigida, as 90 primeiras inversões por jogo incluem 36 acertos, 39 erros, 12 devoluções, um jogo sem FT e 26 sem preço pontual. Os dois últimos números podem sobrepor-se e os restantes indeterminados incluem contratos sem liquidação por FT90.

## Inversões

As tabelas seguintes restringem cada grupo ao mesmo mercado e período. `n` dá o número com lucro unitário computável. O diferencial é a média do caso menos o controlo entre pares em que **ambos** têm preço e liquidação.

| Regra | HEAD original por jogo | Corrigido por jogo | Acertos / erros / devoluções após correção | Comparadores estritos após correção | Diferencial (pares com preço) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Under → Over, mesma linha | 40 jogos, +19,00% (n=19) | 37 jogos, +33,00% (n=17) | 23 / 12 / 1 | 9 de 37 | +35,00 p.p. (3) |
| Over → Under, mesma linha | 35 jogos, −5,50% (n=18) | 35 jogos, −5,50% (n=18) | 13 / 20 / 2 | 7 de 35 | −59,25 p.p. (4) |
| Under → Over, linhas diferentes | 61 jogos, −16,56% (n=55) | 45 jogos, −12,64% (n=39) | 18 / 18 / 9 | 18 de 45 | −1,44 p.p. (16) |
| Over → Under, linhas diferentes | 39 jogos, −31,24% (n=29) | 31 jogos, −61,05% (n=21) | 8 / 20 / 3 | 0 de 31 | — |

| Regra após correção | Jogos pré→pré / pré→LIVE / LIVE→LIVE | Jogos por intervalo <15 / 15–<60 / ≥60 min | Jogos entre fontes iguais / distintas |
| --- | ---: | ---: | ---: |
| Under → Over, mesma linha | 28 / 3 / 8 | 15 / 19 / 19 | 33 / 7 |
| Over → Under, mesma linha | 29 / 4 / 6 | 25 / 13 / 14 | 32 / 8 |
| Under → Over, linhas diferentes | 10 / 27 / 17 | 6 / 14 / 32 | 22 / 32 |
| Over → Under, linhas diferentes | 13 / 14 / 10 | 7 / 9 / 21 | 21 / 17 |

Para Under → Over na mesma linha, a faixa inferior a 15 minutos deu −13,40% em 5 decisões com preço. As faixas de 15 a menos de 60 e de pelo menos 60 minutos deram +67,64% (11) e +59,10% (10). Em pré→pré há seis comparadores, em pré→LIVE há dois e em LIVE→LIVE há um. O detalhe com denominadores, FT, odds, exclusões e emparelhamentos de cada combinação de fontes está na saída do script. As amostras esparsas e sobrepostas impedem atribuir as diferenças ao intervalo ou à fonte.

Casos do próprio diário incluem `sequence:32157:32233` (Under 3,5 → Over 3,5, FT 1–2, perda), `sequence:30729:30816` (Under 5 → Over 5, FT 3–2, devolução asiática) e `sequence:30144:30276` (Under 2,5 → Over 2,5, FT por provar). O caso favorável `sequence:32516:32605` conserva a decisão às 02:44:15.572496 UTC de 24 de novembro, seguida de FT 3–2. A auditoria continua sem identificar inequivocamente os três relatos originais.

## Volume e movimento da odd

| Movimento | HEAD original por alerta | HEAD original por jogo | Corrigido por alerta | Corrigido por jogo |
| --- | ---: | ---: | ---: | ---: |
| Desceu | −29,81% (32 de 72) | −22,86% (21 de 39) | −14,54% (24 de 60) | −14,74% (19 de 35) |
| Estável | −26,22% (68 de 79) | −32,58% (45 de 50) | −18,88% (60 de 67) | −27,90% (40 de 44) |
| Subiu | −3,36% (28 de 65) | −31,71% (17 de 37) | −41,86% (14 de 45) | −64,25% (12 de 31) |

Em cada célula, o numerador é a amostra com preço pontual e liquidação. O denominador é o total de decisões elegíveis para o método da coluna. O HEAD original tinha 23 jogos em ambos os braços descida/estável, 23 descida/subida e 21 subida/estável. Uma análise adicional reserva a primeira decisão com movimento provado em qualquer braço. Na revisão corrigida, isso deixa 17 jogos em descida, 39 em estabilidade e 11 em subida. Os ROIs são +6,14% (7), −19,89% (36) e −67,33% (6), respetivamente.

- **Fonte e fase.** No HEAD original, as subidas tinham ROI de −26,00% na Polymarket (11) e +11,29% no Stake (17). Descidas tinham −23,46% (24) e −48,88% (8), respetivamente. No conjunto original de descidas e subidas havia 32 decisões LIVE em cada braço. A estabilidade tinha 18. A revisão retém 20, 12 e 6, respetivamente. A ausência de minuto no Stake limita fortemente a comparação LIVE.
- **Mercado, seleção, linha e período.** As decisões com movimento provado eram todas no período regulamentar. O handicap, o total de golos e o vencedor contribuem em proporções distintas. A revisão remove a subida atribuída a `quote:32041` a partir da comparação entre os IDs 31984 e 32041. O mercado nativo mudou de 1x2 para Match Result e o segundo alerta é LIVE. A comparação do movimento exige prova de identidade do contrato. As distribuições por papel da seleção, linha exata e mercado estão no JSON.
- **Odd inicial e sobreposição.** No HEAD original, 7 das 72 descidas partiam de uma odd mínima anunciada ≥3,50. Para subidas era 1 de 65. Depois da correção, 29 das 35 primeiras descidas por jogo têm pelo menos um jogo estável de outra fixture com fonte, mercado, período, papel da seleção e linha iguais, considerando todo o corte. Para subida/estável são 25 de 31, e para descida/subida são 22 de 35. Ao exigir também fase antes e depois, classe da odd inicial, tamanho do volume, disponibilidade temporal, contexto da partida e forma do preço, o emparelhamento encontra zero pares nas três comparações. A análise descritiva identifica a mudança de composição. A diferença ajustada fica por estimar.
- **Cobertura de preço.** Na revisão, 440 decisões de grande volume continuam sem direção de movimento comprovada. Outras 172 têm uma direção comprovada com preço pontual e FT em apenas 98. A linha de análise por jogo tem ainda menos observações. As odds são as anunciadas pelo ScanGoal. Uma comparação económica com a 1xBet precisa do preço documentado e disponível para a seleção na decisão.

## Protocolo prospetivo e plano da PR B

O [protocolo proposto](protocol-v1.json) fixa população, escolha por jogo, comparador, desfechos, custos por unidade, janelas, cobertura mínima, três contrastes e tratamento de dados ausentes. H1 testa um Under anterior na primeira oportunidade Over 2,5 comparável em pré-jogo. H2 compara cada um dos movimentos, subida e descida, com estabilidade, dentro da mesma fonte e dos mesmos contratos e contextos. Os parâmetros foram escolhidos após a descoberta. O contador confirmatório inicia em **zero**.

O SHA‑256 do protocolo publicado nesta revisão é `8609974afa58228b4c55da1773b570f3fc7589c87f54f3973c257c3142ac1ae6`. A publicação no commit da auditoria fixa os bytes e o marco da proposta. A entrada em vigor requer registo versionado com hora verificável do servidor, aprovação explícita e referência às fontes autorizadas. A janela depende desse marco e exclui a amostra de descoberta. Alterações posteriores da hipótese ou metodologia exigem outro marco e uma janela nova.

Na PR B, primeiro validar o hash e a prova de registo do protocolo. Depois implementar um replay experimental que reserve cada fixture no primeiro alerta elegível, com recibo da decisão e proveniência da cotação. Construir o emparelhamento a partir das características conhecidas no corte da decisão e congelá-lo antes da leitura dos desfechos. Avaliar os três contrastes depois do seguimento, com cobertura, sensibilidade a custos e tratamento de dependência por dia. Testar relógios, reexecução, dados ausentes e exclusão do histórico de descoberta. A interface existente de eventos alimenta este circuito, preservando o cérebro, as estratégias congeladas e a certificação.

## Reprodução e verificação

Usar a branch de auditoria e o histórico arquivado em `82198c0af45270c6028e58255c21a46c55b19aa1`. O corte é **2026-11-24T08:22:00Z**. O ficheiro `messages.jsonl` tem SHA‑256 `ac2914df9e42d27f61d41629aa1b6571131b5db661589e7e0c2fc9c4b393de8b`.

```sh
mkdir -p /tmp/temporal-pr81-data /tmp/temporal-pr81-head
git archive 82198c0af45270c6028e58255c21a46c55b19aa1 \
  data/messages.jsonl data/results_hl.json data/results_bsd.json data/details_hl.json \
  | tar -x -C /tmp/temporal-pr81-data
git archive df4d31d961376195d0a87e20cff87bc39057952b scangoal \
  | tar -x -C /tmp/temporal-pr81-head
(cd /tmp/temporal-pr81-head && python -m scangoal.event_audit \
  --data-dir /tmp/temporal-pr81-data/data --as-of 2026-11-24T08:22:00Z --format audit) \
  > /tmp/temporal-pr81-original.json
python -m scangoal.event_audit --data-dir /tmp/temporal-pr81-data/data \
  --as-of 2026-11-24T08:22:00Z --format audit > /tmp/temporal-pr81-corrected.json
python -m scripts.audit_temporal_premerge --audit /tmp/temporal-pr81-original.json \
  > /tmp/temporal-pr81-original-study.json
python -m scripts.audit_temporal_premerge --audit /tmp/temporal-pr81-corrected.json \
  > /tmp/temporal-pr81-corrected-study.json
python -m unittest discover -s tests -p test_event_intelligence.py -v
python -m unittest discover -s tests -p test_audit_temporal_premerge.py -v
python -m unittest discover -s tests -v
node --test ops/continuity/watchdog.test.mjs
git diff --name-status 82198c0af45270c6028e58255c21a46c55b19aa1..HEAD
sha256sum audits/temporal-premerge/protocol-v1.json
```

O diff completo da PR contém apenas módulos experimentais, o script de análise, testes e documentação de auditoria. A reexecução da CI do HEAD original serve como referência de infraestrutura. A CI da branch corrigida valida o diff final. A PR permanece em draft.
