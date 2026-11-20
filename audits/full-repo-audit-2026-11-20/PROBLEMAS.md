# C. Problemas encontrados

P0 compromete a integridade de uma garantia essencial. P1 pode produzir resultados errados, avisos indevidos ou perda operacional relevante. P2 reduz a fiabilidade, a clareza ou a capacidade de manutenção. P3 identifica dívida de menor impacto. Os códigos A, O e W referem-se aos [testes independentes](TESTES.md). Uma reprodução com dados sintéticos demonstra o comportamento sob as condições estudadas. A ocorrência ou a frequência em produção exige evidência adicional.

<a id="gr-01"></a>

## GR-01 — Duas tentativas para a mesma intenção de envio · P0

**Evidência e impacto.** A01 usou duas instâncias reais do notificador, clones independentes e um remoto Git bare. As duas planearam a mesma aposta antes de qualquer publicação. Com o mesmo relógio de intenção ao segundo, cada runner fez uma tentativa de envio. O remoto terminou com uma intenção e os dois clones com o mesmo HEAD. Em 12 repetições do mesmo interleaving houve 12 duplicações de tentativa. Em 12 repetições de A37, com alterações materialmente diferentes, o conflito bloqueou a segunda tentativa. A frequência real é desconhecida. O recorte Git contém 166 eventos `delivered` e zero repetidos por chave. Uma só intenção no Git pode corresponder a duas tentativas, pelo que a contagem não exclui duplicados. O grupo `scangoal-write` reduz o risco no workflow habitual. Execução externa, checkout antigo e o ramo de despacho fora de `main` exigem controlos adicionais.

**Causa.** O sucesso do `push` prova o estado da ref depois do comando. O rebase pode descartar uma alteração equivalente e o segundo push ainda ter sucesso. O sucesso deixa de provar que aquele runner adquiriu a intenção. Stake reutiliza este publicador. [Entrega e publicação](../../scangoal/notify.py#L1243-L1382), [envio Stake](../../scangoal/stake_alerts.py#L124-L170).

**Correção proposta.** Registar a intenção por aposta canónica e destino num armazenamento com aquisição exclusiva e durável. A aquisição deve distinguir um novo proprietário de uma intenção já existente. Se a implementação continuar em Git, usar uma ref de outbox dedicada com compare-and-swap sobre o antecessor exato, ID aleatório de tentativa e leitura pós-erro. Uma confirmação de publicação ambígua só permite avançar se a autoria e o estado exclusivo forem verificáveis. Unificar ambas as saídas antes de migrar. O orçamento AI já demonstra a semântica de CAS neste repositório. [Referência de CAS](../../scangoal/llm_budget.py#L82-L109).

**Risco de regressão.** Alto. A migração tem de conservar chaves e tentativas terminais e cobrir conflitos, ACK perdido, restart, janela de expiração e perda de publicação do desfecho. A ausência de uma chave de idempotência no método Bot API observado exige uma via de reconciliação para estado incerto.

<a id="gr-02"></a>

## GR-02 — A quarentena muda retroativamente a amostra de certificação · P0

**Evidência e impacto.** Em A17, um jogo admitido impedia um segundo jogo sobreposto de entrar na cadeia. Um conflito no primeiro jogo, conhecido depois de ambas as decisões, retirou-o da lista de candidatos e admitiu retroativamente o segundo. O e-valor passou de 0,8063 a 1,1860. A44 confirmou que um conflito conhecido depois da decisão também elimina uma unidade da avaliação formal LLM antes da imputação. A demonstração invalida a condição de admissibilidade previsível usada na garantia do e-processo. O registo de certificação estava ausente e havia zero certificados no SHA auditado.

**Causa.** A seleção é reconstruída a partir de apostas já marcadas pela liquidação mais recente. O mesmo campo controla a retirada da unidade em vez de controlar apenas a forma de liquidar uma decisão fixa. [Cérebro](../../scangoal/brain.py#L496-L584), [admissão LLM](../../scangoal/llm_eval.py#L264-L305).

**Correção proposta.** Publicar o compromisso antes do resultado com sequência, política, preço e relógio. Preservar a unidade e a sobreposição originais. Na ausência de resultado confirmado até ao prazo, imputar o limite conservador previsto para cada lado. Revisões posteriores devem constar de um diário de reconciliação. Submeter o algoritmo e o protocolo a revisão estatística independente antes de qualquer certificação.

**Risco de regressão.** Alto. A transição afeta a amostra, o denominador, a data de fecho e a comparabilidade com estudos históricos. Repetir testes com conflitos, pendências, anulações, estados simultâneos e correções antes e depois do prazo.

<a id="gr-03"></a>

## GR-03 — A reconstrução as-of permite que observações futuras alterem o passado · P0

**Evidência e impacto.** A11 apresentou quatro sinais elegíveis no corte histórico. Um quinto sinal lido depois do corte fez a mesma consulta devolver zero pelo critério de lote. A15 apresentou duas fontes em desacordo até à correção de uma delas. A aposta, reavaliada para a hora do desacordo, passou a mostrar vitória usando a primeira hora em que apareceu o marcador vencedor. A18 conservou, antes do início do jogo, um resultado manual sem hora. A34 incluiu uma hipótese de nascimento posterior ao relógio de avaliação. A35 aceitou como decidível uma aposta pré-jogo lida depois do início. O conjunto compromete replays, classificação de hipóteses e contexto LLM. A16 confirma que a regra Polymarket isola uma correção depois do prazo no caso testado.

**Causa.** A leitura materializa o estado mais recente de todo o diário, e a vista temporal aplica filtros depois do agrupamento. O estado atual de consenso pode herdar a disponibilidade de uma observação anterior, inclusive através de um intervalo de conflito. A leitura carimba toda a página no início da operação. Três edições no diário têm hora posterior ao carimbo de recolha, compatível com esse desenho. Há componentes com relógio próprio e elegibilidade pré-jogo diferente entre análise e envio. O parser converte Moscovo para UTC, o notificador usa Europe/Lisbon para apresentação, e a cronologia aceita fuso implícito para algumas horas e exige fuso explícito noutras. [Carregamento e lotes](../../scangoal/report.py#L30-L103), [parser](../../scangoal/parser.py#L6-L55), [resolução FT](../../scangoal/opportunity_results.py#L71-L119), [vista temporal](../../scangoal/chronology.py#L34-L159), [cérebro](../../scangoal/brain.py#L642-L676).

**Correção proposta.** Fazer o corte nos eventos antes da agregação. Guardar disponibilidade real e ordem estável por fonte, publicar a hora em que surgiu consenso e a identidade que o ligou à aposta, versionar as condições e os próprios conjuntos de resultados por decisão. Introduzir um teste metamórfico que compare o mesmo corte antes e depois de anexar cada tipo de evento futuro.

**Risco de regressão.** Alto. O replay antigo produzirá valores diferentes. Conservar os relatórios publicados com a versão das regras e calcular, em paralelo, uma série de referência para a nova metodologia.

<a id="gr-04"></a>

## GR-04 — O histórico aceita FT do encontro errado · P1

**Evidência e impacto.** A12 liquidou como perdida uma aposta num jogo diferente que reutilizava o mesmo ID ScanGoal. A13 liquidou uma aposta com nota FT de equipas iguais publicada 30 minutos antes do início. A19 encontrou um evento de outra competição através de nomes e janela temporal. A45 agregou dois jogos sem ID numa aposta com identidade nula. São casos em que o ROI publicado pode contar ganhos, perdas ou exposição incorretos. O relatório fixado já identificava cinco exclusões de identidade reutilizada no cérebro. A contagem de exclusões por identidade não demonstra quantos resultados históricos estavam errados.

**Causa.** A liquidação operacional junta FT por ID bruto, aplica a primeira nota compatível por nomes e hora de mensagem e decide o fornecedor por nomes normalizados numa janela larga. A identidade canónica chega depois. [Chave e nota FT](../../scangoal/report.py#L26-L27), [liquidação histórica](../../scangoal/report.py#L141-L242), [matching do fornecedor](../../scangoal/results_bsd.py#L79-L118), [identidade](../../scangoal/match_identity.py#L49-L105).

**Correção proposta.** Fixar identidade canónica em todas as populações monetárias e ligar o ID do fornecedor com orientação, competição, hora e prova temporal. Exigir referência inequívoca para notas FT, mandar ambiguidades para quarentena e registar o motivo. Conservar as exclusões antigas enquanto se mede a cobertura no modo paralelo.

**Risco de regressão.** Alto. Aliases, equipas com nomes comuns, jogos repetidos, partidas de categorias diferentes e partidas adiadas precisam de testes. A cobertura pode cair até as identidades serem reconciliadas.

<a id="gr-05"></a>

## GR-05 — Contratos inválidos liquidam e um 1X2 válido bloqueia o relatório · P1

**Evidência e impacto.** A20 obteve vitória para três seleções inválidas em total asiático, BTTS e handicap. A31 reproduziu `KeyError` no relatório com Stake 1X2 selecionado pelo nome completo da equipa, que o parser de seleção aceita. A30 admitiu uma odd infinita como aposta embora a elegibilidade de investigação já rejeite esse valor. A46 ganhou uma janela de cinco minutos em Goal brewing com um golo ocorrido 25 minutos antes do sinal. Partidas com estados explícitos de prolongamento, penáltis, anulação ou interrupção foram corretamente rejeitadas pelo caminho testado em A22. Os marcadores e as linhas asiáticas válidos de A21 passaram.

**Causa.** Os ramos de liquidação presumem vocabulário e linhas válidos. No total, qualquer lado diferente de `Over` é tratado como `Under`. A apresentação 1X2 espera códigos de seleção. Goal brewing verifica só o limite superior da janela. [Parser](../../scangoal/parser.py#L87-L109), [liquidação](../../scangoal/parser.py#L317-L375), [labels e minutos](../../scangoal/report.py#L218-L245), [apresentação 1X2](../../scangoal/report.py#L307-L315).

**Correção proposta.** Normalizar mercado, período, lado, linha e preço numa representação tipada partilhada. Validar quartos admissíveis, preços finitos e limites de marcador, além de concordância do prefixo de golos com o marcador no momento do sinal. Quarentenar linhas não reconhecidas por registo e preservar a restante recolha. Usar o contrato normalizado na apresentação.

**Risco de regressão.** Alto. Verificar formatos históricos, seleções por nome, casas com vocabulário distinto, linhas asiáticas e regras live. Testar também a exportação JSON estrita.

<a id="gr-06"></a>

## GR-06 — Perda de versões e recuperação incompleta da recolha · P1

**Evidência e impacto.** A08 armazenou duas versões de uma sequência texto A → B → A. A09 perdeu uma edição só de botões. A10 deixou os dois leitores indisponíveis após acrescentar uma linha parcial. A41 arquivou corretamente uma página antiga fora do diário de decisões. A deteção de apagamentos e edições cobre a janela relida. Uma edição antiga, uma mensagem apagada entre leituras ou um sinal que caiu fora da janela podem escapar em definitivo. O Git preserva as linhas que chegou a receber. A análise de primeiro pai detetou 864 alterações do diário de mensagens sem linhas removidas.

**Causa.** Deduplicação global por ID Telegram e hash de texto, sem chave de conversa e versão. Atribuição de um carimbo no início da leitura a toda a página. Escrita append sem enquadramento, validação ou recuperação de cauda. A paginação recupera a versão disponível no momento e escreve arquivo separado. [Armazenamento](../../scangoal/store.py#L10-L38), [leitura e backfill](../../scangoal/reader.py#L33-L131).

**Correção proposta.** Criar diário de eventos durável com identidade de diálogo, versão observada, carimbo de receção e fusos explícitos. Usar transação ou escrita com enquadramento e verificação. Arquivar lacunas sem atribuir disponibilidade retroativa. Monitorizar idade da lacuna, mensagens saturadas e hora efetiva da última leitura completa.

**Risco de regressão.** Alto. Necessita migração idempotente que preserve a primeira leitura e o hash das linhas antigas, além de distinguir original de cópia recuperada. Testar alteração e reversão, paginação, perda de sessão, crash e relançamento.

<a id="gr-07"></a>

## GR-07 — Acerto, vantagem económica e dimensão de amostra divergem · P1

**Evidência e impacto.** A24 obteve ROI de +100% com 1 vitória a odd 20 e 9 derrotas a odd 1,10, embora o acerto de 10% ficasse muito abaixo da média de `1/odd`. O prejuízo esperado de uma estratégia depende conjuntamente da probabilidade por aposta e do preço. Sob taxa comum, o limiar de equilíbrio para apostas de uma unidade é `1/média(odd)`. O método publicado usa `média(1/odd)` como referência exploratória. A25 confirmou a aritmética de meias liquidações e devoluções no ROI histórico testado. O veredito de acerto do site compara um intervalo por aposta com a referência média e aplica um limiar de 300 apostas. Esse número pode reunir jogos correlacionados. A narrativa de risco usa variância de apostas independentes. O cérebro aplica modelos Beta a votos fracionários e testa milhares de hipóteses sobrepostas. Esses resultados são sinais de descoberta com pressupostos restritivos.

**Causa.** O projeto reutiliza medidas de acerto como indicadores de lucro e mistura contagem de apostas, contratos, liquidações e jogos. Os IC de ROI agrupam por ID bruto e usam aproximação normal, com outros efeitos de dependência fora do modelo. Os preços são anunciados. Os descontos fixos de 2% e 5% são sensibilidade a preço, sem execução, profundidade, comissões ou limites observados. [ROI e denominadores](../../scangoal/report.py#L318-L393), [modelos e risco](../../scangoal/insights.py#L63-L167), [sorte e regime](../../scangoal/insights.py#L320-L396), [veredito](../../docs/dashboard.js#L22-L31).

**Correção proposta.** Publicar estimandos explícitos por unidade apostada, por jogo e por dia, com resultados brutos, líquidos e observabilidade. Mostrar frações e estados separados, aplicar agrupamento canónico e concentração, e pré-registar o tratamento de exposição e dados em falta. Exigir preços executáveis, mercado disponível e custos para uma alegação de vantagem económica real. Medir a calibração do teste com simulações sob nulidade e dependência plausíveis.

**Risco de regressão.** Médio/alto. Os consumidores precisam de migrar conjuntamente o contrato de UI, alertas, CSV e relatórios. Rankings podem mudar de ordem mesmo com lucro histórico igual.

<a id="gr-08"></a>

## GR-08 — Resultados revistos e denominadores divergem entre consumidores · P1

**Evidência e impacto.** A14 gravou a correção no histórico de versões e conservou o primeiro resultado no snapshot operacional. A28 e A29 demonstraram quarentena na resolução de versões em casos contraditórios. No SHA auditado, 98 resultados Highlightly e 26 BSD tinham apenas snapshot. Dos 1 051 IDs de mensagem partilhados entre as populações elegíveis do legado e da investigação, 69 passaram de resultado numérico a pendente na vista de investigação. O ranking e a auditoria Polymarket coincidiram nos campos comparáveis. Ambos tinham um jogo com FT confirmado. Jogos com conflito são excluídos do denominador do ROI exploratório, enquanto atrasos sem resultado podem contar como perdas. Essa diferença pode favorecer grupos com resultados contraditórios se faltar uma análise de limites.

**Causa.** Os fornecedores fazem `setdefault` em `results`, observam correções apenas se a paginação por outros jogos as trouxer e têm histórias heterogéneas. O percurso legado escolhe fonte por prioridade e o investigador precisa de versões ou de exceções legadas. `details_hl` grava pesquisas iniciais de eventos e deixa de pedir o jogo mesmo quando a primeira descrição fica inconsistente. A representação junta em `w=None` situações de espera, suspensão, anulação e mercado não liquidável. A perda por prazo no estudo pode ainda capturar uma anulação sem estado FT. [Arquivos FT](../../scangoal/opportunity_results.py#L122-L155), [consultas BSD](../../scangoal/results_bsd.py#L195-L239), [consultas Highlightly](../../scangoal/results_hl.py#L61-L115), [minutos](../../scangoal/details_hl.py#L74-L129), [agregação prospetiva](../../scangoal/opportunity_forward.py#L144-L191).

**Correção proposta.** Manter um resolvedor único com corte explícito e estados de `primário`, `corrigido`, `indisponível`, `em quarentena` e anulação. Planear revisão de resultados recentes dentro de uma quota comum. Mostrar contagens reconciliáveis ou limites de sensibilidade para perdas em falta e conflitos. Publicar um manifesto de população, versão e corte em cada consumidor.

**Risco de regressão.** Alto. O resultado publicado em data de fecho precisa de persistir ao lado da reconciliação atual. Quotas e latência precisam de monitorização durante a transição.

<a id="gr-09"></a>

## GR-09 — Avisos Stake podem expirar durante a publicação e perder seleções · P1

**Evidência e impacto.** A07 enviou depois de o sinal atingir 31 minutos com limite de 30. A06 confirmou revalidação de envio no notificador geral. A39 apresentou a mesma seleção nominal em mercados distintos e a segunda foi suprimida. A04 e A05 confirmaram que o reforço da mesma seleção é deduplicado e a seleção nova em um mesmo jogo é avisada no cenário válido. A42 confirmou que uma rejeição explícita, incluindo uma resposta 429 simulada, consome a oportunidade mesmo se o Telegram voltar a tempo. A38 obteve dois avisos de saúde na mesma hora. A02 e A03 confirmaram que em reinício o caminho ensaiado não repete ACK ambíguo. Em A02 o Git preservou a intenção e houve zero tentativas Telegram.

**Causa.** Stake testa frescura apenas no planeamento e marca `sent` na intenção. O filtro legado usa data, nomes e texto da seleção e continua a receber efeitos dos avisos atuais. O envio geral aplica efeitos antes de conhecer o desfecho e trata a falha explícita como terminal. O aviso de saúde depende de minutos do relógio sem registo de envio. Stake usa também ausência de outros tipos acumulados por jogo, uma condição dependente da completude da amostra disponível. [Planeamento e chaves](../../scangoal/notify.py#L557-L634), [efeitos](../../scangoal/notify.py#L1243-L1341), [aviso de saúde](../../scangoal/notify.py#L1439-L1447), [Stake](../../scangoal/stake_alerts.py#L43-L73), [persistência Stake](../../scangoal/stake_alerts.py#L144-L176).

**Correção proposta.** Enviar pelas mesmas transições duráveis, revalidar a seleção e a frescura após a aquisição. Chave canónica por mercado, período, seleção e destino. Converter chaves legadas numa migração com âmbito finito. Usar repetição com prazo só para rejeições confirmadas e evidência de que o destino não aceitou. Tratar a confirmação ambígua como trabalho humano de reconciliação. Aplicar limite de saúde persistente por janela.

**Risco de regressão.** Alto. A alteração de chaves pode soltar avisos de referência e uma repetição aplicada à classe errada pode duplicar envios.

<a id="gr-10"></a>

## GR-10 — Workflow com âmbitos e falhas que excedem a proteção operacional · P1

**Evidência e impacto.** A secção principal de `Collect ScanGoal` faz checkout e publicação no ref do despacho e pode ler as credenciais ou enviar avisos fora de `main`. O lock partilhado serializa execuções cooperantes, sem unir os históricos de ramos. Um despacho manual numa cópia antiga, mesmo em série, pode tentar aviso já marcado noutro ramo. A recolha só persiste os dados normais depois de etapas API, relatório e Telegram. Interrupção pode perder uma primeira leitura que não volta a existir. A43 mostrou que uma leitura rápida bem-sucedida sem versão nova não regenera o relatório. A cadeia automática continua quando passos tolerados falham e tenta retomar falhas persistentes sem uma política de backoff entre runs. Um erro repetido de sessão, dependência ou dados pode manter runs e avisos de saúde em circulação.

**Causa.** A restrição a `main` aparece nos passos rápidos, no orçamento e no sucessor, após os passos sensíveis. Leitura, envio e publicação do dataset só partilham o Git no fim. Permissões `contents:write`, `actions:write` e `id-token:write` abrangem todo o workflow. O tail BSD/OIDC comentado não tem ligação no caminho `main()` atual. [Workflow](../../.github/workflows/collect.yml#L3-L85), [retoma](../../.github/workflows/collect.yml#L104-L136), [caminho BSD](../../scangoal/results_bsd.py#L241-L263), [loop rápido](../../scangoal/watch.py#L28-L71).

**Correção proposta.** Restringir a produção no nível do job a repositório e ref esperados, com ambiente protegido. Persistir a primeira observação antes de processamento downstream. Separar leitura, publicação, dispatcher e notificador com permissões específicas. Remover a concessão OIDC inativa após verificação de consumidores e definir retoma com intervalos e orçamento de falhas.

**Risco de regressão.** Médio/alto. O corte pode afetar a cadeia de sucessores e o tempo até publicação. Ensaiar cancelamento em cada fronteira, autenticação inválida, conflitos no Git e retoma sem segredos.

<a id="gr-11"></a>

## GR-11 — Dependência vulnerável e cobertura incompleta de supply chain · P1

**Evidência e impacto.** `cryptography==44.0.3` consta de cinco advisories GitHub consultados por versão. Incluem distribuições wheel com OpenSSL vulnerável e problemas em rotas específicas da biblioteca. A aplicabilidade do caminho de cada CVE no coletor e na CI não foi verificada. Os workflows principais fixam revisões antigas de checkout e setup-python e o de testes usa tags móveis. As linhas pip não fixam dependências transitivas nem verificam hashes. A consulta de releases indicou checkout v7.0.1 e setup-python v7.1.0, acima das majors referenciadas.

**Causa.** Pins diretos congelados sem atualização, lock transitivo ou workflow dedicado de advisories. `tests.yml` não explicita permissões e guarda as credenciais padrão do checkout. [Pins Python](../../requirements.txt), [CI](../../.github/workflows/tests.yml#L1-L22), [workflow principal](../../.github/workflows/collect.yml#L28-L35).

**Correção proposta.** Atualizar para a versão corrigida e compatível depois de revisão dos advisories. Criar lock com hashes para produção e CI, usar refs completas verificadas para Actions, remover credenciais de checkout em jobs de leitura e auditar bibliotecas transitivas e imagem da plataforma. [GHSA-jwv3-5hgf-82ww](https://github.com/advisories/GHSA-jwv3-5hgf-82ww), [GHSA-g6cj-pr64-35w5](https://github.com/advisories/GHSA-g6cj-pr64-35w5), [GHSA-537c-gmf6-5ccf](https://github.com/pyca/cryptography/security/advisories/GHSA-537c-gmf6-5ccf), [GHSA-m959-cc7f-wv43](https://github.com/advisories/GHSA-m959-cc7f-wv43), [GHSA-r6ph-v2qm-q3c2](https://github.com/advisories/GHSA-r6ph-v2qm-q3c2).

**Risco de regressão.** Médio. Validar instalação limpa, compatibilidade de APIs criptográficas, versão Python, comportamento de checkout, acesso ao Git e compatibilidade dos runners.

<a id="gr-12"></a>

## GR-12 — Dados operacionais públicos e fronteiras de confiança frágeis · P1

**Evidência e impacto.** O repositório é público. Os ficheiros versionados contêm texto da conversa, metadados de botões, estados por aposta e diários de envio. No SHA auditado, 42 linhas continham metadados de botões. A leitura histórica por padrões selecionados devolveu zero resultados de credenciais candidatas. A autorização de sessão imprime o segredo em terminal por desenho. A concatenação do CSV preserva texto proveniente de fornecedor em células sem neutralização para folhas de cálculo. Os clientes HTTP dos fornecedores usam redirecionamento padrão após configurar cabeçalhos de autenticação. Estes pontos têm risco de exposição se forem processados em contextos que interpretam as células ou se o fornecedor redirecionar para outra origem. Nenhuma exploração ou fuga foi verificada. A API pública indicou que `main` está protegido. O detalhe de bypass e permissões dos colaboradores ficou por verificar.

**Causa.** Mistura do histórico da conversa e da saída pública no armazenamento de origem. O mesmo formato CSV trata apresentação e intercâmbio. Os limites de URLs estão no chamador e não em todas as transições HTTP. [Leitura de botões](../../scangoal/reader.py#L13-L42), [autorização](../../scripts/authorize_qr.py#L33-L43), [CSV](../../scangoal/report.py#L486-L501), [BSD](../../scangoal/results_bsd.py#L43-L62), [Highlightly](../../scangoal/results_hl.py#L31-L40), [política padrão em urllib](https://github.com/python/cpython/blob/3.11/Lib/urllib/request.py).

**Correção proposta.** Inventariar e aprovar os dados exportáveis, manter estado de entrega e conversa em armazenamento de acesso restrito e publicar agregados necessários. Criar exportação segura para folhas de cálculo, validar redirecionamentos por origem e usar identidade de sessão de acesso mínimo. Manter o tratamento de stdout de autorização fora de logs persistentes. A inspeção do dashboard encontrou criação de nós com texto e links construídos para destinos conhecidos no percurso analisado.

**Risco de regressão.** Médio. Alterações no CSV podem afetar análise, e acesso separado exige migração do estado e das permissões. A revisão de acesso por proprietário precede qualquer retirada de histórico.

<a id="gr-13"></a>

## GR-13 — Guarda Cloudflare pode bloquear permanentemente a retoma de um coletor · P1

**Evidência e impacto.** O03 simulou uma resposta definitiva 401 ao primeiro despacho e configuração corrigida no tick seguinte. A guarda respondeu `already_requested`, fez um único POST e não retomou. O02 confirmou que a configuração inativa impede o pedido. O04 confirmou que uma atividade recente inibe recuperação. O01 produziu erro de observação quando GitHub devolveu 503. A configuração versionada está em observação e a implantação real ficou por verificar.

**Causa.** A intenção é única por ID de coletor. Só a resposta 429 a remove. A chave não distingue rejeição definitiva, confirmação perdida, pedido aceite, circuito operacional e revisão autorizada. Cada tick depende da API e da visibilidade do sucessor. [Guarda](../../ops/continuity/watchdog.mjs#L44-L75), [configuração](../../ops/continuity/wrangler.toml#L7-L23).

**Correção proposta.** Persistir resultados distintos e só repetir quando há evidência da rejeição. Para estado ambíguo, reconciliar metadados da próxima execução com uma janela e um limite. Dar ao operador uma forma auditável de retomar a partir de uma falha definitiva e um circuito para suspensão. Verificar em staging sem ativar no ambiente atual.

**Risco de regressão.** Alto se uma classificação incorreta provocar ciclos de despacho. Ensaiar 401, 403, 422, 429, timeout, 5xx, ACK perdido, GitHub indisponível e atividade recente.

<a id="gr-14"></a>

## GR-14 — Texto LLM pode afirmar vantagem sem suporte · P1

**Evidência e impacto.** A27 aceitou uma nota categórica de ganhos futuros ancorada apenas no ID da hipótese. A40 rejeitou duas propostas com suporte exatamente igual. As dimensões admissíveis, a redundância e o mínimo de jogos são controlos relevantes, mas a forma do JSON e a lista de palavras de alegação não verificam semântica. Os campos `reason` e `note` podem chegar à página. As provas de cronologia frágeis de GR-03 propagam-se ao resumo que o prompt consome. O vocabulário inclui também rótulos textuais de competições. A restrição de contexto a números agregados aplica-se ao digest Polymarket, com outras secções do prompt contendo texto de categorias.

**Causa.** Aceitação de língua natural por filtro lexical e pouca vinculação de cada afirmação a uma métrica calculada. `top` tende a ordenar probabilidade histórica com amostra baixa. A mensagem de prompt «Hipóteses com mais dados» usa uma lista ordenada por outro critério. [Prompt e saídas](../../scangoal/llm.py#L137-L195), [notas](../../scangoal/llm.py#L217-L239), [propostas](../../scangoal/llm.py#L276-L331), [ordenação](../../scangoal/brain.py#L1013-L1025), [apresentação](../../docs/dashboard.js#L613-L620).

**Correção proposta.** Restringir saídas públicas a sugestões estruturadas com identificadores e evidência calculada localmente. Validar origem, janelas, unidades, hipótese prévia e limite de amostra. Ligar toda a afirmação sobre lucro ao módulo estatístico com texto fixo e manter sugestões livres em revisão.

**Risco de regressão.** Médio. A riqueza das notas pode diminuir. Verificar calibração do contexto, rejeição de alegações, duplicados por jogo e desempenho de descoberta em dados novos.

<a id="gr-15"></a>

## GR-15 — O dashboard pode indicar atualidade indevida ou aceitar JSON incompatível · P2

**Evidência e impacto.** W04 mostrou como «Atualizado» um relatório de seis horas com carimbo recente do coletor. W05 mostrou como «Atualizado» um carimbo de recolha no dia seguinte. W06 verificou que uma secção interna incompleta ultrapassa a validação e introduz um valor em falta no texto do ranking. W03 manteve o último relatório válido e mostrou erro perante JSON cortado. A visão geral do snapshot real mostrou o ROI histórico e a certificação em campos distintos. A agenda depende da frescura do relatório e de testes temporais adicionais dos itens.

**Causa.** Frescura baseada no relógio do coletor em vez de um par coerente geração/recolha. A validação verifica o topo e tipos, mas delega vários contratos internos às vistas. O novo snapshot é adotado antes da renderização completa. [Frescura](../../docs/dashboard.js#L101-L119), [ranking](../../docs/dashboard.js#L538-L556), [validação e atualização](../../docs/dashboard.js#L893-L1036).

**Correção proposta.** Exigir uma versão do esquema, coerência temporal e contrato de cada campo renderizado. Exibir idade da recolha e da publicação separadamente. Validar e renderizar com fallback antes de promover a revisão, isolar erros por painel e conservar uma cópia válida com o estado de atraso.

**Risco de regressão.** Médio. Relatórios legados precisam de migração de esquema e de estados explícitos para dados vazios, atrasados, incompletos e recentes.

<a id="gr-16"></a>

## GR-16 — Simulador reutiliza saldo antes da liquidação · P2

**Evidência e impacto.** A série publicada tem até 600 apostas por hora do sinal e lucro conhecido posteriormente. W09 confronta o simulador com duas apostas ganhas à mesma hora. As operações são avaliadas sequencialmente e o saldo da primeira financia a segunda sem uma hora de libertação. Isto altera a banca, a exposição em simultâneo e a rentabilidade com stake percentual. A interface identifica o exercício como simulação histórica e declara o limite de 600 apostas.

**Causa.** O relatório só publica aposta, hora do sinal, resultado e lucro na série. O simulador liquida cada aposta no momento em que a lê. [Série](../../scangoal/report.py#L417-L426), [simulação](../../docs/dashboard.js#L644-L669), [texto UI](../../docs/index.html#L123-L136).

**Correção proposta.** Publicar hora de entrada e de liquidação. Reservar exposição, distinguir saldo e banca disponível, bloquear mercados pendentes e assinalar a janela histórica. Até essa mudança, tornar explícita a premissa de liquidação sequencial instantânea.

**Risco de regressão.** Médio. A nova curva pode ficar abaixo da antiga e o limite de 600 pode truncar apostas ainda expostas no início. Testar apostas simultâneas, devoluções e pendências.

<a id="gr-17"></a>

## GR-17 — Prova Git incompatível com a recolha padrão e relógios não independentes · P1

**Evidência e impacto.** `certification.history_proof` rejeita clone raso. O checkout da recolha não define `fetch-depth` e o GitHub Action usa por defeito profundidade 1. A ativação manual não bastaria para produzir certificação operacional. Quando o histórico está disponível, `published_versions` trata o timestamp do commit local como hora de publicação. A avaliação LLM usa evidência explícita de servidor para a publicação do protocolo, mas usa timestamps de commits para nascimento e observações subsequentes. Estes carimbos demonstram coerência interna do repositório, com limitações para provar publicação à hora declarada.

**Causa.** Dependência de histórico Git completo é um requisito da prova que a execução por defeito não satisfaz. A verificação mistura tempo declarado pelo autor da alteração e tempo de observação verificável externamente. [Checkout](../../.github/workflows/collect.yml#L28-L34), [histórico de certificação](../../scangoal/certification.py#L142-L214), [prova LLM](../../scangoal/llm_eval.py#L75-L185), [padrão checkout](https://github.com/actions/checkout#usage).

**Correção proposta.** Fixar uma política de publicação com recibos de serviço vinculados ao SHA dos eventos e utilizar leitura de histórico compatível. Estado de certificação desativado até as provas temporal e estatística passarem. Selar cada conclusão com versão, manifest de entradas e marca de corte independente.

**Risco de regressão.** Médio/alto. Aumentar a profundidade de cada checkout faz subir a latência. Pode ser preferível consumir um registo de evidência publicado e verificável com tamanho limitado.

<a id="gr-18"></a>

## GR-18 — Quotas calculadas localmente não garantem custo zero · P1

**Evidência e impacto.** O caminho online LLM exige autorização explícita, ID válido, modo Git em `main` e reserva durável. A26 bloqueou as duas rotas sem aprovação ou sem orçamento. O limite de 8 000 neurónios é um saldo do projeto com preços e estimativa de tokens locais. O código não observa uso de outras aplicações na conta. A telemetria do SHA fixado registava 14 tentativas reservadas naquele dia e 3 080,9 neurónios de contabilidade local. A existência de atividade histórica impede inferir que o serviço esteja globalmente desligado. Highlightly tem duas cadências separadas e o cálculo de máximos configurados é 16 × 8 + 12 × 5 = 188 pedidos/dia antes dos limites declarados pelo fornecedor ou omissão de trabalho. Cada passo perde a visibilidade de quota entre execuções e o forçamento pode encurtar a cadência de resultados. O GitHub público tem minutos gratuitos em runners padrão. Isso não garante capacidade de fornecedores externos.

**Causa.** Orçamentos por cliente e intervalos de execução distintos. A reserva AI controla concorrência do projeto e presume conta/plano confirmados. A quota dos fornecedores vem de cabeçalhos opcionais fornecidos depois do pedido. [Guardas AI](../../scangoal/llm.py#L359-L465), [reserva](../../scangoal/llm_budget.py#L132-L155), [resultados](../../scangoal/results_hl.py#L17-L23), [detalhes](../../scangoal/details_hl.py#L93-L125), [Workers AI](https://developers.cloudflare.com/workers-ai/platform/pricing/), [GitHub Actions](https://docs.github.com/en/actions/concepts/billing-and-usage).

**Correção proposta.** Usar limite duro na conta, registo de orçamento compartilhado por fornecedor, observação de preço, medição de tráfego e comportamento previsível com cabeçalho ausente ou limite confirmado. Definir um ensaio de aumento de carga com dados simulados antes de alterar cadências ou quotas.

**Risco de regressão.** Médio. Limites mais conservadores podem aumentar a proporção de FT em falta. Medir cobertura e atraso por competição e período.

<a id="gr-19"></a>

## GR-19 — Custo de crescimento e observabilidade insuficiente · P2

**Evidência e impacto.** A árvore pesa cerca de 15,8 MB em ficheiros e o registo de hipóteses cerca de 3,84 MB. O relatório de 1 094 748 bytes é publicado duas vezes e pedido pelo dashboard a cada três minutos, além da reabertura da página. Um separador contínuo pode solicitar aproximadamente 525 MB lógicos por dia antes de compressão e cache de entrega. O coletor relê registos completos e reavalia duas populações em cada relatório. As regressões de volume são particularmente relevantes para o limite de 18 minutos e para a cadência em que o workflow estima a oportunidade. O facto de existir um passo com erro tolerado, ou um processo que termina com código zero após falha LLM/detalhes, reduz visibilidade quando não há sinais novos.

**Causa.** File stores e processos em lote repetidos, relatório unificado pesado, crescimento ilimitado das combinações legadas e ausência de SLOs de atraso, falha, cobertura e custo por etapa. [Relatório](../../scangoal/report.py#L581-L704), [combinações](../../scangoal/brain.py#L127-L206), [cadência UI](../../docs/dashboard.js#L1074-L1082), [falhas detalhes](../../scangoal/details_hl.py#L133-L138), [falhas LLM](../../scangoal/llm.py#L517-L523).

**Correção proposta.** Projetar cache e avaliação incremental a partir de eventos, carregar o resumo do dashboard numa resposta pequena e anexos a pedido, ligar metadados de versão e frescura a um endpoint leve. Medir tempos, atraso da fila, slots perdidos, quotas, taxa de desconhecidos, idade dos dados, atraso de Pages e desfechos de envio.

**Risco de regressão.** Médio. A otimização pode reduzir a consistência entre ficheiros se versões e cortes deixarem de ser transacionais. Verificar resultados bit a bit em replays antes de substituir o lote.

<a id="gr-20"></a>

## GR-20 — Documentação de operação descreve contratos obsoletos · P3

**Evidência e impacto.** O README descreve Highlightly «de 4 em 4 horas», checkout integral para certificação e gerador de uma ou duas condições. O workflow executa Highlightly com intervalo de 90 minutos, faz checkout raso por defeito e o cérebro gera até três condições. O rótulo de PR e as reproduções antigas preservam regras diferentes das ativas. Uma decisão de operação baseada nessas descrições pode alterar a quota, a cobertura ou a referência de auditoria.

**Causa.** Atualizações de workflows e lógica sem uma fonte comum para valores e instruções vigentes. [README](../../README.md#L40-L47), [guia do cérebro](../../README.md#L76-L86), [workflow](../../.github/workflows/collect.yml#L28-L49), [gerador](../../scangoal/brain.py#L40-L54).

**Correção proposta.** Documentar os valores ativos no mesmo contrato de configuração e marcar procedimentos históricos com âmbito e SHA. Verificar exemplos executáveis no CI de leitura.

**Risco de regressão.** Baixo, desde que se conservem como evidência os procedimentos e resultados datados.
