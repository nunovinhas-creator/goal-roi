# Segunda revisão adversarial da PR #110

A branch foi reaplicada sobre `main` em `74b16bd2b95b068ba4504e51ecbf8edf6316be34`, revisão recolhida às 15:05:59 de Lisboa, em 26 de novembro de 2026. Os dados e a configuração de produção dessa revisão estão incluídos na branch. O comparativo com esse ponto de base contém apenas os ficheiros da funcionalidade, a sua documentação e a verificação da lista de workflows. A entrega permanece em draft, com zero notificações reais e zero alterações a variáveis. [PR #110](https://github.com/nunovinhas-creator/goal-roi/pull/110)

## Latência medida e alternativa

A amostra imutável usou a revisão `6d27d6794784ad78183d3920e2bc48ae9c3d85c4` do arquivo, SHA-256 `17d78b45ba7c21ae05f5cf83350b8581b65877ab2225f8abeb1dc49e65f7ccbd`. Incluiu 53 sinais únicos, 50 recebidos durante as leituras rápidas e três durante a leitura integral. Por ID Telegram, foi associada a receção arquivada à janela do passo de leitura, ao primeiro commit observável do arquivo e ao fim do primeiro job do coletor que publicou a linha. A duração do job foi calculada para as 32 execuções distintas associadas à publicação. Os 53 pares e respetivas fontes estão no ficheiro de evidência. [CSV por sinal](dropping-odds-latency.csv)

| Segmento | Mediana | P95 | Máximo |
| --- | ---: | ---: | ---: |
| Duração do coletor, 32 jobs | 13 min 29 s | 15 min 07 s | 15 min 07 s |
| Mensagem → receção, 53 sinais | 1 min 54 s | 6 min 16 s | 8 min 19 s |
| Receção → primeiro commit, 53 sinais | 2 min 20 s | 66 min 57 s | 70 min 56 s |
| Primeiro commit → fim do coletor, 53 sinais | 3 min 02 s | 4 min 24 s | 10 min 05 s |
| Receção → fim do coletor, 53 sinais | 6 min 04 s | 69 min 59 s | 73 min 58 s |
| Mensagem → fim do coletor, 53 sinais | 9 min 56 s | 71 min 02 s | 79 min 42 s |

Os percentis usam o posto mais próximo. Os commits têm resolução de segundos e aproximam a primeira visibilidade do arquivo; num caso ficaram cerca de 0,1 s antes da receção precisa. O replay usa o maior dos dois instantes. O fim do coletor é o primeiro instante possível para o evento `workflow_run completed`. O valor real de avaliação também inclui fila do runner, checkout e processamento, que só poderão ser medidos em sombra alojada.

A mediana de receção até fim do coletor foi 6 min 04 s nas leituras rápidas e 14 min 10 s nas três leituras integrais. A cadência de dois minutos é uma oportunidade de leitura. A publicação depende da renovação do relatório e do passo de publicação final. Cinco sinais provenientes de três execuções só aparecem juntos no commit `142d613d0b`, numa execução posterior. Isso explica a cauda de cerca de uma hora nesta amostra. A sondagem de Git só consegue agir depois da publicação. [Execução de leitura](https://github.com/nunovinhas-creator/goal-roi/actions/runs/45709947801), [execução de publicação](https://github.com/nunovinhas-creator/goal-roi/actions/runs/45719106840)

Uma alternativa de menor latência é um job próprio disparado por `workflow_run in_progress` que confira a revisão de `main` a cada 60 s e processe apenas versões publicadas do arquivo, com `completed` como recuperação de fila ou falta de cobertura. Continua a usar a sessão Telegram existente por via do arquivo. Não requer passos novos no coletor, locks comuns, serviços externos ou outro destino. O custo de implementação seria um runner standard por todo o intervalo de sondagem, com até 18 consultas de revisão por execução de 18 minutos, e obtenção do arquivo apenas quando o blob mudar. Para o repositório público, o runner standard é gratuito. Um gatilho `push` não oferece a mesma cobertura, porque os pushes feitos pelo `GITHUB_TOKEN` do coletor não desencadeiam novas execuções. A proposta de sondagem ficou em estudo. [Eventos GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run), [Gatilhos do GITHUB_TOKEN](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow), [Custo GitHub Actions](https://docs.github.com/en/billing/concepts/product-billing/github-actions)

| Relógio de avaliação no replay | Sem limite | 5 min | 10 min | 15 min |
| --- | ---: | ---: | ---: | ---: |
| Receção efetiva | 15 | 13 | 15 | 15 |
| Primeiro commit observável | 15 | 7 | 14 | 15 |
| Primeiro commit + 60 s, sondagem modelada | 15 | 5 | 14 | 15 |
| Fim do coletor, sem fila adicional | 15 | 4 | 9 | 15 |
| Fim do coletor + 60 s | 15 | 4 | 7 | 14 |
| Fim do coletor + 120 s | 15 | 3 | 7 | 14 |

São cenários offline da mesma amostra. A alternativa de 60 s recuperaria cinco sinais na janela de 10 minutos face ao limite inferior de `completed`, assumindo o runner pronto e disponibilidade a partir do commit. O teste também mostra que 15 minutos pode perder sinais com fila adicional. Nenhum limite recebe aprovação por estas contagens.

Em sombra, cada decisão guarda o relógio de avaliação, o instante original, a primeira receção, os três intervalos em milissegundos e o resultado de cada cenário com o respetivo histórico de deduplicação. Um sinal mais recente pode ser elegível num cenário curto se o primeiro tiver expirado nesse cenário. O job identifica o coletor originador com `DROPPING_ODDS_COLLECTOR_RUN`. A futura medição alojada pode juntar a hora efetiva de avaliação ao fim do coletor e separar fila e trabalho do módulo.

Para repetir o cenário do fim do coletor, usar o CSV e o arquivo exato da amostra.

```bash
git show 6d27d6794784ad78183d3920e2bc48ae9c3d85c4:data/messages.jsonl > /tmp/goal-roi-dropping-review.jsonl
sha256sum /tmp/goal-roi-dropping-review.jsonl
python - <<'PY'
import csv
import json
from pathlib import Path

with Path('docs/dropping-odds-latency.csv').open() as file:
    times = {row['message_id']: row['job_completed_utc'] for row in csv.DictReader(file)}
Path('/tmp/goal-roi-dropping-evaluation.json').write_text(json.dumps(times))
PY
python -m scangoal.dropping_odds_alerts replay /tmp/goal-roi-dropping-review.jsonl --evaluation-times /tmp/goal-roi-dropping-evaluation.json
```

## Registo e reprocessamento

- Duas referências independentes das operações de `main` apontam para o mesmo commit. A leitura exige ambas e igualdade de SHA. A publicação atualiza ambas numa operação Git atómica com leases explícitos. Cada commit tem um só pai e deve conservar a origem e os bytes do registo anterior como prefixo. O job regista o SHA verificado na linha `DROPPING_ODDS_CHECKPOINT`.
- Os testes com remoto Git local reproduzem eliminação unilateral, regressão unilateral para um commit íntegro, restauro de uma árvore antiga num novo commit, rejeição de metade da atualização e duas execuções concorrentes. Nesses casos, a proteção impede o envio simulado indevido. O `git push --dry-run --atomic` ao GitHub confirmou a negociação atómica com a credencial de desenvolvimento, sem criar referências. O token do job ainda exige verificação em sombra.
- Um restauro coordenado das duas referências para o mesmo commit antigo válido pode enganar um runner efémero sem um SHA mais recente. Um teste demonstra duas chamadas ao transporte simulado nessa condição. O runner que tinha o checkpoint mais recente deteta a regressão. Um SHA conhecido, retido num registo independente das referências, deve ser comparado com o tip atual antes de qualquer restauro. Usar `git merge-base --is-ancestor <sha-verificado> <tip>` numa cópia com o histórico necessário. Para envio efetivo, cada intenção teria também de ser confirmada numa testemunha monotónica fora das duas referências antes do pedido ao Telegram. Um job pode ser interrompido depois da publicação Git e antes de emitir o SHA no log.
- A ausência das duas referências suspende uma execução normal. Inicializar por engano após eliminação coordenada pode perder o histórico. O procedimento de recuperação é manter o modo desligado, recuperar o último SHA confirmado nos logs do job e avançar ou restaurar as duas referências sob revisão. Os logs têm retenção finita e devem ser registados fora das duas referências para verificação posterior.

## Identidade e edição

A identidade usa competição, o par não ordenado de nomes, o nome exato da equipa selecionada e a linha. Unicode, caixa e espaços de formatação são normalizados. Abreviações, transliterações e mudanças de acentos ou pontuação não recebem equivalência automática. A seleção pelo nome evita trocar o lado quando a ordem das equipas se inverte.

Uma repetição exige a mesma identidade, um mesmo `go=` ou o par de odds exato, e até dois minutos do primeiro início estabelecido. Uma repetição pode acrescentar outro `go=` ao mesmo grupo. O instante de referência mantém-se fixo para impedir que sucessivas mudanças de dois minutos alarguem a janela. Um `go=` já associado à mesma identidade e fora da janela gera `conflicting_kickoff`. Havendo duas correspondências exatas possíveis, o caso fica ambíguo. Um ID reutilizado em identidades diferentes não funde jogos. Uma alteração real do nome, um jogo remarcado que receba outro ID ou uma nova cotação sem identificador comum podem continuar indistinguíveis de um sinal novo.

Edições substantivas visíveis antes da decisão tornam o sinal ambíguo. Versões que chegam depois ficam registadas por digest como `late_versions`, conservando estado terminal, referência do jogo e histórico. O replay histórico desta amostra não continha edições dos sinais e os casos de edição foram exercitados com dados sintéticos.

## Invariância e testes

O comparativo estático da branch com a base de produção confirma igualdade nos ficheiros do coletor, parser, alertas, Brain V5, relatório, simulador e registo prospetivo. Inclui a preservação dos bytes do arquivo de produção incorporados pela atualização.

A comparação offline do comportamento usou as mesmas entradas congeladas de `cea52253aa67b6b4dddebe1bf7a352904cdbf200` e o relógio fixo em 26 de novembro de 2026 às 15:00:30 de Lisboa. Os seguintes SHA-256 foram iguais em ambas as execuções.

| Resultado | SHA-256 |
| --- | --- |
| Relatório | `edbe6754f6a9a29cfddd773e34d3d636caed784280bd9a449590433ab555747a` |
| Simulador | `d8b27c3dd336d3bf8b90f95b13212f836f4034ef068246428d5b0600d429d5f5` |
| Alertas gerais, pré-visualização e estado simulado | `37be82502ea2bd3863d15db2f10aa8ffd2a769c0aa5ce1b14314c6e7491b65ae` |
| Seleções Stake nesse instante | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` |
| Resumo prospetivo | `fe97c829c1ad3003f825583c98406b74d2e209b2b878a90c98a0bd9ccb143840` |
| Registo prospetivo, 450 entradas verificadas | `35cd033d198402a887f037ab91684d094c2e028ed19e48c3794dde2568022034` |

Os 37 testes específicos passaram localmente, incluindo os dez casos novos, com transporte Telegram simulado. Os 12 testes Node de continuidade passaram. A suite completa do SHA final e os resultados da integração ficam ligados na PR.

## Veredito e operação

**GO para um ensaio exclusivamente em sombra após revisão e disponibilização em `main`.** Na primeira execução, verificar a criação das duas referências e guardar o SHA publicado. Usar a telemetria para medir fila, publicação, perda por limiar e falhas do registo. O workflow alojado só admite `shadow`, não recebe segredos Telegram e a entrada Python alojada recusa `on`. O transporte efetivo fica **NO-GO** e os três limites continuam experimentais.

Para desligar um ensaio autorizado, definir `off` e cancelar o job específico. Preservar ambas as referências e o último SHA confirmado. A validação desta PR usou testes locais, GitHub Actions em draft e consultas de leitura à recolha. Serviços adicionais contratados e execuções operacionais do módulo tiveram custo de 0 €.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
