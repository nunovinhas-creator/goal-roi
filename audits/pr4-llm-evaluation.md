# PR 4 — Avaliação incremental das hipóteses LLM (protocolo `llm_incremental_v3`)

Gerado por `scripts/audit_pr4_llm.py` a partir de dados de um commit fixo; reprodutível byte a byte.

## Veredicto

**exploratória — antes do instante confirmatório (sem conclusão)**

Fase: **exploratória**. Horizonte da janela H = 2026-12-16T07:16:49+00:00; instante confirmatório C = 2026-12-19T07:16:49+00:00. Só a avaliação com relógio C é confirmatória
(uma única vez; qualquer execução posterior dá o mesmo resultado). Antes de C, os números não sustentam conclusões.

Mínimos do protocolo não cumpridos:

- groups with >= 10 games < 10
- llm games < 200
- control games < 200
- days with llm games < 14
- paired days < 14
- one day holds more than 20 % of llm games

## Pergunta

As hipóteses do modelo de linguagem escolhem jogos com mais lucro por jogo (odd anunciada −2 %), depois do seu registo,
do que hipóteses comparáveis do gerador que já existiam, no mesmo intervalo? Não é uma medida de vantagem contra o
mercado e não certifica nenhuma tática. Taxas de acerto, incluindo as superiores a 50 %, são só descrição.

## Entradas

- Protocolo: `llm_incremental_v3`, sha256 `e3190be054c982c901dec00099ef2a433f43fc5103d82446a5b0846f361989a4`, início P = 2026-11-16T07:16:49+00:00
- Relógio efetivo da avaliação: 2026-11-16T07:28:38+00:00 (min(as_of pedido, C))
- P = max(hora do commit do protocolo, primeira publicação verificável no GitHub); ver `protocol_published_utc` e `protocol_publication_evidence` nas entradas.
- llm_hypotheses_registered_after_window: `0`
- protocol_commit: `0766a13816a164c88b68488ee98091287438a8be`
- protocol_publication_evidence: `https://github.com/nunovinhas-creator/goal-roi/actions/runs/43822686064`
- protocol_published_utc: `2026-11-16T07:16:49Z`
- python: `3.11`
- ref: `d91d839c410992c00551be74c78772e72648b466`
- ref_commit_time_utc: `2026-11-16T07:28:38+00:00`
- sha256 data/.gitkeep: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- sha256 data/details_hl.json: `d22a36f36f67fd3e8a363581056b37692e806e5ab041a3cb584be9928361db98`
- sha256 data/hypotheses.json: `b0b341e67b9605348b44cdbab2520171cc9a52aef8cccc0e67142617c6e9553a`
- sha256 data/llm_notes.json: `f49731305f4c52b83318607e5c6f849e03aa4f4161aef833bd1713883d4cdb13`
- sha256 data/llm_state.json: `b69ceef0caf024b2fd2875718ac7a2519dd233cd6a49e5a981ac27ff1610749d`
- sha256 data/messages.jsonl: `1eb358c6c78192120f80c3b6830ba3b8b63ae7cac89780e552f58a30ca419ef8`
- sha256 data/notify_state.json: `072bf49238ea87508c8c8e4e88f8f805ceed1eeb70fad0202bcb7dfc8732c3e3`
- sha256 data/report.json: `ca1b2a491e741483191bc671b90aa0c16013b716e86055a80b9fc283ee3d07c9`
- sha256 data/results_bsd.json: `1a0714a9ddfb1b02f3030f34dcdf7a7392644e8512b5a2191d48276d5738dbff`
- sha256 data/results_hl.json: `c1685b78696344d9e73a827bac1ce84dc3d2ca38f0ed10d055701217ce51b37e`
- sha256 data/state.json: `307ddef18b8ce2663437ac3a40da7f0f9b3804df01b1eec383bfdac8b465e058`
- sha256 data/team_aliases.json: `05cd986e98ba1b5eadc5dbe88c24456c9e2406cf1362745cc23c0a06734074f5`

## População

| Medida | Valor |
|---|---|
| Sinais elegíveis na vista do instante | 576 |
| Hipóteses LLM no registo | 56 |
| Avaliadas (nascimento provado) | 56 |
| Excluídas: sem nascimento provado | 0 |
| Grupos de equivalência (τ = 0,85) | 56 |
| Grupos com τ = 0,80 / 0,90 | 56 / 56 |
| Representantes na análise primária | 34 |
| Sem controlos suficientes (< 5) | 17 |
| Duplicados extensionais à nascença | 27 |
| Por modo (novas / refinar / evitar) | 16 / 35 / 5 |

## Comparação primária

D = média ponderada, sobre os dias com jogos dos dois lados, da diferença DENTRO do dia entre o lucro médio por jogo das
hipóteses LLM e o dos controlos do gerador (pesos 1/K); jogos prospetivos desde max(nascimento provado, P) e decididos até H.
Teste: inversão aleatória de sinais por dia, 10000 réplicas, semente 20261115; H1: D > 0; α = 0.05.

| Jogos LLM | Jogos controlo | Dias LLM | Dias emparelhados | Maior fração num dia | D | Diferença por jogo (descritiva) | p |
|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | — | — | — | — |

## Janela pós-registo e pré-protocolo (só descrição)

Mesmos representantes e controlos, jogos prospetivos desde o nascimento provado e decididos antes de P. Não é um teste:
foi observada antes de o protocolo existir e não tem p-valor nem intervalo.

| Jogos LLM | Dias | Lucro médio por jogo LLM | Jogos controlo | Lucro médio por jogo controlo |
|---|---|---|---|---|
| 108 | 5 | -0.0606 | 150 | 0.0334 |

## Análises adicionais (exploratórias, Holm m = 7; p ajustado só em C)

| Análise | Jogos em teste | Jogos de comparação | D | p | p Holm |
|---|---|---|---|---|---|
| S1_family_baseline | 0 | 0 | — | — | — |
| S2_mode_novas | 0 | 0 | — | — | — |
| S3_mode_refinar | 0 | 0 | — | — | — |
| S4_refinar_vs_parents | 0 | 0 | — | — | — |
| S5_evitar_vs_baseline | 0 | 0 | — | — | — |
| S6_tau_0.80 | 0 | 0 | — | — | — |
| S7_tau_0.90 | 0 | 0 | — | — | — |

Nenhuma análise adicional sustenta sozinha um veredicto.

## Hipóteses (descrição)

| Hipótese | Grupo | Modo | Jogos no protocolo | Lucro/jogo | Jogos pré-protocolo | Lucro/jogo | Controlos |
|---|---|---|---|---|---|---|---|
| Betfair \| Tamanho do mercado = 10 mil $ ou mais & Volume na seleção = 10 mil $ ou mais (repr.) | 0 | — | 0 | — | 11 | -0.2997 | 20 |
| Forma de golos \| Competição = liga & Golos em falta para ganhar = até 1 golo (repr.) | 1 | — | 0 | — | 23 | -0.0994 | 0 |
| Stake \| Mercado = golos (total asiático) & Número de apostas = várias (repr.) | 2 | — | 0 | — | 4 | -0.6325 | 0 |
| Betfair \| Quota de mercado = 80 % ou mais & Seleção = equipa da casa & Tamanho do mercado = menos de 10 mil $ (repr.) | 3 | refinar | 0 | — | 6 | -0.1899 | 0 |
| Jogo aberto \| Confiança indicada pelo bot = 95 % ou mais & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 4 | refinar | 0 | — | 29 | 0.1730 | 0 |
| Betfair \| Quota de mercado = 80 % ou mais & Tamanho do mercado = 10 mil $ ou mais (repr.) | 5 | novas | 0 | — | 4 | -1.0000 | 20 |
| Forma de golos \| Golos até ao sinal = nenhum & Minuto = 31 a 60 (repr.) | 6 | refinar | 0 | — | 12 | 0.2663 | 0 |
| Jogo aberto \| Dia = dia de semana & Golos até ao sinal = 3 ou mais (repr.) | 7 | refinar | 0 | — | 7 | 0.3468 | 8 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Ritmo indicado pelo bot = 90 ou mais (repr.) | 8 | refinar | 0 | — | 30 | -0.0285 | 11 |
| Betfair \| Competição = liga & Tamanho do mercado = 10 mil $ ou mais (repr.) | 9 | novas | 0 | — | 4 | -1.0000 | 3 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Golos até ao sinal = 1 ou 2 & Parte do jogo = 2.ª parte (repr.) | 10 | refinar | 0 | — | 27 | -0.1136 | 0 |
| Betfair \| Competição = jovens & Faixa de odd = odd 2,00 a 3,00 (repr.) | 11 | evitar | 0 | — | 0 | — | 20 |
| Jogo aberto \| Dia = dia de semana & Faixa de odd = odd 1,50 a 2,00 & Golos até ao sinal = 1 ou 2 (repr.) | 12 | refinar | 0 | — | 3 | -1.0000 | 0 |
| Betfair \| Hora (UTC) = tarde & Quota de mercado = menos de 80 % & Tamanho do mercado = menos de 10 mil $ (repr.) | 13 | refinar | 0 | — | 6 | -0.0788 | 0 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Golos até ao sinal = 1 ou 2 & Minuto = 31 a 60 (repr.) | 14 | refinar | 0 | — | 29 | -0.0906 | 0 |
| Forma de golos \| Dia = dia de semana & Faixa de odd = odd 1,50 a 2,00 & Golos em falta para ganhar = até 1 golo (repr.) | 15 | novas | 0 | — | 0 | — | 0 |
| Betfair \| Quota de mercado = menos de 80 % & Tamanho do mercado = 10 mil $ ou mais (repr.) | 16 | refinar | 0 | — | 10 | 0.0564 | 20 |
| Jogo aberto \| Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = 90 ou mais (repr.) | 17 | refinar | 0 | — | 27 | 0.1034 | 8 |
| Forma de golos \| Golos até ao sinal = nenhum & Marcador no momento = empatado & Minuto = 31 a 60 (repr.) | 18 | refinar | 0 | — | 12 | 0.2663 | 0 |
| Betfair \| Faixa de odd = odd 1,50 a 2,00 & Hora (UTC) = tarde & Volume na seleção = 10 mil $ ou mais (repr.) | 19 | refinar | 0 | — | 0 | — | 20 |
| Betfair \| Quota de mercado = menos de 80 % & Seleção = equipa da casa & Tamanho do mercado = 10 mil $ ou mais (repr.) | 20 | refinar | 0 | — | 5 | 0.0584 | 20 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Minuto = 31 a 60 & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 21 | refinar | 0 | — | 42 | -0.0746 | 20 |
| Jogo aberto \| Golos em falta para ganhar = até 1 golo & Minuto = 31 a 60 & Parte do jogo = 2.ª parte (repr.) | 22 | refinar | 0 | — | 34 | 0.0679 | 18 |
| Forma de golos \| Golos até ao sinal = 3 ou mais & Marcador no momento = diferença de 2 ou mais golos & Minuto = 31 a 60 (repr.) | 23 | refinar | 0 | — | 0 | — | 0 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Hora (UTC) = tarde & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 24 | refinar | 0 | — | 33 | -0.1388 | 9 |
| Stake \| Mercado = golos (total asiático) & Momento do aviso = até 45 min depois do início (repr.) | 25 | novas | 0 | — | 9 | 0.3720 | 2 |
| Jogo aberto \| Dia = dia de semana & Golos até ao sinal = 3 ou mais & Minuto = 31 a 60 & Ritmo indicado pelo bot = 90 ou mais (repr.) | 26 | refinar | 0 | — | 0 | — | 20 |
| Jogo aberto \| Dia = dia de semana & Golos em falta para ganhar = até 1 golo & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 27 | refinar | 0 | — | 1 | 1.2736 | 20 |
| Jogo aberto \| Hora (UTC) = noite & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 28 | refinar | 0 | — | 5 | 0.2642 | 20 |
| Golo a caminho \| Minuto = 31 a 60 & Vantagem indicada pelo bot = menos de 15 pontos acima do habitual (repr.) | 29 | novas | 0 | — | 0 | — | 0 |
| Jogo aberto \| Golos em falta para ganhar = até 1 golo & Parte do jogo = 1.ª parte (repr.) | 30 | novas | 0 | — | 13 | 0.1036 | 20 |
| Stake \| Mercado = golos (total asiático) & Valor da aposta = 10 mil $ ou mais (repr.) | 31 | novas | 0 | — | 12 | 0.1352 | 2 |
| Betfair \| Competição = liga & Faixa de odd = odd 1,50 a 2,00 & Hora (UTC) = tarde (repr.) | 32 | refinar | 0 | — | 0 | — | 20 |
| Stake \| Mercado = outros mercados & Valor da aposta = menos de 10 mil $ (repr.) | 33 | evitar | 0 | — | 22 | -0.3220 | 13 |
| Golo a caminho \| Minuto = até ao 30 & Vantagem indicada pelo bot = menos de 15 pontos acima do habitual (repr.) | 34 | novas | 0 | — | 0 | — | 0 |
| Jogo aberto \| Competição = feminino & Ritmo indicado pelo bot = menos de 90 (repr.) | 35 | evitar | 0 | — | 6 | 0.0698 | 10 |
| Betfair \| Faixa de odd = odd 2,00 a 3,00 & Hora (UTC) = noite & Quota de mercado = menos de 80 % (repr.) | 36 | novas | 0 | — | 1 | -1.0000 | 20 |
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
| Jogo aberto \| Golos até ao sinal = 3 ou mais & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = menos de 90 (repr.) | 51 | refinar | 0 | — | 0 | — | 20 |
| Polymarket \| Competição = seleções & Faixa de odd = odd 2,00 a 3,00 & Quota de mercado = menos de 80 % & Valor da aposta = menos de 10 mil $ (repr.) | 52 | refinar | 0 | — | 0 | — | 20 |
| Polymarket \| Dia = fim de semana & Mercado = outro mercado & Valor da aposta = 10 mil $ ou mais (repr.) | 53 | novas | 0 | — | 0 | — | 20 |
| Stake \| Competição = seleções & Faixa de odd = odd 1,50 a 2,00 & Liga = UEFA Nations League (repr.) | 54 | novas | 0 | — | 0 | — | 20 |
| Jogo aberto \| Golos em falta para ganhar = até 1 golo & Hora (UTC) = noite & Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = menos de 90 (repr.) | 55 | refinar | 0 | — | 0 | — | 20 |

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
- Jogo aberto | Golos em falta para ganhar = até 1 golo & Hora (UTC) = noite & Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = menos de 90 — condition_adds_nothing — Jogo aberto | Golos em falta para ganhar = até 1 golo & Hora (UTC) = noite & Ritmo indicado pelo bot = menos de 90
- Jogo aberto | Golos em falta para ganhar = até 1 golo & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = menos de 90 — condition_adds_nothing — Jogo aberto | Golos em falta para ganhar = até 1 golo & Marcador no momento = diferença de 2 ou mais golos & Ritmo indicado pelo bot = menos de 90
- Jogo aberto | Hora (UTC) = noite & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não — condition_adds_nothing — Jogo aberto | Hora (UTC) = noite & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte
- Jogo aberto | Marcador no momento = diferença de 2 ou mais golos & Minuto = 61 em diante & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não — condition_adds_nothing — Jogo aberto | Marcador no momento = diferença de 2 ou mais golos & Minuto = 61 em diante & Sinal anterior de outro tipo no mesmo jogo = não
- Jogo aberto | Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = 90 ou mais — same_signals_as — Jogo aberto | Confiança indicada pelo bot = 95 % ou mais
- Polymarket | Dia = fim de semana & Mercado = outro mercado & Valor da aposta = 10 mil $ ou mais — condition_adds_nothing — Polymarket | Mercado = outro mercado & Valor da aposta = 10 mil $ ou mais
- Stake | Competição = seleções & Faixa de odd = odd 1,50 a 2,00 & Liga = UEFA Nations League — condition_adds_nothing — Stake | Faixa de odd = odd 1,50 a 2,00 & Liga = UEFA Nations League
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
