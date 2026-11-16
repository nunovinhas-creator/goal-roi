# PR 5 — Reconstituição offline das últimas 24 h vs. histórico efetivo

Script: `audits/pr5-evidence/replay_24h.py` (só leitura; sem rede, credenciais nem Telegram). Dados: `replay-24h.json`.

- Janela: 2026-11-15T14:02:25Z → 2026-11-16T14:02:25Z (`main` congelado em `61cdb04`, terceira ronda; as rondas anteriores,
  até `60ba009` e `4af4c95`, deram 34 contra 37 com as mesmas diferenças — a janela atual já não inclui um jogo das 13:34).
- 239 commits do coletor que mexeram em `data/notify_state.json`, por ordem. Para cada um: relatório refeito com o código da
  PR 5 a partir dos dados desse commit, no instante do relatório de produção; notificador PR 5 corrido em cadeia a partir do
  estado real anterior à janela (`acec88e`, marcas PR 1 e PR 2).
- Envios reais lidos do histórico de `notify_state.json` (`sent_total`, `watch_sent`, `statuses`, `summary_day`).

## Resultado

| | Produção | PR 5 (reconstituição) |
|---|---|---|
| Mensagens | 36 | 33 |
| Avisos de jogos | 23 (23 jogos) | 23 (os mesmos 23 jogos, **no mesmo commit**) |
| Mudanças de estado depois do corte PR 3 (20:59) | 3 | 3 (as mesmas hipóteses, no mesmo commit) |
| Mudanças de estado antes do corte PR 3 (regras PR 2 em produção) | 9 | 6 |
| Resumo diário | 1 | 1 |
| Palavras "validad…", "confirmad…", "forte" | — | 0 |
| Avisos reais perdidos | — | **0** |

Estados do relatório refeito iguais aos de produção: 146/146 commits depois do corte PR 3; 48/93 antes (antes do corte,
produção usava as regras PR 2; a reconstituição usa as regras atuais em todos os relatórios e fez a re-referência silenciosa
no primeiro commit da janela, como o corte PR 3 fez às 20:58).

## Diferenças, uma a uma (todas antes do corte PR 3)

| Produção (regras PR 2) | PR 5 (regras atuais) | Explicação |
|---|---|---|
| 15:34 — `Golos até ao sinal = 1 ou 2` volta a observação | — | Pelas regras atuais já estava "em observação" no início da janela: não há mudança a anunciar (é o tipo de transição que o corte PR 3 silenciou às 20:58). |
| 15:34 — `Minuto = 31 a 60 & Ritmo < 90` volta a observação | — | Idem. |
| 15:34 — `Outros sinais = um só tipo & 2.ª parte` volta a observação | 15:43 (+1 commit) | O cérebro atual fixa a mudança na hora em que o resultado foi observado (15:37:42); o aviso sai no commit seguinte. |
| 15:34 — `Golos em falta ≤ 1 & 2.ª parte` volta a observação | 15:43 (+1 commit) | Idem. |
| 16:09 — `Parte do jogo = 2.ª parte` volta a observação | 16:17 (+1 commit) | Idem (hora do resultado observado). |
| 17:09 — `Dia = fim de semana & Ritmo < 90` volta a observação | 17:18 (+1 commit) | Idem. |
| 17:09 — `2.ª parte & Ritmo < 90` volta a observação | 17:18 (+1 commit) | Idem. |
| 17:18 — `Ritmo < 90 & Sinal anterior = não` volta a observação | 18:37, numa mensagem com o equivalente seguinte | Às 15:43 a mudança ficou **pendente** pela histerese (U9: pendente nunca é anunciado); fixada às 18:37:35. |
| 17:18 — `Outros sinais = um só tipo & Ritmo < 90` volta a observação | 18:37, como "e mais 1 equivalente" | Pendente até 18:37; partilha ≥ 85 % dos jogos com a anterior e é compatível: **uma** mensagem para as duas (E3). |

Nenhuma diferença envolve avisos de jogos, o resumo ou mudanças depois do corte PR 3. A PR 5 enviou menos ou o mesmo, e cada
aviso real de produção tem correspondente ou explicação acima.

## Limites

- Um commit pode conter várias execuções do notificador (as leituras rápidas gravam uma vez no fim); a reconstituição só vê
  o relatório gravado em cada commit.
- O relatório é refeito com `register=False` e o registo de hipóteses desse commit (já com as hipóteses registadas nessa
  execução), no instante do relatório de produção.
- Na janela não houve reforços do mesmo jogo/seleção nem hipóteses certificadas, por isso a consolidação de reforços e os
  alertas validados são exercitados pelos testes (U3, U4, U10, U19), não por esta reconstituição.
