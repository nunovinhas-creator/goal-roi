# Auditoria UX do Goal-ROI

A comparação usa a revisão `20a24d8a43485e5043b5ac79d3986109de6a6962` da branch `main` e o relatório dessa revisão, gerado em 20 de novembro de 2026 às 18:01:52 UTC. O navegador usa a mesma hora nas duas versões para manter a mesma idade da recolha e a validade dos sinais.

## Problemas encontrados

- A resposta inicial, os totais, a agenda e o ranking surgiam em sequência na mesma página. Para chegar ao saldo ou ao simulador era preciso passar pela agenda e por várias explicações exploratórias.
- A agenda mostrava a fundamentação de cada hipótese de imediato. Num ecrã pequeno, mercado, origem, janela de observação e sinais contraditórios disputavam espaço com vários parágrafos.
- A cor e a proximidade entre ROI, taxa de acerto, táticas promissoras e alertas podiam sugerir que as métricas tinham a mesma força científica. A agenda antiga usava o campo `validated` diretamente na etiqueta. As hipóteses podem partilhar os mesmos jogos.
- Grupos, calibração, táticas, segmentações e qualidade dos dados tinham tabelas extensas. Várias requeriam deslocamento lateral e as regiões de scroll não tinham nome acessível ou foco explícito. O detalhe do saldo dependia do ponteiro ou toque.
- A atualização recriava o interior de `main`, fechava divulgações e removia o foco apesar de conservar alguns valores. A abertura inicial renderizava todas as secções, incluindo tabelas que ficavam fora do ecrã.

## Hierarquia atual

| Área | Primeiro nível | Aprofundamento |
| --- | --- | --- |
| Visão geral | Quatro indicadores, conclusão, hora e estado da recolha | Ligações para os sinais, resultados e metodologia |
| Sinais | Cartões com hora de Lisboa, seleção, mercado, odd, origem, estado e validade | Filtros e análise por cartão |
| Resultados | Saldo, ROI histórico, amostra, desfechos, odds e risco | Simulador, grupos, séries diárias e apostas recentes |
| Laboratório | Estados das hipóteses, jogos distintos, reconstituição cronológica e LLM | Ranking, hipóteses ordenáveis, seguimento prospetivo, notas, entradas, segmentações e análises exploratórias |
| Metodologia | Origem, liquidação, cálculo do ROI, incerteza e certificação | Evidência operacional, controlo de qualidade, jogos por liquidar, CSV e JSON |

Cada área pode ser ligada diretamente. As tabelas complexas têm uma região própria com scroll e nome acessível. Os gráficos de barras incluem os valores em texto. O saldo tem um seletor de apostas navegável com o teclado. A página usa fontes locais do sistema, paletas clara e escura e nenhum pacote externo no site. Os painéis avançados carregam ao abrir, com os dados da mesma revisão do relatório.

## Preservação e interpretação

- `report.json` e `bets.csv` mantêm os mesmos bytes da base. A PR contém apenas interface, testes do dashboard e evidência da auditoria.
- O ROI global e os desfechos usam o grupo de referência Q7 quando os números de apostas e liquidações coincidem com o histórico. A curva usa os pontos acumulados do relatório e até as últimas 600 liquidações. O início da curva representa o saldo imediatamente anterior a essa janela. O simulador mantém a janela, os filtros e a fórmula da versão anterior.
- Rentabilidade histórica, reconstituição cronológica, acompanhamento prospetivo descritivo e certificação têm rótulos próprios. Uma tática recebe indicação de certificação se o relatório publicar método e registo válidos e uma confirmação da própria hipótese. O relatório da comparação não contém certificações.
- O intervalo de atualização continua nos três minutos e ao regressar ao separador. A interface indica recolha parada passados 45 minutos e guarda a última fotografia válida em caso de erro. Uma verificação local atualiza a validade dos sinais entre pedidos.
- Os cenários com odds inferiores reaproveitam os valores de origem do relatório. Odds executadas podem divergir das odds anunciadas. A comparação de grupos mostra os respetivos denominadores e os intervalos ficam nas tabelas. As deduções da investigação dependem da qualidade e disponibilidade das observações publicadas.

## Capturas e redução de texto

As capturas são do Chromium a renderizar o HTML real de cada revisão. As versões usam o mesmo JSON e uma ligação de teste local sem serviços externos. As palavras da primeira janela são contadas por caixa de texto renderizada, excluindo controlos escondidos e conteúdo em divulgações fechadas.

| Janela | Antes | Depois | Redução |
| --- | ---: | ---: | ---: |
| Desktop, 1440 × 1000 | 413 | 74 | 82,1% |
| Telemóvel, 375 × 812 | 157 | 72 | 54,1% |

| Área | Desktop | Telemóvel |
| --- | --- | --- |
| Visão geral antes | [Captura](captures/before-desktop-overview.png) | [Captura](captures/before-mobile-overview.png) |
| Visão geral depois | [Captura](captures/after-desktop-overview.png) | [Captura](captures/after-mobile-overview.png) |
| Sinais | [Captura](captures/after-desktop-sinais.png) | [Captura](captures/after-mobile-sinais.png) |
| Resultados | [Captura](captures/after-desktop-resultados.png) | [Captura](captures/after-mobile-resultados.png) |
| Laboratório | [Captura](captures/after-desktop-laboratorio.png) | [Captura](captures/after-mobile-laboratorio.png) |

O comando `node audits/dashboard-ux/capture.cjs` reproduz as capturas e testa a igualdade das tabelas de grupos e dias e do simulador em duas configurações. Requer a revisão de referência no histórico, Playwright e Chromium. A variável opcional `DASHBOARD_CHROMIUM` permite usar um binário local.

## Limites da verificação

O ensaio de interface usou Chromium a 375, 768 e 1440 px, com emulação móvel, verificação de contraste em amostras das duas paletas, navegação por teclado e inspeção da árvore de acessibilidade. Os testes de interface incluem dados reais e incompletos, falhas, expiração, recolha parada, filtros, ordenação, foco, simulador, tabelas e certificações verificáveis e inválidas. A aceitação em dispositivos físicos, em Safari iOS e com leitores de ecrã reais continua por verificar.

## Correções após a auditoria independente da PR #70

A atualização valida o relatório antes de substituir o estado do painel. Os testes comparam os indicadores, os resultados, as curvas do saldo e do simulador, os controlos e o foco antes e depois de respostas inválidas. A recuperação aplica o relatório válido seguinte e uma resposta atrasada não reverte o painel.

O ensaio da recolha faz o relógio atravessar os 45 minutos e espera pela atualização periódica para verificar a recuperação. Confirma a hora de Lisboa e compara as etiquetas de estado e as janelas de validade dos sinais. As capturas usam o relatório da auditoria com uma hora de última recolha simulada. [Desktop](captures/stale-1440.png) · [Telemóvel](captures/stale-375.png)

Para reproduzir a regressão a 375, 768 e 1440 px, execute o mesmo teste nos ficheiros publicados pelo commit auditado e nos ficheiros locais.

```sh
audit_before=$(mktemp -d)
git archive 0e72b16799eb11bc9cf0ca458bb78fb859ad925d docs | tar -x -C "$audit_before"
node tests/dashboard_refresh.cjs "$audit_before/docs/index.html"
DASHBOARD_CAPTURE_DIR=audits/dashboard-ux/captures node tests/dashboard_refresh.cjs
```

O primeiro comando de teste falha nas três larguras por perda dos sinais filtrados e ausência do aviso após 45 minutos. O segundo também verifica JSON malformado, HTTP 500, estruturas aninhadas inválidas, respostas fora de ordem, estados vazios e recuperação com filtros, simulador e painéis abertos.
