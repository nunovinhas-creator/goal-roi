# Corpus semântico etiquetado — Kalshi, Polymarket, Stake

Só diagnóstico. **Zero admissões.** Parser, diário, protocolo, registo prospetivo e Brain V5 inalterados.

| Ficheiro | Conteúdo |
|---|---|
| `regras.json` | 11 regras com citação literal dos termos oficiais Kalshi (SHA-256 de cada PDF), e o estado das fontes Polymarket e Stake. |
| `fetch_sources.py` | Descarrega os PDFs, confere o SHA-256 e verifica cada citação no texto (`pdftotext`). |
| `corpus.py` | Gera `corpus.jsonl` a partir do diário: uma linha por primeira mensagem rejeitada, com classe, regras, decisão e o que falta. Referência opaca (SHA-256 do ID, 16 hex), sem texto bruto. |
| `corpus.jsonl` | 935 linhas no corte e3a0760 (diário `e4585a25…ccff598`). |
| `tests/test_v7_corpus_semantico.py` | 12 testes: zero admissões, regras com fonte oficial, totais iguais à sonda e ao censo, classificador, regeneração byte a byte e citações. |

## Fontes

| Fonte | Estado |
|---|---|
| Kalshi `SOCCERGAMEWIN`, `SOCCERSPREADS`, `SOCCERTOTALS`, `SOCCERADVANCEMETHOD` | Oficial, obtida de `kalshi-public-docs.s3.amazonaws.com` a 26/11/2026; hashes e 11 citações conferidos. |
| Polymarket (`docs.polymarket.us`) | **Não obtida.** Proxy desta sessão recusou; o pedido de leitura foi depois recusado pelo controlo de permissões. Descrições de terceiros não usadas. |
| Stake (`stake.com/policies/sportsbook`) | **Não obtida**, pelo mesmo motivo. |

## O que as regras Kalshi mostram

1. **Âmbito do vencedor.** `SOCCERGAMEWIN` tem cinco âmbitos (primeira parte, regulamentar, segunda parte, prolongamento, jogo completo) e uma iteração "Draw" (K-GW-SCOPE, K-GW-DRAW). A mensagem do bot não indica o âmbito. Em jogos sem prolongamento, "regulamentar" e "jogo completo" coincidem; em eliminatórias, não.
2. **`not <equipa>`** é o "Não" da iteração de vitória: empate ou vitória do adversário **no mesmo âmbito**. Só equivale a X2/1X no âmbito regulamentar (K-GW-REG-DRAW).
3. **Spreads** são sempre "equipa ganha por mais de N golos" (K-SP-PAYOUT). **Por omissão incluem prolongamento** (K-SP-DEFAULT); empate = Não (K-SP-TIE). `−1.5` equivale ao handicap asiático −1.5 a 90 min só sem prolongamento. Um rótulo `+N` não tem contrato Kalshi correspondente.
4. **Totais** incluem prolongamento por omissão (K-TO-DEFAULT). Abandono depois dos 90 min liquida pelo ocorrido (K-ABANDON-90).
5. **`12`**: nenhum contrato Kalshi obtido usa este rótulo → quarentena permanente.

## Decisões no corpus

| Família | Condicional (revisão humana) | Quarentena permanente | Bloqueado sem fonte oficial |
|---|---:|---:|---:|
| Kalshi (163) | 132 | 29 | 2 |
| Polymarket (144) | 0 | 43 | 101 |
| Stake (628) | 0 | 0 | 628 |

"Condicional" não é admissão. Antes de qualquer proposta de admissão, cada linha tem de cumprir o campo `falta`: âmbito ou período conhecido, ou competição sem prolongamento; alias revisto por humano; liquidante com diferencial contra o parser em uso; nenhum efeito retroativo no registo prospetivo.

Stake: 340 linhas são mercados base (`Match Result`, `1x2`, `Total Goals`, `Asian Handicap`, `Draw No Bet`) que o liquidante recusou. A causa (vocabulário do rótulo ou nome de equipa) fica por investigar no parser; não é uma questão de regras. O resto exige dados que o resultado final não dá (partes, cantos, ordem dos golos) ou regras próprias da Stake (`(2up)`). A regra do projeto para o handicap asiático ao vivo da Stake (contagem a partir do marcador no momento do sinal) é uma convenção interna e não está confirmada por fonte oficial.

## Reproduzir

```sh
export PYTHONDONTWRITEBYTECODE=1
git show e3a0760:data/messages.jsonl > /tmp/m.jsonl
PYTHONPATH=. python3 audits/v7/semantics/corpus.py --archive /tmp/m.jsonl
python3 audits/v7/semantics/fetch_sources.py --dir /tmp/kalshi-pdf
V7_KALSHI_PDF_DIR=/tmp/kalshi-pdf python3 -m pytest -q tests/test_v7_corpus_semantico.py
```

## Vereditos

| Item | Veredito |
|---|---|
| Corpus etiquetado com referências | **GO** para revisão humana |
| Admissão automática de qualquer classe | **NO-GO** |
| Polymarket e Stake | **NO-GO** até obter as regras oficiais (exige autorização de acesso de rede aos domínios de documentação) |

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
