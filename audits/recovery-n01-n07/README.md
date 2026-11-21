# Revalidação da auditoria Goal-ROI

O replay Q7 usa os mesmos dados do SHA `9db4f20c614a645a4949f88562d5054ec53b8181` e o corte `2026-11-20T20:27:44Z`. A referência do código dos rascunhos é `86cdadcf719bdf3a7550f709d02985e9d9082b49`. `replay_q7.py` executa a mesma instrumentação a partir de cada checkout. O relatório publicado e os ficheiros históricos continuam disponíveis nos respetivos commits. [PR #71](https://github.com/nunovinhas-creator/goal-roi/pull/71)

| Cálculo Q7 a odd anunciada | Apostas | Liquidadas | ROI |
| --- | ---: | ---: | ---: |
| Histórico publicado | 1 056 | 832 | 3,16% |
| Rascunhos antes da correção | 1 056 | 807 | 2,47% |
| Candidato no mesmo corte | 1 056 | 739 | 1,83% |
| Reconciliação offline posterior dos 25 casos | 1 056 | 759 | 1,48% |

A reconciliação posterior acrescenta 20 liquidações e −2,305 unidades ao cálculo do candidato. Cinco casos ficam pendentes. Dois precisam de ligar o ID original ao clube correto na Roménia. Três precisam de provas do período ou do contrato do handicap ao vivo. A coluna `delta_ROI_isolado_pp` mede a inclusão isolada de uma aposta e o seu efeito no denominador. O cenário agregado usa as variações de lucro e de denominador em conjunto. [Tabela dos 25 casos](q7.csv) · [Agregados](q7-summary.json)

O candidato passa outras 68 apostas a pendentes por falta de evidência com o contrato mais estrito. Destes casos, 50 vinham de Highlightly, dez de FT embutido, cinco de BSD, dois de notas e um da reconstrução dos 90 minutos. O conjunto de apostas mantém os mesmos identificadores nessa comparação. As 20 decisões posteriores pertencem à análise offline e não retrodatam o instante em que a informação ficou disponível.

## Critérios de GO

| Área | Evidência disponível | Condições em falta |
| --- | --- | --- |
| Operacional | Exclusividade de intenção e recuperação da publicação em remotos de teste. Recusa, confirmação perdida, concorrência, conflito e retomada num clone limpo exercitados. | Validar a recuperação num runner real autorizado, confirmar versão e parâmetros do Worker, inventariar escritores e reconciliar as entregas Telegram incertas no destino. |
| Integridade | Corte comum nos consumidores offline. FT sem prova e identidades contraditórias impedem pagamento. | Obter prova adicional para as 68 liquidações bloqueadas e completar os cinco casos Q7 pendentes. |
| Científico | Verificação criptográfica RFC 3161 com recibo sintético real. Histórico Git isolado e recibos posteriores ao corte reprovados. | Prova externa da série completa de admissões e desfechos, coorte LLM prospetiva testemunhada e revisão estatística independente. |
| Económico | ROI histórico e análise de sensibilidade Q7 reproduzíveis para os dados fixados. | Amostra prospetiva válida, preços executáveis, custos, cobertura e resolução de incertezas de liquidação. |

O GitHub mostrou configuração estática `observe` e `RECOVERY_ENABLED=false` no repositório. A instalação Cloudflare não foi observada. A identidade GitHub pôde consultar colaboradores, concluir que `main` está protegida e verificar os rascunhos em Git. As consultas administrativas a deploy keys, segredos, variáveis e regras detalhadas devolveram `403`. O grupo Actions `scangoal-write` cobre os workflows que o usam. Os escritores externos ficam por verificar. [Continuidade](https://github.com/nunovinhas-creator/goal-roi/blob/main/ops/continuity/wrangler.toml) · [Actions](https://github.com/nunovinhas-creator/goal-roi/actions)

O snapshot de `main` `c68fcf1c6a013960f97732c24d3315a76707d5ae` continha 203 entregas gerais confirmadas e duas incertas no diário, sem confirmação independente do destino para estas últimas. A telemetria LLM registava oito chamadas nesse dia e uma execução `ok` às 07:43:40 UTC. A etapa autónoma aparece no workflow de `main`. A sua remoção consta da #74. [Snapshot](https://github.com/nunovinhas-creator/goal-roi/tree/c68fcf1c6a013960f97732c24d3315a76707d5ae) · [PR #74](https://github.com/nunovinhas-creator/goal-roi/pull/74)
