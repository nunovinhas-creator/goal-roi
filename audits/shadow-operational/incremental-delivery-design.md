# Goal ROI — Entrega incremental de sinais SHADOW (pré-instalação)

**ESTADO: protótipo isolado, não instalado, sem ligação a Telegram ou ao coletor produtivo.**

## Problema a resolver

Em 17/11/2026, o ensaio GitHub Actions SHADOW isolated preflight #1 verificou que 14 em 14 mensagens OPEN_GAME tiveram primeira recolha dentro de 120 segundos. Ver https://github.com/nunovinhas-creator/goal-roi/actions/runs/44200210820

Isto não mede o atraso posterior até à publicação no Git remoto nem à observação por um runner separado. O atual Collect ScanGoal relê de dois em dois minutos, mas normalmente só envia o resultado da fase rápida ao Git no final do ciclo, pelo que não garante 120 segundos para um processo independente.

## Implementação isolada nesta PR

O módulo scangoal/shadow_delivery.py NÃO é importado por workflows nem pelo coletor; não lê Telegram e não envia notificações.

- Fonte autónoma: a instalar apenas com autorização específica, separada da sessão Telegram do coletor. A origem registada no protótipo não substitui autenticação.
- Fila append-only: apenas mensagens OPEN_GAME, original e edições por ID e hash do texto, sem guardar o texto, relógios UTC, encadeamento SHA-256, fsync e substituição atómica.
- Publicação Git: num checkout próprio e num remoto Git bare local, no ramo shadow/signal-delivery. Primeiro commit/push/ls-remote, depois recibo durável com segunda publicação e confirmação remota. Sem force-push, merge ou rebase.
- Recuperação: exportação ZIP com manifesto SHA-256 e bundle Git; restauração num checkout novo com validação dos hashes, correspondência exata entre o diário e o bundle incremental e verificação da base remota. Falha em caso de divergência, sem reescrever o histórico.
- Consumidor: checkout independente verifica que o HEAD local corresponde ao remoto e que não há ficheiros locais por publicar; calcula emissão → observação com um relógio de ensaio fornecido pelo chamador. A admissão científica permanece desativada.

## Provas técnicas

A suite tests/test_shadow_delivery.py usa exclusivamente Git bare local, fixtures sintéticas e relógios controlados. Cobre duplicados, edições, atrasos, adulterações, erro de relógio, falhas no push de dados/recibos, recuperação de outbox em runner novo, conflito com remoto avançado e observação no consumidor.

O teste do pré-ensaio em dois jobs GitHub Actions, já concluído na mesma PR, comprovou o transporte de um outbox sintético entre runners. A solução proposta NÃO mantém novos workflows, porque os testes existentes proíbem alterações dessa superfície.

## Bloqueadores P1 antes de instalar

1. Escolher e autorizar um processo de recolha autónomo que receba sinais vivos sem partilhar sessão Telegram, concorrência ou mecanismo de avisos do coletor.
2. Medir realmente sinal → recolha → push Git → recibo remoto → observação no runner SHADOW, incluindo falhas e percentis. Um agendador GitHub Actions não oferece garantia de arranque a cada evento em segundos.
3. Garantir a persistência do último outbox em armazenamento independente e o seu restauro obrigatório entre runners; um ZIP que fique no disco efémero não é suficiente.
4. Integrar os sinais no consumidor SHADOW juntamente com um snapshot coerente de resultados e a verificação prévia de FT, sem look-ahead e sem alterar as regras H1/H2.
5. Verificar durante um piloto autorizado o custo gratuito, limites de retenção, segurança, permissões, tolerância a falhas e liveness.

## Limites e estado atual

- As flags de teste de origem sintética não provam proveniência autenticada de mensagens reais.
- Uma latência positiva no consumidor não equivale a elegibilidade científica: falta comprovar que o resultado ainda não era conhecido.
- ZERO serviços pagos, alteração ao workflow Collect ScanGoal, Telegram, secrets, data, docs, relatório ou certificação.
- SHADOW permanece prepared_not_installed, authorized=false, installed=false e com zero observações prospetivas.
- A PR deve continuar Draft, sem merge nem ativação.

**Parecer: ponte incremental prototipada para auditoria; não autorizar ainda os 30 dias científicos.**


## Reforços de segurança e medição — continuação

### Proteção da privacidade

A ponte recusa origens diferentes de `synthetic`. Como o chamador controla essa etiqueta, o texto é usado apenas em memória para classificar a mensagem e calcular o hash. O diário local conserva ID, hash e metadados temporais. A publicação aceita exclusivamente um remoto Git bare local e exige que os endereços de leitura e escrita apontem para o mesmo repositório. Relatórios de auditoria omitem texto, IDs de mensagens e hashes de eventos.

Os testes verificam que a tentativa de ingestão com origem real termina em erro e que o ledger permanece vazio.

### Medição histórica sem copiar mensagens

`scripts/shadow_commit_latency.py` lê um **clone Git local com histórico completo**, pesquisa a primeira presença de cada versão OPEN_GAME em `data/messages.jsonl` e mede apenas agregados:

- atraso emissão → primeira leitura pelo coletor
- atraso emissão → timestamp do primeiro commit do percurso first-parent do HEAD fixado que contém a mensagem e comprova a versão avaliada
- atraso primeira recolha → primeiro commit, percentis 50 e 95
- dados ausentes, relógios inconsistentes e commits que excedem os 120 s

Não escreve IDs, textos, URLs dos jogos, identificadores de mensagens, hashes de conteúdo ou nomes de equipas no relatório. Rejeita clones superficiais e ficheiros fonte não commitados. O comando exige um destino novo para não reescrever relatórios anteriores.

Exemplo para um clone com histórico completo e ficheiros locais de dados:

```bash
python scripts/shadow_commit_latency.py --repo . --hours 24 --output /tmp/shadow-git-latency-new.json
```

**Limite de prova.** A pesquisa percorre o histórico por ordem topológica, sem cortar com datas de commit. A amostra parte das mensagens presentes no HEAD fixado. Edições, substituição de versões e cronologias incompatíveis ficam sem confirmação. O timestamp Git serve como limite inferior apenas para o percurso estudado e sob a hipótese de relógios corretos. O push remoto e a observação pelo segundo runner continuam por medir.

### Bloqueadores que permanecem

Não existe fonte Telegram independente instalada, nem persistência de artefactos reais em armazenamento externo garantido, nem integração dos dados entregues com os filtros de resultados prévios da ciência SHADOW.

**Não ativar os 30 dias. Certificação: `not_activated`.**

## Piloto sintético de ponta a ponta — 18/11/2026

Foi acrescentado `scripts/shadow_synthetic_pilot.py` e a suite `tests/test_shadow_synthetic_pilot.py`. O ensaio cria exclusivamente um remoto Git bare temporário, emite uma mensagem OPEN_GAME sintética, injeta uma falha no primeiro push, exporta o outbox, restaura-o noutro checkout, publica dados e recibo e verifica-os num terceiro checkout consumidor.

O ensaio compara uma observação simulada aos 45 segundos (entrega dentro de 120 s) com outra aos 150 segundos (entrega tardia). Os tempos são controlados por fixtures, não por um serviço real; não demonstram SLA, disponibilidade, cobertura ou acesso Telegram. Os relatórios contêm apenas agregados e não guardam textos, IDs nem URLs.

Execução offline (destino de saída novo):

```bash
python -m unittest discover -s tests -p test_shadow_synthetic_pilot.py -v
python -m scripts.shadow_synthetic_pilot --observe-after-seconds 45 --output /tmp/shadow-synthetic-pilot-NEW.json
```

**Barreira mantida:** não há agendador autónomo nem armazenamento persistente real entre runners; o módulo continua a recusar a origem `dedicated-readonly-source`. A ligação a mensagens reais e a ativação científica exigem revisão e autorização separadas.

## Verificações de recuperação

O outbox da ponte usa a versão 2. A exportação cria um commit local quando há dados pendentes e inclui só os objetos posteriores ao HEAD remoto. No restauro, o recetor verifica o manifesto, os hashes, a cadeia append-only, os commits referidos pelos recibos e a correspondência do diário com o bundle antes de avançar o checkout. O mesmo ZIP pode voltar a ser aplicado ao checkout restaurado enquanto a base remota se mantiver. Uma base que avance exige quarentena. Artefactos v1 ficam incompatíveis porque podiam conter texto e divergir do bundle.

As verificações SHA-256 internas detetam corrupção. A autenticidade do ZIP depende da cadeia de custódia externa e da comparação com um digest obtido por um canal de confiança. A prova dos dois jobs do pré-ensaio abrange o outbox do publicador operacional anterior em modo sintético. Os testes da ponte incremental usam novos checkouts e um Git bare local.
