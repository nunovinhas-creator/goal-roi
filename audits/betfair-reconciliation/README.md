# Auditoria Betfair — 17 de novembro de 2026, 17:35 UTC

Snapshot main: `a81dccb2ec69d8b5177762816a3a66aa4c1d95a2`. Hashes dos cinco ficheiros de entrada constam de audit.json.
Não foram alterados dados históricos, resultados, aliases, Telegram, secrets, ROI, táticas, certificação ou workflows.
Esta PR contém ferramentas offline e evidência descritiva, sem integração em produção.

## Inventário

176 apostas Betfair distintas: 110 liquidadas e 66 pendentes (65 elegíveis para ROI; uma excluída por origem).
Dos pendentes: 59 LIVE e 7 PREMATCH; todos são 1X2; 28 têm odd >3 e 38 têm odd <=3.
Há 64 jogos pendentes distintos. Nenhum tem FT conhecido mas mercado ainda por liquidar.
Idades: 12 com <24h, 6 com 24–48h, 17 com 48–72h, 28 com >72h e 3 jogos ainda por começar.
A idade usa o início anunciado quando disponível, senão a hora do sinal; não é a idade desde o fim do jogo.
O JSON contém o cruzamento completo fase × idade × mercado × odds e as consultas já guardadas por fonte.

## Hipótese Betfair LIVE, odd >3

| Medida | Referência fornecida | Snapshot atual |
|---|---:|---:|
| Sinais | 68 | 68 |
| Liquidados | 38 | 41 |
| Pendentes | 30 | 27 |
| Vitórias | 12 | 13 |

As três liquidações adicionais são uma diferença relativamente à referência fornecida, já incorporada nos dados antes desta investigação.
Não se atribuem estas recuperações ao novo auditor. Existem 67 jogos independentes; os 41 sinais liquidados correspondem a 40 jogos.
São 13 vitórias e 28 derrotas. Resultados ausentes não foram transformados em derrotas.

| Cenário | Odd anunciada | Odd −2% | Odd −5% |
|---|---:|---:|---:|
| Lucro observado, unidades | 14,40 | 13,292 | 11,63 |
| ROI entre os 41 liquidados | 35,12% | 32,42% | 28,37% |
| Todas as 27 pendentes perdidas: ROI nos 68 sinais | −18,53% | −20,16% | −22,60% |
| Perdas adicionais para lucro não positivo | 15 | 14 | 12 |

O IC95% descritivo, agrupado por jogo, para ROI à odd anunciada é [−31,68%; 101,93%].
É largo e inclui zero; não confirma vantagem. As pendências são 39,71% da amostra.
Com 10 perdas adicionais e os restantes 17 resultados ainda desconhecidos, o lucro seria 4,40u e o ROI 8,63% nos 51 liquidados.
O JSON inclui 0/5/10/27 perdas adicionais, extremos e reduções de odds. Estes cenários não são liquidações factuais nem probabilidades.
O ROI é retorno bruto à odd anunciada, sem assumir comissões Betfair ou execução efetiva.

## Recuperação e proveniência

Foram reindexados TODOS os 114 resultados já preservados em Highlightly/BSD contra os 64 jogos pendentes, por identidade exata e janela temporal existente.
Resultado: zero correspondências novas inequívocas. Permanecem 66 apostas pendentes; esta execução não reduziu factualmente esse número.
As sugestões de nomes semelhantes existentes são conservadas como diagnóstico, sem criar aliases ou inventar resultados.

As fontes integradas são Highlightly e BSD/Bzzoiro. TheSportsDB não está integrada neste main.
A última resposta Highlightly guardada às 16:42:30 UTC indica 14 pedidos restantes, abaixo da reserva mínima existente de 15.
Não se fizeram pedidos adicionais a esta fonte. A BSD foi consultada pelo coletor às 17:19:09 UTC, com 4 pedidos e zero novos jogos correspondidos.
Não existem credenciais de fornecedor disponíveis neste ambiente local. Não foram lidos/modificados secrets nem disparados workflows de produção.
Total de pedidos novos desta investigação: zero. Não se criaram fornecedores, planos pagos ou subscrições.

A ferramenta aceita envelopes offline de respostas das duas fontes já integradas e guarda o payload importado, hash SHA-256,
resultado, nomes, data, ID de fornecedor e instante de observação. Recusa fonte desconhecida, data impossível, resultado anterior
à decisão/início, extra time/penalties/awarded, múltiplos candidatos ou fontes divergentes.
As propostas ficam separadas do resultado factual e da análise científica. Um resultado ausente nunca vira derrota na tabela factual.
A importação não escreve nos caches de produção. Qualquer alteração efetiva exige revisão de prova.
Não há substituição silenciosa de snapshots: uma pasta de saída existente é recusada.

## Dezasseis divergências

São 16 mensagens/versões distintas, correspondentes a SETE apostas agregadas em SETE jogos; todas são Stake, nenhuma é Betfair.
O ramo atual de `parser.settle` para 1X2 compara a seleção literalmente com 1/X/2. Nestes sinais a seleção é o nome da equipa.
A comparação literal classifica como derrota uma equipa vencedora. A normalização exata, sem alias nem substring,
reproduz vitória nos 16 casos e concorda com a marca do bot. Esta análise não usa a marca para escolher o resultado.
O helper de correção está isolado no auditor; o parser de produção permanece intacto, por exigência de manter ROI inalterado.
Nas sete apostas agregadas, a correção proposta alteraria o lucro em +14,70u no grupo Stake, sem efeito Betfair.
É um P1 de produção confirmado e deve ser corrigido numa intervenção separada com autorização para alterar os resultados publicados.

| Mensagem | Jogo | Seleção | FT | Produção | Auditoria exata | Fonte observada |
|---|---|---|---|---:|---:|---|
| 28103 | Norway — Wales | Norway | 3:1 | -1.0 | 1.0 | 2026-11-14T19:02:33+00:00 |
| 28093 | Norway — Wales | Norway | 3:1 | -1.0 | 1.0 | 2026-11-14T19:08:54+00:00 |
| 28242 | Angola — South Sudan | Angola | 2:0 | -1.0 | 1.0 | 2026-11-14T21:26:27+00:00 |
| 28394 | Granada — Las Palmas | Granada | 1:0 | -1.0 | 1.0 | 2026-11-15T17:18:45+00:00 |
| 28580 | Belarus — Albania | Albania | 0:2 | -1.0 | 1.0 | 2026-11-15T18:54:50+00:00 |
| 28582 | Belarus — Albania | Albania | 0:2 | -1.0 | 1.0 | 2026-11-15T18:54:50+00:00 |
| 28600 | Belarus — Albania | Albania | 0:2 | -1.0 | 1.0 | 2026-11-15T18:54:50+00:00 |
| 28322 | Spain — England | England | 2:3 | -1.0 | 1.0 | 2026-11-15T21:54:20+00:00 |
| 28635 | Spain — England | England | 2:3 | -1.0 | 1.0 | 2026-11-15T21:54:20+00:00 |
| 28691 | Spain — England | England | 2:3 | -1.0 | 1.0 | 2026-11-15T21:54:20+00:00 |
| 28693 | Spain — England | England | 2:3 | -1.0 | 1.0 | 2026-11-15T21:54:20+00:00 |
| 28699 | Spain — England | England | 2:3 | -1.0 | 1.0 | 2026-11-15T21:54:20+00:00 |
| 28760 | Spain — England | England | 2:3 | -1.0 | 1.0 | 2026-11-15T22:07:34+00:00 |
| 28765 | Spain — England | England | 2:3 | -1.0 | 1.0 | 2026-11-15T22:14:13+00:00 |
| 28785 | Germany — Netherlands | Germany | 5:0 | -1.0 | 1.0 | 2026-11-16T21:48:32+00:00 |
| 28811 | Switzerland — Scotland | Switzerland | 4:0 | -1.0 | 1.0 | 2026-11-16T21:48:32+00:00 |

## Tabela individual dos pendentes

FT e liquidação: desconhecidos em todos os casos abaixo. Confiança insuficiente; decisão MANTER PENDENTE.
As fontes já consultadas e os nomes candidatos estão individualizados no JSON; as apostas liquidadas estão no CSV completo.

| Mensagem | Jogo | Sinal UTC | Fase | Seleção 1X2 | Odd | Idade | Motivo |
|---|---|---|---|---|---:|---|---|
| 27450 | Kucukcekmece Sinopspor — Fatsa Belediyespor | 2026-11-11T12:39:51+00:00 | live | X | 1.94 | >72h | source lists checked; no exact covered result |
| 27463 | Powisle Dzierzgon — Jaguar Gdansk | 2026-11-11T13:26:30+00:00 | live | X | 2.6 | >72h | source lists checked; no exact covered result |
| 27472 | Karsiyaka — Gemlikspor | 2026-11-11T14:07:31+00:00 | live | 2 | 1.75 | >72h | source lists checked; no exact covered result |
| 27550 | Soccerstar FC — Sports Gen | 2026-11-12T09:07:24+00:00 | live | 1 | 1.62 | >72h | source lists checked; no exact covered result |
| 27551 | Bengaluru B — Rebels FC India | 2026-11-12T09:12:19+00:00 | live | 1 | 1.76 | >72h | source lists checked; no exact covered result |
| 27555 | Kuala Lumpur FA U20 — Kuching City U20 | 2026-11-12T10:06:55+00:00 | live | 1 | 1.6 | >72h | source lists checked; no exact covered result |
| 27558 | Hannover — TSB Havelse | 2026-11-12T11:21:20+00:00 | live | 1 | 1.6 | >72h | source lists checked; no exact covered result |
| 27562 | Viborg Reserves — Vejle BK Reserves | 2026-11-12T13:13:44+00:00 | live | 1 | 1.91 | >72h | source lists checked; no exact covered result |
| 27595 | Benin — Central African Rep | 2026-11-12T19:27:25+00:00 | live | 1 | 1.79 | >72h | name mismatch in existing source cache; no alias accepted |
| 27597 | Atenas — Club Oriental de Football | 2026-11-12T19:47:44+00:00 | live | 1 | 2.04 | >72h | name mismatch in existing source cache; no alias accepted |
| 27604 | CD Aguila — Municipal Limeno | 2026-11-12T21:46:56+00:00 | live | 1 | 1.69 | >72h | name mismatch in existing source cache; no alias accepted |
| 27605 | Atletico Balboa — CD INCA | 2026-11-12T21:53:38+00:00 | live | 1 | 2.48 | >72h | name mismatch in existing source cache; no alias accepted |
| 27609 | Aurora (GUA) — CSYD Mixco | 2026-11-12T22:10:51+00:00 | live | 1 | 1.96 | >72h | source lists checked; no exact covered result |
| 27630 | Prykarpattya Ivano Frankivsk — Kolos Kovalivka II | 2026-11-13T11:28:53+00:00 | live | 1 | 3.25 | >72h | name mismatch in existing source cache; no alias accepted |
| 27631 | Vasteras SK — Brommapojkarna | 2026-11-13T11:33:56+00:00 | live | 1 | 2.2 | >72h | name mismatch in existing source cache; no alias accepted |
| 27638 | Sigma Olomouc B — FC Brno II | 2026-11-13T13:14:34+00:00 | live | 1 | 1.61 | >72h | source lists checked; no exact covered result |
| 27640 | Fastav Zlin U19 — Dukla Prague U19 | 2026-11-13T13:20:56+00:00 | live | 2 | 2.52 | >72h | source lists checked; no exact covered result |
| 27652 | Saoura — USM Khenchela | 2026-11-13T15:05:53+00:00 | pre | 1 | 1.67 | >72h | source lists checked; no exact covered result |
| 27654 | Etehad Al Reef — Umm Al Hassam | 2026-11-13T16:09:44+00:00 | live | 2 | 2.34 | >72h | source lists checked; no exact covered result |
| 27676 | Shooting Stars Nigeria — Bendel Insurance | 2026-11-13T18:44:56+00:00 | live | 1 | 3.65 | >72h | name mismatch in existing source cache; no alias accepted |
| 27677 | Znicz Pruszkow — Sokol Kleczew | 2026-11-13T18:48:16+00:00 | live | 2 | 7.0 | >72h | source lists checked; no exact covered result |
| 27723 | Fukushima — Sagamihara | 2026-11-14T05:19:45+00:00 | live | 1 | 2.72 | >72h | source lists checked; no exact covered result |
| 27745 | Bosnia U19 — Georgia U19 | 2026-11-14T10:38:57+00:00 | live | 1 | 2.84 | >72h | name mismatch in existing source cache; no alias accepted |
| 27829 | Athletic Bilbao B — Racing Ferrol | 2026-11-14T13:45:08+00:00 | live | 2 | 2.9 | >72h | source lists checked; no exact covered result |
| 27864 | Horsham — Torquay United | 2026-11-14T14:30:06+00:00 | pre | 2 | 3.05 | >72h | name mismatch in existing source cache; no alias accepted |
| 27878 | Larissa — Nestos Chrisoupolis | 2026-11-14T14:36:20+00:00 | live | 1 | 2.92 | >72h | name mismatch in existing source cache; no alias accepted |
| 27891 | Farnham — Ebbsfleet United | 2026-11-14T14:49:25+00:00 | pre | 1 | 2.74 | >72h | name mismatch in existing source cache; no alias accepted |
| 28020 | Hednesford — Hemel Hempstead Town | 2026-11-14T16:41:52+00:00 | live | 1 | 2.82 | >72h | name mismatch in existing source cache; no alias accepted |
| 28096 | St Ouen Aumone — Boulogne | 2026-11-14T17:53:14+00:00 | live | 2 | 2.8 | 48-72h | name mismatch in existing source cache; no alias accepted |
| 28235 | Villa Dalmine — Deportivo Camioneros | 2026-11-14T20:26:16+00:00 | live | X | 6.4 | 48-72h | no reliable final result in integrated observations |
| 28324 | Deportivo Recoleta — Libertad Asuncion | 2026-11-14T22:54:19+00:00 | live | 2 | 28.0 | 48-72h | name mismatch in existing source cache; no alias accepted |
| 28390 | Zwiegen Kanazawa — Kumamoto | 2026-11-15T06:32:36+00:00 | live | 1 | 4.0 | 48-72h | source lists checked; no exact covered result |
| 28391 | Kamatamare Sanuki — Renofa Yamaguchi | 2026-11-15T06:41:10+00:00 | live | 2 | 4.1 | 48-72h | source lists checked; no exact covered result |
| 28392 | Kashima — Yokohama FM | 2026-11-15T07:15:24+00:00 | live | 1 | 3.05 | 48-72h | name mismatch in existing source cache; no alias accepted |
| 28422 | Singburi Warriors — Kasem Bundit University | 2026-11-15T10:09:00+00:00 | live | 2 | 3.2 | 48-72h | source lists checked; no exact covered result |
| 28452 | Apollon Pontou Kalamaria — Athens Kallithea | 2026-11-15T12:07:32+00:00 | live | 2 | 2.92 | 48-72h | name mismatch in existing source cache; no alias accepted |
| 28468 | PFC Dobrudzha Dobrich — Etar | 2026-11-15T12:48:09+00:00 | live | 1 | 3.65 | 48-72h | source lists checked; no exact covered result |
| 28503 | Burnley Women — Southampton WFC Women | 2026-11-15T14:16:07+00:00 | live | X | 5.0 | 48-72h | name mismatch in existing source cache; no alias accepted |
| 28512 | Balikesirspor — Eskisehir Anadolu SF | 2026-11-15T14:28:44+00:00 | live | 1 | 50.0 | 48-72h | source lists checked; no exact covered result |
| 28529 | Hamburg — Hajduk Split | 2026-11-15T14:49:42+00:00 | live | 1 | 4.1 | 48-72h | source lists checked; no exact covered result |
| 28541 | Vallieres — St Etienne | 2026-11-15T15:19:24+00:00 | live | 2 | 6.0 | 48-72h | source lists checked; no exact covered result |
| 28544 | Zamora CF — Real Aviles CF | 2026-11-15T15:21:12+00:00 | live | 1 | 10.5 | 48-72h | source lists checked; no exact covered result |
| 28548 | Nottm Forest Women — Leicester Women | 2026-11-15T15:27:48+00:00 | live | 1 | 2.68 | 48-72h | source lists checked; no exact covered result |
| 28555 | FC Spaeri — FC Iberia 1999 | 2026-11-15T15:40:44+00:00 | live | 1 | 4.2 | 48-72h | name mismatch in existing source cache; no alias accepted |
| 28568 | Algeciras — Antequera | 2026-11-15T16:00:47+00:00 | live | 1 | 2.7 | 48-72h | source lists checked; no exact covered result |
| 28628 | CD Universidad San Martin — Union Comercio | 2026-11-15T17:42:10+00:00 | live | 1 | 3.3 | 24-48h | name mismatch in existing source cache; no alias accepted |
| 28786 | Cerro Porteno — Sol de America | 2026-11-15T22:11:42+00:00 | live | 1 | 3.95 | 24-48h | extra time/penalties; no proven 90-minute score |
| 28792 | Denmark — Norway | 2026-11-15T23:13:25+00:00 | pre | 2 | 2.78 | not_started | no reliable final result in integrated observations |
| 28793 | Cd Suchitepequez — Antigua Gfc | 2026-11-15T23:23:38+00:00 | live | X | 3.25 | 24-48h | name mismatch in existing source cache; no alias accepted |
| 28819 | Kickstart — MEG | 2026-11-16T09:26:42+00:00 | live | 1 | 2.92 | 24-48h | source lists checked; no exact covered result |
| 28825 | FK Bor 1919 — Smederevo | 2026-11-16T12:09:29+00:00 | live | 1 | 3.75 | 24-48h | source lists checked; no exact covered result |
| 28869 | Botswana — Uganda | 2026-11-16T16:16:59+00:00 | pre | 1 | 2.82 | not_started | no reliable final result in integrated observations |
| 28889 | Central African Rep — Benin | 2026-11-16T17:24:29+00:00 | live | 2 | 2.68 | 24-48h | name mismatch in existing source cache; no alias accepted |
| 28896 | Greece U21 — Iceland U21 | 2026-11-16T17:51:48+00:00 | live | 1 | 2.68 | <24h | extra time/penalties; no proven 90-minute score |
| 28930 | Greece U21 — Iceland U21 | 2026-11-16T18:53:17+00:00 | live | 2 | 21.0 | <24h | extra time/penalties; no proven 90-minute score |
| 28936 | Plymouth Argyle U21 — Leicester U21 | 2026-11-16T19:05:13+00:00 | live | 1 | 4.8 | <24h | source lists checked; no exact covered result |
| 28977 | Perugia — ASD Torres Calcio | 2026-11-16T20:37:08+00:00 | live | 1 | 4.5 | <24h | name mismatch in existing source cache; no alias accepted |
| 29055 | Sheff Wed U21 — Hull City U21 | 2026-11-17T12:04:29+00:00 | pre | 2 | 2.68 | <24h | name mismatch in existing source cache; no alias accepted |
| 29078 | Barnsley U21 — Wigan U21 | 2026-11-17T13:12:14+00:00 | live | 1 | 3.0 | <24h | name mismatch in existing source cache; no alias accepted |
| 29083 | HB Koge U21 — Brondby Reserves | 2026-11-17T13:20:06+00:00 | live | 2 | 2.7 | <24h | no reliable final result in integrated observations |
| 29109 | St. Kitts and Nevis — Bonaire | 2026-11-17T14:13:53+00:00 | pre | 2 | 2.84 | not_started | no reliable final result in integrated observations |
| 29120 | Barnsley U21 — Wigan U21 | 2026-11-17T14:40:18+00:00 | live | 2 | 6.0 | <24h | name mismatch in existing source cache; no alias accepted |
| 29148 | Saudi Arabia — Uzbekistan | 2026-11-17T16:09:15+00:00 | live | 1 | 3.6 | <24h | no reliable final result in integrated observations |
| 29159 | Sweden U19 — Portugal U19 | 2026-11-17T16:35:45+00:00 | live | 2 | 2.7 | <24h | no reliable final result in integrated observations |
| 29160 | Jordan — Australia | 2026-11-17T16:39:28+00:00 | live | 2 | 4.4 | <24h | no reliable final result in integrated observations |
| 29163 | Faroe Islands U19 — Azerbaijan U19 | 2026-11-17T16:41:36+00:00 | live | 2 | 3.75 | <24h | no reliable final result in integrated observations |

## Reproduzir

```
python -m scangoal.betfair_audit --output-dir /tmp/goal-roi-betfair-novo --as-of 2026-11-17T17:35:00Z
python -m unittest discover -s tests -p test_betfair_audit.py -v
python scripts/test_offline.py
```

Reprodução exata dos dados requer checkout do snapshot main indicado e os ficheiros novos desta PR, pois o coletor continua a atualizar main.
Sem `--as-of`, usa a hora atual; os buckets de idade mudam com o relógio. A referência 68/38/30/12 foi fornecida no pedido, não é um ledger prospetivo.
Para importar prova: `--evidence-jsonl caminho.jsonl`. Cada linha: `provider` (hl/bsd), `observed_utc` com fuso e `payload` original.
Respostas sintéticas dos testes são fixtures, nunca evidência de jogos reais. O CLI não possui modo de rede nem modo de aplicar resultados.

## Auditoria e decisão

- P0: nenhum resultado inventado ou alterado; não há caminho de apostas ou envio Telegram.
- P1 confirmado em produção: seleções 1X2 com nome de equipa no ramo Stake são liquidadas erradamente pela comparação literal. Sete apostas agregadas demonstradas.
- P1 limitação de cobertura: 66 pendências permanecem; ausência de acesso local às fontes e quota Highlightly sob reserva impedem novas consultas seguras nesta execução.
- P2: os caches antigos não preservam a resposta API bruta nem o status original; a proveniência histórica depende da política do matcher integrado.
- P2: 68 sinais não são 68 jogos independentes; os intervalos descritivos usam agrupamento por jogo.

APROVAR a PR para integração técnica como ferramenta offline de auditoria, sem mudanças de produção, condicionada a CI existente verde.
REJEITAR aplicação automática de resultados/aliases e qualquer alegação de validação científica. H1/H2 continuam INCONCLUSIVAS.

Validação local: 12 testes novos passam. Suite completa: 581 testes executados; 580 passam e um teste Chromium é bloqueado pela restrição de sockets
no ambiente, também presente na base. Nenhum teste foi removido ou relaxado. O CI existente executa a suite de forma independente.
Diff de produção: vazio para data/, docs/, .github/, parser/report/notify/brain/certification.
