# PR #75 · Reconstrução cronológica do simulador

## Defeito

Até `7bdaa4f`, cada posição do simulador tinha um único instante de liquidação (`z`) e o pagamento em vigor no corte
do relatório (`w`). Quando o FT era corrigido depois de a aposta estar liquidada, o relatório seguinte movia `z` para a
hora da correção e trocava `w`. O saldo entre a primeira liquidação e a correção era reescrito: a stake voltava a ficar
reservada nesse intervalo e as decisões tomadas com o pagamento já conhecido desapareciam.

`before_correction.cjs` repete o ciclo do simulador de `7bdaa4f` sem alterações (banca 100 €, stakes a 100 %, odd 2):

| Relatório | Banca | Apostas | Curva |
|---|---|---|---|
| Publicado a −25 min (A ganhou a −60) | 400 € | 2 | 100 → 200 → 400 |
| Depois da correção a −20 (A perdeu) | 0 € | 1 (B sem saldo) | 100 → 0 |

## Correção

- `scangoal/report.py`: a resolução do resultado passou a ser uma função do corte (`resolve_score(cut)`). Para cada
  posição, `settlement_events` reaplica-a a cada instante em que surgiu uma observação, sempre depois da decisão e
  nunca depois do corte do relatório, e regista as transições de pagamento em `s: [[instante, pagamento|null], ...]`.
  `null` indica resultado contraditório ou em quarentena. `z`/`w` passam a ser o pagamento em vigor no corte e o
  instante em que entrou em vigor. Bot marks e minutos de golo da Highlightly, sem versões, mantêm um único instante.
  Se a reconstrução não reproduzir o pagamento usado pelo relatório, não se inventa histórico.
- `docs/dashboard.js`: `simulatePortfolio` é uma função pura. Processa colocações e eventos por ordem do tempo.
  Num empate de instante, a colocação vem primeiro. A stake fica reservada até ao primeiro pagamento comprovado. Uma
  correção move o saldo disponível pela diferença de pagamento. Uma quarentena repõe a reserva. Cada posição tem
  um estado único, por isso não há dupla contabilização. A simulação pára no corte do relatório. O painel mostra o
  saldo realizado, o capital reservado, o saldo disponível e o número de correções aplicadas.
- A validação recusa um histórico fora de ordem, com pagamentos repetidos, anterior à decisão, posterior ao corte
  ou incoerente com `z`/`w`.

## Evidência

- `tests/test_simulator_settlement_history.py`: ganha → perdida, perdida → ganha, quarentena depois de liquidada,
  revisão sem mudança de pagamento, determinismo e invariância. Cortar o relatório mais recente num instante anterior
  reproduz o relatório construído nesse instante. Quatro dos sete testes falham com o código de `7bdaa4f`.
- `tests/dashboard_refresh.cjs`, `settlementCorrections`: os mesmos casos no navegador, com valores calculados à mão,
  mais o saldo disponível negativo depois de reverter ganhos já reapostados, o empate de instante, a reordenação e a
  rejeição de históricos inválidos.
- `invariance_real.py` / `invariance_real.json`: dados reais de `main` (1 924 posições). Os cortes a −24 h e a −72 h
  coincidem com os relatórios construídos nesses instantes, com 0 diferenças. Até 23/11/2026 não houve nenhuma correção
  de FT que mudasse um pagamento (`payoff_revisions: 0`). Nos dados atuais o defeito estava latente.

## Limites

O BSD e a Highlightly guardam só o valor mais recente de cada jogo. Se uma correção chegar por essas fontes, não fica
histórico e o simulador volta a usar o instante único (comportamento anterior). As odds são as anunciadas, sem custos
nem prova de execução. O ROI simulado é descritivo e não certifica nada.

## Regressões verificadas

- O relatório completo dos dados reais, cortado a 2026-11-23T12:00Z, é idêntico byte a byte antes e depois da
  refatoração da resolução, sem contar a secção `simulation`. ROI, liquidações, certificação e identidade dos
  encontros ficam iguais.
- Instrumentação original da auditoria #71 (`browser.cjs`), com o `docs/report.json` publicado: W04, W05, W06 e W09
  falham em `main` e passam na #75. Com um relatório regenerado pela #75, a W09 original fica «NÃO VERIFICADO».
  Altera só `timeline`, que o simulador já não lê quando existe `simulation`. O mesmo cenário sobre
  `simulation.entries` é coberto por `w09TimedEntries` e `simultaneousExposure`.
- `audits/final-recovery-2026-11-21/blockers.py` P01 falha também em `main`. Essa sonda testa a perda total do runner
  antes do artefacto com o diário Cloudflare, que a #74 eliminou. Não é uma regressão desta PR.
