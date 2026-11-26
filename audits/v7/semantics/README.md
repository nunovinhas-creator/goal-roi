# Corpus semântico etiquetado — Kalshi, Polymarket, Stake

Só diagnóstico. **Zero admissões.** Parser, diário, protocolo, registo prospetivo e Brain V5 inalterados.

| Ficheiro | Conteúdo |
|---|---|
| `regras.json` | 24 regras com citação literal de fontes oficiais: Kalshi (4 PDFs), Polymarket US (FAQ de desporto), Polymarket internacional (resolução) e centro de ajuda da Stake (4 artigos). |
| `fetch_sources.py` | Descarrega as fontes (PDF, Markdown, artigo HTML), confere o SHA-256 quando o documento é estável e verifica cada citação no texto. |
| `corpus.py` | Gera `corpus.jsonl` a partir do diário: uma linha por primeira mensagem rejeitada, com classe, regras, decisão e o que falta. Referência opaca (SHA-256 do ID, 16 hex), sem texto bruto. |
| `corpus.jsonl` | 935 linhas no corte e3a0760 (diário `e4585a25…ccff598`). |
| `tests/test_v7_corpus_semantico.py` | 13 testes: zero admissões, regras com fonte oficial, totais iguais à sonda e ao censo, classificador, regeneração byte a byte e as 24 citações. |

## Fontes (lidas a 26/11/2026, só nos domínios autorizados)

| Fonte | Estado |
|---|---|
| Kalshi `SOCCERGAMEWIN`, `SOCCERSPREADS`, `SOCCERTOTALS`, `SOCCERADVANCEMETHOD` | Oficial; SHA-256 dos PDFs conferido |
| Polymarket US, `docs.polymarket.us/faqs/sports-faqs.md` | Oficial; SHA-256 conferido. **Só vale para a Polymarket US** |
| Polymarket internacional, `docs.polymarket.com/concepts/resolution.md` | Oficial; SHA-256 conferido. Diz que cada mercado tem regras próprias; o texto de cada mercado está noutro domínio, não obtido |
| Stake, centro de ajuda (`help.stake.com`: handicap asiático, totais asiáticos, apostas anuladas, handicap europeu) | Oficial mas explicativo; o HTML muda, por isso confere-se o texto citado |
| Stake, regras do sportsbook (`stake.com/policies/sportsbook`) | **Não obtida**: o domínio responde 403 com desafio de navegador. Não foi contornado |

## O que as regras mostram

**Kalshi**
1. O vencedor tem cinco âmbitos e uma iteração "Draw"; a mensagem do bot não indica o âmbito (K-GW-SCOPE, K-GW-DRAW).
2. `not <equipa>` só equivale a X2/1X no âmbito regulamentar (K-GW-REG-DRAW).
3. Spreads e totais **incluem prolongamento por omissão** (K-SP-DEFAULT, K-TO-DEFAULT); `+N` não tem contrato.

**Polymarket**
4. Na Polymarket US, futebol = **90 min + descontos** por omissão, sem prolongamento nem penáltis (PM-US-SOCCER-REG). Com empate listado, só tempo regulamentar. Isto coincide com o liquidante do parser.
5. Um spread tem dois contratos: favorito −N e adversário +N (PM-US-SPREAD-SIDES). Isto torna plausível que, em `Spread: X (-1.5) (Y)`, Y seja o **lado comprado**. Se for assim, as 2 linhas "spread contraditório" são compras do adversário. Mantêm-se em quarentena até confirmação no texto do mercado.
6. A plataforma dos sinais não está identificada. Se for a Polymarket internacional, nenhuma regra geral se aplica (PM-INTL-RULES): vale o texto de cada mercado.

**Stake**
7. O centro de ajuda confirma a semântica de quartos de linha, linha inteira (devolução) e handicap aplicado ao "resultado final do período apostado". Não diz se o período por omissão exclui prolongamento, nem como um handicap ao vivo trata os golos anteriores à aposta. Isso está nas regras do sportsbook, não obtidas.

## Decisões no corpus

| Família | Condicional (revisão humana) | Quarentena permanente | Bloqueado sem regra vinculativa |
|---|---:|---:|---:|
| Kalshi (163) | 132 | 29 | 2 |
| Polymarket (144) | 98 | 43 | 3 |
| Stake (628) | 0 | 0 | 628 |

"Condicional" não é admissão. Cada linha tem no campo `falta` o que tem de ficar provado. Na Polymarket, isso é primeiro a plataforma (US ou internacional) e, se internacional, o texto de regras do mercado. Depois: alias revisto por humano, liquidante com diferencial contra o parser em uso, e nenhum efeito retroativo no registo prospetivo.

Stake: 340 linhas são mercados base que o liquidante recusou. A causa está no parser (vocabulário do rótulo ou nome de equipa), não nas regras. As restantes exigem dados que o resultado final não dá ou regras próprias (`(2up)`). A convenção do projeto para o handicap asiático ao vivo da Stake continua sem confirmação oficial.

## Reproduzir

```sh
export PYTHONDONTWRITEBYTECODE=1
git show e3a0760:data/messages.jsonl > /tmp/m.jsonl
PYTHONPATH=. python3 audits/v7/semantics/corpus.py --archive /tmp/m.jsonl
python3 audits/v7/semantics/fetch_sources.py --dir /tmp/regras
V7_RULES_DIR=/tmp/regras python3 -m pytest -q tests/test_v7_corpus_semantico.py
```

## Vereditos

| Item | Veredito |
|---|---|
| Corpus etiquetado com referências | **GO** para revisão humana |
| Admissão automática de qualquer classe | **NO-GO** |
| Polymarket | **NO-GO** até identificar a plataforma e, se internacional, obter o texto de cada mercado |
| Stake | **NO-GO** até obter as regras do sportsbook (bloqueadas por desafio de navegador; exige leitura humana) |

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
