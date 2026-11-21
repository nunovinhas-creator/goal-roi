# Validação operacional após o merge da PR #49

Registar na PR o SHA integrado, hora UTC, IDs e ligações das execuções. Os testes com respostas simuladas são evidência de CI. Os passos seguintes exigem observação em produção.

## 1. Coletor e recuperação pelo GitHub

1. Após o merge autorizado, acompanhar duas recolhas consecutivas de main. Confirmar o sucesso de “Start the next collection”, identificar o sucessor e comparar os intervalos reais dos jobs. Podem coexistir pedidos em fila. Os jobs de recolha têm de executar em série.
2. Confirmar no histórico Git a continuidade de data/state.json, a preservação das linhas anteriores de messages.jsonl e a ausência de uma segunda entrega para uma chave já entregue no registo dos avisos. Registar apenas contagens e IDs operacionais. Para uma lacuna, verificar os limites e o cursor em collection_windows.json e as páginas de collection_backfill.
3. Numa conclusão normal, confirmar que “Restore collection chain” recebeu o evento e saltou o job restart. Para validar o caminho ativo, acompanhar a próxima falha ou timeout real elegível em main. Ligar o ID de origem ao evento workflow_run e ao job restart. Confirmar o despacho bem-sucedido e uma recolha posterior que gravou dados e lançou o sucessor. Cruzar as horas com outros disparos e classificar a atribuição como inconclusiva se existir outra origem possível.
4. Até existir uma ocorrência elegível, registar “recuperação real pendente”. Qualquer ensaio induzido em produção requer aprovação específica. Preservar a recolha normal e os avisos durante a observação.

## 2. Monitor Cloudflare, após autorização separada

1. Após a instalação autorizada em Workers Free, registar a versão implantada e observar duas invocações agendadas com 15 minutos de intervalo. Confirmar que uma fila ou um job recente impede o despacho.
2. Numa interrupção real, guardar as horas UTC de criação, atualização e início do job. O limiar adotado é de 30 minutos (18 do coletor e 12 de margem). A primeira verificação elegível ocorre no próximo tick. Para in_progress, o monitor verifica também o início efetivo do job para descontar a espera em fila.
3. Correlacionar o evento collection_restart_requested e os IDs assinalados com o aparecimento de um sucessor em main. Confirmar a conclusão, a leitura e a exclusividade do coletor. Um pedido pode ficar em fila enquanto o GitHub mantiver o bloqueio anterior. Medir separadamente o tempo até ao despacho e até à primeira leitura.
4. No tick seguinte confirmar que um sucessor recente inibe um novo pedido, inclusive se tiver havido erro de comunicação no despacho. Repetir a verificação dos avisos e do histórico do ponto 1.2. Se o GitHub ainda não mostrar o pedido aceite, a atribuição fica pendente. A deduplicação após resposta perdida depende da visibilidade do sucessor na API.
5. Fechar a validação com data, evidência e resultado por mecanismo: observado, pendente ou inconclusivo. Guardar apenas metadados operacionais, sem tokens, conteúdo das mensagens ou sessões.

## 3. Diário de ingestão e publicação

O candidato usa o Durable Object SQLite da continuidade com uma identidade separada para o diário. Cada leitura marca a tentativa antes de contactar o Telegram. O leitor aplica o lote localmente depois de receber a confirmação do diário. A publicação guarda um bundle Git antes do primeiro push e confirma o checkpoint após verificar o commit remoto. As lacunas conhecidas ficam em `data/ingestion_checkpoint.json`, no resumo do job e na auditoria do relatório.

O workflow candidato exige `GOAL_ROI_DURABLE_URL` com o endpoint HTTPS `/journal` e o segredo `GOAL_ROI_DURABLE_TOKEN` no Worker e no Actions. O endpoint autentica apenas operações do diário. A configuração da rota e dos segredos depende de autorização de produção. O agendamento do Worker mantém a observação e `RECOVERY_ENABLED=false`.

O despacho suplementar após `workflow_run` requer `GOAL_ROI_RECOVERY_MODE=active` e `GOAL_ROI_RECOVERY_ENABLED=true` nas variáveis do Actions. A observação e a próxima recolha normal têm percursos próprios.

O diário limita cada pedido a 1 800 000 bytes. Uma falha de acesso ou de capacidade impede o avanço do checkpoint. Os artefactos do Actions continuam disponíveis para reconciliação de transações antigas e diagnóstico de falhas. Uma interrupção antes de gravar uma leitura pode deixar versões editadas ou apagadas indisponíveis no Telegram.
