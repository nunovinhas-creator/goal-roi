# P1 — Piloto Polymarket em modo sombra (só leitura)

**Sem apostas, sem ordens, sem credenciais.** Estratégias congeladas, certificação, alertas, workflows e `data/` não
foram alterados. O código novo não está ligado a nenhum workflow.

## Pergunta

É possível observar **prospetivamente**, gratuitamente e sem credenciais, o preço e o custo de execução do contrato
Polymarket que corresponde a um sinal do bot "Over 2,5 pré-jogo"?

## Acesso de rede (23/11/2026)

| Host | Resultado sem credenciais |
|---|---|
| `docs.polymarket.com` | 200 |
| `gamma-api.polymarket.com` | 200 |
| `clob.polymarket.com` | 200 |

O primeiro teste deu 403 **no proxy de saída da sessão**, não na Polymarket. Os três hosts foram acrescentados à lista
de hosts permitidos do ambiente cloud com aprovação explícita do utilizador. O Cloudflare da Polymarket recusa o
User-Agent por omissão do `urllib` (erro 1010), por isso o código envia um User-Agent genérico fixo, sem dados pessoais.

## População

95 mensagens `POLYMARKET / pre / Asian Total / Over 2.5 / match_goals / regulation` → **58 encontros únicos**. Às
17:45 UTC só dois tinham início futuro. Cobertura de identidade e contrato nos 58 (`coverage-58.json`, sem preços):

| Estado | Encontros |
|---|---:|
| Contrato O/U 2.5 regulamentar verificado | 35 |
| Identidade não comprovada (nomes diferentes) | 23 |

As 23 falhas são diferenças de nome ("Tottenham" vs "Tottenham Hotspur FC", "Hull" vs "Hull City AFC",
"Bosnia" vs "Bosnia and Herzegovina", "Al-Ittihad KSA" vs "Al-Ittihad Club"). Nunca se aceitam por semelhança: só
igualdade depois de normalizar, o `alias` publicado pela própria Gamma ou uma entrada revista à mão em
`data/team_aliases.json` (não alterado nesta PR).

## Piloto: AD Ceuta FC — UD Almería (LaLiga 2, 23/11/2026 19:30 UTC)

| Verificação | Resultado |
|---|---|
| Evento | `es2-ceu-alm-2026-11-23` (id 1354800, gameId 90117671); casa/fora e hora exatas |
| Evento "More Markets" | `es2-ceu-alm-2026-11-23-more-markets`, `parentEventId` = 1354800, mesmo gameId |
| Contrato | `es2-ceu-alm-2026-11-23-total-2pt5` (market 6943571), único `totals` com linha 2.5 |
| conditionId | `0xcc8f08f954e303712669cdecc6382f7dede99a8f552474cf9786df8e54636b0e` |
| Token "Over" | `26327110167391870994816581715576912324458716406795184584714962315551123010144` |
| Regras | Over = 3 ou mais golos nos **90 minutos + descontos**; cancelamento ou adiamento além de 25/11 → **50-50**; fonte LaLiga, depois Flashscore/Sofascore |
| Disponibilidade | `active`, não `closed`, `acceptingOrders`; CLOB `ao=true` |
| Parâmetros CLOB | tick 0,01; mínimo 5 ações; taxa só taker `0,05 × p × (1−p)` por ação; atraso de taker de 1 s; **livro limpo no início do jogo** |
| `restricted` | `true` — observar não prova que seja permitido negociar a partir de uma dada jurisdição |

### Livro público (token Over)

| Observação | Receção (UTC) | Carimbo da fonte | Ask | Bid | Odds líquidas, 100 USDC |
|---|---|---|---:|---:|---:|
| `observation-1.json` | 17:57:34.499 | 17:55:59.328 | 0,53 × 6 683 | 0,52 | 1,8435 |
| `observation-2.json` | 17:57:56.348 | 17:55:59.328 | 0,53 × 6 683 | 0,52 | 1,8435 |
| `observation-3.json` | 17:58:18.095 | 17:55:59.328 | 0,53 × 6 683 | 0,52 | 1,8435 |

Os 500 USDC enchem no primeiro nível (VWAP 0,53; odd bruta 1,8868; taxa 11,75 USDC; odd líquida 1,8435). Profundidade
dos cinco melhores asks: cerca de 19 500 USDC. O livro Under é o espelho exato (`mirror_consistent=true`).

**O `timestamp` do livro é a hora da última alteração, não a hora da resposta.** Com o mesmo hash, a idade cresce
(95 → 139 s). Por isso guardam-se as duas horas: a da fonte e a da receção local.

**Os preços da Gamma (`bestAsk`, `lastTradePrice`) são cache.** Às 17:45 a Gamma mostrava `lastTradePrice=0,53` e o
CLOB `0,480`. Ficam registados como `gamma_cached_quote_not_used`. `GET /price?side=BUY` devolve o **melhor bid**
(0,52), por isso o preço de compra vem sempre do lado ask do `/book`.

### Atual ≠ histórico

O primeiro sinal do bot para este jogo foi enviado às 12:39:16 UTC e visto às 12:41:16 UTC, a 1,89 (≈ 0,529). A
cotação acima foi lida **5 h 16–17 min depois**: está marcada `relation_to_signal=current_only`,
`evidence_for_signal=false`. **Não prova** que 1,89 estivesse disponível às 12:41, nem que o sinal fosse executável.
Este sinal também está fora da regra congelada da PR #55 (quota 100 % e valor ≥ 10 000 USD): é só um dos 58 do
universo de referência.

Uma cotação só conta como evidência para um sinal quando é recebida **depois** de o sinal ser visto, **no máximo 120 s
depois** e antes do início. Para os 58 sinais passados não existe nenhuma observação nessas condições. O histórico de
preços da Polymarket (`data-api.polymarket.com/v2/prices-history`) não está na lista de hosts e não tem profundidade
do livro. **A exequibilidade dos sinais antigos continua indeterminada.**

## Veredito

| Pergunta | Veredito |
|---|---|
| Acesso gratuito e sem credenciais a docs, Gamma e CLOB | **GO** |
| Identificar o contrato regulamentar, regras, conditionId e token | **GO** (35/58 com regras estritas; o resto precisa de aliases revistos) |
| Observar prospetivamente preço, profundidade e custo (taxa) antes do início | **GO** (técnico, em modo sombra) |
| Provar retroativamente a exequibilidade dos sinais antigos | **NO-GO** |
| Apostar ou enviar ordens | **NO-GO** (fora do âmbito; `restricted=true`) |

### Limites conhecidos

- **Contemporaneidade:** a recolha corre de 10 em 10 minutos. Para haver evidência por sinal (≤ 120 s depois de visto),
  o observador teria de correr logo a seguir ao leitor, no workflow de produção. Isso é uma alteração de produção e
  precisa de autorização própria (P2). Esta PR não a faz.
- A taxa é modelada pela documentação e pelo campo `fd` do CLOB, somada ao custo e arredondada para cima. Se `fd`
  faltar, as odds líquidas ficam nulas: nunca se presume taxa zero. Comissões de depósito e levantamento, câmbio e
  deslizamento durante o atraso de 1 s ficam fora.
- O livro é limpo no início do jogo. Este observador é só para pré-jogo.

## Reprodução

```sh
python -B -m unittest tests.test_polymarket_shadow -v
python -B -m scripts.polymarket_shadow --upcoming --out /tmp/obs.json     # só GET, só leitura
python -B -m scripts.polymarket_shadow --coverage --out /tmp/cov.json     # identidade, sem preços
```

`--out` recusa `data/`, `docs/`, `.github/`, `audits/strategy-lab/` e `audits/opportunity-lab/`. As fixtures de teste
(`tests/fixtures/polymarket_p1/`) são as respostas públicas reais deste piloto, reduzidas aos campos usados.
