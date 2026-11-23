# Quadro de estratégias e lista curta (issue #56)

Estado: **experimental**. Nenhuma certificação, nenhum alerta novo, nenhuma aposta automática.

## O que existe

- `scangoal/strategy_board.py` — classifica cada hipótese do cérebro (e a regra congelada PR #55) num de quatro estados,
  com motivo explícito, usando só evidência **prospetiva** (uma unidade por encontro, aposta decidida depois do registo da
  hipótese e antes de qualquer resultado, odd anunciada ×0,98):
  - **A. Em observação** — menos de 10 resultados prospetivos comprovados.
  - **B. Evidência insuficiente** — tem resultados, mas falta pelo menos um requisito (lista de motivos).
  - **C. Rejeitada** — falhou um critério científico, económico ou operacional.
  - **D. Validada** — exige certificação ativa, ≥ 300 resultados, limite inferior corrigido > 0, uma só fase, **prova de
    odds executáveis e custos conhecidos**. Não existe hoje nenhuma fonte para os dois últimos, por isso D fica vazio
    por construção (`EXECUTABLE_PRICE_EVIDENCE = False`, `COSTS_KNOWN = False`).
- Critérios congelados em `CRITERIA` (versão `strategy_board_criteria_v1`, registo final a 2026-11-23T14:32:19Z) e trancados
  por `CRITERIA_SHA256`; `tests/test_strategy_board.py` falha se mudarem. Os limiares vêm de constantes que já existiam
  (10, 30, 300 jogos; α = 0,05; ≥ 10 dias; ≤ 20 % num dia), não foram escolhidos a olhar para os resultados.
  Cronologia honesta: os limiares estavam escritos às 14:01Z; a lista curta foi selecionada com dados até às 14:00Z;
  o registo final das 14:32Z só acrescentou regras de falha fechada e corrigiu a contagem de Bonferroni (defeitos
  encontrados pela revisão independente), sem mudar nenhum limiar nem a seleção.
- `audits/strategy-lab/shortlist-v1.json` — lista curta congelada (SHA em `SHORTLIST_SHA256`). Só contam apostas
  decididas **depois** de `registered_utc`; os resultados que serviram para a escolher nunca contam como prova.
  Correção de Bonferroni só sobre o tamanho da lista (2), em vez de sobre as ~5 000 candidatas do quadro.
- `python -m scripts.strategy_board [--as-of ISO] [--select-shortlist]` — só leitura; `--select-shortlist` reproduz a
  regra de seleção da lista curta no mesmo relógio.
- A LLM recebe, no pedido, o veredicto do motor sobre as suas próprias propostas e os motivos (estado, resultados,
  o que falta). Continua sem decidir nada: o motor aceita, rejeita e certifica por regras.

## Regras de rejeição (C)

Hipótese de "evitar" (não é aposta, fica fora das contagens) · registo não verificável · condição posterior ao sinal ·
condição não provada no instante da decisão · mistura pré-jogo e LIVE · abandonada pelo cérebro · mais de 20 % dos jogos
admitidos sem resultado comprovado ou em quarentena (com ≥ 30 admitidos) · ≥ 30 resultados em ≥ 10 dias com limite
superior a 95 % abaixo de zero · mesmo conjunto de jogos que uma candidata já classificada.

## Reprodução da linha de base (2026-11-23T14:00:00Z)

```
python -m scripts.strategy_board --as-of 2026-11-23T14:00:00+00:00
python -m scripts.strategy_board --as-of 2026-11-23T14:00:00+00:00 --select-shortlist
python -m scripts.opportunity_ranking
```

Resultado nesse instante: 14 118 hipóteses registadas (14 020 do gerador, 98 da LLM, 12 delas de "evitar");
A = 3 290, B = 1 730, C = 9 087, **D = 0**. Correção de Bonferroni sobre 5 165 candidatas com ≥ 10 resultados, rejeitadas
incluídas (z = 4,27): nenhuma com limite
inferior positivo. Só 110 candidatas têm resultados espalhados por ≥ 10 dias; 58 delas com ROI positivo, todas da família
"Jogo aberto" (LIVE). Regra PR #55: 6 jogos futuros, 5 com FT confirmado (3 V, 2 D), 1 pendente — em observação.

**Veredito: RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

## Revisão independente (2026-11-23)

Um revisor sem acesso às conclusões tentou falsificar o quadro. Defeitos confirmados e corrigidos (cada um com teste de
regressão que falha na versão anterior, `IndependentReviewRegressions`):
1. A família de Bonferroni excluía as candidatas já rejeitadas (subestimava a multiplicidade).
2. Evidência com NaN, `None`, tipos errados, fases vazias/desconhecidas ou contagens inconsistentes não falhava fechada
   (latente: só as duas flags de preços e custos impediam "validada").
3. A seleção da lista curta aceitava fase desconhecida.
4. Entrada malformada da regra PR #55 e um quadro malformado na LLM podiam interromper o relatório.
Confirmado sem defeito: comportamento anterior de `economic_summary` (900 entradas aleatórias contra `main`), fórmula do
erro-padrão agrupado (implementação independente), ausência de *look-ahead* na lista curta, SHA da lista, filtros de
fase e de condições, ausência de dados pessoais no pedido à LLM.

Pontos plausíveis registados, não alterados: a regra PR #55 mantém a sua janela própria (desde 19/11) dentro da lista
curta; a redundância é por conjunto de jogos, não por apostas; a candidata selecionada está exatamente no mínimo de 10 dias.

## Limitações conhecidas

- Os limites são aproximações normais com erro-padrão agrupado por dia; descritivos, não substituem o e-processo.
- Bonferroni é conservador sob dependência positiva entre hipóteses (muitas partilham jogos): prefere falsos negativos.
- O comparador por família e fase é sobreposto à candidata; nunca entra na decisão.
- Sem odds executáveis, comissões nem limites de aposta, nenhum resultado é líquido: tudo é à odd anunciada ×0,98.
