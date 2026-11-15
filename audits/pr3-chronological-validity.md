# PR 3 — Validade cronológica, testes múltiplos e avaliação prospetiva

**Draft para auditoria independente. Nada foi executado em produção.** Sem merge, deploy, alteração de workflows, secrets, sessões ou `data/`; nenhuma mensagem Telegram enviada; nenhuma migração executada. Comparação reproduzível em `scripts/audit_pr3_chronology.py`, com resultado em `audits/pr3-metrics.json`.

- **Base:** `82ab9d86593055fbc98420265a469728d914288f` (`main` com a PR 2, no início desta PR). Os SHA-256 de `data/` estão no JSON e conferem com a base (`data_matches_base: true`); a auditoria não altera `data/` (`data_unchanged_by_audit: true`).
- **Relógio fixo:** `2026-11-15T14:34:34Z` (hora do commit base). **Python:** 3.11.
- **Rede bloqueada** na auditoria e nos testes; sementes fixas (`20261115`).

> **Revisão #5911603577 (commit `d946ffd`): correções feitas nesta versão.** Ver a secção 0-B. Em resumo:
> - **P1-C:** só são certificáveis hipóteses cujas condições se conhecem todas no primeiro sinal da aposta (lista fechada `AS_OF_DIMENSIONS`).
> - **P1-D:** uma aposta só entra se a decisão mais tardia possível (leitura + 15 min) for anterior à primeira observação de **qualquer** resultado do jogo, por qualquer fonte.
> - **P1-E:** existe agora um registo *append-only* de seleções, com ativação explícita, rank e α fixos em tempo real e cadeia de hashes. As 11 promissoras atuais ficariam **legadas e não certificáveis**.
> - **P2:** o e-processo é calculado em domínio logarítmico.
> - **A validação científica fica DESLIGADA por defeito, até à ativação autorizada. Não há nenhuma alegação de controlo do erro enquanto o registo não estiver ativo e verificado.**
>
> **Revisão #5911487915 (commit `ef4606a`): correções da ronda anterior.** Ver a secção 0. Em resumo:
> - **P1-A e P1-B:** existe agora uma definição única de jogo prospetivo. Um jogo só conta se todas as suas apostas foram emitidas e lidas depois do registo, eram decidíveis e têm hora de resultado provada. Essa definição é usada pelo relatório, pela máquina de estados, pelo `entry_status` e pelo teste de confirmação.
> - **P1 científico:** escolhida a **via 1**. "Forte" e "confirmada" deixam de usar caudas posteriores com Bonferroni e passam a exigir um **certificado** de um e-processo com nível garantido. Acrescenta-se uma proteção fail-closed no cérebro e no Telegram.

## 0-B. Correções após a revisão #5911603577

**P1-C — a amostra certificável dependia de informação posterior à aposta.**
- `brain.AS_OF_DIMENSIONS` é uma lista fechada de 23 dimensões. Todas são calculadas só a partir da primeira mensagem da própria aposta: competição, faixa de odd, dia, hora, liga, momento, quota, tamanho do mercado, antecedência, parte do jogo, confiança, ritmo, golos em falta, vantagem, mercado, momento do aviso, marcador, golos até ao sinal, minuto, seleção, volume na seleção, valor e número de apostas.
- `brain.certifiable(h)` exige registo válido e todas as condições nesta lista. Ficam sempre fora:
  - as dimensões `POST`: reforço, odd e quota depois do sinal, velocidade do reforço, outros sinais no mesmo jogo;
  - "Sinal anterior de outro tipo", que depende de outras mensagens sem prova de leitura;
  - qualquer dimensão nova.
- **No snapshot:** 69 das 143 testadas são certificáveis, e 6 das 11 promissoras. As outras 5 usam "Outros sinais" ou "Sinal anterior".
- **Testes:**
  - todas as dimensões `POST` e "Sinal anterior" → não certificável;
  - sinais posteriores de outra família mudam a etiqueta "um só tipo", mas o certificado de uma hipótese certificável fica igual e a hipótese `POST` nunca é certificada;
  - reforço e odd posteriores (max_mult 1→4, odd_last) → certificado igual;
  - aposta decidida depois do primeiro resultado → ignorada, sem efeito retroativo; com controlo positivo: a mesma aposta decidida antes entra.

**P1-D — resultado já conhecido quando a aposta podia ser decidida.**
- `report.match_first_seen` regista em cada aposta `match_result_first_seen_utc`: a primeira observação de **qualquer** resultado do jogo, por qualquer fonte.
  - **Fontes:** versão do bot com resultado (mesmo que corrigido depois), nota de fim de jogo, Highlightly, BSD, manual (sem `observed_utc` → desconhecida) e minutos dos golos.
  - Não depende das apostas existentes, por isso uma aposta nova nunca a altera.
- `brain.game_core(rows)` mantém só as apostas com leitura + 15 min (atraso máximo do relatório) **antes** desse instante. As restantes são ignoradas, nunca tornando o jogo "misto" retroativamente.
- `operational_game`, `prospective_summary`, `game_steps` e `confirmation` usam o mesmo núcleo. Sem primeira observação provada, o jogo não é operacional (`result_time_unproven`).
- **No snapshot:**
  - 10 das 286 apostas elegíveis com resultado foram decididas depois de o sistema já conhecer um resultado do jogo; 2 delas eram aceites como operacionais em `d946ffd`;
  - pares hipótese–jogo prospetivos: 4 600 → **4 593**, com 111 pares agora em `result_known_before_decision`;
  - a contagem de hipóteses com ≥ 30 jogos prospetivos não muda (74).
- **Testes:**
  - 40 jogos com o resultado visto 4 min depois do envio e o sinal lido aos 8 min dão 0 jogos de certificação, 0 prospetivos e 40 `result_known_before_decision`;
  - resultado visto 10 min depois da leitura (dentro da latência de 15 min) dá 0; com controlo a 145 min dá 40;
  - outra fonte a ver o resultado primeiro também conta;
  - no relatório, a primeira observação é a do 1.º resultado mesmo que corrigido, ou da Highlightly se for anterior ao bot; um manual sem hora dá desconhecida.

**P1-E — ordem de seleção reconstruída.** Criado o módulo `scangoal/certification.py`, com o registo *append-only* `data/certification_ledger.json`. **O ficheiro não existe e não foi criado.**
- **Ativação:** só com o comando manual `python -m scangoal.certification --activate`, a autorizar no corte. A recolha nunca o cria.
- **Legadas:** as hipóteses promissoras ou melhores no momento da ativação ficam **legadas**. Nunca são certificáveis, nem mais tarde, e não gastam α. É a política conservadora para as 11 atuais: nenhuma reconstrução histórica dá certificados.
- **Seleção:** cada relatório com `register=True` acrescenta, com a **hora real**, as hipóteses que estão promissoras, são certificáveis, não são legadas e ainda não constam.
  - Cada entrada guarda rank j, α_j = 0,05/(j(j+1)), `selected_utc`, o hash das condições e o hash da entrada anterior. Nunca se reescreve nada.
  - A confirmação usa **só** este registo: o rank, o α e o início ficam fixos. A seleção reconstruída a partir do replay deixou de existir.
- **Verificação** (`certification.verify`), fail-closed:
  - método e α, ativação e genesis;
  - ranks consecutivos e α conforme o rank;
  - horas não decrescentes e não anteriores à ativação;
  - identidades únicas e não legadas;
  - cadeia de hashes e condições iguais às do registo de hipóteses.
  
  Qualquer falha desliga **toda** a certificação (`state = ledger_invalid`). Um registo inválido nunca é estendido.
- **Testes:**
  - dados tardios (fontes, apostas, sinais de outras famílias) não alteram rank, α, início nem certificado;
  - 10 adulterações diferentes desligam tudo;
  - sem registo → `not_activated`, sem validadas e sem Telegram;
  - as legadas nunca são certificadas e não gastam α;
  - as candidatas são só promissoras certificáveis e entram uma vez;
  - o relatório só escreve o registo com `register=True` e com registo ativo e válido;
  - uma seleção com hora anterior à ativação é rejeitada (o teste apanhou-o com o relógio real).

**P2 — robustez numérica.**
- `log E` é calculado por log-mean-exp e a certificação compara `log E ≥ log(1/α)`.
- `e_value` só é mostrado abaixo de e^700; `log10_e` é sempre mostrado.
- Teste: 5 000 vitórias (log₁₀E = 1 471, certifica, JSON válido) e 5 000 derrotas (log₁₀E < −60), sem erro.

**Contraexemplos da revisão executados contra `d946ffd` e contra este código** (`counterexamples_review_5911603577` nas métricas):

| | `d946ffd` | agora |
|---|---|---|
| P1-D: 40 jogos com o resultado visto antes da leitura | 40 jogos contados | **0** |
| P1-C: condição "um só tipo de sinal" + sinais posteriores de outra família | 69 jogos → a seleção desaparece | **não certificável** |
| P1-E: apostas extra com resultado tardio nos jogos de A | ranks A=1, B=2 → **A=2, B=1** | ranks do registo, iguais antes e depois |
| P2: 5 000 vitórias à odd 3,0 | `OverflowError: math range error` | log₁₀E = 1 471,6, sem erro |

**Simulação retrospetiva (NÃO é certificado).** Aplicado às 11 seleções reconstruídas de `d946ffd`, o e-processo daria 1 a 17 jogos e log₁₀E de −0,11 a +0,27, contra limiares de 40 a 2 640. Nenhuma certificaria. Mesmo assim, por política, nenhuma pode vir a ser certificada.

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
- No snapshot, em `d946ffd`: **11 selecionadas, 0 certificadas**. Nesta versão, a seleção reconstruída foi substituída pelo registo (secção 0-B). Os e-valores atuais vão de 0,78 a 1,87 (máximo histórico 2,44), contra limiares de 40 a 2 640.

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
4. **Validação certificada** (forte/confirmada; revisões #5911487915 e #5911603577). **Desligada por defeito.**
   - **Ativação:** só existe com o registo de seleções ativado e verificado (`certification.selections`). Sem ele, nenhuma hipótese tem certificado, nenhuma é forte/confirmada e o relatório declara "no claim".
   - **Seleção:** a entrada do registo, escrita em tempo real quando a hipótese está promissora, é certificável e não é legada. Dela vêm o rank j, α_j = 0,05/(j(j+1)) e o início `selected_utc`, todos fixos.
   - **Amostra:** só hipóteses `certifiable` (condições em `AS_OF_DIMENSIONS`). Em cada jogo, o núcleo (`game_core`) são as apostas decididas antes da primeira observação de qualquer resultado do jogo. O jogo conta se todas essas apostas forem operacionais depois de `selected_utc`: emitidas e lidas depois, decidíveis e com resultado de hora provada. Os jogos são processados pela ordem em que o núcleo ficou completo.
   - **Estatística:** lucro por jogo do núcleo, y = média das apostas distintas de w·(odd·0,98 − 1) se ganha, w se perde. Inclui meias apostas, y ≥ −1, e as apostas repetidas contam uma vez.
   - **E-processo:** log E_n = log-mean-exp sobre λ ∈ {1/2, 1/4, 1/8, 1/16, 1/32} de Σ log(1 + λ·y_i).
     - **Nulo** H₀(h): em cada jogo usado, o lucro esperado do núcleo, dado tudo o que se sabia quando as suas apostas foram decididas, é ≤ 0.
     - Como todas as apostas do núcleo são decididas antes de qualquer resultado do jogo, a composição e os pesos são previsíveis. Sob H₀, cada produto é uma supermartingala não negativa com início em 1.
     - **Ville:** P(sup_n E_n ≥ 1/α_j) ≤ α_j.
   - **Certificação:** quando log E_n ≥ log(1/α_j) com n ≥ 30.
   - **Erro de família:** α_j e o início são fixados antes dos jogos usados, pelo que, somando sobre os ranks, P(alguma certificação falsa) ≤ Σ α_j ≤ 0,05.
   - **Estado:** forte/confirmada = certificado + ≥ 100/300 jogos + prob ≥ 0,95 (manter: 0,90), com a confirmação em 2 passos. Uma validada sem certificado é rebaixada (`validation_blocked`).
   - **Sobreposição:** cada hipótese registada paga o seu α_j.
   - **O que deixou de existir:** a fasquia Bonferroni, as caudas posteriores, a porta "30 prospetivos com 95 %" e a seleção reconstruída pelo replay. `corrected_bar` passa a `null`.
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

**Garantia matemática: só depois da ativação, sob pressupostos explícitos.** Com o registo ativo e verificado, P(alguma tática sem vantagem ser certificada desde a ativação) ≤ 0,05.
- **Pressupostos:**
  - (A1) **o nulo condicional:** em cada jogo usado, sem lucro esperado à odd −2 %, dado tudo o que se sabia quando as apostas do núcleo foram decididas;
  - (A2) **pertença previsível:** as condições usam só `AS_OF_DIMENSIONS`, conhecidas no primeiro sinal da aposta, e o núcleo é fixado antes de qualquer resultado do jogo;
  - (A3) **horas verdadeiras**, verificadas contra o git: leitura dos sinais, primeira observação do resultado do jogo, registo de seleções;
  - (A4) **registo append-only íntegro:** cadeia de hashes verificada, com o git como relógio externo.
- **Sem registo ativo, nenhuma garantia é declarada** e nenhuma tática é validada. É o estado atual e o desta PR.
- **Verificação empírica:**
  - nulo no limite (odds 1,4–3,5, meias vitórias): 1,7 % das 300 séries cruzam 1/0,05, e 0 % cruzam 1/0,025;
  - sintéticos de odd 2,0 sem vantagem, com registo (rank 1, α = 0,025): 0 %, **0,5 %** e 0 % em 160, 300 e 1000 jogos, todos ≤ 2,5 %.

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
| Validação | — | **desligada** (`not_activated`): 0 registadas, 0 certificadas; se ativada agora, 11 legadas |
| Hipóteses certificáveis (condições as-of): testadas / promissoras | — | 69 de 143 / 6 de 11 |
| `prob_above` e `games` iguais (150 hipóteses comparadas) | — | sim |
| Hipóteses testadas com evidência prospetiva ≥ 30 jogos | — | 74 de 143 (80 em `ef4606a`); 4 593 pares prospetivos (4 600 em `d946ffd`) |
| Walk-forward seguidas: apostas, ROI, ROI odd −2 % | 135, +0,77 %, −0,91 % | 131, +3,12 %, +1,41 % |
| Walk-forward não seguidas: apostas, ROI | 290, −2,66 % | 259, −4,97 % |
| Não reconstituíveis | — | 35 (24 com resultado) |
| P(seguidas renderem mais) | 0,43 | 0,60 |
| IC 90 % *bootstrap* por jogo, ROI das seguidas | — | [−11,2 %; +17,5 %] |
| Primeiro relatório PR 3 sobre o estado Telegram de produção | — | **0 avisos de estado** (sem migração seriam 2) |
| `report.build` (2 medições) | 9,6–11,5 s | 10,0–11,6 s |

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

| Caso | Sementes | PR 2 | PR 3 `ef4606a` (Bonferroni + caudas) | PR 3 (e-processo, registo rank 1, α = 0,025) |
|---|---:|---:|---:|---:|
| sem vantagem, 160 jogos | 200 | 12 % | 0 % | **0 %** |
| sem vantagem, 300 jogos | 200 | 18 % | 0 % | **0,5 %** (1 série; limite 2,5 %) |
| sem vantagem, 1000 jogos | 60 | 25 % | 0 % | **0 %** |
| p = 0,60 (ROI +20 %), 300 jogos | 200 | 97,5 % | 46 % | 62 % |
| p = 0,60, 1000 jogos | 60 | 100 % | 98,3 % | 100 % |
| p = 0,75, 160 jogos | 200 | 100 % | 100 % | 100 % |

Nesta tabela, o registo seleciona a hipótese antes do primeiro jogo. É por isso que a potência é maior do que na ronda anterior, que só começava a contar depois da primeira promoção. A taxa nula de 0,5 % fica abaixo do limite garantido de 2,5 %.

**Leitura:**
- A versão `ef4606a` também não validou falsamente nestes casos. A diferença está na garantia: a sua taxa nula de 0 % era empírica e sem prova, enquanto a do e-processo é limitada por construção, para toda a vida do projeto e qualquer número de táticas selecionadas.
- **Custo:** vantagens realistas (2–5 % de ROI) não serão certificadas com centenas nem com poucos milhares de jogos. Uma tática selecionada em 11.º lugar precisa de E ≥ 2 640. É a consequência honesta do tamanho da amostra, aceite conforme pedido ("rigor antes de facilidade").

## 8. Testes

**Total:** 226 testes offline e determinísticos (`python scripts/test_offline.py`, rede bloqueada). Destes, 178 são anteriores à PR 3 e 48 estão em `tests/test_chronological_validity.py`.

**Novos após a revisão #5911603577:**
- **`AsOfMembership`** (P1-C):
  - a lista branca, com `POST`, "Sinal anterior" e dimensões novas fora;
  - sinal posterior de outra família;
  - reforço e odd posteriores;
  - aposta posterior ao primeiro resultado ignorada, com controlo positivo.
- **`ResultKnownBeforeDecision`** (P1-D):
  - resultado visto antes da leitura → 0;
  - latência de 15 min respeitada, com controlo a 145 min;
  - outra fonte primeiro também conta.
- **`SelectionLedger`** (P1-E):
  - dados tardios não alteram o registo nem os certificados;
  - 10 adulterações e mudança de condições desligam tudo;
  - desligada por defeito, também no Telegram;
  - legadas nunca certificadas e sem gastar α;
  - candidatas só certificáveis e uma única vez.
- **`NumericalRobustness`** (P2): 5 000 vitórias e 5 000 derrotas.
- **`ReportProvenance`:**
  - a primeira observação de qualquer resultado: corrigido, Highlightly antes do bot, manual sem hora;
  - o registo só é escrito com ativação e `register=True`, e um registo inválido nunca é estendido.
- **Reescritos** para usar o registo, porque sem ele não há certificados: os cenários (vantagem certificada, sem vantagem nunca validada, deterioração), `OnlineConfirmation`, `OverlappingHypotheses` e `CorrectedBarIsChronological`. Este último afirma também que, sem registo, nunca há validada.
- **Fixtures:** declaram `match_result_first_seen_utc`, igual à hora do resultado, como já declaravam a proveniência. As do Telegram declaram `state: active` e `ledger_valid: true`.

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

- **Regra nova:** `brain.status_rules = chronological_eprocess_v2`. O mecanismo existente (`notify.migration_key`) dá-lhe a chave `status_rules:chronological_eprocess_v2`, por isso o primeiro relatório PR 3 faz **uma** reposição silenciosa da referência. `eligible_statuses_v1`, `status_rules:independent_games_hysteresis_v1` e o histórico de entregas ficam intactos (teste e ensaio).
- **Ensaio** sobre o `notify_state.json` real, só com `messages()` em memória: 0 avisos de estado com a migração, contra 2 sem ela ("voltou a em observação").
- **Proteção fail-closed:** `notify.eligible_statuses` e os alertas só reconhecem validadas com o método aceite, `validation.state = active`, `ledger_valid = true` e o certificado da tática. Com a validação desligada, que é o estado após o merge até à ativação, nada é anunciado como validado.
- **Ativação do registo de certificação:** é um passo separado, manual e por autorizar (`python -m scangoal.certification --activate`). Não foi executado nem preparado.
- **Não executado.** Como na PR 2, a integração exige corte controlado: pausa, corte e merge. Uma execução com código da PR 2 que corra depois do primeiro relatório PR 3 gravaria estados da PR 2 sob uma referência já migrada, e a seguinte anunciaria essas diferenças. O script de corte da PR 3 (variante de `scripts/pr2_cutover.py` com a chave nova) **não foi criado**: fica para depois da aprovação da metodologia.

## 10. Limitações e riscos residuais

1. **Pré-recolha:** decisões anteriores a 12/11 09:34 são irreconstituíveis. As margens de 5/15 min são limites dos timeouts, não medições por mensagem.
2. **Pressupostos da garantia** (secção 4: A1–A4, válida só depois da ativação):
   - a odd −2 % tem de ser atingível;
   - o e-processo não protege contra dados errados nem contra liquidações erradas.
3. **Alcance reduzido de propósito:**
   - só 69 das 143 testadas (6 das 11 promissoras) são certificáveis;
   - as 11 promissoras atuais serão legadas;
   - táticas registadas mais tarde recebem α muito menor (ordem de registo em tempo real; política documentada, não ajustada ao ROI).
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
python3.11 scripts/test_offline.py                                    # 226 testes
python3.11 -m unittest tests.test_chronological_validity -v
python3.11 scripts/audit_pr3_chronology.py > /tmp/pr3-metrics.json    # ~15 min; data/ tem de ser igual à base
```

## 12. Parecer do autor (não substitui a revisão independente)

- **Técnico: aprovado pelo autor**, sujeito a revisão.
  - P1-C, P1-D, P1-E e P2 estão corrigidos. Os contraexemplos da revisão reproduzem-se em `d946ffd` e falham no código atual.
  - A validação está desligada por defeito e fail-closed no cérebro e no Telegram.
  - Sem alterações em produção.
- **Científico: INCONCLUSIVO** quanto a capacidade preditiva:
  - o walk-forward cronológico dá +3,1 % (IC 90 % −11 % a +18 %), instável a escolhas de minutos;
  - o número de promissoras é compatível com acaso (11 % das simulações nulas dão tantas ou mais);
  - nenhuma tática certificada.
- **Garantia de erro:** **não é declarada** no estado atual. Só passa a valer, sob A1–A4, depois de uma ativação autorizada do registo.
- **Cientificamente bloqueado:**
  - a reconstituição de decisões anteriores a 12/11 09:34;
  - qualquer validação antes da ativação;
  - a certificação das 11 promissoras atuais, que ficam legadas.
