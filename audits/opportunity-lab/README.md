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
