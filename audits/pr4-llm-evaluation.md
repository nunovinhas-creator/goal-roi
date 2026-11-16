# PR 4 — Avaliação incremental das hipóteses LLM (protocolo `llm_incremental_v4`)

Gerado por `scripts/audit_pr4_llm.py` a partir de dados de um commit fixo; reprodutível byte a byte.

## Veredicto

**em curso — sem conclusão (e-processo abaixo de 20)**

Fase: **em curso**. Horizonte H = 2027-11-16T08:05:26+00:00; fim do v4 C = 2027-11-19T08:05:26+00:00. O e-processo é válido a qualquer momento (Ville): olhar repetidamente
não aumenta o erro de tipo I acima de α = 0,05 para H0 (§6-A do protocolo). Só dias fechados entram.

**Suposição A-sel:** A garantia (§6-A) vale para H0 tal como definida; a leitura "por aposta" exige A-sel: a entrada de um jogo num dia não depende dos resultados de outros jogos desse dia (plausível, não verificável pelos dados).

Mínimos do protocolo ainda não cumpridos (sobre os dias fechados):

- groups with >= 10 games < 10
- llm games < 200
- control games < 200
- days with llm games < 14
- paired days < 14
- one day holds more than 20 % of llm games

## Pergunta

As hipóteses do modelo de linguagem escolhem jogos com mais lucro por jogo (odd anunciada −2 %), depois do seu registo,
do que hipóteses comparáveis do gerador que já existiam, no mesmo intervalo? Não é uma medida de vantagem contra o
mercado e não certifica nenhuma tática. Taxas de acerto, incluindo as superiores a 50 %, não são usadas.

## Entradas

- Protocolo: `llm_incremental_v4`, sha256 `055d7a62a2cb1a7edd6aae1880aa6be56d8a84f9090d85430b1ef932f21db167`, início P = 2026-11-16T08:05:26+00:00
- Relógio efetivo da avaliação: 2026-11-16T08:12:21+00:00 (min(as_of pedido, C))
- P = max(hora do commit do protocolo, primeira publicação verificável no GitHub); ver `protocol_published_utc` e `protocol_publication_evidence` nas entradas.
- llm_hypotheses_registered_after_window: `0`
- protocol_commit: `d48e3b7638c56b435a6e7da9245a82cd6781c66d`
- protocol_publication_evidence: `https://github.com/nunovinhas-creator/goal-roi/actions/runs/43828482856`
- protocol_published_utc: `2026-11-16T08:05:26Z`
- python: `3.11`
- ref: `ab60140c914d386f3ae6e51d0ece0f6252aa7e35`
- ref_commit_time_utc: `2026-11-16T08:12:21+00:00`
- requested_ref: `ab60140c914d386f3ae6e51d0ece0f6252aa7e35`
- sha256 data/.gitkeep: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- sha256 data/details_hl.json: `33f0469322347e06621cf27fbbd395f1a0191c2246d5606d6c7819d56be2c2ac`
- sha256 data/hypotheses.json: `b0b341e67b9605348b44cdbab2520171cc9a52aef8cccc0e67142617c6e9553a`
- sha256 data/llm_notes.json: `f49731305f4c52b83318607e5c6f849e03aa4f4161aef833bd1713883d4cdb13`
- sha256 data/llm_state.json: `25715e94e9e653d1b9777786ff785a588a19154313550cc7b8c79766bb7f2762`
- sha256 data/messages.jsonl: `1eb358c6c78192120f80c3b6830ba3b8b63ae7cac89780e552f58a30ca419ef8`
- sha256 data/notify_state.json: `343589e207d8e5e9fad2b931bdc42b97b7d0213e4c44b468b71b7acfd91a0bbf`
- sha256 data/report.json: `3b52e7eb75b4219def421b5e285b6be6b946ff6a587f45c36965be67299bd3a9`
- sha256 data/results_bsd.json: `5a790f98f0feee05a99d33cfac37834f5fe85725953d09cd4d6c1b3d1073dc4c`
- sha256 data/results_hl.json: `c1685b78696344d9e73a827bac1ce84dc3d2ca38f0ed10d055701217ce51b37e`
- sha256 data/state.json: `cb2855c3abde66836c4f820cef021de943cd2801894bf6d7d145b992f6c88529`
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

## Comparação primária: e-processo

Por dia fechado com jogos dos dois lados: X = (lucro médio por jogo LLM − lucro médio por jogo dos controlos) / (1 + ganho
máximo médio dos controlos), sempre >= −1. Riqueza K = Π (1 + λ X), λ previsível em [0, 1/2]. Conclusão quando K >= 20.
Jogos sem resultado em 3 dias: −1 no lado LLM, ganho máximo no lado dos controlos (desfavorável à hipótese em teste).

| Jogos LLM | Jogos controlo | Dias LLM | Dias emparelhados | Maior fração num dia | K final | K máximo | p_e = 1/K máx. | Diferença diária média (descritiva) | Bootstrap-t (diagnóstico) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | — | 1.0000 | 1.0000 | 1.0000 | — | — |

O p do bootstrap-t é só diagnóstico: nunca muda o veredicto.

## Janela pós-registo e pré-protocolo (só descrição)

Mesmos representantes e controlos, apostas com resultado observado, decididas antes de P. Não é um teste.

| Apostas LLM | Lucro médio LLM | Apostas de controlo | Lucro médio controlo |
|---|---|---|---|
| 310 | -0.0042 | 5270 | 0.0345 |

## Análises adicionais (exploratórias; p_e = 1/K máx. com Holm, m = 7)

| Análise | Jogos em teste | Jogos de comparação | Dias emparelhados | K máximo | p_e | p Holm |
|---|---|---|---|---|---|---|
| S1_family_baseline | 0 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| S2_mode_novas | 0 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| S3_mode_refinar | 0 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| S4_refinar_vs_parents | 0 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| S5_evitar_vs_baseline | 0 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| S6_tau_0.80 | 0 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |
| S7_tau_0.90 | 0 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 |

Nenhuma análise adicional sustenta sozinha um veredicto.

## Hipóteses (descrição)

| Hipótese | Grupo | Modo | Jogos no protocolo | Com resultado | Lucro/jogo observado | Apostas pré-protocolo | Lucro/aposta | Controlos |
|---|---|---|---|---|---|---|---|---|
| Betfair \| Tamanho do mercado = 10 mil $ ou mais & Volume na seleção = 10 mil $ ou mais (repr.) | 0 | — | 0 | 0 | — | 11 | -0.2997 | 20 |
| Forma de golos \| Competição = liga & Golos em falta para ganhar = até 1 golo (repr.) | 1 | — | 0 | 0 | — | 23 | -0.0994 | 0 |
| Stake \| Mercado = golos (total asiático) & Número de apostas = várias (repr.) | 2 | — | 0 | 0 | — | 5 | -0.5060 | 0 |
| Betfair \| Quota de mercado = 80 % ou mais & Seleção = equipa da casa & Tamanho do mercado = menos de 10 mil $ (repr.) | 3 | refinar | 0 | 0 | — | 6 | -0.1899 | 0 |
| Jogo aberto \| Confiança indicada pelo bot = 95 % ou mais & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 4 | refinar | 0 | 0 | — | 45 | 0.1115 | 0 |
| Betfair \| Quota de mercado = 80 % ou mais & Tamanho do mercado = 10 mil $ ou mais (repr.) | 5 | novas | 0 | 0 | — | 4 | -1.0000 | 20 |
| Forma de golos \| Golos até ao sinal = nenhum & Minuto = 31 a 60 (repr.) | 6 | refinar | 0 | 0 | — | 12 | 0.2663 | 0 |
| Jogo aberto \| Dia = dia de semana & Golos até ao sinal = 3 ou mais (repr.) | 7 | refinar | 0 | 0 | — | 12 | 0.2023 | 8 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Ritmo indicado pelo bot = 90 ou mais (repr.) | 8 | refinar | 0 | 0 | — | 43 | -0.0199 | 11 |
| Betfair \| Competição = liga & Tamanho do mercado = 10 mil $ ou mais (repr.) | 9 | novas | 0 | 0 | — | 4 | -1.0000 | 3 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Golos até ao sinal = 1 ou 2 & Parte do jogo = 2.ª parte (repr.) | 10 | refinar | 0 | 0 | — | 31 | -0.0990 | 0 |
| Betfair \| Competição = jovens & Faixa de odd = odd 2,00 a 3,00 (repr.) | 11 | evitar | 0 | 0 | — | 0 | — | 20 |
| Jogo aberto \| Dia = dia de semana & Faixa de odd = odd 1,50 a 2,00 & Golos até ao sinal = 1 ou 2 (repr.) | 12 | refinar | 0 | 0 | — | 4 | -0.7500 | 0 |
| Betfair \| Hora (UTC) = tarde & Quota de mercado = menos de 80 % & Tamanho do mercado = menos de 10 mil $ (repr.) | 13 | refinar | 0 | 0 | — | 6 | -0.0788 | 0 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Golos até ao sinal = 1 ou 2 & Minuto = 31 a 60 (repr.) | 14 | refinar | 0 | 0 | — | 32 | -0.0821 | 0 |
| Forma de golos \| Dia = dia de semana & Faixa de odd = odd 1,50 a 2,00 & Golos em falta para ganhar = até 1 golo (repr.) | 15 | novas | 0 | 0 | — | 0 | — | 0 |
| Betfair \| Quota de mercado = menos de 80 % & Tamanho do mercado = 10 mil $ ou mais (repr.) | 16 | refinar | 0 | 0 | — | 10 | 0.0564 | 20 |
| Jogo aberto \| Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = 90 ou mais (repr.) | 17 | refinar | 0 | 0 | — | 38 | 0.0735 | 8 |
| Forma de golos \| Golos até ao sinal = nenhum & Marcador no momento = empatado & Minuto = 31 a 60 (repr.) | 18 | refinar | 0 | 0 | — | 12 | 0.2663 | 0 |
| Betfair \| Faixa de odd = odd 1,50 a 2,00 & Hora (UTC) = tarde & Volume na seleção = 10 mil $ ou mais (repr.) | 19 | refinar | 0 | 0 | — | 0 | — | 20 |
| Betfair \| Quota de mercado = menos de 80 % & Seleção = equipa da casa & Tamanho do mercado = 10 mil $ ou mais (repr.) | 20 | refinar | 0 | 0 | — | 5 | 0.0584 | 20 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Minuto = 31 a 60 & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 21 | refinar | 0 | 0 | — | 54 | -0.0580 | 20 |
| Jogo aberto \| Golos em falta para ganhar = até 1 golo & Minuto = 31 a 60 & Parte do jogo = 2.ª parte (repr.) | 22 | refinar | 0 | 0 | — | 48 | 0.0481 | 18 |
| Forma de golos \| Golos até ao sinal = 3 ou mais & Marcador no momento = diferença de 2 ou mais golos & Minuto = 31 a 60 (repr.) | 23 | refinar | 0 | 0 | — | 0 | — | 0 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Hora (UTC) = tarde & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 24 | refinar | 0 | 0 | — | 41 | -0.1117 | 9 |
| Stake \| Mercado = golos (total asiático) & Momento do aviso = até 45 min depois do início (repr.) | 25 | novas | 0 | 0 | — | 9 | 0.3720 | 2 |
| Jogo aberto \| Dia = dia de semana & Golos até ao sinal = 3 ou mais & Minuto = 31 a 60 & Ritmo indicado pelo bot = 90 ou mais (repr.) | 26 | refinar | 0 | 0 | — | 0 | — | 20 |
| Jogo aberto \| Dia = dia de semana & Golos em falta para ganhar = até 1 golo & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 27 | refinar | 0 | 0 | — | 1 | 1.2736 | 20 |
| Jogo aberto \| Hora (UTC) = noite & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 28 | refinar | 0 | 0 | — | 5 | 0.2642 | 20 |
| Golo a caminho \| Minuto = 31 a 60 & Vantagem indicada pelo bot = menos de 15 pontos acima do habitual (repr.) | 29 | novas | 0 | 0 | — | 0 | — | 0 |
| Jogo aberto \| Golos em falta para ganhar = até 1 golo & Parte do jogo = 1.ª parte (repr.) | 30 | novas | 0 | 0 | — | 15 | 0.0898 | 20 |
| Stake \| Mercado = golos (total asiático) & Valor da aposta = 10 mil $ ou mais (repr.) | 31 | novas | 0 | 0 | — | 12 | 0.0797 | 2 |
| Betfair \| Competição = liga & Faixa de odd = odd 1,50 a 2,00 & Hora (UTC) = tarde (repr.) | 32 | refinar | 0 | 0 | — | 0 | — | 20 |
| Stake \| Mercado = outros mercados & Valor da aposta = menos de 10 mil $ (repr.) | 33 | evitar | 0 | 0 | — | 19 | -0.1623 | 13 |
| Golo a caminho \| Minuto = até ao 30 & Vantagem indicada pelo bot = menos de 15 pontos acima do habitual (repr.) | 34 | novas | 0 | 0 | — | 0 | — | 0 |
| Jogo aberto \| Competição = feminino & Ritmo indicado pelo bot = menos de 90 (repr.) | 35 | evitar | 0 | 0 | — | 7 | 0.0599 | 10 |
| Betfair \| Faixa de odd = odd 2,00 a 3,00 & Hora (UTC) = noite & Quota de mercado = menos de 80 % (repr.) | 36 | novas | 0 | 0 | — | 1 | -1.0000 | 20 |
| Stake \| Mercado = outros mercados & Momento do aviso = até 45 min depois do início & Valor da aposta = 10 mil $ ou mais (repr.) | 37 | novas | 0 | 0 | — | 0 | — | 20 |
| Jogo aberto \| Competição = feminino & Minuto = até ao 30 (repr.) | 38 | evitar | 0 | 0 | — | 0 | — | 20 |
| Jogo aberto \| Golos em falta para ganhar = mais de 2 golos & Ritmo indicado pelo bot = 90 ou mais (repr.) | 39 | evitar | 0 | 0 | — | 0 | — | 20 |
| Jogo aberto \| Dia = fim de semana & Golos até ao sinal = 1 ou 2 & Marcador no momento = diferença de 2 ou mais golos & Ritmo indicado pelo bot = menos de 90 (repr.) | 40 | refinar | 0 | 0 | — | 2 | 0.4357 | 20 |
| Jogo aberto \| Faixa de odd = odd 1,50 a 2,00 & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 41 | refinar | 0 | 0 | — | 8 | -0.1485 | 20 |
| Jogo aberto \| Golos até ao sinal = 1 ou 2 & Marcador no momento = diferença de 2 ou mais golos & Minuto = 31 a 60 & Ritmo indicado pelo bot = menos de 90 (repr.) | 42 | refinar | 0 | 0 | — | 0 | — | 20 |
| Jogo aberto \| Golos em falta para ganhar = até 1 golo & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = menos de 90 (repr.) | 43 | refinar | 0 | 0 | — | 5 | 0.0938 | 20 |
| Jogo aberto \| Confiança indicada pelo bot = 95 % ou mais & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 44 | refinar | 0 | 0 | — | 1 | 0.6464 | 20 |
| Jogo aberto \| Dia = fim de semana & Golos até ao sinal = 1 ou 2 & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte (repr.) | 45 | refinar | 0 | 0 | — | 1 | 0.4014 | 20 |
| Jogo aberto \| Golos até ao sinal = 1 ou 2 & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = menos de 90 (repr.) | 46 | refinar | 0 | 0 | — | 1 | 0.4014 | 20 |
| Jogo aberto \| Marcador no momento = diferença de 2 ou mais golos & Minuto = 61 em diante & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 47 | refinar | 0 | 0 | — | 1 | 0.4014 | 20 |
| Jogo aberto \| Golos até ao sinal = 3 ou mais & Marcador no momento = diferença de 1 golo & Parte do jogo = 2.ª parte (repr.) | 48 | novas | 0 | 0 | — | 1 | 0.0000 | 20 |
| Jogo aberto \| Dia = fim de semana & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = menos de 90 (repr.) | 49 | refinar | 0 | 0 | — | 1 | 0.5974 | 20 |
| Jogo aberto \| Golos em falta para ganhar = até 1 golo & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Sinal anterior de outro tipo no mesmo jogo = não (repr.) | 50 | refinar | 0 | 0 | — | 1 | 0.6464 | 20 |
| Jogo aberto \| Golos até ao sinal = 3 ou mais & Marcador no momento = diferença de 2 ou mais golos & Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = menos de 90 (repr.) | 51 | refinar | 0 | 0 | — | 0 | — | 20 |
| Polymarket \| Competição = seleções & Faixa de odd = odd 2,00 a 3,00 & Quota de mercado = menos de 80 % & Valor da aposta = menos de 10 mil $ (repr.) | 52 | refinar | 0 | 0 | — | 0 | — | 20 |
| Polymarket \| Dia = fim de semana & Mercado = outro mercado & Valor da aposta = 10 mil $ ou mais (repr.) | 53 | novas | 0 | 0 | — | 0 | — | 20 |
| Stake \| Competição = seleções & Faixa de odd = odd 1,50 a 2,00 & Liga = UEFA Nations League (repr.) | 54 | novas | 0 | 0 | — | 0 | — | 20 |
| Jogo aberto \| Golos em falta para ganhar = até 1 golo & Hora (UTC) = noite & Parte do jogo = 2.ª parte & Ritmo indicado pelo bot = menos de 90 (repr.) | 55 | refinar | 0 | 0 | — | 0 | — | 20 |

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

- A garantia do e-processo é para H0 (§6-A): esperança condicional da diferença diária normalizada <= 0. A leitura
  "cada aposta da LLM não é melhor" exige a suposição A-sel (composição de cada dia sem dependência dos resultados desse dia).
- Poder baixo: em simulação, com efeito de +20 % na probabilidade de ganhar, ≈ 25 % a 90 dias e ≈ 84 % a 365 dias.
- A hora de publicação das observações é a hora do commit (committer date) na história de primeiro-pai.
- A hora de publicação do protocolo vem de um registo do servidor GitHub indicado ao avaliador; a auditoria independente confirma-a.
- Os controlos são hipóteses do gerador com tamanho e apoio comparáveis; não esgotam o que um analista humano escolheria.
- A janela pré-protocolo é pós-registo, mas foi observada antes de o protocolo existir: só descrição.
- O resultado não altera estados, certificação, avisos Telegram ou o registo de hipóteses.
