# PR 3 — Validade cronológica, testes múltiplos e avaliação prospetiva

**Draft para auditoria independente. Nada foi executado em produção.** Sem merge, deploy, alteração de workflows, secrets, sessões ou `data/`; nenhuma mensagem Telegram enviada; nenhuma migração executada. Comparação reproduzível em `scripts/audit_pr3_chronology.py`, com resultado em `audits/pr3-metrics.json`.

- **Base:** `82ab9d86593055fbc98420265a469728d914288f` (`main` com a PR 2, no início desta PR). Os SHA-256 de `data/` estão no JSON e conferem com a base (`data_matches_base: true`); a auditoria não altera `data/` (`data_unchanged_by_audit: true`).
- **Relógio fixo:** `2026-11-15T14:34:34Z` (hora do commit base). **Python:** 3.11.
- **Rede bloqueada** na auditoria e nos testes; sementes fixas (`20261115`).

> **Revisão #5911845265 (commit `9da69d8`): correções feitas nesta versão.** Ver a secção 0-D. P1-F e P1-G foram aprovados e não mudaram. Em resumo:
> - **P1-H:** a prova de integridade histórica (`certification.history_proof`, contra o histórico git publicado) passa a fazer parte do percurso real que autoriza a certificação. Sem prova: `history_unverified`, sem certificados, sem alertas validados e sem novas entradas.
> - **P2-I:** o prazo de 3 dias fecha como perda pelo relógio explícito do relatório (`as_of`), mesmo sem outra aposta.
>
> **Revisão #5911747123 (commit `345e008`): correções da ronda anterior.** Ver a secção 0-C. Em resumo:
> - **P1-F:** a certificação usa uma **cadeia sem sobreposição**. Um jogo só entra se, na hora da decisão, o jogo anterior da cadeia já tiver resultado observado. Um jogo sem resultado em 3 dias fecha como perda.
> - **P1-G:** conta **uma aposta comprometida por jogo**, a primeira decidida. Deixa de haver média por jogo.
> - **P2:**
>   - `verify` deixa de levantar exceções;
>   - `verify_history` prova o crescimento append-only contra versões publicadas, e o relatório expõe a cabeça da cadeia;
>   - a ativação tem pré-condições verificadas.
> - A **certificação continua desligada**.
>
> **Revisão #5911603577 (commit `d946ffd`): correções da ronda anterior.** Ver a secção 0-B. Em resumo:
> - **P1-C:** só são certificáveis hipóteses cujas condições se conhecem todas no primeiro sinal da aposta (lista fechada `AS_OF_DIMENSIONS`).
> - **P1-D:** uma aposta só entra se a decisão mais tardia possível (leitura + 15 min) for anterior à primeira observação de **qualquer** resultado do jogo, por qualquer fonte.
> - **P1-E:** existe agora um registo *append-only* de seleções, com ativação explícita, rank e α fixos em tempo real e cadeia de hashes. As 11 promissoras atuais ficariam **legadas e não certificáveis**.
> - **P2:** o e-processo é calculado em domínio logarítmico.
> - **A validação científica fica DESLIGADA por defeito, até à ativação autorizada. Não há nenhuma alegação de controlo do erro enquanto o registo não estiver ativo e verificado.**
>
> **Revisão #5911487915 (commit `ef4606a`): correções da ronda anterior.** Ver a secção 0. Em resumo:
> - **P1-A e P1-B:** existe agora uma definição única de jogo prospetivo. Um jogo só conta se todas as suas apostas foram emitidas e lidas depois do registo, eram decidíveis e têm hora de resultado provada. Essa definição é usada pelo relatório, pela máquina de estados, pelo `entry_status` e pelo teste de confirmação.
> - **P1 científico:** escolhida a **via 1**. "Forte" e "confirmada" deixam de usar caudas posteriores com Bonferroni e passam a exigir um **certificado** de um e-processo com nível garantido. Acrescenta-se uma proteção fail-closed no cérebro e no Telegram.

## 0-D. Correções após a revisão #5911845265

**P1-H — integridade histórica no percurso efetivo.** `report.build` chama `certification.history_proof(registo, cópia atual, as_of=now)` antes de `brain.build`. A prova exige tudo o que se segue, e qualquer falha é fail-closed:
1. git disponível e repositório **não shallow** (histórico completo);
2. o registo publicado **pelo menos uma vez**. `verify_history([])` deixou de ser prova;
3. todas as versões publicadas são válidas e cada uma prolonga a anterior sem tocar no génesis, nas legadas nem nas entradas;
4. a cópia atual prolonga a última publicada;
5. um ficheiro apagado ou ilegível numa versão é um problema;
6. **coerência temporal:** o génesis e cada entrada foram publicados no commit que os introduziu entre o seu carimbo e +30 min (`PUBLISH_TOLERANCE_MIN`), nunca antes. 30 min cobre o job de recolha: o relatório escreve, o commit do mesmo job publica, e o job tem timeout de 15 min;
7. entradas ainda não publicadas não podem ser anteriores à última publicação nem posteriores ao relógio do relatório.

`brain.build(..., history_problems=...)` só ativa a certificação com a prova feita **e** vazia. Com `None` (prova não feita) ou com problemas, o estado é `history_unverified`: sem certificados, sem candidatas (nada é acrescentado ao registo) e sem "tática validada" no Telegram, porque `ledger_valid = false`.

**Limites desta âncora, declarados:**
- as horas de commit são as do runner do GitHub Actions; o histórico de pushes do GitHub seria uma âncora mais forte, mas exigiria rede e API no relatório;
- **o workflow atual faz checkout shallow** (`actions/checkout` com profundidade 1), por isso em produção esta prova falha sempre e a certificação fica fechada. É o comportamento correto até um corte autorizado acrescentar `fetch-depth: 0` (ou equivalente) ao workflow. Isso não foi feito: workflows proibidos nesta PR.

**P2-I — fecho aos 3 dias.**
- `confirmation(..., as_of)`: um jogo admitido sem resultado provado fecha como **perda** no seu prazo logo que `as_of ≥ decisão + 3 dias`, mesmo que não apareça mais nenhuma aposta.
- Sem `as_of` (avaliações históricas sem relógio) nada é antecipado.
- `report.build(now=...)` usa um só instante para avaliar, registar seleções e `generated_at_utc`, por isso `register=False` e `register=True` no mesmo instante dão a mesma validação.

**Demonstração contra `9da69d8` e agora** (`counterexamples_review_5911845265`):

| | `9da69d8` | agora |
|---|---|---|
| Registo reescrito com hashes recalculados (verificação interna OK) | **`active`, `x` certificada** | `history_unverified`, nada certificado. Prova git: "version 1 is not an append-only extension" |
| Jogo pendente sozinho, sem mais apostas | pendente para sempre | aos 2d23h pendente; **aos 3d1h perda** (1 jogo, 1 anulado) |

**Testes** (245 no total, 10 novos):
- **`LedgerHistoryEndToEnd`** usa repositórios git temporários reais, com `report.build` como em produção:
  - histórico genuíno: ativa e acrescenta, publicado dentro da tolerância;
  - reescrita com hashes recalculados, antes e depois de ser commitada: `history_unverified`, o ficheiro nunca é estendido e não há "validada" nem ALERTA no Telegram;
  - nunca publicado, clone shallow, sem git, apagado e recriado;
  - génesis publicado antes da ativação;
  - entrada publicada 2 h depois;
  - entradas não publicadas fora do tempo;
  - `brain.build` sem prova fechado e com prova `[]` ativo.
- **`ThreeDayDeadline`:** 2d23h / 3d exatos / 3d1h; sem relógio não antecipa; resultado depois do prazo continua perda; o mesmo instante dá a mesma resposta.

Contra `9da69d8`, 9 dos 10 falham. O que passa, "sem relógio não antecipa", protege um comportamento que já existia.

**Sem regressões** (mesmo snapshot, mesmo relógio): os estados (11 promissoras), o walk-forward, a simulação nula, a sensibilidade, as definições prospetivas, as taxas sintéticas, o ensaio Telegram e **todos** os contraexemplos das revisões anteriores (P1-C a P1-G e P2) dão **exatamente** os mesmos valores da ronda anterior. As 235 regressões anteriores passam.

**Pré-requisitos do corte futuro.** Ficam documentados, mas nada foi preparado nem executado:
- o workflow tem de passar a fazer checkout com histórico completo (sem isso, a certificação fica fechada);
- verificação externa (GitHub Actions) de zero execuções antigas ativas ou pendentes, `main` congelada e proibição de re-run; `--confirm-collection-paused` é só uma declaração;
- hashes dos inputs (`data/`) iguais entre a avaliação fresca, a migração Telegram e a gravação do registo;
- primeiro commit do registo dentro de 30 min da ativação;
- todas as marcas Telegram anteriores preservadas; a regra nova é `status_rules:chronological_eprocess_v3`.

## 0-C. Correções após a revisão #5911747123

**P1-F — jogos simultaneamente em aberto.** A revisão mostra que o nulo "sem vantagem dada a informação na hora de cada aposta" não chega para um produto pela ordem de revelação quando há jogos abertos em simultâneo. Com 120 jogos ligados pela mesma moeda X, `345e008` certificava com probabilidade 0,5.

A correção mais conservadora é uma **cadeia sem sobreposição** (`brain.confirmation`):
- um candidato só é admitido se, na hora da sua decisão, o último jogo admitido já estiver **fechado**, isto é, com o resultado observado; os restantes são ignorados;
- a admissão depende só de horas conhecidas nesse instante, por isso é previsível e não muda com dados posteriores (os carimbos são em tempo real);
- um jogo admitido sem resultado provado até 3 dias depois da decisão (`OPEN_TIMEOUT_DAYS` = `NO_SOURCE_AFTER_DAYS`) fecha nesse prazo como **perda** (y = −1), definitivamente;
- como (1 − λ) ≤ 1 + λ·y para qualquer y ≥ −1, a perda nunca aumenta a esperança do fator. Não é preciso nenhum pressuposto sobre dados em falta, e um jogo anulado (y = 0) também fica coberto.

**Prova.**
1. Seja F_k a informação na decisão do k-ésimo jogo admitido. Pela cadeia, F_k contém os resultados de todos os jogos anteriores.
2. Sob H₀, E[y_k | F_k] ≤ 0, e o fator efetivo é ≤ 1 + λ·y_k. Logo E[fator_k | F_k] ≤ 1.
3. O produto é uma supermartingala não negativa na filtração (F_k), para **qualquer** dependência entre jogos.
4. Ville dá P(sup E ≥ 1/α_j) ≤ α_j, e a soma sobre os ranks fixos do registo dá ≤ 0,05.

**P1-G — média com pesos posteriores.** Com uma segunda aposta só quando um sinal posterior aponta para X = −1, a média por jogo tinha esperança +0,225 com apostas individualmente sem vantagem.
- Agora conta **uma aposta comprometida por jogo** (`committed_bet`): a primeira aposta da hipótese a ser decidida nesse jogo.
- Consideram-se também as apostas abertas e devolvidas, para que um *push* não faça "subir" a segunda.
- Apostas posteriores noutros mercados nunca a substituem nem entram numa média.

**Contraexemplos executados contra `345e008` e contra este código** (`counterexamples_review_5911747123`):

| | `345e008` | agora |
|---|---|---|
| P1-F: 120 jogos correlacionados, todos abertos; X = +1 / X = −1 | certifica / não; **P(falsa certificação) = 0,5** | 1 admitido e 119 ignorados nos dois ramos; **P = 0** |
| P1-G: esperança exata do lucro usado (enumeração de X, Y) | +0,225 (média por jogo) | −0,02 (aposta comprometida) |
| P1-G: 200 séries de 200 jogos no limite do nulo, α = 0,05 | **94 %** certificam | **1 %** |
| P2: entrada com `id = []` | `TypeError` | problema `entry 1`, certificação desligada |
| P2: registo reescrito com hashes recalculados | aceite | interno OK, **`verify_history` rejeita** |

**Controlos:**
- jogos sequenciais com vantagem real (3/4) certificam;
- jogos sequenciais sem vantagem: ≤ α em 100 séries;
- nulo no limite com odds 1,4–3,5 e meias vitórias: 1 % das 300 séries cruzam 1/0,05, e 0 % cruzam 1/0,025;
- **o preço explícito:** vantagem real em 120 jogos todos abertos ao mesmo tempo → 1 admitido, sem certificação.

**P2 — registo e ativação.**
- **`verify`** nunca levanta exceções. Valida tipos (ids em texto, rank inteiro, α em vírgula flutuante, campos exatos) e as legadas (dicionários com id, únicas e ordenadas). Qualquer erro é só um problema.
- **Alcance da cadeia de hashes, agora explícito:** só prova **consistência interna**. A prova de append-only é externa: `verify_history(versões)` exige que cada versão publicada prolongue a anterior sem alterar o génesis, as legadas nem as entradas.
  - O relatório expõe `ledger_head_sha256`, que fica também no histórico git de `report.json`.
  - Testado com reescrita completa (internamente válida, rejeitada pelo histórico), prefixo divergente e truncagem.
- **Ativação** (`activation_plan`, `python -m scangoal.certification --activate --confirm-collection-paused`). Exige:
  - confirmação explícita de recolha parada;
  - nenhum registo existente;
  - `report.json` gerado sob as regras atuais há no máximo 20 min, com o mesmo `status_list` de uma avaliação fresca;
  - a migração Telegram das regras atuais feita;
  - estado `not_activated`.
  
  As legadas são **todas** as hipóteses que estão, estiveram ou estão a caminho de ser promissoras, e a função verifica que nenhuma promissora atual ficou de fora. Cada pré-condição em falha tem teste. **Não foi executada.**

**No snapshot:**
- não há nenhuma certificação (validação desligada);
- a simulação retrospetiva das 11 seleções antigas, que **não é certificado**, admitiria 1 a 13 jogos de cadeia, com log₁₀E entre −0,17 e +0,06 e nenhuma certificação.

**Testes** (235 no total, 9 novos):
- `SimultaneouslyOpenGames`, com 4 testes;
- `AdaptiveBetsPerGame`, com 2;
- `LedgerHardening`, com 3.

Os testes de certificação passaram a usar jogos a cada 3 h, não sobrepostos. Contra `345e008`, 13 dos testes da PR 3 falham:
- 3 por comportamento: a hipótese era certificada com os 120 jogos correlacionados, as apostas posteriores mudavam o jogo, e a migração;
- 10 por API ou erro, incluindo o `TypeError` do `verify`.

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
4. **Validação certificada** (forte/confirmada; revisões #5911487915, #5911603577 e #5911747123). **Desligada por defeito.**
   - **Ativação:** só existe com o registo de seleções ativado e verificado (`certification.selections`). Sem ele, nenhuma hipótese tem certificado, nenhuma é forte/confirmada e o relatório declara "no claim".
   - **Seleção:** a entrada do registo, escrita em tempo real quando a hipótese está promissora, é certificável e não é legada. Dela vêm o rank j, α_j = 0,05/(j(j+1)) e o início `selected_utc`, todos fixos.
   - **Amostra:** só hipóteses `certifiable` (condições em `AS_OF_DIMENSIONS`).
     - **Aposta comprometida:** em cada jogo conta só a primeira aposta da hipótese a ser decidida.
     - **Candidato:** o jogo é candidato se essa aposta foi emitida e lida depois de `selected_utc`, era decidível, e nenhum resultado do jogo foi observado até à decisão mais tardia (leitura + 15 min). Um resultado existente sem hora provada exclui o jogo.
     - **Cadeia:** um candidato só é admitido se o último admitido já estava fechado na hora da sua decisão.
     - **Fecho:** um jogo admitido fecha com o seu resultado ou, ao fim de 3 dias sem resultado provado, como perda.
   - **Estatística:** y = w·(odd·0,98 − 1) se ganha, w se perde e 0 se devolvida, sempre ≥ −1.
   - **E-processo:** log E_n = log-mean-exp sobre λ ∈ {1/2, 1/4, 1/8, 1/16, 1/32} de Σ log(1 + λ·y_k).
     - **Nulo** H₀(h): para cada aposta admitida, E[y | tudo o que o sistema sabia na decisão] ≤ 0.
     - **Supermartingala:** pela cadeia, a decisão do jogo k já conhece os resultados dos anteriores. Sob H₀, E[fator_k | F_k] ≤ 1 e o produto é uma supermartingala não negativa, para qualquer dependência entre jogos.
     - **Ville:** P(sup_n E_n ≥ 1/α_j) ≤ α_j.
   - **Certificação:** quando log E_n ≥ log(1/α_j) com n ≥ 30 jogos fechados. **Erro de família:** Σ α_j ≤ 0,05.
   - **Estado:** forte/confirmada = certificado + ≥ 100/300 jogos + prob ≥ 0,95 (manter: 0,90), com a confirmação em 2 passos. Uma validada sem certificado é rebaixada (`validation_blocked`).
   - **Sobreposição de hipóteses:** cada hipótese registada paga o seu α_j.
   - **O que deixou de existir:**
     - a fasquia Bonferroni e as caudas posteriores;
     - a porta "30 prospetivos com 95 %";
     - a seleção reconstruída;
     - a média por jogo e a ordenação de jogos sobrepostos por hora de revelação.
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
  - (A1) **nulo condicional:** para cada aposta admitida, sem lucro esperado à odd −2 %, dado tudo o que o sistema sabia na decisão. Pela cadeia, isso inclui os resultados de todos os jogos anteriores admitidos;
  - (A2) **amostra previsível:** condições só em `AS_OF_DIMENSIONS`, uma aposta comprometida por jogo, e admissão e fecho decididos só com horas conhecidas na decisão;
  - (A3) **horas verdadeiras**, verificadas contra o git: leitura dos sinais, primeira observação do resultado do jogo, registo de seleções;
  - (A4) **registo append-only:** a cadeia interna prova a consistência; o crescimento e as horas verificam-se em cada relatório contra o histórico git publicado (`history_proof`), e sem prova fica fechado.
- **Não exige** independência entre jogos nem entre hipóteses, nem nenhum pressuposto sobre resultados em falta (fecham como perda).
- **Sem registo ativo, nenhuma garantia é declarada** e nenhuma tática é validada. É o estado atual e o desta PR.
- **Verificação empírica:**
  - nulo no limite com odds variadas: 1 % das séries cruzam 1/0,05, e 0 % cruzam 1/0,025;
  - sintéticos de odd 2,0 sem vantagem, com registo (rank 1, α = 0,025): 0 %, 0,5 % e 0 % em 160, 300 e 1000 jogos;
  - contraexemplos P1-F e P1-G: 0 e 1 %.

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
| `report.build` (2 medições, ruidosas neste contentor) | 8,4–8,8 s | 9,7–9,9 s |

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

Nesta tabela, os jogos são separados por 3 h, ou seja, não sobrepostos; esta ronda acrescentou o espaçamento aos sintéticos. O registo seleciona a hipótese antes do primeiro jogo. É por isso que a potência é maior do que na ronda anterior, que só começava a contar depois da primeira promoção. A taxa nula de 0,5 % fica abaixo do limite garantido de 2,5 %.

**Leitura:**
- A versão `ef4606a` também não validou falsamente nestes casos. A diferença está na garantia: a sua taxa nula de 0 % era empírica e sem prova, enquanto a do e-processo é limitada por construção, para toda a vida do projeto e qualquer número de táticas selecionadas.
- **Custo:** vantagens realistas (2–5 % de ROI) não serão certificadas com centenas nem com poucos milhares de jogos. Uma tática selecionada em 11.º lugar precisa de E ≥ 2 640. É a consequência honesta do tamanho da amostra, aceite conforme pedido ("rigor antes de facilidade").

## 8. Testes

**Total:** 245 testes offline e determinísticos (`python scripts/test_offline.py`, rede bloqueada; os testes de histórico usam git local). Destes, 178 são anteriores à PR 3 e 67 estão em `tests/test_chronological_validity.py`. Os novos das revisões #5911747123 e #5911845265 estão nas secções 0-C e 0-D.

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

- **Regra nova:** `brain.status_rules = chronological_eprocess_v3`. O mecanismo existente (`notify.migration_key`) dá-lhe a chave `status_rules:chronological_eprocess_v3`, por isso o primeiro relatório PR 3 faz **uma** reposição silenciosa da referência. `eligible_statuses_v1`, `status_rules:independent_games_hysteresis_v1` e o histórico de entregas ficam intactos (teste e ensaio).
- **Ensaio** sobre o `notify_state.json` real, só com `messages()` em memória: 0 avisos de estado com a migração, contra 2 sem ela ("voltou a em observação").
- **Proteção fail-closed:** `notify.eligible_statuses` e os alertas só reconhecem validadas com o método aceite, `validation.state = active`, `ledger_valid = true` e o certificado da tática. Com a validação desligada, que é o estado após o merge até à ativação, nada é anunciado como validado.
- **Ativação do registo de certificação:** é um passo separado, manual e por autorizar (`python -m scangoal.certification --activate`). Não foi executado nem preparado.
- **Não executado.** Como na PR 2, a integração exige corte controlado: pausa, corte e merge. Uma execução com código da PR 2 que corra depois do primeiro relatório PR 3 gravaria estados da PR 2 sob uma referência já migrada, e a seguinte anunciaria essas diferenças. O script de corte da PR 3 (variante de `scripts/pr2_cutover.py` com a chave nova) **não foi criado**: fica para depois da aprovação da metodologia.

## 10. Limitações e riscos residuais

1. **Pré-recolha:** decisões anteriores a 12/11 09:34 são irreconstituíveis. As margens de 5/15 min são limites dos timeouts, não medições por mensagem.
2. **Pressupostos da garantia** (secção 4: A1–A4, válida só depois da ativação):
   - a odd −2 % tem de ser atingível;
   - o e-processo não protege contra dados errados nem contra liquidações erradas.
3. **Potência reduzida pela cadeia (escolha conservadora explícita):**
   - jogos sobrepostos da mesma tática não contam: com sinais a cada hora e resultado ~2,5 h depois, só cerca de 1 em cada 3 jogos entra;
   - jogos sem resultado em 3 dias contam como perda.
   
   Não foi alterado nada para recuperar potência.
4. **Alcance reduzido de propósito:**
   - só 69 das 143 testadas (6 das 11 promissoras) são certificáveis;
   - as 11 promissoras atuais serão legadas;
   - táticas registadas mais tarde recebem α muito menor (ordem de registo em tempo real; política documentada, não ajustada ao ROI).
5. **Potência:** a validação fica, na prática, fora de alcance para vantagens realistas. É o comportamento pretendido, mas deve ser comunicado (PR 5).
5. **Promissora continua sem correção.** O relatório e o dashboard dizem-no, e `multiplicity` mostra a sobreposição. A consolidação dos avisos redundantes fica para a PR 5.
6. **Fora do âmbito:** o regime (avisos de mudança de comportamento), o quadro e a agenda continuam a usar a evidência atual, que é exploratória. Não decidem estados.
7. **Atributos dependentes de outros sinais** ("Sinal anterior de outro tipo"): usam sinais com `date_utc` anterior. O leitor relê as 400 mensagens mais recentes, por isso estes foram lidos na mesma recolha ou antes. Só uma lacuna de mais de 400 mensagens o violaria (o leitor avisa com `SCANGOAL_GAP_POSSIBLE`).
8. **Desempenho:** `report.build` mede 8,6–10,3 s em ambas as versões neste contentor (várias corridas). A diferença (≈ +0–15 %) está próxima do ruído das medições.
9. **Integração:** exige o corte da secção 9.

## 11. Reprodução

```bash
git fetch --unshallow origin    # a verificação git precisa do histórico completo
git switch --detach <SHA da PR 3>
python3.11 scripts/test_offline.py                                    # 245 testes
python3.11 -m unittest tests.test_chronological_validity -v
python3.11 scripts/audit_pr3_chronology.py > /tmp/pr3-metrics.json    # ~15 min; data/ tem de ser igual à base
```

## 12. Parecer do autor (não substitui a revisão independente)

- **Técnico: aprovado pelo autor**, sujeito a revisão.
  - P1-H e P2-I estão corrigidos no percurso real, com testes end-to-end em repositórios git.
  - P1-F e P1-G, aprovados, ficam inalterados.
  - Sem regressões no mesmo snapshot.
  - Certificação desligada; sem alterações em produção.
- **Científico: INCONCLUSIVO** quanto a capacidade preditiva:
  - o walk-forward cronológico dá +3,1 % (IC 90 % −11 % a +18 %), instável a escolhas de minutos;
  - o número de promissoras é compatível com acaso;
  - nenhuma tática certificada.
- **Garantia de erro:** **não é declarada** no estado atual. Depois de uma ativação autorizada, decorre de A1–A4 pela prova da secção 0-C (cadeia sem sobreposição, aposta comprometida, Ville e α_j), sem pressupor independência.
- **Cientificamente bloqueado:**
  - a reconstituição de decisões anteriores a 12/11 09:34;
  - qualquer validação antes da ativação;
  - a certificação das legadas.
