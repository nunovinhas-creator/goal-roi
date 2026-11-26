# Goal-ROI V7 — Relatório executivo

**Decisão:** avançar com a revisão dos drafts offline. A ativação dos snapshots e a migração de produção ficam em **NO-GO**. A arquitetura recomendada combina arquivo segmentado, imutável e portátil com publicação separada das vistas derivadas.

O corte principal de 26/11/2026 é e3a0760813f8b2ba4f4953a84dde0a697a117a10. A PR #98 foi ensaiada em 33b469e47f20cd0a6ece2ef7ad369dce18ad54c7. [Corte principal](https://github.com/nunovinhas-creator/goal-roi/commit/e3a0760813f8b2ba4f4953a84dde0a697a117a10) · [PR #98](https://github.com/nunovinhas-creator/goal-roi/pull/98)

| Componente | Resultado | Veredito |
|---|---|---|
| Cobertura do parser | **1 379** primeiras mensagens no contador, entre **5 687** mensagens e **9 079** versões verificadas. São 772 Kalshi, 424 Betfair e 183 de outras famílias. Há ainda 144 rejeições Polymarket fora do contador. | **GO** para diagnóstico offline. **NO-GO** para admissão automática ou reprocessamento científico histórico. |
| Git | **129,7 MiB** de pesos de objetos alcançáveis. Os blobs únicos dos dois relatórios ocupam **100,3 MiB**, cerca de **77%**. | **NO-GO** como único arquivo definitivo. |
| Arquivo segmentado | Reconstruiu o diário real em 24 segmentos, byte a byte. Verificou **1 825 revisões** simuladas do diário, com **68,0 MiB** comprimidos após cinco anos. | **GO** para revisão offline. |
| Snapshots #98 | Corte atual com primeiro conteúdo em **2,15 s** e conclusão em **2,78 s**. Foram reproduzidas corrupção persistente, mistura após interrupção e divergência da entrada prospetiva secundária. | **NO-GO** para ativação. |
| Continuidade | Recuperou **670** e **1 497** IDs visíveis em janelas simuladas de 24 e 72 h, sem duplicações e com diário vivo intacto. | **GO** para o plano de recuperação offline. **NO-GO** para failover automático entre fornecedores. |

O aumento de 16 desde o corte de 1 363 deve-se a 13 movimentos de odds, dois Betfair e um aviso de serviço. A gramática isolada identifica a estrutura de 766 Kalshi e 424 Betfair. A elegibilidade exige verificação separada de contrato, mercado, identidade, relógio e proveniência. [Corte anterior](https://github.com/nunovinhas-creator/goal-roi/commit/12e1de7d5dfe5abb5a4c867d9c17f7c5206eb12f)

O protótipo gerou localmente um tar compatível com o formato Pages, com seis snapshots de hashes validados. O resultado ainda precisa de prova de deploy e cache num ambiente previamente autorizado. A V6.1, o Brain V5, a PR #98, o protocolo e o registo prospetivo foram preservados.

[Draft de auditoria do parser](https://github.com/nunovinhas-creator/goal-roi/pull/101) · [Draft de armazenamento](https://github.com/nunovinhas-creator/goal-roi/pull/102) · [Auditoria técnica](auditoria-tecnica.md)

## Atualização — continuação de 26/11/2026

Retoma do trabalho do Codex no ramo `claude/goal-roi-v7-continuacao-ozagkj`, que junta as PRs #101 e #102 sem as alterar. A PR #98, a produção, o Brain V5, o protocolo e o registo prospetivo não foram tocados.

| Componente | Resultado novo | Veredito |
|---|---|---|
| Reprodução | Censo do parser, continuidade (24/72 h) e watchdog dão JSON idêntico ao do Codex no corte e3a0760. Os quatro defeitos da PR #98 reproduzem-se. | **GO** |
| Parser — semântica | Dos 105 nomes Kalshi: 73 aliases do vencedor, 9 alias + handicap, 20 código `12`, 3 sem lado. 52 negados (50 equipa, 2 resultado exato). Polymarket: 2 spreads contraditórios, 12 cabeçalhos sem relógio. [SEMANTICA.md](../parser/SEMANTICA.md) | **NO-GO** para qualquer admissão; zero admissões |
| Snapshots — correções | Publicador isolado com duas ranhuras, ponteiro atómico e fixação conjunta de relatório, entrada prospetiva e protocolo; cliente só por SHA-256. Corrupção reparada, 0 revisões misturadas em 7 pontos de interrupção + SIGKILL, entrada prospetiva divergente recusada, adulteração de igual comprimento recusada. [README](../snapshots/README.md) | **GO** para revisão offline; **NO-GO** para ativação |
| Snapshots — desempenho | 20 repetições: primeiro conteúdo p50/p95 1,21/1,25 s, completo 1,76/1,79 s (também com CPU ocupada). Os 2,15/2,78 s anteriores não se reproduzem. | **GO** em laboratório; falta staging HTTPS/CDN |
| Arquivo — todas as fontes | 240 revisões reais byte-idênticas; índice reconstruído idêntico; retenção das vistas com lápides; exportação idêntica. Por commit, o segmentado custa ~8× os deltas Git; selado a cada 12 h, ~1,2 MB/dia (~2,2 GB em 5 anos). As vistas são 80% do pack diário (38,1 MB/dia). [ARQUITETURA.md](ARQUITETURA.md) | **GO** para cópia selada a 12 h; **NO-GO** para segmentado por commit e para mudar rotas |
| Restauração | 7/7 cenários: perda do Git, bloco e HEAD corrompidos, espelho incompleto (recusa), espelho atrasado (RPO declarado), restauração interrompida, bundle sem cadeia (recusa). | **GO** offline; **NO-GO** para recuperação automática |

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
