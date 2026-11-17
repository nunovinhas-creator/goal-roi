# Goal ROI — Entrega incremental de sinais SHADOW (pré-instalação)

**ESTADO: protótipo isolado, não instalado, sem ligação a Telegram ou ao coletor produtivo.**

## Problema a resolver

Em 17/11/2026, o ensaio GitHub Actions SHADOW isolated preflight #1 verificou que 14 em 14 mensagens OPEN_GAME tiveram primeira recolha dentro de 120 segundos. Ver https://github.com/nunovinhas-creator/goal-roi/actions/runs/44200210820

Isto não mede o atraso posterior até à publicação no Git remoto nem à observação por um runner separado. O atual Collect ScanGoal relê de dois em dois minutos, mas normalmente só envia o resultado da fase rápida ao Git no final do ciclo, pelo que não garante 120 segundos para um processo independente.

## Implementação isolada nesta PR

O módulo scangoal/shadow_delivery.py NÃO é importado por workflows nem pelo coletor; não lê Telegram e não envia notificações.

- Fonte autónoma: a instalar apenas com autorização específica, separada da sessão Telegram do coletor. A origem registada no protótipo não substitui autenticação.
- Fila append-only: apenas mensagens OPEN_GAME, original e edições por ID e hash do texto, relógios UTC, encadeamento SHA-256, fsync e substituição atómica.
- Publicação Git: num checkout próprio, no ramo shadow/signal-delivery; nunca na main. Primeiro commit/push/ls-remote, depois recibo durável com segunda publicação e confirmação remota. Sem force-push, merge ou rebase.
- Recuperação: exportação ZIP com manifesto SHA-256 e bundle Git; restauração num checkout novo com validação dos hashes, integridade do diário e base remota. Falha em caso de divergência, sem reescrever o histórico.
- Consumidor: checkout independente verifica que o HEAD local corresponde ao remoto e que não há ficheiros locais por publicar; mede emissão → receção nesse runner. Nunca declara aprovação científica.

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
