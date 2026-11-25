# Goal-ROI Intelligence V6

> V6.1: ver `v6.1-resolucao.md` (matriz de resolução da auditoria independente), `snapshots-integration.md` (PR #98) e `mobile-heights-before-after.json` (alturas e scroll horizontal em telemóvel de 375 px, antes e depois).

Terminal de análise em `docs/v6/`, publicado ao lado do dashboard clássico (`docs/index.html`). O clássico, o simulador e as análises anteriores não foram alterados e continuam disponíveis. Há uma ligação "Clássico" em todas as vistas da V6.

## Recuperação do trabalho do Codex

Revisão de partida: `2404d129` (`main`, 25/11/2026).

- **Branch `feature/intelligence-dashboard-v6`:** não existe no remoto, nem em nenhuma das 7 branches, nem nas tags de arquivo.
- **Ficheiros `v6-app.js` e `test_v6_science.py`:** não estão em nenhuma árvore.
- **Branches do Codex:** as duas branches remotas `codex/*` tratam de outros temas (auditoria BSD e proveniência).
- **Estado local:** sem stash, sem worktrees e sem alterações locais.
- **Conclusão:** o trabalho estava apenas no ambiente cloud do Codex, inacessível daqui. A V6 foi reconstruída de raiz e os dois ficheiros foram recriados com os nomes previstos.

Factos do Codex revalidados nesta revisão:

| Facto | Estado atual |
| --- | --- |
| `docs/report.json` = `data/report.json` | idênticos (`cmp`), com 3 049 743 bytes cada (o Codex mediu 3 048 450, numa revisão anterior) |
| Pedido do relatório completo a cada 3 minutos | confirmado (`docs/dashboard.js`, `setInterval(agendaRefresh, 3 * 60000)`, com `cache: 'no-store'`) |
| Histórico por jogo cobre poucos jogos | 21 jogos em `report.matches`, de 1022 jogos no histórico (2,1 %) |
| Testes de base | 1117 testes; 1 falha pré-existente em `main` (`test_one_coherent_snapshot_across_refreshes`): o teste procura um cartão "Stake + misto + por começar" que os dados atuais não têm. Não está relacionada com a V6. |

## Áreas

| # | Área | Fonte | Cobertura indicada |
| --- | --- | --- | --- |
| 1 | Command Center | resumo + operações | revisão, frescura da recolha, H1/H2 em relação a 200 jogos |
| 2 | Market Intelligence | `matches`, `simulation.entries`, `insights.price_move`, `polymarket_over25` | cronologia em 21 de 1022 jogos; o mapa de calor usa o histórico completo |
| 3 | Radar de sinais | `brain.agenda` | sinais da agenda atual, duplicados agrupados |
| 4 | Brain Intelligence | `brain.*` | o total "replicação exploratória" refere-se só ao topo publicado |
| 5 | Scientific Lab | `data/prospective_report.json` + protocolo congelado | leitura direta da produção; nada é recalculado |
| 6 | Performance Analytics | `groups`, `questions`, `timeline`, `insights`, `simulation`, `walk_forward` | o saldo cobre as últimas 600 liquidações |
| 7 | Data Quality & Operations | `audit`, exclusões, elegibilidade, prospetivo | tabela de cobertura efetiva por vista |
| 8 | Knowledge Center | — | **desativado** (ver abaixo) |

Regras seguidas:
- **Valores copiados, nunca recalculados:** ROI, IC95, z, estados H1/H2 e certificação vêm do relatório tal como publicados.
- **Agregações feitas no navegador, todas rotuladas:**
  - somas por fonte e por semana, com a regra `pl` de `scangoal/report.py` (a soma bate com Q7: −19,37 vs −19,36);
  - queda máxima dentro da janela;
  - contagens de duplicados;
  - convergências e contradições entre fontes.
- **Proteção contra certificações inválidas:** um estado prospetivo que viole os critérios congelados aparece como "certificação inválida". Os critérios são 200 jogos, z ≥ 2,576 e faixas com 30 ou mais jogos; o teste Python compara-os com `scangoal/prospective.py`. Uma estratégia "validada" sem certificação ativa é mostrada como 0.
- **Identidades:** não se inferem identidades de apostadores. Não se mostram amostras de mensagens em bruto.

## Desempenho

`node audits/v6-intelligence/measure.cjs` usa Chromium em 412×915, com CPU 4× mais lenta, rede de 1,6 Mbit/s e 150 ms, e servidor com ETag. Os valores são a mediana de 3 execuções e estão em `performance.json`.

| | Clássico (antes) | V6.1 · relatório completo | V6.1 · snapshots |
| --- | ---: | ---: | ---: |
| Primeiro conteúdo | 15,9 s | 17,0 s | **1,8 s** |
| Página completa | 15,9 s | 17,1 s | **4,7 s** |
| Transferência inicial | 3,19 MB | 3,24 MB | **0,75 MB** |
| Tarefas longas | 398 ms | 502 ms | 51 ms |
| 10 atualizações sem dados novos | **30,5 MB** | **12 KB** (pedido parcial da revisão + 304) | **10 KB** |

Snapshots derivados (`scripts/build_dashboard_snapshots.cjs`), 354 KB no total contra 3,05 MB do relatório:

| Ficheiro | Tamanho |
| --- | ---: |
| `dashboard-summary.json` | 3,7 KB |
| `dashboard-signals.json` | 129 KB |
| `dashboard-performance.json` | 113 KB |
| `dashboard-science.json` | 8 KB |
| `dashboard-operations.json` | 15 KB |
| `dashboard-learning.json` | 85 KB |

O manifesto traz o SHA-256 e o tamanho de cada ficheiro.

- **Carregamento progressivo:** o resumo chega primeiro e os restantes ficheiros seguem em paralelo.
- **Atualização seletiva:** os snapshots não têm hora de geração no cabeçalho, por isso só se descarregam os ficheiros cujo hash mudou.
- **Consistência:** todos os ficheiros têm de corresponder ao manifesto da mesma revisão; um hash diferente é recusado e mantém-se a revisão anterior.
- **Ordem:** um sequenciador ignora respostas fora de ordem e revisões mais antigas.

**Ativação pendente (decisão humana):** os workflows de produção não foram alterados, como pedido. Sem um passo em `collect.yml` que corra o gerador para `docs/v6/snapshots/`, a V6 em produção usa o modo "relatório completo". Nesse modo, a revalidação condicional (`cache: 'no-cache'` em vez de `no-store`) já reduz as atualizações sem dados novos de 3 MB para uma resposta 304. O passo seria:

```
node scripts/build_dashboard_snapshots.cjs --report data/report.json --out docs/v6/snapshots
```

## Knowledge Center (desativado)

O Goal-ROI Brain é um cofre privado, e a página da V6 é pública. Não existe um canal que publique apenas indicadores agregados, por isso nada do Brain é carregado. As condições para ativar estão descritas na própria vista.

## Segurança e privacidade

- **Filtro de privacidade nos snapshots e na derivação no navegador:**
  - retira chaves como `unparsed_samples`, `chat_id`, `token`, `session` e `api_key`;
  - oculta valores com aspeto de e-mail, token de bot Telegram, token GitHub, chave de API, telefone, `@utilizador` ou ligação `t.me`;
  - o gerador recusa publicar se sobrar algum destes valores.
- **Escape e conversão:** todo o HTML gerado passa por escape; os valores numéricos do relatório prospetivo são convertidos para número.
- **Política de segurança de conteúdo:** só scripts locais; ligações apenas à própria origem e a `raw.githubusercontent.com`, onde está o relatório prospetivo público.
- **Sem custos novos:** sem dependências nem serviços pagos e sem credenciais.
- **Auditoria adversarial independente** (agente só de leitura):
  - encontrou 9 problemas: injeção de HTML pelo JSON prospetivo, Command Center sem marcar certificação inválida, tooltip do saldo com o desfecho em vez das unidades, queda da janela sobrestimada, desvios ao filtro de privacidade e casos-limite do carregador;
  - todos foram corrigidos e cada um tem um teste de regressão.

## Testes

- `tests/v6_core.test.cjs` (18 testes, `node --test`):
  - dados incompletos;
  - incoerências;
  - certificações inválidas;
  - duplicados;
  - contradições;
  - respostas fora de ordem;
  - dados desatualizados;
  - privacidade;
  - números copiados sem alteração;
  - gerador determinístico;
  - snapshots incompatíveis;
  - atualização seletiva.
- `tests/v6_dashboard.cjs` (Playwright, servidor local real):
  - falhas HTTP 500/503 e JSON truncado;
  - revisão mais antiga;
  - recolha parada;
  - relatório incoerente;
  - secções em falta;
  - certificação inválida e conflitos;
  - injeção de HTML;
  - dados privados;
  - prospetivo indisponível;
  - snapshots progressivos, seletivos e incompatíveis;
  - 8 vistas em telemóvel, tablet e desktop, nos modos claro e escuro, sem scroll horizontal nem `undefined`/`NaN`.
- `tests/test_v6_science.py` (no `unittest` da CI) corre os dois anteriores e verifica, de forma independente em Python:
  - o relatório prospetivo é copiado tal como está;
  - os critérios congelados coincidem com `scangoal/prospective.py`;
  - os ficheiros protegidos não mudam.

Capturas: `captures/<vista>-<telemovel|tablet|desktop>-<claro|escuro>.webp` (48 capturas) e `captures/antes-classico-*.webp` para comparação. Para as regenerar: `V6_SHOTS=dir node tests/v6_dashboard.cjs`.

## Limitações

- **Modo em produção:** até à ativação dos snapshots, o primeiro carregamento em telemóvel continua a descarregar 3 MB, como no clássico.
- **Origem do relatório prospetivo:** em modo relatório, é lido em `raw.githubusercontent.com` (`main`, cache de 5 minutos). Se falhar, o Scientific Lab indica-o e o resto da página funciona.
- **Cronologia de mercado:** existe apenas para os jogos em `report.matches` (2,1 % do histórico). A cobertura é indicada na própria vista.
- **Sem estatística nova:**
  - as somas por fonte e por semana não têm intervalo de confiança;
  - a "replicação exploratória" refere-se só ao topo publicado (150 hipóteses);
  - nada disto é validação.
- **Teste herdado:** o teste pré-existente do dashboard clássico continua a falhar com os dados atuais. Não foi alterado.

RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.
