# Goal-ROI · Auditoria integral do repositório

**Veredito global — NO-GO para avisos operacionais de confiança, certificação e alegações de vantagem económica.** O projeto é utilizável para investigação exploratória com ressalvas de identidade, liquidação e proveniência.

| Referência | Valor |
|---|---|
| Repositório | `nunovinhas-creator/goal-roi` |
| Ramo auditado | `main` |
| SHA fixado | [`9db4f20c614a645a4949f88562d5054ec53b8181`](https://github.com/nunovinhas-creator/goal-roi/commit/9db4f20c614a645a4949f88562d5054ec53b8181) |
| Última integração de código | [PR #70](https://github.com/nunovinhas-creator/goal-roi/pull/70), merge `5291d0d8b51f758bdd79f89dc58a0636650f9178` |
| Relação entre os dois SHAs | A referência é um commit posterior ao merge. Alterou apenas `data/notify_state.json` e `data/notify_log.jsonl`. |
| Relógio do relatório auditado | 2026-11-20 20:27:44 UTC |
| Branch da entrega | `audit/full-repo-audit-2026-11-20` |

**Anexos** · [Inventário e fluxo](INVENTARIO.md) · [Achados e correções propostas](PROBLEMAS.md) · [Método e resultados dos testes](TESTES.md) · [Agregados do SHA auditado](snapshot.json)

## A. Resumo executivo

Duas instâncias do notificador fizeram duas tentativas simuladas para uma só intenção no Git. Foi possível liquidar uma aposta com um resultado de outro jogo. Uma observação posterior alterou a elegibilidade de sinais e o conhecimento de um resultado num corte passado. Uma quarentena aplicada depois da decisão mudou o conjunto de jogos do teste de certificação. Essas falhas afetam garantias essenciais mesmo com a suite existente a passar. São três achados P0, nos IDs GR-01 a GR-03. [Reproduções A01, A11–A19 e A44](TESTES.md#testes-adversariais-novos).

A auditoria fixou uma única revisão para código, dados, linhas de referência e testes. Inventariou a árvore, seguiu os percursos críticos entre componentes e confrontou os artefactos públicos com o produtor. A observação de Pages e Actions foi registada à parte quando ultrapassou a hora do SHA. A suite existente passou 796 testes Python, incluindo o caso de navegador, e a suite Node de continuidade passou. Nos 59 cenários novos, 28 passaram e 31 falharam. As integrações externas e os limites de verificação em produção estão na matriz. [Método e datas](TESTES.md).

A aritmética do histórico publicado foi reproduzida. Em 1 056 apostas, 832 foram liquidadas em 457 IDs de jogo e produziram **+26,26 unidades e ROI de +3,16% à odd anunciada**. O IC nominal de 95% é de **−3,62% a +9,93%**. A perda de 2% de odd reduz o ROI para +1,27%. Com 5% de perda, o ROI fica em −1,56%. A escolha de 832 liquidações deixa 224 apostas fora do denominador. Se todas elas fossem perdas de uma unidade, o ROI sobre as 1 056 ficaria em −18,73%, antes dos custos. É uma análise de sensibilidade, sem estimativa da probabilidade desse cenário. [Agregados](snapshot.json), [denominadores e preços](PROBLEMAS.md#gr-07).

O relatório de produção tinha **zero estratégias certificadas**. O estudo Polymarket tinha um jogo com FT e ranking de amostra insuficiente. O ensaio do protocolo LLM ficou em curso com um dia emparelhado e e-valor final de 0,934194, perto do capital inicial e abaixo da fasquia 20. Os resultados publicados sustentam monitorização e descoberta. Faltam observações prospetivas admissíveis e execução de preço para sustentar uma alegação económica. [Estudo LLM](TESTES.md#suite-existente), [Polymarket](snapshot.json).

### Os dez problemas prioritários

| Ordem | ID | Prioridade | Resultado e impacto |
|---:|---|---|---|
| 1 | [GR-01](PROBLEMAS.md#gr-01) | P0 | Dois runners obtiveram sucesso de publicação e cada um tentou enviar. Ficou uma intenção no remoto. |
| 2 | [GR-02](PROBLEMAS.md#gr-02) | P0 | Um conflito futuro retirou uma aposta já admissível e mudou a cadeia da certificação e a amostra da LLM. |
| 3 | [GR-03](PROBLEMAS.md#gr-03) | P0 | Uma observação futura apagou sinais elegíveis de um corte anterior e um consenso posterior foi apresentado como se já existisse. |
| 4 | [GR-04](PROBLEMAS.md#gr-04) | P1 | ID reutilizado, nota FT antecipada e confronto por nomes sem competição permitem atribuir resultado do encontro errado. |
| 5 | [GR-05](PROBLEMAS.md#gr-05) | P1 | Mercados inválidos receberam vitória. Uma seleção 1X2 válida interrompeu o relatório. |
| 6 | [GR-06](PROBLEMAS.md#gr-06) | P1 | A reversão de uma edição perdeu a nova versão. Uma cauda de diário incompleta bloqueou recolha e relatório. |
| 7 | [GR-08](PROBLEMAS.md#gr-08) | P1 | O legado conservou o marcador original após correção. Sessenta e nove liquidações legadas ficaram pendentes na investigação. |
| 8 | [GR-07](PROBLEMAS.md#gr-07) | P1 | A taxa média requerida pelos preços não estima vantagem monetária com odds heterogéneas. Há dependência, dados em falta e preços teóricos. |
| 9 | [GR-09](PROBLEMAS.md#gr-09) | P1 | Stake enviou alerta expirado. A deduplicação legada suprimiu um mercado diferente. Uma rejeição confirmada consumiu um sinal ainda atual. |
| 10 | [GR-10](PROBLEMAS.md#gr-10) | P1 | A recolha principal pode operar fora de `main`. Persistência tardia, permissões amplas e recuperação em cadeia comprometem a continuidade. |

A dependência vulnerável, o alcance dos dados públicos, os testes de continuidade Cloudflare e a prova de ativação científica estão no catálogo. [20 achados por prioridade](PROBLEMAS.md).

## B. Veredito por componente

| Componente | Estado | Risco | Prioridade de correção |
|---|---|---|---|
| Recolha | DEGRADED | Versões perdidas, lacunas sem reconstrução integral, cauda incompleta | P1 |
| Integridade histórica | NO-GO | Replays com informação futura e decisões sem um compromisso verificável | P0 |
| Parser | DEGRADED | Contratos inválidos passam e formatos aceites podem bloquear o relatório | P1 |
| Liquidação | NO-GO | Resultado de outro jogo, falsos ganhos e correções visíveis só em parte do sistema | P1 |
| ROI | DEGRADED | Aritmética reproduzida em dados históricos, com liquidação, seleção e preços a limitar a inferência | P1 |
| Cérebro | NO-GO | Elegibilidade reconstruída, retrospectiva e amostra de certificação alterável após decisão | P0 |
| LLM | DEGRADED | Guardas para chamada online verificáveis no código, notas livres e amostra formal condicionada | P1 |
| Telegram | NO-GO | Corrida de aquisição, perdas por confirmação e atualização incompleta de frescura e chaves | P0 |
| GitHub Actions | DEGRADED | Serialização local, âmbito de despacho alargado, persistência e retoma frágeis | P1 |
| Cloudflare | DEGRADED | Recuperação configurada para observação no código. Retoma condicional com bloqueio demonstrado em simulação | P1 |
| Website | DEGRADED | KPIs reproduzíveis e separação explícita de certificação. Relógio, validação e simulação têm falhas | P2 |
| Segurança | DEGRADED | Dependência vulnerável, estado público, permissões operacionais e fronteiras de confiança | P1 |

## C. Problemas encontrados

O [catálogo de problemas](PROBLEMAS.md) inclui identificador, ficheiro e linhas, impacto, evidência, causa, correção proposta, risco de regressão e prioridade. Os mecanismos dependentes de produção ficam com o alcance indicado na matriz de testes.

## D. Verificações, coerência e continuidade

### As populações respondem a perguntas diferentes

| Consumidor | População e significado no SHA fixado |
|---|---|
| ROI histórico e KPIs Pages | Apostas com primeira versão aceite pelo legado, um preço anunciado por combinação, FT e prioridade de origem legados. Q7 reproduzido com 1 056 apostas e 832 liquidadas. |
| Acerto legado e evidência operacional | O legado inclui sinais sem odd e leituras tardias. A vista operacional exige elegibilidade e conserva a liquidação legada. Acerto e retorno exigem denominadores explícitos. |
| Cérebro e walk-forward | 1 051 sinais de investigação elegíveis, entre 1 267 agrupados. A identidade e a prova FT são mais restritas. Ficam 69 resultados numéricos legados sem resultado na comparação dos mesmos IDs de mensagem. |
| Registo de hipóteses | 10 080 entradas, das quais 9 992 geradas e 88 LLM. O relatório mostra 21 promissoras, 27 abandonadas e 10 032 em observação. O estado promissor é exploratório. |
| Auditoria e ranking Polymarket | Um jogo FT no candidato, no mesmo conjunto de cinco jogos de referência, com sobreposição. População de primeira cotação elegível depois de ativação e protocolo. Sem ranking por amostra insuficiente. |
| Prompt LLM e avaliação incremental | O digest Polymarket recebe contagens de insuficiência abaixo de 30 FT. O restante prompt inclui histórico descritivo e grupos experimentais. A avaliação auditável aplica outro desenho de controlo e imputação. |
| Alertas regulares, Polymarket e Stake | Combinam populações e regimes de tempo distintos. A regra Polymarket pode mostrar uma cotação posterior elegível da mesma recolha. O certificado, quando existe, exige estado ativo e prova associada à hipótese. |
| Dashboard | A visão geral valida os totais de Q7 antes de usar o ROI e apresenta estado científico em campo próprio. O laboratório e o quadro operam sobre os blocos do JSON. W04–W06 limitam a confiança de atualidade e validação. |

O sistema tem comparações verificáveis entre produtos quando partilham regra, relógio e população. A equivalência foi reproduzida para auditoria/ranking Polymarket e Q7/visão geral. As transições de liquidação entre legado e investigação estão quantificadas. O prompt, o aviso e o simulador usam conceitos de tempo e preço diferentes. É necessária uma chave de coorte, corte e versão para reconciliar cada passagem. [Agregados](snapshot.json), [testes do deploy](TESTES.md#verificação-do-deploy-depois-da-pr-70).

### Comportamento em falha

| Evento | Consequência no desenho atual |
|---|---|
| Telegram de origem indisponível ou sessão revogada | A etapa de leitura falha. As leituras rápidas tentam de novo com limites. Faltam nova ingestão, confirmação de edições e prova de que o relatório está atual. A cadeia do workflow pode continuar a chamar sucessores. |
| GitHub recolhe mas falha publicação | Os dados podem ficar só num runner efémero. Um aviso durável depende de primeiro publicar a intenção. O próximo checkout só vê o que ficou em Git. Uma intenção sem desfecho pode tornar-se incerta. |
| Telegram de saída indisponível | Tentativas rejeitadas ou ambíguas ficam terminais na sequência ensaiada, e o restante lote pode esperar. Expiração e chaves legadas podem perder oportunidades após a recuperação. |
| Cloudflare indisponível com GitHub ativo | Mantém-se a cadeia GitHub, se rotinas, credenciais e filas estiverem operacionais. O controlo externo de retoma deixa de observar. |
| GitHub, Telegram e Cloudflare indisponíveis | Cessa a recolha com publicação e cessa a entrega. O Pages pode manter uma versão em cache. O watchdog depende do GitHub para observar ou despachar. Depois da recuperação, a leitura regular retoma a janela disponível. O backfill arquiva páginas antigas. A versão original que já desapareceu ou o sinal expirado pode perder-se. |
| Nenhum sucessor ficou em fila | A retoma depende de um cron que dispare, de um evento de falha elegível, de despacho autorizado ou do watchdog ativado e saudável. A amostra observada favorece despachos. Tem 99 despachos e um cron de recolha. |

Este quadro deriva de fluxos versionados, testes offline e metadados públicos. A retoma em produção após falha simultânea ficou **NÃO VERIFICADA**. Também ficaram por verificar a frequência real de duplicação, os privilégios de bypass, a faturação, a configuração efetiva do watchdog e a receção de avisos pelo destino Telegram. [Matriz de evidência](TESTES.md#não-verificado-em-produção).

### Website depois da PR #70

O primeiro ecrã mostra contagem, ROI à odd anunciada e estado científico. Na captura testada, o código publicado coincidiu com o código do SHA fixado e os dados da publicação posterior foram confrontados com o SHA de deploy correspondente. Teclado, contraste medido, filtros, gráfico e simulador passaram nos cenários base em 320, 375 e 1 440 px. O refresh rejeitou JSON truncado e preservou último relatório válido. O site pode apresentar atualidade indevida perante carimbos antigos ou futuros, e a banca percentual pode usar lucro ainda bloqueado. Os problemas de inferência do produtor propagam-se às vistas que o mostram. [Resultados do navegador](TESTES.md#verificação-do-deploy-depois-da-pr-70), [GR-15](PROBLEMAS.md#gr-15), [GR-16](PROBLEMAS.md#gr-16).

## E. Dívida técnica

- A dupla materialização de apostas e de resultados tem semânticas diferentes no `report.build`. Partilhar o resolvedor, a identidade, o contrato temporal e as funções de retorno diminui a diferença entre consumidores e reduz verificações duplicadas. [Relatório](../../scangoal/report.py#L581-L704).
- A máquina de entrega de `notify.py` tem 107 funções e métodos de topo ou de classe localizados por inventário textual. Persiste, publica, decide frescura, formata e aplica efeitos. A saída Stake reimplementa estado e política de envio. O publicador genérico tem outra semântica da reserva da LLM. A consolidação começa pela especificação da chave e das transições. [Notificador](../../scangoal/notify.py), [Stake](../../scangoal/stake_alerts.py), [CAS do orçamento](../../scangoal/llm_budget.py).
- `llm_eval.evaluate`, `report.build` e `llm._run_locked` têm mais de 120 linhas cada e acumulam seleção de população, relógio, persistência ou apresentação. A divisão útil acompanha invariantes testáveis e retira estado mutável de `report.LAST` das interfaces. [Avaliação](../../scangoal/llm_eval.py#L469-L700), [relatório](../../scangoal/report.py#L581-L704), [LLM](../../scangoal/llm.py#L385-L514).
- Os ensaios e migrações históricos têm valor de arquivo. Marcar SHA, suporte, entradas e saídas de cada CLI reduz o risco de executar uma ferramenta antiga sobre estado ativo. O comentário OIDC aponta para um percurso BSD que a entrada atual não liga. [Inventário das ferramentas](INVENTARIO.md#entradas-operacionais-e-ferramentas).
- Manter testes existentes e acrescentar invariantes de decisão temporal, concorrência e crash entre processos. Separar verificações metodológicas de controlos de compatibilidade de formato e de regressões visuais. [Regressões independentes](TESTES.md#testes-adversariais-novos).

## F. Plano de correção

| Horizonte | Trabalho proposto e evidência de saída |
|---|---|
| **Imediato · P0** | Fixar as decisões prospetivas antes do resultado, reconstruir as-of por eventos e aplicar aquisição exclusiva de intenção a todas as saídas. Manter certificação indisponível até validação independente. O corte de saída exige A01, A11, A15, A17, A18, A34 e A44 a passar e demonstração de que estado incerto não é reenviado. |
| **Imediato · P1** | Resolver identidade canónica e FT por período, tratar contratos inválidos, persistir versão antes de downstream e isolar credenciais e despachos de produção. Corrigir fluxo Stake e política de rejeição confirmada. Atualizar a dependência afetada e obter revisão das permissões e dos dados públicos. Corrigir a seleção de quarentena, garantias de retoma e mecanismo de prova de publicação antes de qualquer ativação. |
| **Curto prazo · P2** | Publicar explicações e contagens reconciliáveis por coorte. Medir cobertura FT, idade, versões recusadas, tentativas e recuperação. Empregar esquema e carimbos de versão no dashboard, melhorar simulação e repetir ensaios em dispositivos e leitores de ecrã. Verificar redução de carga de publicação. |
| **Evolução** | Usar um diário transacional, armazenamento de estado restrito e projeções incrementais. Preservar relatórios publicados como evidência. Medir capacidade, orçamento externo e recuperação em staging. Pré-registar e recolher amostra de preços executáveis e custos, com bloco temporal, concentração, tratamento de resultados em falta e uma avaliação independente antes de alegar vantagem. |

As correções referidas são propostas para autorização futura. A branch da auditoria materializa só relatório, testes e agregados.

## G. Avaliação final

| Pergunta | Resposta |
|---|---|
| O projeto é operacionalmente fiável? | **Não para garantias profissionais.** A rotina básica produz dados durante funcionamento normal. A corrida de entrega, o armazenamento e a retoma falham em cenários reproduzidos. |
| Os dados são historicamente íntegros? | **A integridade é parcial.** Os 3 259 registos do diário verificado coincidiram com o hash textual e o histórico local não mostrou remoções nesse ficheiro. Faltam eventos que o leitor não consegue recuperar e o passado pode mudar em reprocessamento. |
| O ROI apresentado está corretamente calculado? | **A fórmula histórica foi reproduzida para as liquidações publicadas.** A correção económica depende da identidade, do FT, dos resultados em falta, de preço executado e de custos. |
| O cérebro analisa evidência admissível? | **A admissibilidade falhou nos testes adversariais.** A quarentena e a reconstrução permitem que o futuro altere as decisões históricas. |
| A LLM pode gerar conclusões incorretas? | **Sim.** Uma nota categórica sem suporte foi aceite. As barreiras orçamentais funcionaram nos casos offline e a existência de atividade anterior está registada. |
| Os alertas são seguros contra perdas e duplicações? | **Não.** Há duas tentativas para uma intenção num ensaio concorrente. A política de confirmação incerta pode perder oportunidades e uma rejeição explícita pode consumir uma aposta ainda válida. |
| O site apresenta informação cientificamente honesta? | **A certificação e o ROI histórico têm apresentação distinta e consistente com o JSON testado.** A validade do estudo cronológico, a impressão de atualidade e as noções de vantagem e banca exigem correções. |
| O projeto está preparado para crescer sem custos adicionais? | **Não demonstrado.** Os runners padrão em repositório público têm minutos gratuitos. O volume Git/Pages, a taxa de chamadas, o custo da conta e as quotas dos fornecedores precisam de limites e medições. |
| O que falta para atingir qualidade profissional? | Persistência de eventos e decisões, aquisição durável de intenção, identidade e liquidação verificadas, recuperação com testes de falha realistas, proteção de dados operacionais, contrato de publicação e teste prospetivo com odds executáveis. |

A passagem da suite valida cenários definidos pelos testes. O veredito **NO-GO** decorre das garantias essenciais violadas com evidência independente. A estimativa histórica e os estudos exploratórios continuam disponíveis para investigar hipóteses.
