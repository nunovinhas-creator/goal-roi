# Recuperação da associação BSD — 25/11/2026

**NO-GO para integração imediata.** A amostra autenticada confirma 12 vínculos BSD no coletor. Seis passaram a verificação do primeiro sinal prospetivo e todos concordaram com o Highlightly já registado. O tratamento prospetivo de versões simultâneas contraditórias permanece em aberto. [Evidência autenticada](authenticated-evidence.json)

## Cronologia e causa-raiz

| Observação | Hora de Lisboa | Evidência |
|---|---|---|
| Último novo resultado BSD, AC Milan–Frosinone, ID 210167 | 22/11 19:40:36 | [Arquivo](https://github.com/nunovinhas-creator/goal-roi/commit/3f32344f672476fc8d783a8be326d85ba0b1d139) |
| Primeiro ciclo da sequência sem novos resultados | 22/11 20:30:51 | [Arquivo](https://github.com/nunovinhas-creator/goal-roi/commit/cf59434c) |
| Última execução anterior à alteração, 50 alvos, 430 eventos, 5 pedidos e 0 novos | 23/11 11:03:47 | [Arquivo](https://github.com/nunovinhas-creator/goal-roi/commit/2cad43976da21983062b9a7a9ed3f119bee2843b) |
| Integração do filtro de competição e de N03 na `main` | 23/11 11:06:48 | [PR #73](https://github.com/nunovinhas-creator/goal-roi/pull/73) · [Merge](https://github.com/nunovinhas-creator/goal-roi/commit/7014a0a7331aa4b50ee0f9d96756d37186af9f90) |
| Primeira execução posterior, também 50 alvos, 430 eventos, 5 pedidos e 0 novos | 23/11 12:05:39 | [Arquivo](https://github.com/nunovinhas-creator/goal-roi/commit/25ec46d69c98dbfc5774ac0379088b9c429ffeb2) |
| Primeira rejeição documentada por falta de competição, Lecco–Lumezzane | 23/11 21:58:50 | [Arquivo](https://github.com/nunovinhas-creator/goal-roi/commit/58cd373ecb07df58f04afb0417cde6ac2c9ed6f2) |
| Diagnóstico com 67 alvos, 232 eventos, 4 pedidos, 0 novos e 48 guardados | 25/11 09:12:37 | [Arquivo](https://github.com/nunovinhas-creator/goal-roi/blob/9f9f61c77538fb06ae4d2505b4ae9ff133e410b8/data/results_bsd.json) · [Execução](https://github.com/nunovinhas-creator/goal-roi/actions/runs/45583114748/job/139097855958) |

A PR #73 introduziu `competition_ok()` no caminho de recolha BSD. O filtro interpretava `league` ou `competition` como nome. Em v2, o identificador da competição é `league_id`. `pending_targets()` fornece os metadados do jogo-alvo e nenhum ID BSD previamente verificado. Sem nome interpretado nem ID prévio, os 17 diagnósticos reproduzem a rejeição. A PR #95 ajustou a compatibilidade dos nomes e estendeu a procura a jogos prospetivos, mantendo N03. As primeiras recolhas com zero antecedem a PR #73 e a causa desses zeros fica indeterminada. [PR #95](https://github.com/nunovinhas-creator/goal-roi/pull/95) · [B1/B4 original](https://github.com/nunovinhas-creator/goal-roi/blob/a740c5c46bef088e25409b71be2a14a8bff206ad/audits/prospective/README.md)

## Contrato do fornecedor e correção

- A listagem `/api/v2/events/` e o detalhe `/api/v2/events/{id}/` usam `EventDetailV2Schema`. O campo de competição é `league_id`, inteiro ou nulo quando o evento está sem associação. O contrato desses endpoints omite `league`, `competition` e `league_name`. A lista live v2 usa `league_id` e `league_name`. A v1 representa `league` num objeto aninhado com `id`, `name` e país. O catálogo v2 `/api/v2/leagues/` e `/api/v2/leagues/{id}/` fornece o nome, o país e a categoria, incluindo `is_women`. [OpenAPI](https://sports.bzzoiro.com/api/schema/) · [Eventos](https://sports.bzzoiro.com/docs/football/events/) · [Catálogo](https://sports.bzzoiro.com/docs/football/leagues/)
- As 17 páginas públicas do mesmo fornecedor ligam os IDs dos eventos a competições identificadas. As nove competições foram verificadas nas páginas individuais do catálogo. A sondagem autenticada posterior encontrou `league_id` em 232/232 eventos e como inteiro em 17/17 candidatos. Nenhum dos 232 tinha `league`, `competition` ou `league_name`. O detalhe 655441 concordou com a lista. O arquivo original guarda o ID do evento, a hora e `league: null` depois da interpretação do coletor. O corpo da resposta original está ausente, pelo que os cinco pedidos replicam o corte com a resposta atual. [Amostra pública](public-evidence.json) · [Sondagem autenticada](authenticated-evidence.json)
- Para novos vínculos v2, o coletor compara um `league_id` observado com o catálogo local de nove IDs. As grafias aceites do alvo são revistas, incluindo país nas designações domésticas ambíguas. O resultado guarda o ID BSD do jogo, o ID e o nome da liga BSD, o campo de origem, os links oficiais e o método de associação. O relatório verifica novamente essa prova contra o alvo. A competição declarada no sinal serve para comparação.
- O novo vínculo exige casa e fora exatas após normalização ou alias revisto, início explícito dentro de ±15 minutos e candidato único. Ficam pendentes IDs de liga desconhecidos, identidades sobrepostas, respostas contraditórias no primeiro vínculo e lotes truncados pelo limite de páginas. Placar final aos 90 minutos é obrigatório. Os controlos públicos distinguem fim normal, prolongamento e penáltis. [Prolongamento](https://sports.bzzoiro.com/matches/604464/) · [Penáltis](https://sports.bzzoiro.com/matches/602350/)
- `near_misses` e os 48 resultados legados ficam como evidência histórica. Os IDs desses registos carecem de prova independente para efeito de N03. A proteção contra colisões usa um ID histórico apenas para impedir uma nova ocupação por outro jogo. Um vínculo novo verificável pode confirmar uma observação posterior do mesmo ID na ausência de metadados de liga, com equipas e hora iguais e sem liga contraditória.

## Antes e depois

| Medida | Antes | Replay público BSD | Lista autenticada BSD |
|---|---:|---:|---:|
| Vínculos BSD entre os 17 diagnósticos | 0/17 | 12/17 | 12/17 |
| Vínculos BSD entre os candidatos ainda na fila do corte de 25/11 | 0/16 | 12/16 | 12/16 |
| Vínculos aceites contra o primeiro sinal prospetivo congelado | 0/13 | 6/13 | 6/13 |
| Primeira confirmação independente adicional na coorte | 0 | 0 | 0 |
| Divergências BSD–Highlightly nos vínculos prospetivos aceites | 0 | 0 | 0 |
| Novas liquidações ou entradas de produção nesta auditoria | 0 | 0 | 0 |

O replay público usa os 17 diagnósticos retidos e metadados observados depois em páginas BSD. A verificação autenticada repetiu as quatro páginas, sem paginação adicional, e avaliou a unicidade entre os 232 eventos devolvidos. Os 17 IDs apareceram uma vez cada e coincidiram em competição, hora e placar com as páginas públicas. Os 12 vínculos são recuperáveis no corte histórico isolado. A janela normal de três dias continua a determinar o que está elegível numa execução corrente. O `near_misses` acumula observações. Lecco–Lumezzane aparece no corte original de 23/11 e está fora da fila pendente do corte de 25/11.

Quatro dos 17 carecem de hora de início explícita no alvo do coletor. Lecco–Lumezzane tem diferença de 15 minutos e competição genérica `Italian Serie C`, insuficiente para provar `Serie C, Girone A`. Sete dos 13 primeiros sinais prospetivos carecem de hora explícita. Doze jogos prospetivos já tinham resultado Highlightly. América de Cali–Deportivo Cali é o único sem esse resultado e fica pendente pela ausência de hora explícita no sinal. [Comparação por jogo](replay-results.json) · [Protocolo congelado](https://github.com/nunovinhas-creator/goal-roi/blob/9f9f61c77538fb06ae4d2505b4ae9ff133e410b8/scangoal/prospective_protocol.json)

```sh
git worktree add --detach /tmp/goal-roi-bsd-nov25 9f9f61c77538fb06ae4d2505b4ae9ff133e410b8
git worktree add --detach /tmp/goal-roi-bsd-lecco 58cd373ecb07df58f04afb0417cde6ac2c9ed6f2
python3 audits/bsd-settlement/replay.py /tmp/goal-roi-bsd-nov25 /tmp/goal-roi-bsd-lecco
```

O replay verifica os hashes das fontes históricas, reexecuta o filtro anterior, usa a evidência pública no novo caminho e verifica o arquivo temporário em memória e o primeiro sinal congelado. A recolha de evidência pública guardou campos mínimos, URLs, datas e hashes dos corpos dos documentos. O arquivo de produção, o diário append-only e o protocolo foram consultados em leitura.

## Sondagem autorizada e testes

O GitHub Actions disponibilizou `BSD_API_KEY` apenas no passo de leitura de um workflow próprio, numa branch temporária. O job exigia a criação do ref e a primeira tentativa, com permissões GitHub de leitura. O cliente recusou redirecionamentos e encerrou após quatro páginas e um detalhe. As cinco respostas foram HTTP 200 entre 10:07:10 e 10:07:15. O log conservou hashes e resultados derivados. O valor do segredo, os cabeçalhos completos e os corpos das respostas permaneceram fora do log e da PR. A sondagem usa um workflow separado, que fica fora do diff desta PR. [Workflow isolado](https://github.com/nunovinhas-creator/goal-roi/blob/9bf689d25070c5f859f4cf280be5221648ee1ac0/.github/workflows/bsd-audit-probe.yml) · [Job](https://github.com/nunovinhas-creator/goal-roi/actions/runs/45591800573/job/139127617269)

A CI do candidato passou **1.107 testes Python 3.11 com Chromium**, incluindo 16 adversariais novos, e **12 testes Node**. A baseline passou 1.091 testes Python e 12 Node. [Suite integral](https://github.com/nunovinhas-creator/goal-roi/actions/runs/45591346532)

## Continuidade do registo prospetivo

Na ativação, `code_sha256` registou os cinco ficheiros de implementação, incluindo `results_bsd.py`. A barreira de gravação compara o SHA-256 do ficheiro de protocolo com o valor da ativação. Mudanças no manifesto de código são registadas num `code_version` antes de qualquer nova inscrição. O candidato alterou apenas `results_bsd.py` no manifesto. O SHA-256 do protocolo `4ab189247d0e4b83f17ffd7931bf772c73bad37b2b7f270f2855a157f1731101` coincidiu com o da ativação. [Gravador e manifesto](https://github.com/nunovinhas-creator/goal-roi/blob/11ca75fc7ed6f9ff30b77f4883e2253722823aed/scangoal/prospective.py)

- O ensaio de aplicação e reversão reproduziu uma única entrada `code_version` por mudança de versão. A repetição de cada corte acrescentou zero entradas, todas as cadeias foram verificadas e as 149 entradas originais ficaram preservadas byte a byte. Um controlo que alterou o protocolo numa cópia temporária foi bloqueado antes de gravar. [Manifesto e hashes das etapas](ledger/evidence.json)
- O ensaio de continuidade usou o corte real a seguir, executando `prospective.record()` e o pipeline de sinais sobre cópias, com rede bloqueada. A baseline reproduziu o diário publicado byte a byte. O candidato preservou as 149 entradas e acrescentou `code_version` seguido dos mesmos dois sinais de Gamba Osaka–Port FC. Os campos dos sinais coincidiram, salvo os de encadeamento. A repetição acrescentou zero entradas e não houve novos resultados ou conflitos. [Corte com os dois sinais](https://github.com/nunovinhas-creator/goal-roi/commit/1429777178a0b8a6d7be3fa50e9ed0aca06f5a30) · [Prova da inscrição](ledger/registration-evidence.json)

A transição usa a versão já prevista no diário. Aplicar o commit cria uma versão e revertê-lo regista o manifesto original como nova entrada. Este fluxo preserva a cadeia, a população inscrita e a evidência acumulada. O ensaio mediu continuidade do registo. A certeza científica de cada nova liquidação continua a depender da sua própria prova.

```sh
git worktree add --detach /tmp/goal-roi-bsd-base 11ca75fc7ed6f9ff30b77f4883e2253722823aed
git worktree add --detach /tmp/goal-roi-bsd-candidate f597717bcb253dc80f80f5276ecc52ed06929e00
python3 audits/bsd-settlement/ledger/replay.py . 11ca75fc7ed6f9ff30b77f4883e2253722823aed f597717bcb253dc80f80f5276ecc52ed06929e00
python3 audits/bsd-settlement/ledger/registration.py . /tmp/goal-roi-bsd-base /tmp/goal-roi-bsd-candidate 1429777178a0b8a6d7be3fa50e9ed0aca06f5a30
```

## Ativação, reversão e riscos residuais

1. Usar o replay fixado, o registo dos cinco pedidos e a suite executada para rever o catálogo, a unicidade e a rejeição de períodos fora dos 90 minutos. Resolver a propagação prospetiva de versões simultâneas contraditórias antes da aprovação manual.
2. A recolha normal tem uma janela de três dias. O patch usa as mesmas páginas, limite de pedidos e intervalo mínimo, com zero pedidos adicionais de catálogo ou detalhe. O diário regista a versão de código pela regra já em vigor e só pode anexar novas observações à hora em que sejam verificadas.
3. Nos primeiros ciclos normais, conferir contagens, provas e conflitos. Se houver sinais de associação incorreta, suspender a recolha BSD por intervenção operacional revista e reverter o commit da aplicação. Preservar as observações e entradas já anexadas. Rever esses casos pelo mecanismo de conflito do protocolo, mantendo a cadeia verificável.

IDs desconhecidos e jogos sem início explícito ficam pendentes. A cobertura depende da estabilidade do catálogo BSD e da revisão das grafias. O identificador da liga pode aparecer nulo. Um `league_id` incorreto na fonte pode exigir verificação do detalhe. Num controlo sintético com um resultado já registado, dois marcadores distintos observados no mesmo instante ficaram no arquivo de versões e foram omitidos do snapshot. O diário conservou o resultado inicial sem acrescentar `outcome_conflict`, e o jogo continuou na contagem de H1. Cada um dos 17 candidatos autenticados apareceu uma só vez. Resolver a passagem dessas observações para o mecanismo de conflito congelado é condição de ativação.

## Nota candidata para o Goal-ROI Brain

Atualizar, após revisão humana, a observação do Brain sobre a ausência de liga. O nome está ausente no contrato da lista e do detalhe v2. A identidade de liga está disponível pelo `league_id` quando atribuído. A coorte de 17 diagnósticos mede cobertura operacional da amostra. Os seis vínculos prospetivos aceites são concordância adicional sobre jogos já confirmados e deixam a contagem de jogos com resultado independente inalterada. [Relações científicas do Brain](https://github.com/nunovinhas-creator/goal-roi-brain/blob/main/Conhecimento/Rela%C3%A7%C3%B5es%20cient%C3%ADficas.md)

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
