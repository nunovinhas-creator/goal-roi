# Alertas Stake do Goal-ROI

## Auditoria antes da implementação

A base auditada foi a `main` em `ec6ac1c`. O workflow **Collect ScanGoal** corria na `main` e lançava a recolha seguinte. **Tests** cobria as PRs e **Pages** publicava o dashboard. A API do GitHub também listava **SHADOW isolated preflight** como ativo, mas o seu ficheiro não estava na árvore da `main` e a PR [#47](https://github.com/nunovinhas-creator/goal-roi/pull/47) continuava em rascunho na branch `research/shadow-preflight-isolated-20261117`. Os restantes ramos remotos identificados eram de auditoria, investigação ou correções. A recolha em produção observada corria na `main`.

| Componente | Comportamento confirmado |
|---|---|
| `scangoal/reader.py` e `scangoal/watch.py` | Leem a mesma conversa ScanGoal em modo só de leitura com a sessão existente. O ciclo completo relê até 400 mensagens e a leitura rápida até 40, a cada 120 segundos durante a janela rápida. |
| `scangoal/store.py` | Acrescenta versões a `data/messages.jsonl`. Cada linha tem ID Telegram, carimbo da mensagem, carimbo de edição, texto, metadados de botões, carimbo de recolha e hash. O armazenamento existente deduplica por ID e hash do texto. |
| `scangoal/parser.py` e `scangoal/report.py` | O parser reconhece Stake como `STAKE_LOAD` pelo cabeçalho, lê o mercado, seleção e odd, e distingue o esquema pré-jogo do esquema com marcador. O relatório trata versões, jogos, odds, elegibilidade, resultados e hipóteses em separado. |
| `scangoal/notify.py` | Planeia os avisos atuais a partir do relatório e usa a Bot API com `TG_BOT_TOKEN` e `TG_CHAT_ID`. O estado e o registo próprios são publicados pelo coletor. O envio existente não expõe a duração de `retry_after` ao chamador. |
| SHADOW | `shadow.py` e `shadow_ops.py` pertencem à investigação científica. A PR #47 acrescenta um pré-ensaio isolado. O percurso dos alertas Stake lê diretamente a publicação do coletor e é independente desse ensaio. |

## Origem e formato

O consumidor lê exclusivamente `refs/heads/main:data/messages.jsonl` do repositório público Goal-ROI, num clone bare próprio. Os acessos Git limitam-se a verificar o clone, obter a referência remota e ler o ficheiro. O consumidor não consulta a Stake, a conversa Telegram, o relatório, resultados, ROI ou certificação. Usa somente a biblioteca padrão de Python e Git.

Os cabeçalhos ScanGoal observados foram `💸 🟦 Stake money load` e `💸💸 🟦 Stake: load growing`. Um bloco identificado pelo cabeçalho Stake pode ter vários registos explícitos `📊 mercado → seleção`. Cada linha distinta gera um alerta. Os cabeçalhos de outras origens fecham o bloco. Uma referência à palavra «stake» sem cabeçalho de alerta fica fora. Não há limiar de odd, volume ou mercado. Uma faixa de odds é preservada como aparece. O resumo ScanGoal pode indicar várias transações para a mesma seleção, sem as descrever individualmente. Nesse caso há uma ocorrência identificável.

O alerta inclui o ID da mensagem ScanGoal e a hora original em Lisboa quando disponível. Para uma versão original, o par de linhas com marcador indica LIVE e o separador entre as duas equipas indica PRÉ-JOGO, desde que o horário da mensagem não contradiga esse esquema. Uma mensagem anterior ao início previsto pode ser classificada como PRÉ-JOGO. Uma versão recebida já editada pode ter o marcador reescrito, por isso, à falta de evidência temporal suficiente, indica NÃO DETERMINADO. Campos ausentes ficam de fora. Quando a estrutura da aposta é ambígua, segue o texto original do bloco Stake. Texto superior ao limite de um envio é preservado em partes com a mesma referência.

A leitura offline da base encontrou **303 primeiras ocorrências Stake**, todas detetadas. A classificação devolveu 119 pré-jogo, 110 live e 74 com fase não determinada. Este ensaio não coloca o arquivo histórico na fila.

## Progresso e falhas

- A inicialização grava a versão publicada, todos os IDs já conhecidos e o instante de corte. Um ID publicado mais tarde fica de fora quando a mensagem ou a recolha é anterior ou igual ao corte. Uma edição do mesmo ID não cria um alerta novo. Dois IDs distintos com a mesma seleção geram alertas distintos.
- A base SQLite privada grava em conjunto o prefixo processado, os IDs e a fila. Um prefixo truncado ou reescrito interrompe o consumidor e conserva o estado. Uma falha temporária a obter o Git é registada e a leitura é repetida no ciclo seguinte.
- Um bloqueio de ficheiro assegura uma instância por base SQLite no mesmo disco. Antes do pedido de envio, a intenção é confirmada em disco. Uma interrupção após esse registo ou uma resposta ambígua deixa a parte como `uncertain` e nunca a repete automaticamente. Uma resposta Telegram positiva marca `sent`. Um erro definitivo de conteúdo marca `rejected`.
- Uma recusa recuperável, incluindo 429, falta de acesso ao destino ou falha de ligação confirmada, mantém a parte na fila. O consumidor respeita `retry_after` indicado pela API e espaça os próprios pedidos em pelo menos 3,1 segundos. Uma falha ambígua aplica uma pausa de cinco minutos para proteger as ocorrências seguintes. Os avisos atuais usam o mesmo bot, pelo que um 429 também pode refletir atividade simultânea desse sistema. [Telegram Bot API](https://core.telegram.org/bots/api#responseparameters)
- O utilizador pode inspecionar localmente os estados de entrega por ID e parte na tabela `outbox`. O processo regista contagens e estados sem tokens, IDs do chat ou texto das mensagens. A base guarda o texto necessário para filas pendentes e remove-o do registo quando o envio fica terminal.
- A Bot API não oferece uma chave de idempotência para `sendMessage`. Um timeout pode ter acontecido antes ou depois de o Telegram publicar. A prioridade nesse caso é impedir um alerta artificial. Consequentemente, uma ocorrência `uncertain` pode ter ficado por entregar.

## Latência

O consumidor consulta a publicação a cada dois minutos. Com o processo ligado, a deteção normalmente acrescenta até aproximadamente dois minutos **após a publicação** da mensagem, mais o tempo do fetch e da fila. As leituras rápidas podem só ficar publicadas no fim da janela do coletor. Numa amostra de dez ocorrências Stake em commits recentes, mediram-se 37 a 509 segundos entre a mensagem e a data do primeiro commit que a continha, com mediana de 231 segundos. O carimbo do commit é uma aproximação e a amostra não mede a hora do push nem a da entrega Telegram. A arquitetura não permite garantir alertas em 120 segundos ou em tempo real absoluto.

## Ativação manual, quando autorizada

Usar um anfitrião já disponível, com Python 3.11 ou posterior, Git, disco local persistente, rede e relógio sincronizado. É preciso manter o processo ligado para receber alertas sem interrupções. O módulo não provisiona serviços nem tem dependências adicionais. Executar a partir do checkout que inclui o módulo, mantendo separados o código, o clone bare e a pasta de estado privada.

```sh
install -d -m 700 ~/goal-roi-stake
git clone --bare --depth 1 --single-branch --branch main \
  https://github.com/nunovinhas-creator/goal-roi.git ~/goal-roi-stake/source.git
python -m scangoal.stake_alerts --initialize \
  --source-repo ~/goal-roi-stake/source.git --state ~/goal-roi-stake/state.sqlite3
```

A inicialização apenas fixa o corte. Para arrancar, disponibilizar `TG_BOT_TOKEN` e `TG_CHAT_ID` do bot habitual ao processo através do ambiente privado do anfitrião. As credenciais não são argumentos, nem fazem parte do clone, da base SQLite ou do Git.

```sh
python -m scangoal.stake_alerts --enable-send \
  --source-repo ~/goal-roi-stake/source.git --state ~/goal-roi-stake/state.sqlite3
```

Para desativar, parar apenas o processo Stake e preservar a base SQLite. Ao retomar com a mesma base, o consumidor recupera a fila e processa o que foi publicado depois do seu último cursor. Para mudar de anfitrião, parar a instância antiga e mover o estado fechado e atualizado antes de iniciar a nova instância. Restaurar uma cópia anterior aos últimos envios não preserva a garantia de deduplicação. O rollback do código equivale a parar o processo e reverter apenas o commit deste módulo. Nenhum passo de rollback precisa de acesso de escrita ao coletor.

## Validação offline

```sh
python -m unittest discover -s tests -p test_stake_alerts.py -v
python -m unittest discover -s tests -v
```

Os testes usam amostras sintéticas, um produtor Git local e respostas simuladas da Bot API. Incluem a deduplicação depois de reiniciar, fallback sem truncagem, bloqueio de um segundo processo, salto de histórico, mensagens com múltiplas apostas, reescrita do arquivo, entrega ambígua e isolamento do arquivo do coletor.
