# PR 3 — Validade cronológica, testes múltiplos e avaliação prospetiva

**Draft para auditoria independente. Nada foi executado em produção.** Sem merge, deploy, alteração de workflows, secrets, sessões ou `data/`; nenhuma mensagem Telegram enviada; nenhuma migração executada. Comparação reproduzível em `scripts/audit_pr3_chronology.py`, com resultado em `audits/pr3-metrics.json`.

- **Base:** `82ab9d86593055fbc98420265a469728d914288f` (`main` com a PR 2, no início desta PR). Os SHA-256 de `data/` estão no JSON e conferem com a base (`data_matches_base: true`); a auditoria não altera `data/` (`data_unchanged_by_audit: true`).
- **Relógio fixo:** `2026-11-15T14:34:34Z` (hora do commit base). **Python:** 3.11.
- **Rede bloqueada** na auditoria e nos testes; sementes fixas (`20261115`).

> **Revisão #5911487915 (commit `ef4606a`): correções feitas nesta versão.** Ver a secção 0. Em resumo:
> - **P1-A e P1-B:** existe agora uma definição única de jogo prospetivo. Um jogo só conta se todas as suas apostas foram emitidas e lidas depois do registo, eram decidíveis e têm hora de resultado provada. Essa definição é usada pelo relatório, pela máquina de estados, pelo `entry_status` e pelo teste de confirmação.
> - **P1 científico:** escolhida a **via 1**. "Forte" e "confirmada" deixam de usar caudas posteriores com Bonferroni e passam a exigir um **certificado** de um e-processo com nível garantido. Acrescenta-se uma proteção fail-closed no cérebro e no Telegram.

## 0. Correções após a revisão #5911487915

**P1-A — sinais históricos recuperados tarde.**
- A condição antiga era `decision_time > born`, sinal a sinal. Um sinal emitido antes do registo, mas lido depois, contava como prospetivo.
- Agora `brain.operational_bet(b, t)` exige, sem exceções:
  - sinal emitido depois de t (`date_utc > t`);
  - carimbo de leitura depois de t;
  - sinal decidível quando foi lido (`chronology.decidable`, a mesma política do walk-forward);
  - hora do resultado verificada (não desconhecida nem contraditória).
- Cada jogo é classificado numa de quatro categorias: `prospective`, `mixed`, `before_registration` (incluindo os recuperados tarde) e `after_registration_not_operational` (sinal lido tarde demais ou hora do resultado não provada).
- **Regressão:** 40 vitórias pré-registo, recuperadas depois do registo, dão 0 jogos prospetivos no relatório e no replay, ficam em `before_registration` e não permitem certificação.

**P1-B — definição única.**
- `operational_game(rows, t)` = todas as apostas decididas do jogo são operacionais; um jogo misto não conta em lado nenhum.
- `prospective_summary` recebe os mesmos `ordered_games` da máquina de estados e usa a mesma função que `game_steps`, `entry_status` e o teste de confirmação.
- **Testes:**
  - 35 jogos mistos dão 0 no relatório e 0 no replay; o controlo com 35 jogos completos dá 35 nos dois;
  - em dados aleatórios (lados do registo, atrasos, horas desconhecidas e contraditórias), relatório = replay = contagem direta, e as categorias somam todos os jogos.
- **Impacto no snapshot** (143 hipóteses testadas):

| | Definição de `ef4606a` | Definição única |
|---|---:|---:|
| Pares hipótese–jogo prospetivos (relatório) | 4 671 | **4 600** |
| Idem, no replay | 4 662 | **4 600** |
| Hipóteses com relatório ≠ replay | **6** | 0 |
| Jogos mistos / não operacionais depois do registo | contados | 9 / 62, **excluídos** |
| Jogos anteriores ao registo (incl. recuperados tarde) | — | 813 |
| Hipóteses testadas com ≥ 30 jogos prospetivos | 80 | **74** (6 mudam de nível) |

**P1 científico — via 1.** Construí um teste com nível garantido em vez de afirmar o que a aproximação não demonstrava (secção 3, ponto 4). Em síntese:
- **Seleção:** primeira promoção confirmada a promissora, com hora provada. É exploratória.
- **Confirmação:** e-processo de apostas, só com jogos operacionais posteriores à seleção.
- **Orçamento:** α_j = 0,05/(j(j+1)) pela ordem de seleção.
- **Garantia:** desigualdade de Ville + união, ou seja, P(alguma tática sem vantagem ser certificada, **em toda a vida do projeto**) ≤ 0,05, sob qualquer dependência entre hipóteses e com observação contínua, sob o nulo enunciado.
- **Proteção fail-closed:**
  - (i) o cérebro rebaixa a promissora qualquer validada sem certificado (`validation_blocked`);
  - (ii) o Telegram só anuncia "tática validada" ou um ALERTA se o relatório declarar `validation.method = eprocess_online_alpha_v1` **e** essa tática estiver em `validation.certified`, com `certified: true` na razão. Caso contrário trata-a como promissora, também no texto.
- Parâmetros fixados por princípio e nunca pelo ROI: α = 0,05, odd −2 % (constante `ODDS_LOSS` existente), grelha geométrica de λ, mínimo de 30 jogos (`HINT_AT`).
- No snapshot: **11 selecionadas, 0 certificadas**. Os e-valores atuais vão de 0,78 a 1,87 (máximo histórico 2,44), contra limiares de 40 a 2 640.

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
4. **Validação certificada** (forte/confirmada; revisão #5911487915).
   - **Seleção:** τ = primeira mudança confirmada para promissora, com hora provada. É exploratória e pode usar todos os dados anteriores.
   - **Evidência de confirmação:** só jogos com `operational_game(rows, τ)`: todas as apostas emitidas e lidas depois de τ, decidíveis, com resultado de hora provada. São processados pela ordem provada de observação.
   - **Estatística:** lucro por jogo, y = média das apostas distintas do jogo de w·(odd·0,98 − 1) se ganha, w se perde. Inclui meias apostas, y ≥ −1, e as apostas repetidas contam uma vez.
   - **E-processo:** E_n = média sobre λ ∈ {1/2, 1/4, 1/8, 1/16, 1/32} de ∏(1 + λ·y_i).
     - **Nulo** H₀(h): em cada jogo usado, E[y_i | passado] ≤ 0, isto é, sem lucro esperado à odd −2 %.
     - Sob H₀, cada produto é uma supermartingala não negativa com início em 1, e a média também.
     - **Ville:** P(sup_n E_n ≥ 1/a) ≤ a. Vale para qualquer olhar, odds heterogéneas e resultados fracionários.
   - **Orçamento:** a j-ésima tática selecionada (por τ, empates pela identidade) recebe α_j = 0,05/(j(j+1)), e Σ_j α_j = 0,05.
     - Certifica-se no primeiro jogo com E_n ≥ 1/α_j e n ≥ 30. O certificado é registado (`confirmation.certified_utc`).
     - α_j depende do passado, mas o teste de h usa só jogos posteriores a τ_h. Condicionando em F_τ, P(falsa certificação de h) ≤ α_{J_h}, e somando sobre índices distintos o total fica ≤ α.
   - **Estado:** forte/confirmada = certificado + ≥ 100/300 jogos + prob ≥ 0,95 (manter: 0,90), com a confirmação em 2 passos da PR 2.
   - **Sobreposição:** hipóteses sobre os mesmos jogos não partilham orçamento. Cada uma paga o seu α_j, o que é conservador.
   - **O que deixou de existir:** a fasquia Bonferroni com caudas posteriores, a repartição por olhares e a porta "30 jogos prospetivos com 95 %". `corrected_bar` passa a `null` no relatório. `evaluate()` devolve 1 − α/testadas só por compatibilidade, e esse valor não decide nada.
5. **Evidência prospetiva** (relatório, `evidence_level`): jogos com `operational_game(rows, born)`, a mesma definição aplicada ao registo. É descritiva; a decisão está no ponto 4.
6. **Promissora e abandonada:** os limiares ficam iguais (0,95/0,90 e 0,10/0,20, sem correção). Continuam explicitamente **exploratórias**.
7. **Walk-forward cronológico.**
   - Cada decisão é tomada na hora da leitura do sinal, só com resultados observados antes e só com hipóteses registadas antes. As propostas da LLM e manuais só usam sinais posteriores ao seu registo.
   - Sinais lidos tarde demais contam como **não reconstituíveis**, nunca como decisões.
8. **Relatório.**
   - Por hipótese: `born_verified`, `at_birth` (evidência disponível no registo), `prospective`, `evidence_level` (exploratória / prospetiva / não verificável), `result_time_unknown` e `game_set`.
   - Por hipótese: `prospective.categories` e `confirmation` (rank, α, limiar, jogos, e-valor atual e máximo, seleção, certificado).
   - No cérebro:
     - `multiplicity`: só descritivo, com conjuntos distintos, união e soma dos jogos das promissoras e categorias;
     - `validation`: método, α, nulo, pressupostos, afirmação exata, selecionadas, certificadas e as mais próximas.
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
- hipóteses acrescentadas ou selecionadas depois não alteram certificados nem decisões anteriores;
- a evidência prospetiva tem uma definição, partilhada pelo relatório e pelo replay;
- a ordem física dos dados não conta;
- as entradas inválidas falham fechadas;
- nenhuma validada sem certificado chega ao relatório ou ao Telegram.

**Demonstrado matematicamente, sob pressupostos explícitos:** P(alguma certificação falsa em toda a vida do projeto) ≤ 0,05.
- **Pressupostos:**
  - (A1) o nulo vale condicionalmente ao passado em cada jogo usado;
  - (A2) a pertença de um jogo a uma hipótese nunca depende do seu resultado (condições fixas, atributos conhecidos no fim do jogo);
  - (A3) as horas guardadas são verdadeiras (verificadas contra o git).
- **Verificação empírica:**
  - nulo no limite, com odds 1,4–3,5 e meias vitórias: 1,7 % das 300 séries cruzam 1/0,05, e 0 % cruzam 1/0,025;
  - nos sintéticos de odd 2,0 (sem vantagem), há 0 falsas validações até 1000 jogos.

**Não demonstrável retrospetivamente:**
- **Decisões anteriores a 12/11 09:34**, antes de haver recolha: 29 sinais com odd foram lidos na recolha inicial e são **não reconstituíveis**. Os 6 sinais de 15/11 e os outros casos lidos tarde estão contados na mesma categoria.
- **A hora real do fim do jogo e do resultado no mundo:** não é observada. Usa-se só a observação pelo sistema, que é um limite superior conservador.
- **A robustez do nulo:** a garantia é sobre lucro **à odd anunciada −2 %**. Se as odds realmente obtidas forem piores, uma certificação pode não se traduzir em lucro. A garantia também não cobre erros de liquidação.
- **Capacidade preditiva:** não pode ser demonstrada com esta amostra (secções 6 e 7).

## 5. Antes/depois (mesmo snapshot, mesmo relógio)

| Medida | PR 2 | PR 3 |
|---|---:|---:|
| Promissoras / abandonadas / validadas (elegível) | 13 / 0 / 0 | 11 / 0 / 0 |
| Promissoras (exploratório) | 58 | 57 |
| Hipóteses testadas | 143 | 143 |
| Regra das validadas | Bonferroni (1 − 0,05/m) com `prob_above` arredondado | certificado de e-processo, α_j = 0,05/(j(j+1)) |
| Selecionadas / certificadas | — | 11 / 0 (e-valores 0,78–1,87; limiares 40–2 640) |
| `prob_above` e `games` iguais (150 hipóteses comparadas) | — | sim |
| Hipóteses testadas com evidência prospetiva ≥ 30 jogos | — | 74 de 143 (eram 80 com a definição de `ef4606a`) |
| Walk-forward seguidas: apostas, ROI, ROI odd −2 % | 135, +0,77 %, −0,91 % | 131, +3,12 %, +1,41 % |
| Walk-forward não seguidas: apostas, ROI | 290, −2,66 % | 259, −4,97 % |
| Não reconstituíveis | — | 35 (24 com resultado) |
| P(seguidas renderem mais) | 0,43 | 0,60 |
| IC 90 % *bootstrap* por jogo, ROI das seguidas | — | [−11,2 %; +17,5 %] |
| Primeiro relatório PR 3 sobre o estado Telegram de produção | — | **0 avisos de estado** (sem migração seriam 2) |
| `report.build` (2 medições) | 8,7–10,3 s | 10,3–10,6 s |

**As 2 hipóteses que mudam** (promissora → em observação) são as que a PR 2 descrevia como "mantidas pela histerese":
- **"Jogo aberto | Golos até ao sinal = 1 ou 2"** (43 jogos, 0,9496). Na PR 2, a promoção foi confirmada a "15/11 00:17", uma hora **fictícia** dada pela aproximação de 150 min. Na ordem provada, a sequência dos passos 30–43 é 0,92, 0,94, 0,91, 0,87, 0,91, 0,93, 0,944, 0,946, **0,955**, 0,93, 0,94, **0,956**, 0,93, 0,95: nunca houve dois jogos seguidos ≥ 0,95.
- **"Jogo aberto | Minuto = 31 a 60 & Ritmo < 90"** (36 jogos, 0,9429): mesmo mecanismo.

As outras 11 promissoras mantêm-se, todas com a mudança datada depois do registo. As 11 são as táticas selecionadas para confirmação.

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
- **Westfall–Young (máximo, FWER 5 %, um olhar):** a cauda limite é 3,8·10⁻³, contra 3,5·10⁻⁴ de Bonferroni.
  - É descritivo: com esta sobreposição, uma correção de um só olhar que ignore a dependência seria cerca de 11× mais exigente do que o necessário.
  - Já não decide nada: as promissoras são exploratórias e as validadas usam o e-processo.

**Taxas de validação sintéticas** (uma hipótese, odd 2,0, parâmetros fixados antes; "sem vantagem" = p 0,50, abaixo do equilíbrio à odd −2 %):

| Caso | Sementes | PR 2 | PR 3 `ef4606a` (Bonferroni + caudas) | PR 3 (e-processo) |
|---|---:|---:|---:|---:|
| sem vantagem, 160 jogos | 200 | 12 % | 0 % | **0 %** |
| sem vantagem, 300 jogos | 200 | 18 % | 0 % | **0 %** |
| sem vantagem, 1000 jogos | 60 | 25 % | 0 % | **0 %** |
| p = 0,60 (ROI +20 %), 300 jogos | 200 | 97,5 % | 46 % | 46,5 % |
| p = 0,60, 1000 jogos | 60 | 100 % | 98,3 % | 100 % |
| p = 0,75, 160 jogos | 200 | 100 % | 100 % | 99,5 % |

**Leitura:**
- A versão `ef4606a` também não validou falsamente nestes casos. A diferença está na garantia: a sua taxa nula de 0 % era empírica e sem prova, enquanto a do e-processo é limitada por construção, para toda a vida do projeto e qualquer número de táticas selecionadas.
- **Custo:** vantagens realistas (2–5 % de ROI) não serão certificadas com centenas nem com poucos milhares de jogos. Uma tática selecionada em 11.º lugar precisa de E ≥ 2 640. É a consequência honesta do tamanho da amostra, aceite conforme pedido ("rigor antes de facilidade").

## 8. Testes

**Total:** 211 testes offline e determinísticos (`python scripts/test_offline.py`, rede bloqueada). Destes, 178 são anteriores à PR 3 e 33 estão em `tests/test_chronological_validity.py`.

**Novos ou reescritos após a revisão #5911487915:**
- **`ProspectiveEligibility`** (P1-A/P1-B):
  - pré-registo recuperado tarde nunca é prospetivo (nem no relatório, nem no replay, nem no certificado);
  - emitidos depois do registo mas lidos tarde, ou sem hora de resultado → não operacionais;
  - jogos mistos contam 0 em todo o lado, com controlo de jogos completos;
  - relatório = replay = contagem direta em dados aleatórios.
- **`OnlineConfirmation`:**
  - certificado, rank, α e limiar, e jogos contados só depois da seleção;
  - orçamento por ordem de seleção (1.ª 40, 2.ª 120), sem que uma seleção posterior altere a anterior;
  - nível sob o nulo no limite (≤ 5 %, com E[E_n] ≈ 1);
  - história forte sem evidência posterior à seleção não certifica;
  - a guarda rebaixa uma validada sem certificado.
- **`OverlappingHypotheses`:** 4 hipóteses sobre os mesmos jogos dão 1 conjunto, 4 ranks e 4 limiares (40/120/240/400).
- **`TelegramNeverClaimsUncertifiedValidation`:**
  - sem bloco `validation`, com outro método, com a lista vazia ou malformada → nunca "validada", "forte" ou ALERTA;
  - certificado na tática mas não na razão → sem ALERTA;
  - certificada → ALERTA e aviso.
- **`CorrectedBarIsChronological`:** forte/forte, mesmas mudanças e mesmo certificado com 1 ou 401 hipóteses (rank 1).
- **Mantidos:**
  - hora dos resultados, horas desconhecidas e contraditórias, registo, unidade de jogo, reordenação física, decidibilidade e proveniência no relatório;
  - cenários com 10 sementes: vantagem verdadeira certificada e lucrativa no walk-forward, sem vantagem nunca validada, deterioração retira a validação.

**Testes existentes preservados.** As asserções ficam iguais. As únicas alterações são de fixtures ou de regras próprias da PR 3:
- **`CorrectedBarIsRetroactive` → `CorrectedBarIsChronological`:** era a limitação da PR 2, agora corrigida.
- **Fixtures do walk-forward:** declaram a proveniência com o tempo de sempre.
- **Fixtures do Telegram** (`test_notify_migration.eligible_report` e `test_scangoal.Notify`): declaram `validation` com o certificado das táticas "forte" que já usavam. Sem isso, a proteção nova trata-as, corretamente, como promissoras.
- **`scripts/pr2_cutover.py`:** fica fixo na regra da PR 2.

## 9. Telegram: marcador e corte

- **Regra nova:** `brain.status_rules = chronological_eprocess_v1`. O mecanismo existente (`notify.migration_key`) dá-lhe a chave `status_rules:chronological_eprocess_v1`, por isso o primeiro relatório PR 3 faz **uma** reposição silenciosa da referência. `eligible_statuses_v1`, `status_rules:independent_games_hysteresis_v1` e o histórico de entregas ficam intactos (teste e ensaio).
- **Ensaio** sobre o `notify_state.json` real, só com `messages()` em memória: 0 avisos de estado com a migração, contra 2 sem ela ("voltou a em observação").
- **Proteção fail-closed nova:** `notify.eligible_statuses` e os alertas só reconhecem validadas com o certificado do método aceite (secção 0).
- **Não executado.** Como na PR 2, a integração exige corte controlado: pausa, corte e merge. Uma execução com código da PR 2 que corra depois do primeiro relatório PR 3 gravaria estados da PR 2 sob uma referência já migrada, e a seguinte anunciaria essas diferenças. O script de corte da PR 3 (variante de `scripts/pr2_cutover.py` com a chave nova) **não foi criado**: fica para depois da aprovação da metodologia.

## 10. Limitações e riscos residuais

1. **Pré-recolha:** decisões anteriores a 12/11 09:34 são irreconstituíveis. As margens de 5/15 min são limites dos timeouts, não medições por mensagem.
2. **Pressupostos da garantia** (secção 4):
   - o nulo é condicional ao passado;
   - a pertença é independente do resultado;
   - as horas são verdadeiras;
   - a odd −2 % é atingível.
   
   O e-processo não protege contra dados errados nem contra odds piores do que −2 %.
3. **Ordem de seleção:** a seleção é exploratória e sem correção. Táticas selecionadas mais tarde recebem α muito menor, por isso as primeiras a ser promovidas são favorecidas. É uma escolha de desenho, documentada e não ajustada ao ROI.
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
python3.11 scripts/test_offline.py                                    # 211 testes
python3.11 -m unittest tests.test_chronological_validity -v
python3.11 scripts/audit_pr3_chronology.py > /tmp/pr3-metrics.json    # ~15 min; data/ tem de ser igual à base
```

## 12. Parecer do autor (não substitui a revisão independente)

- **Técnico: aprovado pelo autor**, sujeito a revisão.
  - P1-A e P1-B estão corrigidos com uma definição única e testes adversariais.
  - A validação passou a ter nível demonstrado (via 1), com proteção fail-closed no cérebro e no Telegram.
  - Sem alterações em produção.
- **Científico: INCONCLUSIVO** quanto a capacidade preditiva:
  - o walk-forward cronológico dá +3,1 % (IC 90 % −11 % a +18 %), instável a escolhas de minutos;
  - o número de promissoras é compatível com acaso sob a dependência real (11 % das simulações nulas dão tantas ou mais);
  - nenhuma tática certificada.
- **Cientificamente bloqueado:**
  - a reconstituição de decisões anteriores a 12/11 09:34;
  - qualquer validação sem certificado. Hoje há 11 táticas selecionadas, nenhuma com 100 jogos (máximo 78) e todas com e-valores entre 0,78 e 1,87, muito abaixo dos limiares (40 a 2 640).
