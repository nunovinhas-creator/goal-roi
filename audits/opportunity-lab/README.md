# Goal-ROI — laboratório de oportunidades (PR #55)

**Estudo separado; sem certificação, novos avisos ou apostas reais.**

## Hipótese fixada antes de observar novos jogos

A PR #55 tornou pública em `main`, às **2026-11-19T15:16:22Z** (hora do merge confirmada pelo GitHub), a regra:

- fonte `POLYMARKET`, momento `pre`
- `market=Asian Total`, `selection=Over 2.5` do jogo inteiro
- `share < 80`, `total < $10,000`, `odds >= 1.10`
- primeiro sinal original, sem FT/edição na primeira leitura, em jogo com identidade verificável

O JSON `polymarket-over25-v1.json` fixa estas condições e a referência de publicação. O início da contagem prospetiva é o **mais tardio** entre esse merge e a ativação real `migrations.polymarket_over25_v1.at_utc` no registo do notificador. A versão original tem de ter sido emitida **e** lida depois do início de contagem.

Os nove jogos históricos que levaram à escolha desta regra **não entram como validação prospetiva**.

## Auditoria read-only

```bash
python -m unittest discover -s tests -p test_opportunity_forward.py -v
python -m scripts.opportunity_forward_audit
```

O script usa apenas `data/messages.jsonl`, `notify_state.json` e provas FT de `results_hl.json`/`results_bsd.json`/edições do próprio bot. Não requer credenciais; não altera `data/`, GitHub, Telegram, Cloudflare, workflows, SHADOW ou o notificador.

Uma aposta teórica por jogo: **primeiro sinal elegível**, com a odd e quota originais. O sinal só entra se a leitura estiver comprovada e deixar mais de 15 minutos antes do início. O resultado só conta como conhecido se a hora do primeiro registo for provada, posterior ao início e anterior à auditoria. Divergências de FT ficam em quarentena. Jogos ainda sem resultado ficam pendentes; depois de 3 dias, perdas por ausência de resultado contam como **−1 unidade**. O ROI é calculado à odd anunciada e com **redução de 2% da odd**; comissões e execução real continuam sem prova.

## Leitura correta dos resultados

- `not_activated`: não há prova de ativação do alerta; zero jogos prospetivos admissíveis.
- `forward_games`: jogos únicos com sinal original elegível após o corte.
- `settled` inclui perdas por 3 dias sem FT comprovado; `pending` e `quarantined` não são vitórias.
- `roi_advertised` e `roi_odds_minus_2pct` são diagnósticos de retorno **teórico**, sem slippage real nem preço executado.
- `scientific_claim=none`, `validation=not_activated`, `alerts_changed=false` em **todos** os relatórios.

Esta não é a certificação `brain/certification` nem a auditoria da LLM. Não muda automaticamente o estado de hipóteses nem o ranking em produção. As portas da [issue #56](https://github.com/nunovinhas-creator/goal-roi/issues/56) continuam obrigatórias: tamanho de amostra, independência, estabilidade, baselines, correção de multiplicidade, cronologia e verificação em produção. Não promover estratégias com 8/9 resultados passados.

**Limite conhecido:** esta auditoria analisa sinais originais existentes no GitHub (captura partilhada), não demonstra uma fonte SHADOW autónoma nem SLA de 120 segundos. O trabalho da PR #47 continua separado.

## Fase 3: ranking conservador e benchmark (somente leitura)

A execução independente `python -m scripts.opportunity_ranking` produz um JSON agregado sem identificadores de jogos, sem escrever ficheiros ou enviar mensagens. A única candidata inscrita, por enquanto, é a regra **pré-registada** `polymarket_pre_over25_share_lt80_total_lt10000_v1` da PR #55.

O benchmark usa **sinais Polymarket pré-jogo, Asian Total Over 2.5 sem os cortes de quota e valor**, respeitando *o mesmo instante inicial*, odds válidas, versão original, primeira leitura comprovada, pelo menos 15 minutos antes do jogo, mesmo tratamento de resultados e uma aposta por jogo. É um benchmark **sobreposto**, não um controlo independente, e **não** fornece prova causal ou estatística de vantagem.

Métricas calculadas para a candidata e o benchmark:

- `forward_games`, `settled`, `pending`, `quarantined`, `wins`, `losses`, `timeouts_counted_as_losses`.
- `roi_advertised`: lucro teórico médio por jogo liquidado à odd original.
- `roi_odds_minus_2pct`: ROI após penalização fixa de 2% na odd (não é medida de slippage/comissões executados).
- `roi_minus_baseline_descriptive`: diferença de ROIs observados, sem inferência.
- `descriptive_two_se_score`: ROI reduzido menos duas vezes o erro-padrão amostral dos lucros individuais **se houver pelo menos 30 jogos futuros liquidados**. Esta penalização exploratória **não é um intervalo de confiança** nem uma correção de multiplicidade.
- `rank_exploratory`: nulo enquanto existirem menos de 30 jogos liquidados. Mesmo após 30, qualquer prioridade é **apenas para investigação**, nunca uma classificação como aposta lucrativa.

A forma do relatório admite candidatos adicionais futuramente, mas a implementação só reconhece **uma** regra congelada. Mais regras exigem registo prospectivo independente, orçamento de multiplicidade e testes adversariais. O campo `statistical_claim` permanece `none`, `certification=not_activated`, `automatic_alerts_changed=false` em todos os casos. **Não** altera o cérebro, a LLM, o Telegram nem o workflow de produção. Se a auditoria anterior e o novo cálculo não coincidirem, recusa a avaliação em vez de publicar estatísticas inconsistentes.

Este passo **não conclui** a Fase 3 no seu sentido científico integral: faltam calibração estatística validada, custo de execução real, qualidade das odds, vários candidatos pré-registados e a correção de testes múltiplos. É a infraestrutura de *ranking exploratório sem ativação* para prosseguir a investigação sem olhar para resultados futuros.

## Fase 4: a LLM recebe apenas conclusões operacionais com limites explícitos

`scangoal.llm.opportunity_digest` consulta localmente a regra fixa da PR #55, o carimbo de ativação efetivo e o registo de resultados provados **sem rede ou novos segredos**. Aplica a mesma auditoria cruzada de `scangoal/opportunity_ranking.py` e produz apenas agregados; um erro, prova ausente ou divergência suprime o contexto em vez de inventar evidência.

`build_prompt` aceita uma síntese opcional `opportunity`. A secção nova tem um **máximo de três frases**, sempre com o nome fixo da regra e quantidades agregadas. Com menos de 30 jogos futuros liquidados mostra apenas `n` e a indicação de amostra insuficiente: **não envia ROI, probabilidades de lucro nem recordes de 8/9 históricos ao modelo**. Só com 30 ou mais jogos futuros são mostrados o ROI teórico com odd ×0,98 e o benchmark sobreposto, rotulados exclusivamente como *diagnóstico exploratório, não certificado*.

Nem `build_prompt` nem `opportunity_digest` acrescentam qualquer permissão ao modelo. As propostas continuam sujeitas à validação de características, suporte e redundância e nascem com carimbo posterior ao registo (`born_utc`). Não há novas chamadas à Cloudflare Workers AI, mudança de modelo, alteração de `DAILY_NEURONS=8000`, reativação da certificação, envio ao Telegram ou incorporação no ranking científico.

A interface é **fail-closed**: num ambiente sem ativação, devolve `None` e mantém o pedido anterior; dados corrompidos, cronologias contraditórias, identificadores de regra diferentes ou classificações falsamente certificadas são rejeitados. O percurso usa apenas ficheiros de produção já existentes, em modo de leitura. Os testes de `tests/test_opportunity_llm_context.py` impedem fugas de nomes/textos privados e a contaminação por lucros de amostras curtas.
