# Betfair P1 — reconciliação individual, sem aplicação de resultados

Base de código: `f03bda08878085a27f22735336012a7a4026d445` (snapshot inicial em initial-snapshot.json).
Dados finais consultados: main `0840b03cca5146e76907da9ce3ee24af4e0815ae`, relatório de 17-11-2026 18:32:28 UTC.
A PR #43 é reaproveitada sem merge. O relatório e inventário originais ficam intactos em audits/betfair-reconciliation/.
A nova CLI permite declarar/verificar o SHA dos dados, comparando byte a byte os seus hashes com os blobs desse commit.

## Inventário e atribuição correta

| Medida | Referência #43 | Início desta intervenção | Último snapshot |
|---|---:|---:|---:|
| Apostas Betfair | 176 | 183 | 183 |
| Liquidadas | 110 | 116 | 116 |
| Pendentes | 66 | 67 | 67 |
| Jogos pendentes | 64 | 65 | 65 |
| Pendentes LIVE | 59 | 60 | 60 |
| Pendentes PREMATCH | 7 | 7 | 7 |

Do inventário original de 66 pendentes, DUAS apostas passaram a liquidadas com prova preservada, restando 64.
Entraram três pendentes novos: 66−2+3=67. São sete apostas novas no total, quatro já liquidadas e três pendentes.
As duas liquidações foram efetuadas pelo coletor existente ANTES do snapshot inicial desta intervenção. Não são atribuídas ao novo código.
**Resultados adicionais recuperados/aplicados por esta execução: ZERO. Pendentes início→fim: 67→67.**
O auditor reconcilia as duas apostas da referência individualmente e conserva todas as fontes, sem editar produção.

| Mensagem da aposta | Jogo | Seleção | Odd | FT | Liquidação | Provas atuais |
|---|---|---|---:|---|---:|---|
| 29148 | Saudi Arabia — Uzbekistan | 1 | 3,60 | 1–2 | -1u | quatro versões ligadas do bot; HL 1401912654; BSD 655422, ambos início 15:30 UTC e FT 1–2 |
| 29160 | Jordan — Australia | 2 | 4,40 | 2–0 | -1u | versão ligada do bot, equipas/data/identidade exatas; sem confirmação API preservada |

Provas completas: reconciled-source-evidence.json, incluindo linhas, IDs, mensagens originais, hashes, horários de observação e registos dos fornecedores.
O segundo caso tem proveniência do bot, sem o apresentar como validação independente por duas APIs.
As confirmações HL/BSD do primeiro caso chegaram às 18:14:20/21 UTC pelo coletor existente, sem qualquer chamada desta investigação.

## Pendências atuais

Todos os 67 pendentes são 1X2; 28 têm odd >3,00; 66 são elegíveis para ROI e um excluído por origem.
Idades no snapshot: 12 <24h; 6 entre 24–48h; 17 entre 48–72h; 29 >72h; três ainda por começar.
A idade é desde o início anunciado ou, quando ausente, desde o sinal. Zero mercados com FT conhecido mas não liquidado.

Classificação principal individual, sem sobreposição: 29 sem correspondência exata em listagens completas; 25 com nomes divergentes;
três sem prova de marcador regulamentar; dez sem resultado fiável nas observações integradas. Zero datas pré-jogo contraditórias identificadas.
A tabela JSON também conserva causas sobrepostas, sem somá-las como apostas diferentes.
Existem 26 conjuntos de candidatos a aliases (25 apostas com nomes divergentes), sem aplicação nem aprovação automática.

Os três casos com resultado regulamentar ambíguo correspondem a Cerro Porteño–Sol de América e às duas seleções de Greece U21–Iceland U21.
Os detalhes Highlightly indicam penalties e listas de golos inconsistentes. Não se eliminam eventos aos 120 minutos para fabricar um FT90.
São mantidos em quarentena até existir prova explícita do período regulamentar.
As tabelas completas de todos os 183 sinais e dos 67 pendentes estão nos CSV de final/ e em reconciliation.json.

Foram reindexados os 116 resultados HL/BSD agora guardados contra os 65 jogos pendentes: zero novas correspondências inequívocas.
Os 67 casos não são resolúveis com prova atualmente preservada nas fontes integradas. Isso não significa que jamais possam ser resolvidos.

## ROI da hipótese LIVE odd >3

| Medida | Referência #43 | Último snapshot |
|---|---:|---:|
| Sinais | 68 | 74 |
| Liquidados | 41 | 47 |
| Pendentes | 27 | 27 |
| Vitórias | 13 | 15 |
| Lucro observado | 14,40u | 19,00u |
| ROI observado | 35,12% | 40,43% |

A variação é dos dados do coletor existente, não da nova ferramenta. Não há alteração de ROI de produção nesta PR.
Com redução de odd de 2%: 17,68u, ROI 37,62%; com 5%: 15,70u, ROI 33,40%.
Se as 27 pendências fossem perdas: lucro -8u, ROI nos 74 sinais -10,81%; a -2%: -12,59%; a -5%: -15,27%.
IC95% descritivo agrupado por jogo: [-21,72%; 102,57%], inclui zero. Não confirma vantagem.
Há 19 perdas adicionais até lucro não positivo à odd anunciada; são cenários, nunca liquidações factuais.
final/audit.json contém também cenários com 0/5/10/27 perdas adicionais e limites extremos.

## Fontes e quotas

ZERO pedidos novos, zero secrets lidos/modificados, zero workflows novos e zero fornecedores adicionados.
HL guardava 12 pedidos restantes às 18:14:20, abaixo da reserva mínima de 15: não foi consultada por esta execução.
BSD fez quatro pedidos às 18:14:21 no coletor existente, com um novo match correspondido. Não se repetiram estas consultas.
Não há credenciais de fornecedores disponíveis neste ambiente. Não é usado TheSportsDB.
Importações futuras continuam offline, só como propostas, com controlo de identidade, data, FT90, cronologia, ambiguidades e divergências.
Nenhuma pendência é convertida em derrota por uma regra científica conservadora; tal cenário fica separado da tabela factual.

## Reproduzir e testes

```
python -m scangoal.betfair_audit --output-dir /tmp/bf-final-novo --as-of 2026-11-17T18:32:28Z --snapshot-sha 0840b03cca5146e76907da9ce3ee24af4e0815ae
python -m scangoal.betfair_reconcile --reference audits/betfair-reconciliation/audit.json --current /tmp/bf-final-novo/audit.json --output /tmp/bf-reconciliacao-nova.json
python -m unittest discover -s tests -p 'test_betfair*.py' -v
python scripts/test_offline.py
```

Os dados têm de ser os blobs do SHA final indicado (SCANGOAL_DATA_DIR pode apontar para uma cópia desses blobs); destinos novos, sem substituição silenciosa.
O início desta intervenção e a referência anterior continuam preservados. O helper de teste preexistente usa as_of fixo para evitar a corrida de relógio observada na frente Stake.
Suite completa: 587 testes; 586 passam localmente, um Chromium bloqueado por sockets do ambiente. Os 12 testes de auditoria e seis novos de reconciliação passam.
CI final obrigatório. Diff vazio em data/, docs/, .github/, parser/report/brain/certification/reader/watch/notify.

## Parecer

APROVAR como ferramenta de reconciliação offline após CI verde. REJEITAR aplicação automática de resultados ou aliases.
P0: nenhum identificado. P1: 67 pendências continuam sem prova suficiente; nenhum ganho fictício foi apresentado.
P2: três casos de penalty/goal-details inconsistentes e 26 conjuntos de candidatos a aliases necessitam prova adicional; não há remendo por inferência.
Certificação permanece not_activated. Nenhuma PR é integrada automaticamente.

## Validação acrescentada após a auditoria de 17 de novembro

O SHA completo dos dados é obrigatório. Os cinco inputs de referência têm de existir no
commit e na cópia de entrada, com hashes iguais. A presença ou ausência de
`manual_results.json` também é verificada. A análise lê cópias privadas dos blobs Git.
O relatório separa o SHA dos dados, o SHA do checkout do auditor e os hashes dos módulos
usados. A reconciliação reconstrói as linhas factuais dos dois commits e verifica o
inventário e a ordem dos snapshots. O artefacto histórico da #43 serve como referência,
com o seu formato original.

No snapshot final indicado acima, a remoção de `results_hl.json` e a utilização dos
dados da referência com o inventário final são erros antes de a CLI produzir um ficheiro.
A execução íntegra mantém 183 apostas, 67 pendentes em 65 jogos, 27 pendentes LIVE
acima de 3,00 e duas transições anteriores com prova. A divergência atual de Stake é
zero. As 16 ocorrências anteriores à #44 constam do comparador histórico, que não é
usado no inventário nem em propostas de liquidação.
