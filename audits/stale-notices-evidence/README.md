# P1 17/11/2026 — avisos Telegram de jogos antigos: evidência

## Incidente

Entre as 12:58:31 e as 13:07:16 UTC de 17/11/2026, depois de a tática
"Betfair | Faixa de odd = odd acima de 3,00 & Momento = durante o jogo" passar a promissora (12:56:54), o notificador
enviou 12 avisos "A seguir" (11 entregues, 1 incerto: Burnley Women) com sinais ao vivo de 14/11 20:26 a 17/11 12:11.
O mais recente tinha 47 minutos; o mais antigo, quase 3 dias. A fila escoou-se em lotes de 5 + 5 + 2
(`MAX_WATCH_PER_RUN`), em três execuções seguidas (recolha normal e leituras rápidas).

Causas:
- `notify.game_notices`: `if level == 'alerta' and not still_bettable(w, now)` — a validade temporal só se aplicava aos
  alertas certificados; os indícios passavam com qualquer idade.
- `brain.build`: `watch` incluía qualquer aposta sem resultado liquidado (`w is None`), de qualquer idade. Nos dados de
  17/11 há 59 apostas "em aberto" cujo jogo já tem resultado final conhecido (mercado não liquidável).

## Correção

- `scangoal/freshness.py`: regra única e documentada (hora original do sinal; ao vivo ≤ 30 min; pré-jogo só antes de
  um início conhecido; jogo terminado, horas ausentes, inválidas, futuras ou incoerentes: rejeitado).
- Barreira 1: `brain.build(..., as_of=now)` só põe em `watch` oportunidades atuais (e marca `phase` e `finished`).
- Barreira 2: `notify.game_notices` aplica a mesma regra, na hora do envio, a TODOS os níveis, antes de qualquer
  marcação; um sinal rejeitado nunca é marcado como avisado e nunca fica em fila.

## Ficheiros

| Ficheiro | O que prova |
|---|---|
| `make_fixture.py` | Gera `tests/fixtures/incident_2026_11_17/` a partir do git (relatório 66d7fcb, estado e06c1f2, registo 18488a2). |
| `tests_before_fix.txt` | `tests/test_stale_notices.py` com `notify.py`/`brain.py` originais: 18 falhas + 1 erro em 26. |
| `tests_after_fix.txt` | O mesmo ficheiro depois da correção: 26/26 OK. |
| `full_suite_after_fix.txt` | Suite completa: 529/529 OK. |
| `compare_reports.py` / `compare_reports.txt` | Relatório completo antes/depois, à mesma hora e com os mesmos dados (12:58:11 e 13:07): idêntico fora de `brain.watch`; o `watch` perde só os 12 sinais do incidente (`live_too_old`). |
| `replay_history.py` / `replay_history.json` | Os 24 avisos de jogo do registo real reavaliados na hora real do envio: os 12 anteriores ao incidente continuam válidos; os 12 do incidente são rejeitados (10 `live_too_old`, 2 `finished`). |

Nenhum script escreve em `data/`; todos trabalham sobre cópias temporárias ou sobre o git.
