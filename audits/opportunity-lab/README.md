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

Uma aposta teórica por jogo: **primeiro sinal elegível**, com a odd e quota originais. O sinal só entra se a leitura estiver comprovada e deixar mais de 15 minutos antes do início. O resultado só conta quando uma observação FT válida está disponível no corte, após a duração mínima do jogo. Uma divergência visível põe o encontro em quarentena. O desfecho primário fecha três dias após o início. Um encontro sem FT comprovado e sem conflito até esse prazo conta como **−1 unidade imputada**, e revisões posteriores têm contagens próprias. O ROI é calculado à odd anunciada e com **redução de 2% da odd**; comissões e execução real continuam sem prova.

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

- `forward_games`, `ft_confirmed`, `pending`, `quarantined` e `timeouts_counted_as_losses` tornam a cobertura explícita. `settled` soma FT confirmados e perdas imputadas.
- As métricas de stake, lucro absoluto e ROI são distintas. A taxa de acerto usa apenas FT confirmados. `expected_profit` fica nulo enquanto o estudo for exploratório.
- `concentration` mede maior grupo e proporção por dia UTC de decisão, campeonato, encontro e lote de recolha. `overlap` conta encontros comuns e cotações coincidentes ou distintas.
- `descriptive_two_se_score` mostra o ROI com odd ×0,98 menos dois erros-padrão agrupados por dia UTC de decisão. Exige 30 FT confirmados, pelo menos dez dias com FT e no máximo 20% num dia, tanto nos FT como nos resultados liquidados. Esta penalização descritiva pode deixar dependência residual entre dias.
- `rank_exploratory` só aparece quando o diagnóstico acima é calculável. Trinta jogos dão início à análise, com perdas imputadas, cobertura e concentração visíveis.

### Plano confirmatório para revisão independente

1. Congelar publicação, regra, população, janela, custos, stake, definição de cotação executável, política de dados em falta e a diferença mínima economicamente relevante antes de observar a nova amostra. Recolher evidência numa janela posterior à descoberta. Fixar a potência e o horizonte por simulações que preservem lotes, campeonatos e dependência temporal.
2. Para retorno absoluto, escolher antecipadamente um teste sequencial de lucro com nulo condicional. Fixar a política de admissão: comprometer um encontro ou lote com stake total predefinido antes da decisão e admitir a unidade seguinte apenas depois de o resultado aproveitado estar fechado. Rever a prova de previsibilidade e simular nulos com séries e lotes correlacionados. O e-processo de `brain.confirmation` documenta a versão por encontro; as suas suposições e evidência de preços teriam de ser verificadas para este estudo.
3. Para benefício incremental, congelar um comparador separado por mercado, período e oportunidade. Se houver jogos comuns, definir estatística emparelhada e um nulo comparativo próprios. O benchmark atual serve como referência descritiva.
4. Registar previamente as hipóteses primárias e análises secundárias. Para múltiplas análises confirmatórias, usar alocação sequencial de α antes de cada seleção ou Holm numa família fixa de valores-p válidos sob a dependência. Documentar regras de paragem, falsos positivos e dados censurados. O relatório atual mantém `statistical_claim=none` e `certification=not_activated`.


## Fase 4: a LLM recebe apenas conclusões operacionais com limites explícitos

`scangoal.llm.opportunity_digest` consulta localmente a regra fixa da PR #55, o carimbo de ativação efetivo e o registo de resultados provados **sem rede ou novos segredos**. Aplica a mesma auditoria cruzada de `scangoal/opportunity_ranking.py` e produz apenas agregados; um erro, prova ausente ou divergência suprime o contexto em vez de inventar evidência.

`build_prompt` aceita uma síntese opcional `opportunity`. A secção nova tem um **máximo de três frases**, sempre com o nome fixo da regra e quantidades agregadas. Com menos de 30 jogos futuros liquidados mostra apenas `n` e a indicação de amostra insuficiente: **não envia ROI, probabilidades de lucro nem recordes de 8/9 históricos ao modelo**. Só com 30 ou mais jogos futuros são mostrados o ROI teórico com odd ×0,98 e o benchmark sobreposto, rotulados exclusivamente como *diagnóstico exploratório, não certificado*.

Nem `build_prompt` nem `opportunity_digest` acrescentam qualquer permissão ao modelo. As propostas continuam sujeitas à validação de características, suporte e redundância e nascem com carimbo posterior ao registo (`born_utc`). Não há novas chamadas à Cloudflare Workers AI, mudança de modelo, alteração de `DAILY_NEURONS=8000`, reativação da certificação, envio ao Telegram ou incorporação no ranking científico.

A interface é **fail-closed**: num ambiente sem ativação, devolve `None` e mantém o pedido anterior; dados corrompidos, cronologias contraditórias, identificadores de regra diferentes ou classificações falsamente certificadas são rejeitados. O percurso usa apenas ficheiros de produção já existentes, em modo de leitura. Os testes de `tests/test_opportunity_llm_context.py` impedem fugas de nomes/textos privados e a contaminação por lucros de amostras curtas.
