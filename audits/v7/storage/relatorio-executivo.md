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

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
