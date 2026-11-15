# PR 3 — Validade cronológica, testes múltiplos e avaliação prospetiva

**Draft para auditoria independente. Nada foi executado em produção.** Sem merge, deploy, alteração de workflows, secrets, sessões ou `data/`; nenhuma mensagem Telegram enviada; nenhuma migração executada. Comparação reproduzível em `scripts/audit_pr3_chronology.py`, com resultado em `audits/pr3-metrics.json`.

- **Base:** `82ab9d86593055fbc98420265a469728d914288f` (`main` com a PR 2, no início desta PR). Os SHA-256 de `data/` estão no JSON e conferem com a base (`data_matches_base: true`); a auditoria não altera `data/` (`data_unchanged_by_audit: true`).
- **Relógio fixo:** `2026-11-15T14:34:34Z` (hora do commit base). **Python:** 3.11.
- **Rede bloqueada** na auditoria e nos testes; sementes fixas (`20261115`).

## 1. Problemas medidos

**A. Fasquia corrigida retroativa.** Na PR 2, `evaluate()` calculava a fasquia com o número **atual** de hipóteses testadas e aplicava-a a toda a reconstituição. Demonstrado em `CorrectedBarIsRetroactive`: os mesmos 102 jogos davam forte com 1 hipótese e promissora com 401.

Há dois problemas adicionais, encontrados nesta PR:
- **Olhares repetidos.** O estado é reavaliado a cada jogo novo. Com uma só hipótese e **nenhuma vantagem** (p = 0,5 à odd 2,0), a regra da PR 2 chega a forte em **12 %** das séries de 160 jogos e **18 %** das de 300 (200 sementes), contra os 5 % nominais.
- **Arredondamento.** `prob_above` é arredondado a 4 casas: qualquer cauda abaixo de 5·10⁻⁵ aparece como 1,0 e passaria qualquer fasquia mais exigente do que 0,9999.

**B. Hora do resultado inventada.** `known_at()` assumia o resultado conhecido 150 min depois do início (ou do sinal). Comparado com a primeira observação provada pelo sistema, em 286 resultados elegíveis:
- 251 foram observados **antes** do que a aproximação assumia (mediana: 58 min antes): a aproximação desperdiçava informação;
- **35 foram observados depois**, até **18,6 h** depois (por exemplo, jogos de 11/11 cujo resultado só entrou na recolha inicial de 12/11). Nesses casos o walk-forward e a reconstituição usavam resultados que o sistema ainda não tinha;
- no walk-forward da PR 2, **5 das 135 apostas seguidas** foram decididas com resultados ainda não observados, e 2 eram sinais que o sistema só leu depois de deixarem de ser apostáveis.

**C. Registo atual aplicado ao passado.** O walk-forward e a reconstituição usavam todas as hipóteses do gerador desde o início dos dados, mesmo as registadas dias depois. 3 915 das 5 483 hipóteses nasceram a 14/11.
- Neste snapshot, a porta de registo **não muda nenhuma decisão do walk-forward**: só 4 das 390 decisões reconstituíveis são anteriores à primeira hipótese (12/11 15:16), e as táticas que levam a seguir são as simples, registadas cedo. A porta é necessária por construção (testes), não por ter alterado este resultado.

**D. Multiplicidade e sobreposição.** As 143 hipóteses testadas (≥ 30 jogos) assentam em só **165 jogos** diferentes:
- 119 das 143 são "Jogo aberto";
- com Jaccard ≥ 0,5 formam apenas 5 grupos, com Jaccard ≥ 0,8 formam 38;
- as **11 promissoras somam 449 jogos, mas são só 76 jogos diferentes**, e com Jaccard ≥ 0,5 formam 2 grupos.

Contar "N promissoras" como N descobertas é enganador.

## 2. Proveniência dos timestamps (o que é prova e o que não é)

| Instante | Campo | Origem | Provado? |
|---|---|---|---|
| Sinal | `date_utc` | Telegram (hora da mensagem) | sim |
| Início do jogo | `kickoff_utc` | texto do bot | declarado pelo bot |
| Fim do jogo / resultado no mundo | — | não observado | **não** (nunca se inventa) |
| Leitura do sinal pelo sistema | `signal_seen_utc` ← `collected_at_utc` da 1.ª versão | recolha | sim (início do passo) |
| 1.ª observação do resultado usado | `result_known_utc` ← 1.ª versão com o resultado final / marca / nota; `matched_at_utc` (Highlightly, BSD); `fetched_at_utc` (minutos dos golos) | recolha | sim (início do passo) |
| Resultado manual | `observed_utc` (campo novo, opcional) | revisão à mão | só se preenchido; senão **desconhecido** |
| Registo da hipótese | `born_utc` | relatório/LLM | sim |
| Decisão | max(`date_utc`, `signal_seen_utc`) | derivado | sim |

**Verificação independente com o histórico git** (o commit que publicou cada valor é um relógio externo, o do push):
- **Versões de mensagens:** 1 184 verificadas, 0 com `collected_at_utc` posterior ao commit; desfasamento mediano de 0,7 min, máximo de 6,2 min.
- **Hipóteses:** 5 483 verificadas, 0 com `born_utc` posterior ao commit que as publicou; máximo de 6,1 min; 0 `born_utc` alterados, 0 hipóteses removidas.
- **Highlightly, BSD e minutos dos golos:** 49, 19 e 49 verificados, 0 carimbos em falta, 0 posteriores ao commit.

**Defeito encontrado nos carimbos:** os coletores escrevem a hora no **início** do passo. 2 sinais têm `collected_at_utc` 1 s antes da própria mensagem. Por isso a política é conservadora nos dois sentidos:
- um resultado só conta **5 min** depois do carimbo, o limite dos timeouts de leitura (Telegram 220 s, Highlightly 8 × 25 s, BSD 12 × 20 s);
- a decisão usa só o que existia no carimbo da leitura do sinal;
- um sinal só é decidível se, mesmo com o relatório a sair **15 min** depois do carimbo (timeout do job), ainda dava para apostar: jogo por começar, ou até 30 min depois do sinal, a mesma janela dos alertas.

As três margens foram fixadas pelos timeouts **antes** de correr o walk-forward. A secção 6 mostra a sensibilidade.

## 3. Método

1. **Cronologia** (`scangoal/chronology.py`, novo).
   - Hora ausente, sem fuso, malformada ou contraditória → **desconhecida**. É contraditória quando o resultado é observado antes do sinal ou do início, ou o sinal é lido mais de 5 min antes de ser enviado.
   - Um resultado desconhecido **nunca** entra numa decisão histórica: só conta "agora", depois de todos os provados.
   - `report.load()`/`build_bets()` passam a registar a proveniência sem mudar nenhum valor existente: liquidação, elegibilidade e ROI ficam iguais.
2. **Reconstituição dos estados** (`brain.ordered_games`, `game_steps`).
   - Ordem = hora provada de observação. Um jogo fica conhecido quando todas as suas apostas o estão.
   - Empates: pela hora do jogo e depois pela identidade. A ordem física dos dados não conta.
   - A histerese e a confirmação da PR 2 ficam iguais.
3. **Registo** (`born_utc`).
   - A evidência anterior ao registo junta-se num único passo "no registo". Como a PR 2 exige 2 passos para mudar de estado, uma hipótese nova precisa de pelo menos um jogo posterior para ganhar estado.
   - Sem `born_utc` válido não há estados nem uso no walk-forward.
   - O registo existente **não é alterado** e nenhuma data é reescrita.
4. **Testes múltiplos sob dependência** (forte/confirmada).
   - Uma decisão tomada com n jogos no instante t só é validada se a cauda posterior **não arredondada** (`insights.tail_below`) for ≤ α · 100 / (n(n+1)) / m(t), com:
     - α = 0,05;
     - m(t) = hipóteses **registadas** que tinham ≥ 30 jogos **provadamente conhecidos** em t;
     - 100/(n(n+1)) = parte de α gasta no olhar com n jogos. É uma série telescópica: soma exatamente 1 sobre n ≥ 100.
   - É uma desigualdade da união (Bonferroni) sobre hipóteses **e** sobre olhares. **Não assume independência:** vale para qualquer dependência e, com hipóteses sobrepostas, é conservadora.
   - A fasquia e m(t) ficam registados em cada mudança (`bar`, `tested`). Hipóteses que chegam depois **não reescrevem** decisões fixadas.
   - Manter: o dobro do erro, como na PR 2.
   - 1 − cauda é tratado como valor-p unilateral. É uma aproximação: o prior Beta centrado na taxa necessária encolhe para "sem vantagem", o que a torna ligeiramente conservadora.
5. **Porta prospetiva.** Forte/confirmada exigem ainda ≥ 30 jogos **prospetivos** (sinais lidos pelo sistema depois do registo) com prob ≥ 0,95 (manter: ≥ 0,90).
   - É um requisito de replicação em dados que não existiam quando a hipótese nasceu.
   - Usa constantes já existentes (`HINT_AT`, `PROMOTE_AT`, `KEEP_PROMISING`).
6. **Promissora e abandonada:** os limiares ficam iguais (0,95/0,90 e 0,10/0,20, sem correção). Continuam explicitamente **exploratórias**.
7. **Walk-forward cronológico.**
   - Cada decisão é tomada na hora da leitura do sinal, só com resultados observados antes e só com hipóteses registadas antes. As propostas da LLM e manuais só usam sinais posteriores ao seu registo.
   - Sinais lidos tarde demais contam como **não reconstituíveis**, nunca como decisões.
8. **Relatório.**
   - Por hipótese: `born_verified`, `at_birth` (evidência disponível no registo), `prospective`, `evidence_level` (exploratória / prospetiva / não verificável), `result_time_unknown` e `game_set`.
   - No cérebro: `multiplicity` (método, família, fasquia atual, conjuntos distintos, união e soma dos jogos das promissoras) e `corrected_bar_tail`.
   - `prob_above`, `edge` e as restantes probabilidades **não mudam** de fórmula nem de valor.

**Quatro avaliações, distintas no relatório:**
1. histórica exploratória: as colunas de sempre e todos os dados;
2. evidência disponível no registo: `at_birth`;
3. avaliação prospetiva após o registo: `prospective`;
4. decisões que eram possíveis na data: walk-forward cronológico.

## 4. O que fica demonstrado e o que não pode ser

**Demonstrado (testes determinísticos e verificação git):**
- nenhuma decisão usa um resultado observado depois dela, nem um resultado sem hora provada;
- nenhuma hipótese decide antes de existir;
- hipóteses acrescentadas depois não reescrevem decisões fixadas;
- a fasquia é a do instante;
- a ordem física dos dados não conta;
- as entradas inválidas falham fechadas.

**Não demonstrável retrospetivamente:**
- **Decisões anteriores a 12/11 09:34**, antes de haver recolha: 29 sinais com odd foram lidos na recolha inicial e são **não reconstituíveis**. Os 6 sinais de 15/11 e os outros casos lidos tarde estão contados na mesma categoria.
- **A hora real do fim do jogo e do resultado no mundo:** não é observada. Usa-se só a observação pelo sistema, que é um limite superior conservador.
- **A FWER global no tempo:** com uma família que cresce, a correção cronológica controla o erro da família **em cada instante**, não a soma ao longo de famílias sucessivas. Uma correção *online* estrita (α_j por ordem de entrada) é possível, mas tornaria quase impossível validar as hipóteses registadas mais tarde. Fica para decisão do auditor.
- **Capacidade preditiva:** não pode ser demonstrada com esta amostra (secção 6).

## 5. Antes/depois (mesmo snapshot, mesmo relógio)

| Medida | PR 2 | PR 3 |
|---|---:|---:|
| Promissoras / abandonadas / validadas (elegível) | 13 / 0 / 0 | 11 / 0 / 0 |
| Promissoras (exploratório) | 58 | 57 |
| Hipóteses testadas | 143 | 143 |
| Fasquia da primeira validação (n = 100) | 0,99965 | 0,9999965 (cauda 3,5·10⁻⁶) |
| `prob_above` e `games` iguais (150 hipóteses comparadas) | — | sim |
| Hipóteses testadas com evidência prospetiva ≥ 30 jogos | — | 80 de 143 |
| Walk-forward seguidas: apostas, ROI, ROI odd −2 % | 135, +0,77 %, −0,91 % | 131, +3,12 %, +1,41 % |
| Walk-forward não seguidas: apostas, ROI | 290, −2,66 % | 259, −4,97 % |
| Não reconstituíveis | — | 35 (24 com resultado) |
| P(seguidas renderem mais) | 0,43 | 0,60 |
| IC 90 % *bootstrap* por jogo, ROI das seguidas | — | [−11,2 %; +17,5 %] |
| Primeiro relatório PR 3 sobre o estado Telegram de produção | — | **0 avisos de estado** (sem migração seriam 2) |
| `report.build` (2 medições) | 8,6–9,7 s | 9,7–10,3 s |

**As 2 hipóteses que mudam** (promissora → em observação) são as que a PR 2 descrevia como "mantidas pela histerese":
- **"Jogo aberto | Golos até ao sinal = 1 ou 2"** (43 jogos, 0,9496). Na PR 2, a promoção foi confirmada a "15/11 00:17", uma hora **fictícia** dada pela aproximação de 150 min. Na ordem provada, a sequência dos passos 30–43 é 0,92, 0,94, 0,91, 0,87, 0,91, 0,93, 0,944, 0,946, **0,955**, 0,93, 0,94, **0,956**, 0,93, 0,95: nunca houve dois jogos seguidos ≥ 0,95.
- **"Jogo aberto | Minuto = 31 a 60 & Ritmo < 90"** (36 jogos, 0,9429): mesmo mecanismo.

As outras 11 promissoras mantêm-se. Cada uma tem a mudança datada depois do registo e a fasquia/família desse instante (m entre 27 e 119) registada.

## 6. Walk-forward, sensibilidade e ablações

| Variante | Seguidas | ROI seguidas | ROI odd −2 % | ROI não seguidas | P(melhor) |
|---|---:|---:|---:|---:|---:|
| PR 3 (margem 5 min, atraso 15 min) | 131 | +3,12 % | +1,41 % | −4,97 % | 0,60 |
| margem 0 / 15 / 60 min | 132 / 133 / 130 | +3,21 / +4,00 / +5,01 % | +1,53 / +2,26 / +3,27 % | −4,90 / −5,63 / −6,03 % | 0,59 / 0,62 / 0,58 |
| atraso 0 / 5 min | 131 | +3,12 % | +1,41 % | −4,39 % | 0,58 |
| sem porta de registo | 131 | +3,12 % | +1,41 % | −4,97 % | 0,60 |
| horas da PR 2 (150 min) | 133 | +0,16 % | −1,51 % | −3,08 % | 0,42 |
| decidir todos os sinais à hora de envio | 133 | +3,70 % | +1,98 % | −4,32 % | 0,60 |

**Leitura:**
- Só a hora dos resultados move o ROI das seguidas cerca de 3 pontos (+0,2 % → +3,1 %).
- Margens de minutos movem-no entre +3,2 % e +5,0 %.
- O IC 90 % por jogo vai de −11 % a +18 %, e P(seguidas melhores) = 0,60.

Um resultado que muda tanto com escolhas de implementação **não é evidência de vantagem**. Nenhum destes números foi usado para escolher parâmetros.

## 7. Multiplicidade sob dependência

**Simulação nula com a dependência real** (400 simulações):
- **Método:** cada aposta (jogo, mercado, seleção) é sorteada como ganha com probabilidade 1/odd, ou seja, sem vantagem. O mesmo sorteio é partilhado por todas as hipóteses que a contêm, e calculam-se as mesmas estatísticas do cérebro.
- **Hipóteses testadas com prob ≥ 0,95:**
  - observado: **12**;
  - nulo: média 4,4, mediana 1, percentil 95 **20**, percentil 99 44;
  - 48,5 % das simulações dão 0;
  - **11 %** das simulações dão ≥ 12.
- Os "acasos" vêm em cachos, porque são os mesmos jogos e os mesmos sorteios. O número observado de promissoras **é compatível com acaso**.
- **Westfall–Young (máximo, FWER 5 %, um olhar):** cauda 3,8·10⁻³, contra 3,5·10⁻⁴ de Bonferroni.
  - Com esta sobreposição, Bonferroni é cerca de 11× mais exigente do que o necessário.
  - Mantém-se Bonferroni porque é válido sem modelo. O WY depende do modelo nulo (sorteios independentes entre mercados do mesmo jogo, e 1/odd sem margem) e custa minutos por relatório.

**Taxas de validação sintéticas** (uma hipótese, 200 sementes, parâmetros fixados antes):

| Caso | PR 2 | PR 3 sem repartição por olhares | PR 3 |
|---|---:|---:|---:|
| sem vantagem, 160 jogos | 12 % | 12 % | **0 %** |
| sem vantagem, 300 jogos | 18 % | 18 % | **0 %** |
| p = 0,60 à odd 2,0 (ROI +20 %), 300 jogos | 97,5 % | 97,5 % | 46 % |
| p = 0,75, 160 jogos | 100 % | 100 % | 100 % |

**Custo explícito:** vantagens realistas (2–5 % de ROI) **nunca** serão validadas com centenas de jogos, por esta regra ou pela da PR 2 (que as validaria por acaso). É a consequência honesta do tamanho da amostra.

## 8. Testes

**Total:** 207 testes offline e determinísticos (`python scripts/test_offline.py`, rede bloqueada).

**29 novos** em `tests/test_chronological_validity.py`:
- **Hora dos resultados:**
  - resultado observado depois da decisão não a influencia (com controlo positivo);
  - reconstituição pela ordem provada;
  - jogo conhecido só com todas as apostas.
- **Hora desconhecida, contraditória ou malformada:**
  - nunca presumida;
  - conta "agora", depois das provadas;
  - sem fuso, texto, número ou vazio → desconhecida.
- **Registo:**
  - hipótese registada depois do sinal não o decide;
  - evidência anterior ao registo é um passo e precisa de confirmação;
  - hipóteses acrescentadas depois não reescrevem mudanças nem o walk-forward;
  - `born_utc` ausente ou malformado não decide.
- **Fasquia cronológica:**
  - registada em cada mudança;
  - uma família que já existia aplica-se;
  - os olhares são pagos (série telescópica);
  - linha temporal da família.
- **Porta prospetiva:**
  - só a história nunca valida;
  - a confirmação prospetiva valida;
  - prospetivo fraco bloqueia;
  - o arredondamento não passa uma fasquia mais exigente.
- **Mesmo jogo, uma unidade:** passo e walk-forward, com controlo positivo.
- **Sobreposição:** 4 hipóteses sobre os mesmos jogos dão 1 conjunto e contam 4 testes (nunca relaxa a fasquia).
- **Reordenação física** dos sinais e do registo: resultado idêntico.
- **Decidibilidade:** recolha tardia e sinal não lido → não reconstituíveis; janela igual à dos alertas.
- **Cenários com 10 sementes:** vantagem verdadeira validada e lucrativa no walk-forward; sem vantagem nunca validada; deterioração retira a validação.
- **Telegram:** migração das regras novas silenciosa e única, marcadores das PRs 1 e 2 intactos, mudança genuína posterior anunciada.
- **Relatório:** hora = primeira recolha do resultado (não a última versão); Highlightly com `matched_at_utc`; resultado manual sem `observed_utc` → desconhecido.

**Contra o código da PR 2**, 25 dos 29 falham:
- 7 em asserções de comportamento: resultado tardio usado, hora desconhecida usada, hipótese futura usada, estado antes do registo, só história valida, família anterior ignorada, migração;
- 18 por API/campos que não existiam.

Os 4 que passam:
- 2 testam só o módulo novo de cronologia;
- 1 é a reordenação física;
- 1 é a hipótese acrescentada depois, que passa na PR 2 só por o arredondamento a 4 casas esconder a diferença de fasquia.

**Testes existentes preservados.** As asserções ficam todas iguais, exceto:
- **`CorrectedBarIsRetroactive` → `CorrectedBarIsChronological`:** era a limitação documentada da PR 2 e agora afirma o comportamento corrigido (forte/forte e o mesmo histórico, contra forte/promissora). A série passa a ser determinística, 7 em cada 10.
- **Fixtures:** as do walk-forward (`sig` em `test_independence_stability.py` e `EvidencePerMatch.row` em `test_scangoal.py`) **declaram a proveniência** com o mesmo tempo que sempre assumiram: lido ao envio, resultado disponível 150 min depois. Não muda nenhuma asserção, como a PR 1 fez com `in_roi` e a PR 2 com `games`/`match_id`.
- **`scripts/pr2_cutover.py`** (e o ensaio e o teste): fica fixo na sua própria regra (`independent_games_hysteresis_v1`), para nunca aceitar relatórios de regras posteriores.

## 9. Telegram: marcador e corte

- **Regra nova:** `brain.status_rules = chronological_prospective_v1`. O mecanismo existente (`notify.migration_key`) dá-lhe a chave `status_rules:chronological_prospective_v1`, por isso o primeiro relatório PR 3 faz **uma** reposição silenciosa da referência. `eligible_statuses_v1`, `status_rules:independent_games_hysteresis_v1` e o histórico de entregas ficam intactos (teste e ensaio).
- **Ensaio** sobre o `notify_state.json` real, só com `messages()` em memória: 0 avisos de estado com a migração, contra 2 sem ela ("voltou a em observação").
- **Não executado.** Como na PR 2, a integração exige corte controlado: pausa, corte e merge. Uma execução com código da PR 2 que corra depois do primeiro relatório PR 3 gravaria estados da PR 2 sob uma referência já migrada, e a seguinte anunciaria essas diferenças. O script de corte da PR 3 (variante de `scripts/pr2_cutover.py` com a chave nova) **não foi criado**: fica para depois da aprovação da metodologia.

## 10. Limitações e riscos residuais

1. **Pré-recolha:** decisões anteriores a 12/11 09:34 são irreconstituíveis. As margens de 5/15 min são limites dos timeouts, não medições por mensagem.
2. **Família crescente:** controlo por instante, não global (secção 4).
3. **Aproximação de valor-p:** 1 − cauda posterior com prior Beta(10) não é um valor-p exato. A repartição por olhares é conservadora; um e-processo (*testing by betting*) seria uma alternativa sequencial exata.
4. **Potência:** a validação fica, na prática, fora de alcance para vantagens realistas. É o comportamento pretendido, mas deve ser comunicado (PR 5).
5. **Promissora continua sem correção.** O relatório e o dashboard dizem-no, e `multiplicity` mostra a sobreposição. A consolidação dos avisos redundantes fica para a PR 5.
6. **Fora do âmbito:** o regime (avisos de mudança de comportamento), o quadro e a agenda continuam a usar a evidência atual, que é exploratória. Não decidem estados.
7. **Atributos dependentes de outros sinais** ("Sinal anterior de outro tipo"): usam sinais com `date_utc` anterior. O leitor relê as 400 mensagens mais recentes, por isso estes foram lidos na mesma recolha ou antes. Só uma lacuna de mais de 400 mensagens o violaria (o leitor avisa com `SCANGOAL_GAP_POSSIBLE`).
8. **Desempenho:** `report.build` mede 8,6–10,3 s em ambas as versões neste contentor (várias corridas). A diferença (≈ +0–15 %) está próxima do ruído das medições.
9. **Integração:** exige o corte da secção 9.

## 11. Reprodução

```bash
git fetch --unshallow origin    # a verificação git precisa do histórico completo
git switch --detach <SHA da PR 3>
python3.11 scripts/test_offline.py                                    # 207 testes
python3.11 -m unittest tests.test_chronological_validity -v
python3.11 scripts/audit_pr3_chronology.py > /tmp/pr3-metrics.json    # ~5 min; data/ tem de ser igual à base
```

## 12. Parecer do autor (não substitui a revisão independente)

- **Técnico: aprovado pelo autor.** Os quatro defeitos A–D estão corrigidos ou expostos. A cronologia é provada e verificada contra o git, e os testes adversariais cobrem cada garantia, com controlo positivo. Não há alterações em produção.
- **Científico: INCONCLUSIVO** quanto a capacidade preditiva:
  - o walk-forward cronológico dá +3,1 % (IC 90 % −11 % a +18 %), instável a escolhas de minutos;
  - o número de promissoras é compatível com acaso sob a dependência real (11 % das simulações nulas dão tantas ou mais).
- **Cientificamente bloqueado:**
  - a reconstituição de decisões anteriores a 12/11 09:34;
  - qualquer validação (forte/confirmada) antes de haver ≥ 100 jogos, dos quais ≥ 30 prospetivos, por tática. Hoje: nenhuma validada e nenhuma com 100 jogos (máximo 78); 80 das 143 testadas já têm ≥ 30 jogos prospetivos.
