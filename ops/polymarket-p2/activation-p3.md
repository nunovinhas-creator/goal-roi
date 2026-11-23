# Ativação do observador

1. Integrar o código desligado apenas após revisão da PR, CI verde, confirmação de que a variável está ausente ou desligada e autorização expressa para o merge.
2. Só após uma segunda autorização expressa, definir a variável de repositório `GOAL_ROI_POLYMARKET_SHADOW` como `on`. Anotar UTC, SHA e primeiro run. O escritor único continua a ser o job de recolha da main.

Acompanhar três execuções consecutivas. Registar para cada run o SHA publicado, leitura principal e rápida, estado da recuperação e da execução seguinte, e entrega de cada aviso devido no diário Telegram. Confirmar eventos start/stop com o mesmo run_id e verificar CPU, RSS, pedidos, erros HTTP e circuito. Executar `python -m scripts.polymarket_p2_audit --since <início-UTC> --as-of <fim-UTC>` num checkout de leitura. Registar elegíveis, respostas em 120 s, preços frescos e respetiva cobertura. Sem elegíveis, a cobertura operacional fica por demonstrar.

Suspender se faltar leitura, publicação ou aviso devido, houver publicação pendente, faltar o stop, houver duplicação ou sinal histórico, uma cotação inválida contar como fresca, ou ocorrer 403/429, abertura do circuito, mais de 80 pedidos, 60 s de CPU ou 512 MiB de memória. Investigar cobertura zero com elegíveis antes de prosseguir.

Para reverter, remover a variável ou pô-la em off. Isto afeta as próximas execuções. Num runner em curso com acesso operacional autorizado, pedir a paragem cooperativa com `GOAL_ROI_POLYMARKET_SHADOW=on python -m scangoal.polymarket_observer stop --spool "$RUNNER_TEMP/goal-roi-polymarket"` e confirmar STOPPED True. Num runner hospedado sem terminal, desligar a variável e deixar terminar o job limitado. Evitar cancelar o escritor durante push, entrega Telegram ou preservação do artefacto. Em emergência que obrigue a cancelar o job inteiro, reconciliar o último SHA, os intents e qualquer artefacto pendente antes de considerar a continuidade recuperada. Preservar os diários.

Receção em 120 s antes do início, livro entre 0 e 300 s à receção, profundidade disponível para um montante e execução efetiva são quatro evidências distintas. O ensaio de rede só verifica a rota e o instante consultados. A entrega Telegram real e a publicação real com o observador ligado só podem ser verificadas após a segunda decisão.

RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.
