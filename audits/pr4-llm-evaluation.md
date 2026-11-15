# PR 4 — Avaliação incremental das hipóteses LLM (protocolo `llm_incremental_v2`)

Gerado por `scripts/audit_pr4_llm.py` a partir de dados de um commit fixo; reprodutível byte a byte.

## Veredicto

**evidência insuficiente**

Mínimos do protocolo não cumpridos:

- groups with >= 10 games < 10
- llm games < 200
- control games < 200
- days with llm games < 14
- one day holds more than 20 % of llm games
- valid replicates < 9000

## Pergunta

As hipóteses do modelo de linguagem escolhem jogos com mais lucro por jogo (odd anunciada −2 %), depois do seu registo,
do que hipóteses comparáveis do gerador que já existiam, no mesmo intervalo? Não é uma medida de vantagem contra o
mercado e não certifica nenhuma tática. Taxas de acerto, incluindo as superiores a 50 %, são só descrição.

## Entradas

- Protocolo: `llm_incremental_v2`, sha256 `1f4a1001b5eefd90c9f6676a40bd63915900395314bd2243a4162d9433081339`, início P = 2026-11-15T22:36:47+00:00
- Avaliado em: 2026-11-15T23:30:46+00:00
- P = max(hora do commit do protocolo, primeira publicação verificável no GitHub); ver `protocol_published_utc` e `protocol_publication_evidence` nas entradas.
- protocol_commit: `25cd2d24ca77c90d8de6e39acf69ecabe803a45f`
- protocol_publication_evidence: `https://github.com/nunovinhas-creator/goal-roi/actions/runs/43770264461`
- protocol_published_utc: `2026-11-15T22:36:47Z`
- python: `3.11`
- ref: `96c176228b00c7d45aead41eb75e0be2992042c0`
- ref_commit_time_utc: `2026-11-15T23:30:46+00:00`
- sha256 data/.gitkeep: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- sha256 data/details_hl.json: `26cde7515897e01c1dbd1136873b6f4fe64fde692191efc249f5c0cf0aee98ad`
- sha256 data/hypotheses.json: `14f9ea7b66dbae19dadbd8c6afe28269654843e6519368723b4a2bb46ee9b4c7`
- sha256 data/llm_notes.json: `9980dcc6034e9244d8d60e5653cee7d6502748a9488d59f9b0ab274fb7fe517e`
- sha256 data/llm_state.json: `e028ca451f5c9ccfc169afa4a2365388f7ab085994743b3bd987bfe32d9a0b4f`
- sha256 data/messages.jsonl: `5ccd6588ba1c35090cea78f4dd722dc0f31028e5ac2840ef76795d8971e5f192`
- sha256 data/notify_state.json: `e944d67f3ecc3dbaadcc2e1d1fdbcc6db17118249ab49926109b00afb097cedd`
- sha256 data/report.json: `da3e10dcfa6eecb012b5aac6543e5cbef9b46a6270ff99bcc0d12e1b54be2296`
- sha256 data/results_bsd.json: `af41bb059c3d30dfc7d225ece4c1817caf0f19c9c5dd3892812dbdb8366199d8`
- sha256 data/results_hl.json: `939952c651cb89efd0fc9e694c090d6c5a44649b78594c8cce7f43b90d8f05b5`
- sha256 data/state.json: `d39684b08b51d9cb53496ed7883bcfc494126548af364b56ba8c4676813f63e1`
- sha256 data/team_aliases.json: `05cd986e98ba1b5eadc5dbe88c24456c9e2406cf1362745cc23c0a06734074f5`

## População

| Medida | Valor |
|---|---|
| Sinais elegíveis na vista do instante | 562 |
| Hipóteses LLM no registo | 51 |
| Avaliadas (nascimento provado) | 51 |
| Excluídas: sem nascimento provado | 0 |
| Grupos de equivalência (τ = 0,85) | 51 |
| Grupos com τ = 0,80 / 0,90 | 51 / 51 |
| Representantes na análise primária | 29 |
| Sem controlos suficientes (< 5) | 17 |
| Duplicados extensionais à nascença | 24 |
| Por modo (novas / refinar / evitar) | 14 / 32 / 5 |

## Comparação primária

D = lucro médio por jogo das hipóteses LLM − lucro médio por jogo dos controlos do gerador (pesos 1/K), jogos prospetivos
desde max(nascimento provado, P). Bootstrap por blocos de dia, 10000 réplicas, semente 20261115; H1: D > 0; α = 0.05.

| Jogos LLM | Jogos controlo | Dias LLM | Maior fração num dia | D | Limite inferior 95 % | p | Réplicas válidas |
|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | — | — | — | — | 0 |

## Janela pós-registo e pré-protocolo (só descrição)

Mesmos representantes e controlos, jogos prospetivos desde o nascimento provado e decididos antes de P. Não é um teste:
foi observada antes de o protocolo existir e não tem p-valor nem intervalo.

| Jogos LLM | Dias | Lucro médio por jogo LLM | Jogos controlo | Lucro médio por jogo controlo |
|---|---|---|---|---|
| 101 | 4 | -0.0181 | 142 | 0.0565 |

## Análises adicionais (exploratórias, Holm m = 7)

| Análise | Jogos em teste | Jogos de comparação | D | p | p Holm |
|---|---|---|---|---|---|
| S1_family_baseline | 0 | 0 | — | — | 1.0000 |
| S2_mode_novas | 0 | 0 | — | — | 1.0000 |
| S3_mode_refinar | 0 | 0 | — | — | 1.0000 |
| S4_refinar_vs_parents | 0 | 0 | — | — | 1.0000 |
| S5_evitar_vs_baseline | 0 | 0 | — | — | 1.0000 |
| S6_tau_0.80 | 0 | 0 | — | — | 1.0000 |
| S7_tau_0.90 | 0 | 0 | — | — | 1.0000 |

Nenhuma análise adicional sustenta sozinha um veredicto.

## Hipóteses (descrição)

| Hipótese | Grupo | Modo | Jogos no protocolo | Lucro/jogo | Jogos pré-protocolo | Lucro/jogo | Controlos |
|---|---|---|---|---|---|---|---|
| Betfair \| Tamanho do mercado = 10 mil $ ou mais & Volume na seleção = 10 mil $ ou mais (repr.) | 0 | — | 0 | — | 8 | -0.0372 | 20 |
| Forma de golos \| Competição = liga & Golos em falta para ganhar = até 1 golo (repr.) | 1 | — | 0 | — | 21 | -0.0865 | 0 |
| Stake \| Mercado = golos (total asiático) & Número de apostas = várias (repr.) | 2 | — | 0 | — | 4 | -0.6325 | 0 |
| Betfair \| Quota de mercado = 80 % ou mais & Seleção = equipa da casa & Tamanho do mercado = menos de 10 mil $ (repr.) | 3 | refinar | 0 | — | 6 | -0.1899 | 0 |
| Jogo aberto \| Confiança indicada pelo bot = 95 % ou mais & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 4 | refinar | 0 | — | 29 | 0.1730 | 0 |
| Betfair \| Quota de mercado = 80 % ou mais & Tamanho do mercado = 10 mil $ ou mais (repr.) | 5 | novas | 0 | — | 2 | -1.0000 | 20 |
| Forma de golos \| Golos até ao sinal = nenhum & Minuto = 31 a 60 (repr.) | 6 | refinar | 0 | — | 12 | 0.2663 | 0 |
| Jogo aberto \| Dia = dia de semana & Golos até ao sinal = 3 ou mais (repr.) | 7 | refinar | 0 | — | 7 | 0.3468 | 8 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Ritmo indicado pelo bot = 90 ou mais (repr.) | 8 | refinar | 0 | — | 30 | -0.0285 | 11 |
| Betfair \| Competição = liga & Tamanho do mercado = 10 mil $ ou mais (repr.) | 9 | novas | 0 | — | 1 | -1.0000 | 3 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Golos até ao sinal = 1 ou 2 & Parte do jogo = 2.ª parte (repr.) | 10 | refinar | 0 | — | 26 | -0.0795 | 0 |
| Betfair \| Competição = jovens & Faixa de odd = odd 2,00 a 3,00 (repr.) | 11 | evitar | 0 | — | 0 | — | 20 |
| Jogo aberto \| Dia = dia de semana & Faixa de odd = odd 1,50 a 2,00 & Golos até ao sinal = 1 ou 2 (repr.) | 12 | refinar | 0 | — | 2 | -1.0000 | 0 |
| Betfair \| Hora (UTC) = tarde & Quota de mercado = menos de 80 % & Tamanho do mercado = menos de 10 mil $ (repr.) | 13 | refinar | 0 | — | 6 | -0.0788 | 0 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Golos até ao sinal = 1 ou 2 & Minuto = 31 a 60 (repr.) | 14 | refinar | 0 | — | 28 | -0.0581 | 0 |
| Forma de golos \| Dia = dia de semana & Faixa de odd = odd 1,50 a 2,00 & Golos em falta para ganhar = até 1 golo (repr.) | 15 | novas | 0 | — | 0 | — | 0 |
| Betfair \| Quota de mercado = menos de 80 % & Tamanho do mercado = 10 mil $ ou mais (repr.) | 16 | refinar | 0 | — | 8 | 0.3206 | 20 |
| Jogo aberto \| Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = 90 ou mais (repr.) | 17 | refinar | 0 | — | 26 | 0.0584 | 8 |
| Forma de golos \| Golos até ao sinal = nenhum & Marcador no momento = empatado & Minuto = 31 a 60 (repr.) | 18 | refinar | 0 | — | 12 | 0.2663 | 0 |
| Betfair \| Faixa de odd = odd 1,50 a 2,00 & Hora (UTC) = tarde & Volume na seleção = 10 mil $ ou mais (repr.) | 19 | refinar | 0 | — | 0 | — | 20 |
| Betfair \| Quota de mercado = menos de 80 % & Seleção = equipa da casa & Tamanho do mercado = 10 mil $ ou mais (repr.) | 20 | refinar | 0 | — | 4 | 0.3230 | 20 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Minuto = 31 a 60 & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 21 | refinar | 0 | — | 42 | -0.0746 | 20 |
| Jogo aberto \| Golos em falta para ganhar = até 1 golo & Minuto = 31 a 60 & Parte do jogo = 2.ª parte (repr.) | 22 | refinar | 0 | — | 32 | 0.0636 | 18 |
| Forma de golos \| Golos até ao sinal = 3 ou mais & Marcador no momento = diferença de 2 ou mais golos & Minuto = 31 a 60 (repr.) | 23 | refinar | 0 | — | 0 | — | 0 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Hora (UTC) = tarde & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 24 | refinar | 0 | — | 33 | -0.1388 | 9 |
| Stake \| Mercado = golos (total asiático) & Momento do aviso = até 45 min depois do início (repr.) | 25 | novas | 0 | — | 9 | 0.3720 | 2 |
| Jogo aberto \| Dia = dia de semana & Golos até ao sinal = 3 ou mais & Minuto = 31 a 60 & Ritmo indicado pelo bot = 90 ou mais (repr.) | 26 | refinar | 0 | — | 0 | — | 20 |
| Jogo aberto \| Dia = dia de semana & Golos em falta para ganhar = até 1 golo & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 27 | refinar | 0 | — | 0 | — | 20 |
| Jogo aberto \| Hora (UTC) = noite & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 28 | refinar | 0 | — | 5 | 0.2642 | 20 |
| Golo a caminho \| Minuto = 31 a 60 & Vantagem indicada pelo bot = menos de 15 pontos acima do habitual (repr.) | 29 | novas | 0 | — | 0 | — | 0 |
| Jogo aberto \| Golos em falta para ganhar = até 1 golo & Parte do jogo = 1.ª parte (repr.) | 30 | novas | 0 | — | 13 | 0.1036 | 20 |
| Stake \| Mercado = golos (total asiático) & Valor da aposta = 10 mil $ ou mais (repr.) | 31 | novas | 0 | — | 11 | 0.1092 | 2 |
| Betfair \| Competição = liga & Faixa de odd = odd 1,50 a 2,00 & Hora (UTC) = tarde (repr.) | 32 | refinar | 0 | — | 0 | — | 20 |
| Stake \| Mercado = outros mercados & Valor da aposta = menos de 10 mil $ (repr.) | 33 | evitar | 0 | — | 21 | -0.3761 | 13 |
| Golo a caminho \| Minuto = até ao 30 & Vantagem indicada pelo bot = menos de 15 pontos acima do habitual (repr.) | 34 | novas | 0 | — | 0 | — | 0 |
| Jogo aberto \| Competição = feminino & Ritmo indicado pelo bot = menos de 90 (repr.) | 35 | evitar | 0 | — | 6 | 0.0698 | 10 |
| Betfair \| Faixa de odd = odd 2,00 a 3,00 & Hora (UTC) = noite & Quota de mercado = menos de 80 % (repr.) | 36 | novas | 0 | — | 0 | — | 20 |
| Stake \| Mercado = outros mercados & Momento do aviso = até 45 min depois do início & Valor da aposta = 10 mil $ ou mais (repr.) | 37 | novas | 0 | — | 0 | — | 20 |
| Jogo aberto \| Competição = feminino & Minuto = até ao 30 (repr.) | 38 | evitar | 0 | — | 0 | — | 20 |
| Jogo aberto \| Golos em falta para ganhar = mais de 2 golos & Ritmo indicado pelo bot = 90 ou mais (repr.) | 39 | evitar | 0 | — | 0 | — | 20 |
| Jogo aberto \| Dia = fim de semana & Golos até ao sinal = 1 ou 2 & Marcador no momento = diferença de 2 ou mais golos & Ritmo indicado pelo bot = menos de 90 (repr.) | 40 | refinar | 0 | — | 2 | 0.4357 | 20 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 41 | refinar | 0 | — | 6 | -0.1980 | 20 |
| Jogo aberto \| Golos até ao sinal = 1 ou 2 & Marcador no momento = diferença de 2 ou mais golos & Minuto = 31 a 60 & Ritmo indicado pelo bot = menos de 90 (repr.) | 42 | refinar | 0 | — | 0 | — | 20 |
| Jogo aberto \| Golos em falta para ganhar = até 1 golo & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = menos de 90 (repr.) | 43 | refinar | 0 | — | 4 | 0.1172 | 20 |
| Jogo aberto \| Confiança indicada pelo bot = 95 % ou mais & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 44 | refinar | 0 | — | 1 | 0.6464 | 20 |
| Jogo aberto \| Dia = fim de semana & Golos até ao sinal = 1 ou 2 & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte (repr.) | 45 | refinar | 0 | — | 1 | 0.4014 | 20 |
| Jogo aberto \| Golos até ao sinal = 1 ou 2 & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = menos de 90 (repr.) | 46 | refinar | 0 | — | 1 | 0.4014 | 20 |
| Jogo aberto \| Marcador no momento = diferença de 2 ou mais golos & Minuto = 61 em diante & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 47 | refinar | 0 | — | 1 | 0.4014 | 20 |
| Jogo aberto \| Golos até ao sinal = 3 ou mais & Marcador no momento = diferença de 1 golo & Parte do jogo = 2.ª parte (repr.) | 48 | novas | 0 | — | 0 | — | 20 |
| Jogo aberto \| Dia = fim de semana & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = menos de 90 (repr.) | 49 | refinar | 0 | — | 1 | 0.5974 | 20 |
| Jogo aberto \| Golos em falta para ganhar = até 1 golo & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 50 | refinar | 0 | — | 1 | 0.6464 | 20 |

## Duplicados extensionais à nascença

- Betfair | Quota de mercado = 80 % ou mais & Tamanho do mercado = 10 mil $ ou mais — same_signals_as — Betfair | Sinal anterior de outro tipo no mesmo jogo = não & Tamanho do mercado = 10 mil $ ou mais
- Betfair | Tamanho do mercado = 10 mil $ ou mais & Volume na seleção = 10 mil $ ou mais — condition_adds_nothing — Betfair | Tamanho do mercado = 10 mil $ ou mais
- Forma de golos | Competição = liga & Golos em falta para ganhar = até 1 golo — condition_adds_nothing — Forma de golos | Competição = liga
- Forma de golos | Dia = dia de semana & Faixa de odd = odd 1,50 a 2,00 & Golos em falta para ganhar = até 1 golo — condition_adds_nothing — Forma de golos
- Forma de golos | Golos até ao sinal = 3 ou mais & Marcador no momento = diferença de 2 ou mais golos & Minuto = 31 a 60 — condition_adds_nothing — Forma de golos | Golos até ao sinal = 3 ou mais
- Forma de golos | Golos até ao sinal = nenhum & Marcador no momento = empatado & Minuto = 31 a 60 — condition_adds_nothing — Forma de golos | Minuto = 31 a 60
- Forma de golos | Golos até ao sinal = nenhum & Minuto = 31 a 60 — condition_adds_nothing — Forma de golos | Minuto = 31 a 60
- Golo a caminho | Minuto = 31 a 60 & Vantagem indicada pelo bot = menos de 15 pontos acima do habitual — condition_adds_nothing — Golo a caminho
- Golo a caminho | Minuto = até ao 30 & Vantagem indicada pelo bot = menos de 15 pontos acima do habitual — condition_adds_nothing — Golo a caminho
- Jogo aberto | Confiança indicada pelo bot = 95 % ou mais & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não — condition_adds_nothing — Jogo aberto | Confiança indicada pelo bot = 95 % ou mais
- Jogo aberto | Dia = dia de semana & Faixa de odd = odd 1,50 a 2,00 & Golos até ao sinal = 1 ou 2 — condition_adds_nothing — Jogo aberto | Faixa de odd = odd 1,50 a 2,00
- Jogo aberto | Dia = dia de semana & Golos até ao sinal = 3 ou mais — condition_adds_nothing — Jogo aberto | Golos até ao sinal = 3 ou mais
- Jogo aberto | Faixa de odd = odd 1,50 a 2,00 & Golos até ao sinal = 1 ou 2 & Minuto = 31 a 60 — condition_adds_nothing — Jogo aberto | Faixa de odd = odd 1,50 a 2,00 & Minuto = 31 a 60
- Jogo aberto | Faixa de odd = odd 1,50 a 2,00 & Golos até ao sinal = 1 ou 2 & Parte do jogo = 2.ª parte — condition_adds_nothing — Jogo aberto | Faixa de odd = odd 1,50 a 2,00 & Parte do jogo = 2.ª parte
- Jogo aberto | Faixa de odd = odd 1,50 a 2,00 & Minuto = 31 a 60 & Sinal anterior de outro tipo no mesmo jogo = não — condition_adds_nothing — Jogo aberto | Faixa de odd = odd 1,50 a 2,00 & Minuto = 31 a 60
- Jogo aberto | Faixa de odd = odd 1,50 a 2,00 & Ritmo indicado pelo bot = 90 ou mais — same_signals_as — Jogo aberto | Minuto = 31 a 60 & Ritmo indicado pelo bot = 90 ou mais
- Jogo aberto | Golos até ao sinal = 3 ou mais & Marcador no momento = diferença de 1 golo & Parte do jogo = 2.ª parte — condition_adds_nothing — Jogo aberto | Marcador no momento = diferença de 1 golo & Parte do jogo = 2.ª parte
- Jogo aberto | Golos em falta para ganhar = até 1 golo & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = menos de 90 — condition_adds_nothing — Jogo aberto | Golos em falta para ganhar = até 1 golo & Marcador no momento = diferença de 2 ou mais golos & Ritmo indicado pelo bot = menos de 90
- Jogo aberto | Hora (UTC) = noite & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não — condition_adds_nothing — Jogo aberto | Hora (UTC) = noite & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte
- Jogo aberto | Marcador no momento = diferença de 2 ou mais golos & Minuto = 61 em diante & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não — condition_adds_nothing — Jogo aberto | Marcador no momento = diferença de 2 ou mais golos & Minuto = 61 em diante & Sinal anterior de outro tipo no mesmo jogo = não
- Jogo aberto | Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = 90 ou mais — same_signals_as — Jogo aberto | Confiança indicada pelo bot = 95 % ou mais
- Stake | Mercado = golos (total asiático) & Momento do aviso = até 45 min depois do início — same_signals_as — Stake | Faixa de odd = odd 1,50 a 2,00 & Número de apostas = uma
- Stake | Mercado = golos (total asiático) & Número de apostas = várias — condition_adds_nothing — Stake
- Stake | Mercado = golos (total asiático) & Valor da aposta = 10 mil $ ou mais — same_signals_as — Stake | Faixa de odd = odd 1,50 a 2,00 & Número de apostas = uma

## Ameaças à validade e limitações

- A hora de publicação é a hora do commit (committer date) na história de primeiro-pai; um commit com data falsificada
  não seria detetado por este método (as horas têm de ser não decrescentes).
- A hora de publicação do protocolo vem de um registo do servidor GitHub indicado ao avaliador; a auditoria independente confirma-a.
- Os controlos são hipóteses do gerador com tamanho e apoio comparáveis; não esgotam o que um analista humano escolheria.
- Hipóteses diferentes partilham jogos: por isso a unidade é o jogo (um voto por jogo e por lado) e o bootstrap é por dia.
- A janela pré-protocolo é pós-registo, mas foi observada antes de o protocolo existir: só descrição.
- O resultado não altera estados, certificação, avisos Telegram ou o registo de hipóteses.
