# D. Métodos, resultados e provas

## Método reprodutível

Referência de código e dados `9db4f20c614a645a4949f88562d5054ec53b8181`. Os scripts de evidência são novos. Os testes de Python usam diretórios temporários, dados sintéticos, relógios controlados e destinos de envio substituídos. A01, A02 e A37 usam um remoto Git local, com duas cópias independentes. O teste de navegador intercepta todos os pedidos e serve ficheiros locais. Os pedidos de comparação a Pages e de metadados públicos foram leituras separadas. Os relatórios resultantes de scripts existentes ficaram em diretório temporário.

`offline.py` aplica seccomp antes da execução em Linux. Bloqueia a criação de sockets IPv4, IPv6 e AF_PACKET no processo e nos filhos. Os comandos executados tinham ambiente limpo e credenciais ausentes, à exceção de valores sintéticos locais nos testes. Para Chromium foram usados sockets Unix e Playwright com rotas em memória. Em ambiente que restringe o arranque Chromium, o ensaio pode ser executado com o bloqueio seccomp preservado.

Executar da raiz da branch de auditoria com Python ≥ 3.11, Git e, para o frontend, Node, Playwright e Chromium instalados.

```sh
audit_dir=audits/full-repo-audit-2026-11-20
python "$audit_dir/offline.py" python "$audit_dir/adversarial.py"
python "$audit_dir/offline.py" node "$audit_dir/operations.mjs"
DASHBOARD_CHROMIUM=/usr/bin/chromium python "$audit_dir/offline.py" node "$audit_dir/browser.cjs"
python "$audit_dir/offline.py" python "$audit_dir/snapshot.py"
```

Os executores independentes terminam com código 1 quando o sistema viola pelo menos uma condição auditada. `--trace` no executor Python mostra exceções de infraestrutura de teste. `GOAL_ROI_PUBLIC_SNAPSHOT` no navegador acrescenta W08 usando uma pasta local com `index.html`, `dashboard.js`, `dashboard.css` e `report.json` do mesmo deploy. O checkout deve coincidir com o SHA do relatório em todos os ficheiros de produção. O script de agregados verifica esse vínculo.

## Suite existente

| Ensaio | Resultado | Observação |
|---|---|---|
| `python scripts/test_offline.py` | PASSOU | 796 de 796 em 83,8 s na cópia isolada. CPython 3.11.18, `cryptography` 44.0.3, Telethon 1.45.0. Incluiu a regressão de navegador com Node 24.22.1, Playwright 1.62.1 e Chromium 156.0.8078.79. A CI fixa Playwright 1.56.1. |
| `node --test ops/continuity/watchdog.test.mjs` | PASSOU | Uma suite Node com respostas simuladas. |
| `git diff` de código, dados e workflow em relação ao SHA do trabalho | PASSOU | Os ensaios e agregados usam os ficheiros correspondentes ao SHA fixado. |
| `snapshot.py` e reexecução de Q7 e dos grupos | PASSOU | Os grupos e Q7 coincidiram com o JSON publicado no checkout. O JSON em `data/` e `docs/` é idêntico. |
| Comparação de auditoria e ranking Polymarket ao gerar o relatório | PASSOU | Os campos do candidato coincidiram. Um jogo elegível com FT. Metodologia exploratória e ranking sem amostra suficiente. [Agregados](snapshot.json). |
| `scripts/audit_pr4_llm.py` com o protocolo da PR #27 | PASSOU | Execução e protocolo aceites pelo verificador do projeto. Estudo em curso. 88 hipóteses registadas, 66 avaliadas, 23 jogos LLM e 28 controlos em um dia emparelhado. K final 0,934194 e K máximo 1, perante a fasquia 20 e os mínimos de amostra. A validade do método continua condicionada a GR-02 e GR-03. [PR #27](https://github.com/nunovinhas-creator/goal-roi/pull/27). |

A primeira execução de preparação omitiu o navegador porque o caminho dos módulos Node não estava presente após limpar o ambiente. A execução que conta acima incluiu o navegador com seccomp e o arranque autorizado. Versão do Git local 2.52.0.

Comando do estudo LLM verificado em cópia isolada. O carimbo de servidor é a hora de integração avistada no GitHub para a PR que contém o protocolo.

```sh
python audits/full-repo-audit-2026-11-20/offline.py python scripts/audit_pr4_llm.py \
  --ref 9db4f20c614a645a4949f88562d5054ec53b8181 \
  --protocol-commit bb1b07caae263f73cecbea0a918f6363e747554d \
  --protocol-published-utc 2026-11-16T09:51:30Z \
  --protocol-publication-evidence https://github.com/nunovinhas-creator/goal-roi/pull/27 \
  --out /tmp/goal-roi-llm-audit
```

O resultado científico calculado por esse comando tem SHA-256 `507bdd1e4f0a6106d8ac5fc517376e28185d34836d94038217701942aadb969b`. É um identificador da reprodução. A prova do Git aceita os carimbos do próprio histórico para as observações, e os limites metodológicos documentados nesta auditoria continuam a aplicar-se.

## Testes adversariais novos

PASSOU indica que o comportamento esperado foi observado nas condições do caso. FALHOU inclui violações deliberadas de requisitos da auditoria, mesmo onde a política atual aceita o compromisso entre segurança e disponibilidade. NÃO VERIFICADO indica ausência de ambiente, dados ou evidência adequada.

| Caso | Estado | Asserção ou observação |
|---|---|---|
| A01 | FALHOU | Duas tentativas simuladas, uma intenção remota, HEAD comum após rebase. |
| A02 | PASSOU | Push real em Git local seguido de ACK perdido. A cópia retomada passou a intenção a incerta e houve zero tentativas de Telegram. |
| A03 | PASSOU | A confirmação de Telegram perdeu-se depois de envio simulado. Total de uma tentativa em duas execuções. |
| A04 | PASSOU | Receber nova odd para a mesma seleção não fez segundo envio. |
| A05 | PASSOU | Uma seleção diferente no mesmo jogo originou um segundo aviso com identificação de atualização. |
| A06 | PASSOU | O notificador geral bloqueou aposta pré-jogo que expirou durante a publicação. |
| A07 | FALHOU | Stake enviou quando a idade chegou a 31 min durante a publicação. |
| A08 | FALHOU | Versões A → B → A armazenadas como 1, 1, 0 registos novos. |
| A09 | FALHOU | Edição só de botão foi perdida. |
| A10 | FALHOU | Uma linha final parcial causou `JSONDecodeError` ao consumir o diário com uma linha anterior completa. |
| A11 | FALHOU | O quinto sinal futuro no mesmo segundo retirou quatro sinais elegíveis num único corte histórico. |
| A12 | FALHOU | FT de segundo jogo com ID reutilizado marcou a primeira aposta como perdida. |
| A13 | FALHOU | Nota FT anterior ao início atribuiu uma liquidação por nomes iguais. |
| A14 | FALHOU | A correção foi guardada em versões, sem atualizar o resultado selecionado pelo legado. |
| A15 | FALHOU | Consenso posterior aparece na hora anterior em que existia conflito. |
| A16 | PASSOU | Correção Telegram após três dias ficou em reconciliação sem mudar o lucro primário da regra congelada. |
| A17 | FALHOU | O conflito retirou um jogo admitido e substituiu a sobreposição. E-valor 0,8063 → 1,1860. |
| A18 | FALHOU | Resultado sem hora comprovada ficou liquidado numa vista pré-jogo. |
| A19 | FALHOU | O único evento de nomes e hora compatíveis, pertencente a outra competição, foi atribuído. |
| A20 | FALHOU | Total, BTTS e handicap inválidos receberam vitória. |
| A21 | PASSOU | Vetores de referência de 1X2, BTTS, quartos asiáticos e devoluções. O nome parcial 1X2 ficou em aberto. |
| A22 | PASSOU | Estados explícitos rejeitaram prolongamento, penáltis, adiamento, atribuição e abandono no caminho testado. |
| A23 | PASSOU | Minutos de compensação no 90 e golos de prolongamento recompuseram corretamente o marcador aos 90. |
| A24 | PASSOU | A aritmética do lucro a preços heterogéneos bateu certo. O caso ilustra a divergência entre ROI e a regra média de acerto. |
| A25 | PASSOU | Denominador de três unidades para duas meias liquidações e uma devolução, com uma aposta pendente fora do ROI liquidado. |
| A26 | PASSOU | Sem autorização ou sem orçamento durável, o código bloqueou qualquer pedido Workers AI. |
| A27 | FALHOU | Nota com promessa de ganhos futuros passou o filtro e foi aceite. |
| A28 | PASSOU | Dois fornecedores contraditórios ficaram em quarentena na regra Polymarket. |
| A29 | PASSOU | Marcadores da mesma hora contrariando-se ficaram em quarentena na resolução versionada, independentemente da ordem. |
| A30 | FALHOU | A odd infinita passou o parser como aposta. |
| A31 | FALHOU | Nome completo numa seleção Stake 1X2 causou `KeyError` no relatório. |
| A32 | PASSOU | O leitor do arquivo BSD recusou snapshot corrompido em vez de inicializar estado vazio. |
| A33 | PASSOU | A regra comum muda de válida para expirada ao passar de 29 para 31 min. |
| A34 | FALHOU | O cérebro incluiu uma hipótese registada depois do relógio de avaliação. |
| A35 | FALHOU | A regra cronológica considerou decidível um pré-jogo só lido depois de começar. |
| A36 | PASSOU | O agrupador Telegram manteve hipóteses distintas com métricas idênticas no cenário testado. |
| A37 | PASSOU | Com carimbos diferentes, o conflito de escrita bloqueou a segunda tentativa na prova local. |
| A38 | FALHOU | Dois erros na mesma janela provocaram dois avisos de saúde simulados. |
| A39 | FALHOU | Uma seleção nominalmente igual em outro mercado foi descartada pela chave legada. |
| A40 | PASSOU | A segunda proposta LLM com o mesmo conjunto de sinais suportados foi rejeitada. |
| A41 | PASSOU | O backfill arquivou uma página e preservou o diário vivo. |
| A42 | FALHOU | Erro de entrega definitivo consumiu o sinal, apesar de a recuperação ter sido antes da expiração. A política atual privilegia uma tentativa. |
| A43 | FALHOU | A leitura rápida sem mudanças não atualizou o relatório da página. |
| A44 | FALHOU | Evento de conflito futuro eliminou uma unidade da avaliação LLM antes de aplicar imputação. |
| A45 | FALHOU | Dois encontros sem identidade foram agregados numa aposta. |
| A46 | FALHOU | Golo anterior ao sinal contou como vitória numa janela «próximos cinco minutos». |
| O01 | PASSOU | GitHub 503 simulado resultou em erro de observação sem autorização de retoma. |
| O02 | PASSOU | Guarda inativa não efetuou pedidos. |
| O03 | FALHOU | O primeiro despacho rejeitado com 401 bloqueou a retoma após restabelecer a credencial simulada. |
| O04 | PASSOU | Uma execução recente e pendente impediu o despacho. |
| W01 | PASSOU | Apostas, liquidações e ROI da visão geral coincidiram com Q7. Certificação inativa e vantagem por demonstrar. |
| W02 | PASSOU | Navegação, teclado, filtros, gráfico e simulador em 320, 375 e 1 440 px sem overflow global nas vistas testadas. |
| W03 | PASSOU | Após o intervalo de atualização, resposta truncada foi rejeitada com o último indicador preservado e erro comunicado. |
| W04 | FALHOU | Relatório de seis horas com recolha recente foi marcado como atualizado. |
| W05 | FALHOU | Carimbo de recolha no dia seguinte foi marcado como atualizado. |
| W06 | FALHOU | Uma secção sem tamanho mínimo de amostra passou a validação e mostrou `undefined` no ranking. |
| W07 | PASSOU | Mínimo de 6,79:1 entre os seletores medidos, nos dois temas, com fundo opaco. |
| W08 | PASSOU | Os indicadores da captura publicada coincidiram com o JSON do deploy capturado. |
| W09 | FALHOU | Duas apostas simultâneas e vencedoras a 100% da banca a odd 2 transformaram 100 € em 400 €. O primeiro lucro não estava disponível no instante do segundo sinal. |

**Total independente.** 59 cenários. 28 passaram e 31 falharam. Entre os 59, W08 pertence ao deploy externo identificado abaixo. Os outros 58 verificam caminhos ou artefactos da revisão fixada.

A01 e A37 tiveram 12 repetições adicionais cada, em remotos descartáveis. A01 reproduziu duas tentativas em 12 de 12. A37 bloqueou a segunda tentativa em 12 de 12. Estas proporções medem as condições impostas pelos testes, sem estimar a taxa de concorrência ou duplicação em produção.

## Verificação do deploy depois da PR #70

Pages respondeu HTTP 200 em cinco ativos testados. `index.html`, `dashboard.js` e `dashboard.css` coincidiram byte a byte com `docs/` no SHA auditado. A API de Pages indicou publicação a partir de `main:/docs` com HTTPS forçado. A captura de dados corresponde byte a byte ao deploy de `f15b580abb7d28c8ffd489f71515f8422984537c`, concluído às 20:37:48 UTC e observado separadamente. A captura foi reproduzida offline em W08 com 1 057 apostas, 832 liquidadas e exibição de 3,2%. O relatório dessa captura foi gerado às 20:36:40 UTC. W01 usa o SHA de auditoria com 1 056 apostas, 832 liquidadas e o mesmo arredondamento na UI. Os dois cortes têm manifestos distintos.

| Ativo | Bytes da captura | SHA-256 |
|---|---:|---|
| `index.html` | 23 759 | `872903b0d8ece2988e81168ffd5e20a3fa50ddfe40ad25c571c71e380430b9fb` |
| `dashboard.js` | 79 011 | `a30092dcd4c365c5f29f3fc4be7cb5a7daf787bbe94c895603cf7fa9e83862d8` |
| `dashboard.css` | 23 701 | `61ff86e08d393dd59d8664f061ebcdaea40822aaf855cd230951d58944440908` |
| `report.json` | 1 090 958 | `cd0deb38efdee4e7e7614879ef801e4bfa5718f53e3f8e9e4dbf8e515ffef92a` |
| `bets.csv` | 204 376 | `7532fe36ae171e89f5e6d64186a2c48380165100d90abb055d026eb484401bed` |

O documento carregou os indicadores em aproximadamente 0,3 a 0,6 s nas execuções locais de Chromium. O tempo mede DOM e renderização local, sem rede. As verificações de contraste e teclado cobrem os pontos enumerados em W02 e W07. [Páginas](https://nunovinhas-creator.github.io/goal-roi/), [deploy correspondente](https://github.com/nunovinhas-creator/goal-roi/tree/f15b580abb7d28c8ffd489f71515f8422984537c/docs), [WCAG 1.4.3](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).

## Segurança e operação em leitura

- A referência fixada e o histórico local disponível foram pesquisados por padrões de tokens GitHub, Telegram Bot API, chaves privadas, identificadores AWS e tamanho aproximado de sessão exportada. Pesquisas Git limitaram a saída a caminhos e contagens e não devolveram candidatos. Segredos sem um padrão detetável, histórico de refs não clonadas e logs do operador ficam fora dessa prova.
- A resolução local instalou `cffi 2.1.1`, `pycparser 3.12`, `pyaes 1.6.1`, `rsa 4.9.1` e `pyasn1 0.6.5`. A base GitHub Advisories não devolveu avisos para essas versões, Telethon 1.45.0 ou Playwright 1.56.1. Devolveu cinco para cryptography 44.0.3. A wheel Linux instalada reportou OpenSSL 3.4.1. O alcance de cada vulnerabilidade depende da superfície usada. [Consulta de advisories](https://docs.github.com/en/rest/security-advisories/global-advisories#list-global-security-advisories).
- Uma amostra pública de 100 runs de `collect.yml` entre 04:30:32 e 20:37:08 UTC teve 99 despachos, um cron, 97 conclusões `success`, uma `cancelled`, uma `failure` e uma execução em curso. As 97 concluídas com sucesso tiveram uma mediana de 9,90 minutos entre `run_started_at` e `updated_at`. A janela da consulta cruza a hora do SHA fixado. Serve para avaliar a cadência operacional, sem alterar os resultados auditados do repositório. [Actions](https://github.com/nunovinhas-creator/goal-roi/actions/workflows/collect.yml).
- A amostra da continuidade tinha quatro eventos agendados bem-sucedidos e quatro eventos por conclusão de workflow marcados como skipped. A amostra não confirma retoma real após falha. A ref de orçamento AI existia no momento da leitura. A API de branches indicou proteção de `main`. [Continuidade](https://github.com/nunovinhas-creator/goal-roi/actions/workflows/continuity.yml).

## NÃO VERIFICADO em produção

| Área | Evidência em falta |
|---|---|
| Sessão e origem Telegram | Autorização atual, reemissão e recuperação de sessão real. Cobertura de edições e apagamentos ocorridos entre leituras e fora da janela. |
| Alertas | Quantidade efetiva de mensagens publicadas, duplicações ou perdas no destino. Frequência de interleavings de A01 e existência de escritores externos. |
| FT e preços | Contrato real dos fornecedores para todos os mercados e competições, alterações ainda não observadas e execução de preços anunciados. |
| Continuidade | Ativação, logs e configuração implantada no Cloudflare. Retoma real após falha simultânea e comportamento de credenciais atuais. |
| GitHub e secrets | Definições de bypass, regras de colaboradores, uso efetivo de OIDC fora do código e inventário de credenciais em produção. |
| Orçamentos | Faturação e utilização de contas externas, tráfego a grande escala, limites de planos atuais dos fornecedores e modelo de capacidade. |
| Acessibilidade e desempenho | Leitores de ecrã reais, revisão WCAG integral, Safari, Firefox e medições de rede e Core Web Vitals em produção. |
| Inferência | Capacidade preditiva, rentabilidade líquida, resultados prospetivos em escala e condições externas da seleção de jogos. |
