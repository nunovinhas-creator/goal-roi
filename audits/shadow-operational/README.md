# Integração operacional SHADOW — preparada, não ativada

Base: `f03bda08878085a27f22735336012a7a4026d445`. Aproveita o código, testes, ledger e protocolo da PR #42, sem merge dessa PR.
O protocolo histórico de 17-11-2026 17:30:21 UTC é preservado byte a byte. O seu ledger continua vazio.
A instalação NÃO existe, o novo protocolo operacional ainda não tem timestamp e há ZERO observações prospetivas reais.
`installation.template.json` é um documento inativo: authorized=false, installed=false, operacional_start=null.

## Implementação real

`scangoal.shadow_ops` oferece activate, tick, supervise, analyse, export-outbox e restore-outbox.
É um consumidor dos ficheiros locais produzidos pelo coletor existente, usando uma cópia coerente por execução.
Não cria cliente Telegram, não pede resultados, não escreve dados/ROI/certificação e não altera reader/watch/notify ou workflows.
O CLI só pode publicar num checkout separado, ramo `shadow/h1-h2-operational`; main/master são recusados.
O source checkout permanece dedicado ao coletor e os ficheiros científicos são publicados exclusivamente no ramo SHADOW.

A ativação exige prova explícita de autorização e instalação; fixa um novo protocolo v2 depois de testar realmente o acesso de publicação.
Mantém regras, min_games, alpha, lambda, testes, critério sequencial, estimando emparelhado e critérios de decisão do protocolo histórico.
Só mudam a versão operacional, o SHA do código/dados instalado, a referência de autorização e o início/fim da janela efetiva.
Cada protocolo é guardado pelo seu hash, sem substituir versões antigas. Uma ativação falhada é preservada e exige uma nova tentativa versionada.
O registo só começa depois de o génesis ter sido confirmado no remoto; nada do intervalo sem acompanhamento é reconstruído.

Cada ciclo usa a hora real da captura e aplica os 120 segundos originais. Sinais antigos são registados como missed, sem decisões fictícias.
Captura a primeira entrada de cada regra e guarda as posteriores. Conhecimento prévio de FT/marca impede nova decisão.
Commit, push e ls-remote confirmado são a barreira ANTES de observar novos inputs. O recibo guarda SHA publicado, SHA remoto confirmado,
hora de verificação real e hashes exatos das decisões. Os recibos são também publicados.
A verificação científica exige recibo <=300s e anterior à primeira observação de QUALQUER FT/marca do jogo, inclusive fontes corrigidas.
Publicação ausente, tardia ou após o resultado mantém os eventos e bloqueia a inferência; nunca promove evidência antiga ao retomar.

Datas Git têm resolução de um segundo. Apenas no protocolo operacional v2, o verificador trata o carimbo Git como intervalo de um segundo;
não inventa nova hora de publicação. A prova temporal estrita continua a ser o recibo real com microssegundos e limite exato de 300s.
O protocolo v1 e os seus critérios não são alterados. A certificação existente permanece not_activated.

## Durabilidade e limites

Lock de processo não bloqueante; escrita temporária com fsync/rename; commit limitado à pasta científica; nenhum force-push/reset/rebase.
Retoma commits pendentes ANTES de ler novos resultados. Conflitos de ramos ficam retidos e falham com código 3 para revisão explícita.
Um crash entre escrita e commit é recuperado sem perder decisões. Correções de resultado são anexadas; resultados anteriores ficam preservados.
Um bundle Git incremental e os ficheiros científicos permitem retomar num runner novo, preservando os mesmos commits/odds/timestamps.
O outbox inclui diagnósticos, nunca Git config/credentials/secrets. Verifica hashes, prefixos, protocolos, caminhos e tamanho ao restaurar.
Arquivos antigos só são ignorados se os ledgers já forem prefixos publicados. Divergências permanecem em quarentena, sem fusão científica automática.

O consumidor faz ZERO chamadas a fornecedores. O exemplo usa um orçamento de 480 segundos no job dedicado e consulta ficheiros a cada 10 s.
Os outboxes têm teto de 5 MB comprimidos e 25 MB expandidos. O job dedicado tem de preservar a versão mais recente, mesmo em falha.
O exemplo shell é inativo e não é chamado por workflows. Nenhum agendamento ou workflow de chamadas externas foi criado.
A cadência do leitor existente pode deixar sinais acima de 120s; esses casos ficam contados como falhas de captura, nunca recuperados retrospetivamente.

## Ensaios reproduzíveis

```
python -m unittest discover -s tests -p test_shadow_ops.py -v
python -m unittest discover -s tests -p test_shadow.py -v
python scripts/rehearse_shadow_operational.py --output /tmp/shadow-git-prova-nova.json
python scripts/test_offline.py
```

`local-git-rehearsal.json` contém prova REAL de push/ls-remote para um remoto Git bare local, com relógio real e sinal sintético.
Delay observado: 0,033877 segundos; ledger remoto idêntico; reinício sem duplicados; prova externa local válida; estado INCONCLUSIVO.
Não é uma publicação em produção nem uma observação real. Os testes de fronteiras temporais usam relógios controlados, explicitamente fixtures.
Dezoito testes operacionais cobrem 120/121s, publicação a 299s, publicação tardia, falha de push, concorrência, conflitos, crash,
runner novo/outbox, correção, conhecimento prévio, corrupção, nomes H1/H2 com entradas/odds diferentes, limite de runtime e traversal.
Os doze testes herdados cobrem ainda cadeia serial, jogos sobrepostos, ausência de resultados, pares e não confusão ROI/segunda parte.

## Condições exatas ainda necessárias para iniciar os 30 dias

1. Rever e autorizar expressamente os merges. Não integrar automaticamente #42/#43 nem esta PR.
2. Rever o SHA composto que ficará em produção (incluindo a correção Stake, se integrada) e confirmar o respetivo CI.
3. Provisionar o checkout separado e o ramo remoto SHADOW, com autorização Git de escrita existente, sem credenciais dentro de artefactos.
4. Provisionar a execução SHADOW num runner ou job dedicado, com grupo de concorrência independente e fonte só de leitura. Demonstrar a latência de entrega da fonte para a janela de 120 s.
5. Restaurar o último outbox por consumir antes de iniciar um runner novo. Preservar o novo ZIP também em falha e verificar a sua disponibilidade no runner seguinte. Reservar tempo adicional no job dedicado para a transferência do artefacto. O exemplo continua inativo.
6. Criar a autorização instalada com referência, hora UTC, SHA aprovado e identificação do runner. Executar activate UMA vez.
7. Confirmar remotamente o novo génesis e o funcionamento de captura/publicação. Só então declarar o novo período operacional iniciado.

Sem estes passos não há acompanhamento contínuo. Falta de liveness, artefactos ou recibos é uma falha explícita, nunca certificação.

## Parecer

APROVAR a integração isolada como infraestrutura preparada, condicionada ao CI final. REJEITAR ativação ou certificação neste estado.
P0: nenhum identificado nos ensaios. P1 operacional: ligação ao runner e preservação de artefactos ainda não instaladas/autorizadas.
P2: H1 continua limitada aos minutos 46..90 para provar segunda parte; não se altera silenciosamente essa medição ou o estimando congelado.
Premissas: relógios locais corretos, fonte append-only fiel e autorização/artefactos controlados pelo operador; Git não autentica a veracidade de um marcador.

## Verificação da suite e integridade

Suite completa local: 599 testes; 598 passam e um Chromium é bloqueado pelos sockets deste ambiente (mesma falha já conhecida na base).
Os 18 testes operacionais e 12 herdados passam. O helper preexistente de teste de avisos usa agora um `as_of` fixo, para eliminar
uma corrida de relógio observada no primeiro CI da frente Stake; nenhuma asserção foi retirada e nenhum mecanismo de envio foi modificado.
O diff contra main é exclusivamente aditivo em módulos/scripts/auditorias SHADOW, mais essa fixture determinística.
`data/`, `docs/`, `.github/`, reader/watch/notify/report/brain/certification de produção permanecem sem alterações neste ramo.
O parecer final exige CI verde no SHA publicado. Não há autorização implícita de merge ou ativação.

## Correções da auditoria de 17 de novembro

A admissão lê primeiro o snapshot local, incluindo marcas, resultados externos,
`manual_results.json` e FT90 de `details_hl.json`. Uma entrada associada a conhecimento
já presente é excluída antes de criar uma decisão, mesmo com carimbo em falta ou
contraditório. O ledger conserva a exclusão. O protocolo e os recibos continuam a
exigir captura até 120 s e confirmação Git remota até 300 s.

`supervise` requer um destino de outbox novo e externo aos dois checkouts. Usa um
prazo monotónico absoluto, interrompe a fase de trabalho 15 s antes do prazo e limita
cada processo Git ao menor valor entre 40 s e o tempo restante. Em timeout, cancela
o grupo de processos Git. O outbox é criado e publicado atomicamente no tempo
reservado. Quando há falha, o exit code pertence ao job SHADOW. Os ficheiros duráveis
do checkout dedicado permitem retoma mesmo se a exportação do ZIP falhar.

O exemplo destina-se a um job ou runner separado do Collect ScanGoal. A entrega
atempada dos inputs e a transferência/restauração de artefactos entre runners reais
continuam por demonstrar antes de qualquer instalação.
