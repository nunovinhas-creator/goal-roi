# Piloto de entrega Dropping Odds — proposta não ativada

Manter shadow até autorização. O modo on exige limite explicitamente aprovado (5, 10 ou 15 minutos), início e fim UTC no próprio dia civil de Lisboa. Sem envio retroativo. O fim é revisto antes de cada pedido.

Criar uma regra exclusiva para goal-roi-dropping-odds-anchor: ativa, sem bypass, impedir eliminações e force pushes, permitir criação e fast-forward por Actions. Não alterar main. Confirmar configuração e token alojado em shadow. O código verifica regras efetivas por API antes de anexar/ler/publicar a âncora e antes de enviar; os atores de bypass exigem confirmação operacional independente.

Em shadow, obter o último SHA confirmado pelos logs e pelas duas notes. Executar manualmente com anchor_checkpoint igual a esse SHA e initialize=false. A âncora aponta ao mesmo commit. A partir daí as três referências evoluem atomicamente. Não reinicializar nem corrigir divergências automaticamente. Confirmar duas execuções automáticas com sinais novos e três tips iguais; medir latência e decidir o limite.

Após autorização própria, configurar idade e janela, só então selecionar on. Ao terminar, voltar a shadow e cancelar jobs on ativos. Usar apenas o bot existente. O timeout pode sacrificar alertas para evitar reenvios. A proteção presume a regra ativa sem bypass; um administrador que altere a própria política não é uma ameaça que este repositório consiga eliminar. Sem alterações ao coletor, V7, Brain ou modelos científicos.

Testes Git locais não provam a política alojada. Faturação e latência real carecem de validação. Não fazer merge, ativar ou enviar Telegram nesta PR.

RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.
