# GOAL ROI — preflight isolado do SHADOW (não instalado)

## Objetivo

Validar as condições necessárias à instalação futura de H1/H2 sem modificar a recolha produtiva, nem ativar os 30 dias.

Este ensaio **não equivale a ativação operacional** e não comprova a cobertura prospetiva real.

### Conteúdo desta PR

- \`scripts/shadow_preflight.py measure\`: lê, sem rede, \`data/messages.jsonl\` e mede a demora da primeira observação de mensagens OPEN_GAME pelo coletor. Conta versões por ID, erros de relógio e atrasos superiores a 120 segundos. Só mede a latência **sinal -> primeira recolha**.
- \`scripts/shadow_preflight.py stage/restore\`: reproduz, apenas com sinais sintéticos, uma falha de push, exporta o outbox e restaura os mesmos eventos num checkout novo. Verifica um único sinal e um único recibo, com Git bare local.
- \`.github/workflows/shadow-preflight.yml\`: workflow dedicado, sem agendamento nem acesso a secrets, com concorrência independente. A primeira job publica um artefacto de teste (retido 1 dia); a segunda job, num runner novo, transfere e restaura o outbox sintético.
- \`tests/test_shadow_preflight.py\`: testes de deduplicação, janelas temporais, fail-closed, artefacto e integridade.

## Limitações explícitas

1. O primeiro avistamento pelo coletor é o limite inferior da possibilidade de captura. **O código não mede** o instante em que o commit entrou no Git remoto, nem a hora em que o runner independente recebeu o snapshot. Sem estes dois atrasos, não se pode garantir captura em 120 segundos.
2. O ensaio de outbox entre jobs usa um **remoto local sintético e um relógio de fixture**. Prova a transferência entre runners da Actions e integridade do formato, mas não prova recuperação de falha numa publicação real nem resistência a uma indisponibilidade de GitHub.
3. A execução do workflow usa **apenas permissões contents:read**; não recebe segredos, não consulta APIs de resultados e não envia mensagens.
4. \`shadow_ops.activate\` não é invocado com dados reais. A certificação científica fica \`not_activated\` e o ledger de produção não é tocado.
5. Não se modifica o workflow \`Collect ScanGoal\`, o encadeamento de execuções, \`reader.py\`, \`watch.py\`, \`notify.py\`, os relatórios, \`data/\` ou \`docs/\`.

## Critério de conclusão

Não declarar SHADOW pronto para 30 dias enquanto não existir prova do atraso **sinal -> Git remoto -> observação no runner dedicado**, permissões de publicação no ramo SHADOW, restauro obrigatoriamente válido do último outbox real e ensaio operacional de falhas/liveness sem interferência com o coletor.

Os resultados desta PR só podem ser **PREFLIGHT TESTADO** ou **INCONCLUSIVO / NÃO INSTALADO**, nunca «SHADOW ativado».

## Operação manual (opcional, apenas após CI)

No GitHub Actions, executar \`SHADOW isolated preflight\` através de \`workflow_dispatch\`. O workflow não modifica a main nem credenciais. O trigger \`pull_request\` executa o mesmo ensaio automaticamente para revisão desta PR, quando o GitHub permitir workflows da branch em PR.

Após testes verdes, rever métricas e cobertura real antes de aprovar qualquer alteração de produção.
