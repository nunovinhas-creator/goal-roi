# PR 2 — Independência estatística e estabilidade das hipóteses

**Draft para auditoria independente. Nada foi executado em produção.** Sem merge, deploy, alteração de workflows, secrets ou dados; nenhuma mensagem Telegram enviada. Comparação reproduzível em `scripts/audit_pr2_independence.py`, com resultado em `audits/pr2-metrics.json`.

- **Base:** `13ae250ccc4f14bf8a5bfa18844e68be0d6d5bc4`, que é `main` depois da PR 1 e dos primeiros ciclos de produção. Os SHA-256 de todos os ficheiros de `data/` estão no JSON.
- **Relógio fixo:** `2026-11-15T10:20:00Z`.
- **Python:** 3.11.18, a versão de produção.

## 1. Problema medido (deficiências 2 e 3)

- **Unidade errada nos limiares.** A evidência (`prob_above`) já contava um voto por jogo, mas o `n` que decide estados, fasquia corrigida, quadro, agenda e walk-forward contava **sinais**.
  - 184 hipóteses tinham n ≥ 30, mas só **120 têm ≥ 30 jogos**.
  - 4 das 5 abandonadas tinham 13–22 jogos; por exemplo, "Stake | Competição = seleções" tinha 31 sinais em 13 jogos.
  - "Stake | Dia = fim de semana" tem 37 sinais em 18 jogos.
- **Correlação dentro do jogo.** Há 182 jogos com resultado elegível e 32 deles têm mais de um sinal (até 16). Quase toda essa multiplicidade é feita de mercados ou seleções diferentes: só existem 3 pares repetidos de (jogo, mercado, seleção). São resultados correlacionados, não observações independentes.
- **Instabilidade.** Os estados eram recalculados de raiz, sem memória. Reconstituindo o histórico jogo a jogo com as regras da PR 1:
  - 24 hipóteses mudaram de estado 52 vezes;
  - uma mudou 7 vezes;
  - 8 mudaram 3 ou mais vezes.
- **Caso Kashiwa — Kawasaki** (15/11, 10:00 UTC): uma vitória levou 4 hipóteses redundantes de 0,940–0,945 para 0,951–0,955, e saíram 4 avisos "passou a promissora".

## 2. Desenho

1. **Unidades** (`insights.Votes`). Há três níveis: sinal → aposta (jogo, mercado, seleção) → jogo.
   - Sinais repetidos na mesma aposta contam uma vez.
   - Apostas diferentes no mesmo jogo repartem um voto.
   - Acertos e falhas acumulam-se separadamente por aposta, para que resultados contraditórios não se anulem.
   - A soma segue uma ordem fixa, por isso o resultado não depende da ordem dos dados.
   - O relatório mostra `n` (sinais), `bets` (apostas) e `games` (jogos).
2. **Limiares em jogos independentes:**
   - estados: 30, 100 e 300 jogos;
   - fasquia corrigida: `tested` passa a contar hipóteses com ≥ 30 jogos;
   - quadro, agenda e walk-forward: 8 jogos (`BOARD_MIN`);
   - balanço das propostas: 10 jogos (`TRACK_MIN`).

   A fórmula da evidência não muda.
3. **Estabilidade** (`brain.stable_status`). O estado é **reconstituído jogo a jogo**, pela ordem em que os resultados ficaram conhecidos (`known_at`). É determinístico, derivado só dos dados, e não cria nenhuma camada de estado nova.
   - **Histerese** — os limiares de entrada não mudam; para manter o estado admite-se o **dobro do erro**:
     - promissora: entra a ≥ 0,95 e mantém-se enquanto ≥ 0,90;
     - abandonada: entra a ≤ 0,10 e mantém-se enquanto ≤ 0,20;
     - forte/confirmada: mantêm-se enquanto ≥ 1 − 2·(1 − fasquia).
   - **Confirmação:** cada mudança exige que a condição nova se mantenha em `CONFIRM_STEPS = 2` jogos seguidos.
   - **Parâmetros:** foram fixados por este princípio **antes** de correr a comparação e não foram ajustados aos resultados históricos.
4. **Transparência.** Cada hipótese passa a expor:
   - `pending_status`: mudança à espera de confirmação;
   - `entry_status`: o estado que os limiares de entrada dariam sem memória;
   - `status_changes`/`last_change`: o histórico de mudanças.

   No dashboard, a coluna passa a "Jogos", mostrando os sinais entre parênteses quando diferem. O estado indica "a confirmar" ou "mantida pela histerese", e as notas descrevem os limiares em jogos.
5. **Telegram.**
   - O relatório declara `brain.status_rules = independent_games_hysteresis_v1`.
   - `notify.migration_key()` dá a esta regra uma chave própria (`status_rules:…`), por isso a mudança de regras faz **uma** migração silenciosa da referência.
   - O marcador `eligible_statuses_v1` da PR 1 e todo o histórico de entregas ficam intactos.
   - Relatórios sem `status_rules` mantêm exatamente o comportamento da PR 1.

## 3. Antes/depois (mesmo snapshot, mesmo relógio)

| Medida | PR 1 | PR 2 |
|---|---:|---:|
| Promissoras / abandonadas (elegível) | 15 / 5 | 13 / 0 (+4 promoções e 1 abandono à espera de confirmação) |
| Promissoras / abandonadas (exploratório) | 52 / 7 | 48 / 0 |
| Hipóteses testadas / fasquia corrigida | 184 / 0,999728 | 120 / 0,999583 |
| Hipóteses com `prob_above` diferente | — | 0 |
| Reconstituição histórica: hipóteses com mudanças / total de mudanças | 24 / 52 | 13 / 13 |
| Máximo de mudanças numa hipótese / hipóteses com ≥ 3 | 7 / 8 | 1 / 0 |
| Walk-forward, seguidas: apostas, ROI, ROI com odd −2 % | 135, −0,89 %, −2,60 % | 116, +0,99 %, −0,71 % |
| Walk-forward, não seguidas: apostas, ROI | 238, −2,59 % | 257, −3,42 % |
| Probabilidade de as seguidas renderem mais | 0,39 | 0,48 |
| Agenda: favorável / misto / contraditório / evitar | 1 / 5 / 2 / 5 | 1 / 2 / 2 / 8 |
| Primeiro relatório PR 2 sobre o estado Telegram de produção | — | **0 transições** (sem a migração seriam 11) |
| Tempo de `report.build` | 5,1 s | 7,2 s |

Verificações automáticas na comparação:
- dados históricos com os mesmos hashes antes e depois;
- *accuracy* exploratória, regime operacional (avisos de mudança de comportamento) e ROI estrito por grupo inalterados.

**As 11 hipóteses que mudam de estado:**
- **4 abandonadas → observação**, por terem só 13–22 jogos: Stake | Competição = seleções (31 sinais em 13 jogos), Stake | Sinal anterior de outro tipo = sim (31 em 14), Stake | Outros sinais = vários tipos (36 em 17) e Polymarket | odd 1,50–2,00 (31 em 22).
- **1 abandonada → observação, com abandono pendente:** Betfair | Dia = dia de semana (33 jogos, 0,0967). Desceu abaixo de 0,10 há um só jogo.
- **4 promissoras → observação, com promoção pendente:** as hipóteses do caso Kashiwa — Kawasaki.
- **2 observação → promissora, mantidas pela histerese** (visíveis em `entry_status`): "Jogo aberto | Golos até ao sinal = 1 ou 2" (0,9496, 43 jogos) e "Jogo aberto | Minuto = 31 a 60 & Ritmo < 90" (0,9429, 36 jogos). Ambas foram promovidas com confirmação e nunca desceram abaixo de 0,90.

**Caso Kashiwa — Kawasaki, reavaliado com as regras novas** sobre os snapshots `d371c4e` (antes da vitória) e `bf3ca31` (depois):
- antes: as 4 em observação nas duas versões;
- depois: PR 1 promissora ×4; PR 2 em observação com `pending_status = promissora` ×4, ou seja, **0 avisos**.

Com mais uma vitória, as 4 serão promovidas e anunciadas: a redundância continua, ver secção 5. Com uma derrota, nada é anunciado; a PR 1 teria enviado mais 4 avisos de despromoção.

## 4. Testes

21 testes novos em `tests/test_independence_stability.py`, todos offline e determinísticos:
- **Unidades:** repetição na mesma aposta; mercados correlacionados no mesmo jogo; resultados contraditórios; independência da ordem.
- **Limiares em jogos:** 40 sinais em 10 jogos; padrão Stake/fim de semana (33 sinais, 14 jogos); quadro, agenda e walk-forward.
- **Estabilidade:**
  - promoção no limite, que precisa de um segundo jogo;
  - 24 oscilações: 23 mudanças sem memória, 0 com as regras novas;
  - deterioração genuína detetada com atraso limitado a 1 jogo por mudança;
  - regresso legítimo à observação e saída do abandono;
  - estado mantido pela histerese visível;
  - banda das validadas;
  - sem evidência nova não há mudança;
  - coerência entre a reconstituição e o valor publicado.
- **Caso Kashiwa — Kawasaki:** 4 avisos → 0; vitória e derrota: 8 → 0; vitória, vitória e derrota: 0, 4, 0; a redundância documentada.
- **Telegram:** migração de regras silenciosa, única e com histórico intacto; mudança genuína posterior notificada; chave da PR 1 preservada.

As fixtures de três testes existentes passaram a declarar `games`, sem alterar nenhuma asserção, tal como a PR 1 fez com `in_roi`.

**Total: 153 testes**, com a rede bloqueada (`python scripts/test_offline.py`).

Contra o código da PR 1, 18 dos 21 testes novos falham. Os 3 que passam são intencionais:
- dois protegem propriedades que a PR 1 já tinha (massa de resultados contraditórios e independência da ordem);
- um documenta a redundância que fica para as PRs 3/5.

## 5. Limitações e riscos

- **A histerese mantém estados abaixo do limiar de entrada.** Hoje são 2 promissoras a 0,943–0,950. Está exposto em `entry_status` e no dashboard, mas promissora continua a não significar validada.
- **Redundância não resolvida (PRs 3/5).** Hipóteses sobre os mesmos jogos mudam juntas; uma promoção confirmada continua a dar 4 avisos.
- **Cronologia (PR 3):**
  - a reconstituição usa a fasquia corrigida atual em todo o histórico e a aproximação de 150 minutos;
  - as hipóteses do gerador são reconstituídas também sobre o período anterior ao seu registo;
  - o walk-forward continua a usar o registo atual.
- **Walk-forward:** a melhoria das seguidas (−0,89 % → +0,99 %) resulta da mudança da regra de seguimento (8 jogos em vez de 8 sinais). Com cerca de 100 jogos e probabilidade de as seguidas renderem mais de 0,48, **não é evidência de capacidade preditiva**.
- **Agenda:** passa a dizer "evitar" mais vezes (5 → 8), porque menos táticas boas atingem 8 jogos.
- **Fora do âmbito:**
  - o regime (avisos de mudança de comportamento) continua a contar sinais, e as variações estimadas por esse módulo podem sobrestimar a confiança;
  - os textos do Telegram ainda dizem "em N resultados", com N = sinais (PR 5).
- **Prudência deliberada:** mercados diferentes do mesmo jogo repartem um voto. Isto perde alguma informação, mas nunca sobrecontagem.
- **Desempenho:** `report.build` passa de cerca de 5 s para 7 s, por cada recolha ou leitura com mensagens novas.
- **Integração:** exige um corte controlado como o da PR 1 (pausa, corte e merge), porque uma execução antiga a correr depois do corte compararia referências diferentes. O `scripts/pr1_cutover.py` recusa correr com o marcador da PR 1 já presente, por isso terá de ser adaptado à chave da regra antes da integração, e essa adaptação terá revisão própria.

## 6. Reprodução

```bash
git switch --detach <SHA da PR 2>
python3.11 scripts/test_offline.py                                         # 153 testes
python3.11 -m unittest discover -s tests -p test_independence_stability.py -v
python3.11 scripts/audit_pr2_independence.py > /tmp/pr2-metrics.json       # mesmo JSON (a data/ tem de ser igual à base)
```

## 7. Parecer do autor

- **Técnico:** as unidades e os limiares passam a contar observações independentes. As oscilações de um jogo deixam de mudar estados e de gerar avisos, e as deteriorações genuínas continuam a ser detetadas com atraso limitado. A transição no Telegram é silenciosa e única.
- **Científico: INCONCLUSIVO** quanto a capacidade preditiva. A PR mede estabilidade e corrige a contagem da amostra; não demonstra vantagem.
- O parecer é do autor da implementação e não substitui a revisão independente.
