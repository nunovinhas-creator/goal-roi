# Integração operacional dos snapshots V6 (PR #98, draft)

Esta PR é separada e está empilhada sobre a #97. **Fica inativa por omissão:** nada muda em produção enquanto a variável do repositório `GOAL_ROI_V6_SNAPSHOTS` não for `on`.

## Crescimento do histórico Git (medição e projeção)

**Medição.** Usei 60 revisões reais e consecutivas de `docs/report.json` do `main`. Para cada uma corri o gerador de snapshots e fiz um commit em três repositórios descartáveis. No fim corri `git gc` e comparei o tamanho do pack. O custo por revisão é a inclinação entre 10 e 60 revisões, para excluir o primeiro commit completo.

| Esquema | Custo por revisão | Acréscimo dos snapshots |
| --- | ---: | ---: |
| Só o relatório (produção atual) | 19,7 KiB | — |
| Snapshots com nomes estáveis (**esta versão**) | 23,7 KiB | **+4,0 KiB** |
| Snapshots com o hash no nome (versão anterior desta PR) | 38,6 KiB | +18,9 KiB |

**Taxa de revisões:** entre 381 e 540 commits de recolha por dia. Contagem pela pesquisa do GitHub: 2795 nos últimos 7 dias e 724 desde 25/11.

| Projeção | 30 dias | 90 dias | 365 dias |
| --- | ---: | ---: | ---: |
| Relatório atual (sem V6) | 220–312 MiB | 660–935 MiB | 2,6–3,7 GiB |
| + snapshots com nomes estáveis | +45–63 MiB | +134–190 MiB | +0,53–0,75 GiB |
| + snapshots com hash no nome (rejeitado) | +211–299 MiB | +633–897 MiB | +2,5–3,6 GiB |

Os nomes estáveis reduzem o acréscimo em cerca de 79 %, para cerca de +20 % sobre o crescimento atual. O crescimento do próprio relatório (2,6–3,7 GiB por ano) é um risco que já existe, independente da V6, e merece uma decisão própria.

## Arquiteturas comparadas

| Opção | Crescimento | Atomicidade e coerência | Custo e risco | Decisão |
| --- | --- | --- | --- | --- |
| **A. Nomes estáveis no `main`, no mesmo commit do relatório** | +1,5–2,1 MiB/dia | O commit publica relatório, snapshots e manifesto juntos. O cliente verifica o SHA-256 de cada ficheiro e pede-o com `?v=<sha>` (contorna caches do CDN). Uma revisão incompleta leva ao relatório completo nesse ciclo. | 0 €. Sem infraestrutura nova. | **Adotada** |
| B. Nomes com o hash (versão anterior) | +7–10 MiB/dia | As mesmas garantias. | Duplica o crescimento. | Rejeitada |
| C. Branch órfã reescrita (um só commit) servida por `raw.githubusercontent.com` | ~0 permanente | Não é atómica com o commit do relatório; a coerência fica só no SHA-256. Cache de 5 minutos. | Exige force-push automático pela recolha e um segundo caminho de publicação. | Não recomendada |
| D. Pages publicado por artefacto de Actions (`deploy-pages`), sem commit dos snapshots | 0 | Publicação atómica do site inteiro. | Muda a origem do Pages (decisão humana nas definições). É gratuito, mas é uma alteração de infraestrutura. | Melhor opção a longo prazo; **fora do âmbito** |
| E. Sem snapshots (produção atual) | 0 | — | Primeiro carregamento de 3 MB. | Estado atual |

## Garantias desta versão

- **Nomes estáveis:** `dashboard-<nome>.json` e `dashboard-manifest.json`, sempre os mesmos 7 ficheiros. Restos de esquemas anteriores e ficheiros temporários são removidos.
- **Coerência:** o manifesto regista o SHA-256 de `docs/report.json` e de cada snapshot. Se o relatório mudar durante a construção, o resultado é descartado.
- **Atomicidade por revisão:** cada ficheiro é escrito com um nome temporário e renomeado; o manifesto é o último. A publicação Git leva tudo no mesmo commit. O cliente rejeita qualquer ficheiro cujo hash não coincida e usa o relatório completo.
- **Manifestos malformados:** qualquer forma inesperada gera `SnapshotError`. Exemplos: tipos errados, entradas a mais, caminhos fora da lista, sha ou bytes inválidos, JSON ilegível. Qualquer outra exceção termina com código 3, sem traceback.
- **Publicação restrita:** `check_paths` só aceita exatamente `docs/v6/snapshots/dashboard-(manifest|summary|signals|performance|science|operations|learning).json`. Recusa `.tmp`, HTML, subpastas, nomes com hash e nomes desconhecidos.
- **Testes-guarda do workflow:** o passo V6 e a linha da variável nas leituras rápidas estão fixados como texto exato. `strip_v6` remove só esse texto, e o resultado é byte a byte igual ao `collect.yml` do `main` (`a725e5d2…`). Testes de mutação confirmam que estas alterações seriam detetadas:
  - um segredo acrescentado ao passo;
  - a remoção de `continue-on-error`;
  - um comando extra no passo;
  - a variável passada a outro passo;
  - um passo novo dentro da região.
- **Concorrência:** um lock de ficheiro serializa a recolha e as leituras rápidas. A publicação Git (fast-forward com retoma) foi testada com um escritor concorrente.

## Ativação e reversão (decisão humana)

- **Ativar:** depois do merge da #97 e da #98, criar a variável do repositório `GOAL_ROI_V6_SNAPSHOTS=on`. Na execução seguinte, confirmar que existem os 7 ficheiros e que `report_sha256` coincide com `docs/report.json` do mesmo commit.
- **Desligar:** remover a variável. Na execução seguinte já nada é escrito. O dashboard deteta que os snapshots ficaram atrás do relatório e usa o relatório completo, sem recarregar.
- **Limpar:** opcional, só com a variável desligada. Fazer um commit que apague `docs/v6/snapshots/`. O publicador aceita essa remoção (testado).
- **Reverter o código da #98:**
  1. Desligar a variável.
  2. Esperar uma execução da recolha cujo passo "Check for an unpublished transaction" não indique pendentes.
  3. `git revert -m 1 <merge>`.

  Com a variável desligada nada é escrito, por isso nenhuma transação pendente pode conter snapshots (testado). Uma transação antiga com snapshots, aplicada já sobre o código revertido, seria recusada pelo `check_paths` antigo e abandonada ao fim das tentativas. As intenções de entrega já estariam no `main`.

## Testes

`tests/test_dashboard_snapshots.py` (19 testes) cobre:
- inativo por omissão;
- nomes estáveis e coerência;
- remoção de esquemas antigos;
- manifestos malformados;
- falhas do builder;
- relatório alterado a meio da construção;
- saída adulterada;
- timeout e ausência de `node`;
- 4 construções concorrentes;
- caminhos de publicação exatos;
- posição do passo no workflow;
- publicação Git real com um escritor concorrente;
- isolamento e mutações dos testes-guarda;
- reversão com transações pendentes.

O isolamento Polymarket (60 testes) e a continuidade da recolha (35 testes) continuam a passar.
