# Sonda semântica das rejeições Kalshi e Polymarket

Corte e3a0760813f8b2ba4f4953a84dde0a697a117a10, diário com SHA-256 `e4585a25…ccff598` (igual ao de [auditoria-parser.json](auditoria-parser.json)). Só diagnóstico: **zero admissões**, parser, diário, protocolo e registo prospetivo inalterados. [Dados](sonda-semantica.json) · [Código](semantic_probe.py)

## Reprodução

```sh
export PYTHONDONTWRITEBYTECODE=1; unset SCANGOAL_DATA_DIR
PYTHONPATH=. python3 audits/v7/parser/parser_audit.py --inventory /tmp/v7-inv.csv > /tmp/v7-parser.json
cmp <(python3 -c 'import json,sys;print(json.dumps(json.load(open("/tmp/v7-parser.json")),sort_keys=True))') \
    <(python3 -c 'import json,sys;print(json.dumps(json.load(open("audits/v7/parser/auditoria-parser.json")),sort_keys=True))')
PYTHONPATH=. python3 audits/v7/parser/semantic_probe.py > /tmp/v7-sonda.json
python3 -m pytest -q tests/test_v7_parser_audit.py tests/test_v7_semantic_probe.py
```

A reprodução do censo do Codex no corte fixado deu JSON idêntico (1 379 no contador, 766 Kalshi e 424 Betfair estruturais, 144 Polymarket fora do contador).

## 105 nomes Kalshi sem vínculo literal

| Hipótese candidata | Primeiras mensagens | Evidência | Bloqueio semântico |
|---|---:|---|---|
| Alias do vencedor (ex.: PSG ↔ Paris Saint Germain, Bayern Munich ↔ Bayern Munchen) | 73 | Só um lado coincide (as 82 linhas com alias, incluindo handicap: 46 por sigla, 36 por prefixo comum) | Tabela de aliases sem validação humana. Regra Kalshi de 90 min + compensação por confirmar. |
| Alias + handicap (`Bayern Munich −1.5`) | 9 | Só um lado coincide; 6 usam o sinal U+2212 | Linha e sinal dependem do contrato Kalshi (spread) e da convenção do bot. |
| Código `12` | 20 | Nenhuma | Duas leituras: dupla hipótese casa/fora ou `not Tie`. Sem prova, não normalizar. |
| Nome sem lado | 3 | 2 coincidem com os dois lados (Al Nassr ↔ Al Ahli/Al Nasr Riyadh), 1 sem evidência textual (FC Köln ↔ Cologne) | Ambíguo por construção: quarentena. |

Total 105, igual ao contador `market:selecao_sem_vinculo_literal`.

## 52 contratos Kalshi negados

50 são `not <equipa>` com alias resolvido num só lado e 2 são `not <resultado exato>`. 49 surgem em live e 3 em pré-jogo. Um `not <equipa>` equivale a X2/1X apenas se o mercado Kalshi liquidar no resultado regulamentar; com prolongamento ou apuramento a equivalência falha. `not 2:0` não tem liquidante no parser atual.

## 144 rejeições Polymarket fora do contador

| Classe | Contagem | Observação |
|---|---:|---|
| Cabeçalho `LIVE` sem minuto nem marcador | 12 | Mesmo defeito das 6 Kalshi: sem relógio não há fase verificável. |
| Contrato negado | 45 | 39 com alias num só lado, 6 sem lado (`Republic of Ireland` ↔ `Rep of Ireland` não passa o critério de prefixo). |
| Alias do vencedor | 41 | Nomes oficiais longos (`Paris Saint-Germain FC`, `FK Bodø/Glimt`, `Türkiye`). |
| Spread coerente | 15 | Equipa nomeada e resultado entre parênteses no mesmo lado. |
| **Spread contraditório** | **2** | Equipa nomeada num lado e resultado no outro (ex.: `FC Barcelona (-3.5) (Sabah FK)`). O rótulo não identifica de forma única a posição. |
| Spread sem lado resolvido | 5 | Nomes truncados ou com cidade (`1. FC Koln`). |
| Nome sem lado | 12 | Inclui `Stade Rennais FC 1901` ↔ `Rennes`. |
| Código `12` | 6 | Mesma ambiguidade da Kalshi. |
| Total de equipa | 3 | `Liverpool FC Total Over 1.5`. |
| Mercado de jogador | 2 | Ex.: marcador a qualquer momento; não liquidável pelo FT. |
| Apuramento | 1 | Depende de prolongamento/penáltis. |

## Vereditos

| Item | Veredito |
|---|---|
| Reprodução do censo e da sonda | **GO** (byte-idêntico, hash do diário conferido) |
| Admissão de qualquer classe acima | **NO-GO** até haver: regras escritas de cada contrato (Kalshi/Polymarket), tabela de aliases revista por humano, liquidante para negação e spread, e corpus etiquetado por classe |
| Spreads Polymarket contraditórios, `12`, nomes sem lado e cabeçalhos sem relógio | **NO-GO permanente** sem informação externa: quarentena |
| Reprocessamento do histórico ou do registo prospetivo | **NO-GO** (protocolo congelado) |

A tabela revista à mão `data/team_aliases.json` (13 entradas, nome do bot → fornecedores de resultados) não cobre nenhum destes aliases e tem outro propósito (identidade para resultados, não rótulos de mercado). Não deve ser estendida sem a mesma revisão humana.

Ordem sugerida para a revisão humana: (1) aliases da tabela em `aliases_para_revisao_humana`; (2) regra de liquidação regulamentar Kalshi para `not <equipa>`; (3) convenção do sinal e lado nos spreads. Cada passo exige diferencial com o parser em uso e nenhum efeito no contador antes de nova aprovação.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
