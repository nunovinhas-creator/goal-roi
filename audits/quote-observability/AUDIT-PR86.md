# Auditoria científica e técnica da PR #86

**GO técnico para a fase 0 exclusivamente offline, com as correções desta revisão. NO-GO para ativar a fase 1, iniciar avaliação confirmatória ou alegar rentabilidade.** O desenho da fase 1 fica disponível para revisão e requer autorização separada.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

## Âmbito e método

O objeto auditado foi o HEAD `8d5a4271b584d06683f3e27e9cd5e7c3bca7d9ad`. A PR #86 assenta em #85 (`6401562`), que assenta em #84 (`01cef30`) e #83 (`0798e69`). A #83 fornece definição, pesquisa determinística e seguimento de hipóteses. A #84 alarga o vocabulário e melhora a recuperação estrutural, mas a sua inferência foi bloqueada para confirmação. A #85 define comparação no mesmo conjunto de risco e corrige a previsão de meias liquidações. A #86 extrai evidência do jornal e diagnostica cobertura. Foram usados contraexemplos novos, reprodução a partir do commit auditado e comparação explícita com as saídas congeladas. Há independência dos novos casos de teste face aos bancos de calibração. A mesma assistência que implementou a fase 0 executou esta revisão. Continua a ser necessária revisão externa por outra pessoa antes de uma validação confirmatória. [PR #83](https://github.com/nunovinhas-creator/goal-roi/pull/83) · [PR #84](https://github.com/nunovinhas-creator/goal-roi/pull/84) · [PR #85](https://github.com/nunovinhas-creator/goal-roi/pull/85)

## Defeitos confirmados

| Prioridade e causa | Contraexemplo | Resultado desta revisão |
| --- | --- | --- |
| Alta · ausência do antecedente no controlo | A cotação Stake do controlo é emitida em t−10 min. Um antecedente Polymarket elegível do mesmo jogo chega em t−4 min, antes do fecho do caso. A v3.1 emparelha 1 controlo porque pesquisa o prefixo só até à emissão da cotação Stake. | O diagnóstico preserva o par histórico e exclui-o da amostra de planeamento com `control_history_changed_since_quote`. O filtro conservador rejeita um alerta interveniente conhecido no instante. A correção da regra de decisão exige protocolo novo. |
| Alta · ordenação arbitrária dentro do recibo | Caso e comparador têm a mesma emissão, disponibilidade e contrato. Inverter apenas a ordem dos IDs do lote altera o resultado v3.1 de 0 para 1 par. | O caso de regressão fixa a reprodução. A densidade avalia o lote por inteiro. A ordem do emparelhador congelado é uma limitação conhecida do diagnóstico histórico e bloqueia o seu reaproveitamento confirmatório. |
| Alta · recibos mal tipados entram no relógio de potência | `receipt_version: true` e `receipt_version: 1.0` são tratados como `completed_page` no replay congelado apesar de o extrator já os rejeitar. Dois falsos recibos criavam um dia de exposição. | A projeção verificada reconstrói relógios, identidade, prefixos e liquidação a partir de uma cópia temporária que exclui esses registos. Expõe a contagem de anomalias e mantém o replay histórico documentado. |
| Média · preço simultâneo e cotação mais recente | Um intervalo `1.90–1.90` ou dois pontos incompatíveis emitidos no mesmo segundo contavam como pontos na densidade. Um recibo tardio de uma emissão mais antiga também podia substituir uma emissão mais recente. | Densidade e amostra de planeamento exigem preço pontual explícito. A densidade aplica o conflito por emissão e disponibilidade, e escolhe a última emissão conhecida. No snapshot o indicador de janelas de ±45 min com os dois preços pontuais muda de 82 para **80**. |
| Média · proveniência de edições | Um alerta Polymarket editado para cabeçalho de Stake aparecia como contexto de outra fonte no mesmo jogo. A consulta só com recibos completos recuperava o estado anterior quando uma revisão já conhecida tinha apenas recibo antigo. | A revisão com mudança de fonte fica sem identidade de jogo. A consulta atual resolve a publicação e os conflitos antes de aplicar o requisito de recibo completo. |
| Alta · simulação de resultados impossíveis | Um Over 2,25 pode liquidar em −0,5 com dois golos, mas nunca em +0,5. A simulação podia criar ambos. Com odd 1,10, o baseline justo de 90,9% torna +12 pp impossível. O simulador cortava em 100% e continuava a reportar +12 pp. | A estimativa fica ausente para linhas de quarto e efeitos impossíveis às odds observadas. A saída distingue ausência de pares observados, suporte abaixo do mínimo, modelo inaplicável e simulação executada. |
| Média · atribuição retrospetiva | Os motivos de falha congelados consultam o catálogo do snapshot, incluindo relógios de cotações que chegaram depois do caso e a reserva agregada da hipótese. | A saída identifica a tabela como heurística retrospetiva. O total de pares reproduz o emparelhador. As categorias separadas servem para diagnóstico de cobertura. |

A verificação de recibos e o filtro de controlo só qualificam o diagnóstico da PR #86. O hash do intérprete congelado e os artefactos das dependências conservam a comparabilidade com os resultados publicados. Uma sequência ausente num feed esparso pode ser indeterminável. Ausência de alertas, silêncio do coletor, correspondência incompleta e períodos sem livro continuam a ser causas distintas de falta de informação.

## Resultado no snapshot

Corte `2026-11-24T08:22:00Z`. A reprodução inicial do HEAD auditado foi idêntica byte a byte ao seu JSON versionado. A revisão conserva SHA-256 `1f2bf65d751dc735d2a0688c700e7ff24ad9b41474e87f7768efc38c9e102944`, 54 hipóteses determinísticas, 1.851 casos (505 únicos), 13 pares liquidados (5 identidades de par) e a cascata histórica completa. Os 348 preços recuperados, 1.464 eventos distintos de golo reportados e 2.167 versões ligadas a jogos com volume acumulado também se reproduzem. [Snapshot](snapshot.json)

Os dois braços dos 13 pares têm apenas recibos antigos. Em oito dos 13 emparelhamentos, o histórico do controlo recebeu pelo menos outro alerta emitido entre a cotação do controlo e o fecho do caso, já observável no instante. Isso mede a exposição à lacuna de relógio. São oito emparelhamentos, e a presença de um alerta interveniente por si só não prova um antecedente elegível.

Há **zero pares** na janela com recibos completos e zero pares elegíveis para planeamento. O esquema repetido produz zero retenção condicional. A amostra não estima a cadência futura nem a potência prospetiva. Cotações mais frequentes podem aumentar observabilidade, mas o número de comparadores depende também do contrato, da oportunidade terminal, do histórico determinável e da alocação de jogos.

O minerador da v2 continua a propor hipóteses sem intervenção. Os campos enriquecidos constituem entrada para uma futura versão do motor. O benchmark de uma versão que combine esses campos deverá medir o terminal certo e o caminho completo, além da presença de um jogo na regra encontrada. Esse benchmark e a avaliação confirmatória são trabalhos distintos. [Desenho da fase 1](PHASE-1-DESIGN.md)

## Reprodução

```sh
PYTHONPATH=. python audits/quote-observability/diagnose.py --verify-frozen --summary > /tmp/quote-observability.json
cmp /tmp/quote-observability.json audits/quote-observability/snapshot.json
python -m unittest discover -s tests -p test_quote_observability.py -v
node --test ops/continuity/watchdog.test.mjs
git diff --check 8d5a4271..HEAD
```

Os ensaios sintéticos correm no workflow existente. A revisão modifica apenas observabilidade, auditoria e os respetivos testes.
