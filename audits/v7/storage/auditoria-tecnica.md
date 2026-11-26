# Goal-ROI V7 — Auditoria técnica e plano de continuidade

O corte de produção é e3a0760813f8b2ba4f4953a84dde0a697a117a10 (26/11/2026, 10:54:17 UTC), com relatório gerado às 10:50:48 UTC. A PR #98 foi avaliada no commit 33b469e47f20cd0a6ece2ef7ad369dce18ad54c7. As adversidades correram em checkouts separados, em arquivos locais ou fixtures. [Produção fixada](https://github.com/nunovinhas-creator/goal-roi/commit/e3a0760813f8b2ba4f4953a84dde0a697a117a10) · [PR #98](https://github.com/nunovinhas-creator/goal-roi/pull/98)

## Cobertura integral e diagnóstico do parser

O diário tem **9 079 linhas válidas**, de **5 687 IDs**, com SHA-256 do texto conferido em todas as versões. As 3 392 versões adicionais fazem parte da história. O relatório usa a primeira versão de cada ID e conta tipo nulo ou erro. A reprodução obteve **1 379**, ou **24,25%**. O corte histórico de 1 363 tinha 5 663 IDs às 07:44:44 UTC. Dos 24 IDs adicionais, oito ficaram fora do contador, 13 são movimentos de odds, dois são Betfair com gramática nova e um corresponde a serviço. [Corte histórico](https://github.com/nunovinhas-creator/goal-roi/commit/12e1de7d5dfe5abb5a4c867d9c17f7c5206eb12f) · [Contagens e instruções para gerar o censo](https://github.com/nunovinhas-creator/goal-roi/pull/101)

| Classe exclusiva no contador | Primeiras mensagens | Gravidade | Exemplo estrutural |
|---|---:|---|---|
| Kalshi com estrutura reconhecida | 766 | P1 | Kalshi · LIVE 44′ 0:0 e cotação pontual ou por intervalo. |
| Kalshi sem relógio ou marcador | 6 | P2 | Cabeçalho termina em LIVE. |
| Betfair com volume ao marcador | 424 | P1 | 1X2 market at 0:1 $… · live. O parser exige a expressão whole 1X2 market. |
| Nota de resultado de sinal | 91 | P2 | Signal result, duas equipas com golos e ausência de ID inequívoco. |
| Contexto de equipa técnica | 46 | P3 | NEW COACH, sem cotação no formato suportado. |
| Movimento de odds | 41 | P2 | Odds 2.xx → 1.xx, observação de mercado avulsa. |
| Serviço ou marketing | 5 | P3 | Boas-vindas, suporte, menu de estratégias e anúncio. Irrelevantes para extrair uma cotação. |

O CSV inclui, por linha, a data, a família, a categoria, o estado do parser e a validade do hash. Os exemplos acima abreviam a estrutura. Os drafts têm código e fixtures sintéticas.

| Diagnóstico complementar | Contagem e implicação |
|---|---|
| Kalshi e mudança temporal | 596 live e 170 pré-jogo com estrutura. 296 cotações por intervalo e 470 pontuais. O formato estruturado é observado a partir de 18/11, e a variante de emoji duplo aparece em 85 mensagens. A variante LIVE sem relógio está presente desde 21/11. |
| Mercados Kalshi | 436 handicap por lado, 87 totais, 63 vencedor literal ou código, 15 ambas marcam, 52 contratos negados, quatro de primeira parte, dois totais de equipa, dois de apuramento e 105 sem vínculo literal da seleção. Negação, apuramento e período requerem prova antes de qualquer normalização. |
| Betfair | 424 do novo corpo, dos quais 88 com emoji duplo e 12 com volume em milhões. 372 continuam reconhecidos pelo formato aceite. Coexistem formatos entre 17/11 e 26/11. Volume ao marcador e volume global mantêm escopos distintos. |
| Polymarket | 144 em 1 666 primeiras mensagens trazem tipo reconhecido, sem aposta e sem erro, fora dos 1 379. São 45 contratos negados, 12 cabeçalhos sem relógio ou incompletos e 87 outros mercados ou identidades. Trata-se de rejeição silenciosa de diagnósticos, sem evidência de que o filtro devesse aceitar o contrato. |
| Stake | 1 231 primeiras cotações e zero no contador de erro. Um ensaio estrutural do liquidante legado com FT sintético recusou 628 combinações. Os maiores grupos foram Match Result (128), 1st Half - Asian Total (58), Total Goals (55), 1x2 (54) e Draw No Bet (47). Isso mede limites do liquidante de FT isolado. |
| Mercado e liquidação | O mesmo verificador isolado recusa 1 011 combinações Polymarket, das quais 1 002 com rótulo genérico. As marcas do bot e outros caminhos de liquidação ficam fora dessa sonda. No relatório integral, 378 apostas tinham FT e mercado por liquidar. |
| Identidade | Zero pares normalizados diferentes ou kickoffs diferentes para o mesmo ID explícito entre entradas comparáveis. Existem 105 seleções Kalshi sem vínculo literal e o relatório regista 126 casos próximos HL e 67 BSD. Aproximações e aliases precisam de confirmação humana. |
| Relógio | Zero ISO inválido na primeira versão, cinco primeiras observações anteriores à publicação ou edição indicada e nove observações FT impossíveis no relatório. Stake conta 131 layouts sem marcador após o início e 271 com envio após 45 min nas métricas próprias. |
| Duplicações | Zero versões adjacentes idênticas no mesmo ID. Sete hashes de texto são partilhados por mais de um ID, com oito IDs adicionais. Repetições textuais e revisões legítimas não são removidas. |
| Associações de resultados | As 91 notas Signal result têm sintaxe de marcador e zero IDs inequívocos. O ligador devolveu 43 observações ligadas para 79 primeiras notas Full time com marcador. Uma observação pode ligar vários IDs do mesmo jogo, pelo que os denominadores não são intercambiáveis. Foram identificados 1 016 jogos com FT do bot. |

A adaptação isolada reconhece a estrutura de **1 190** primeiras mensagens com **zero admissões automáticas**. A reprodução mantém o relatório de produção em 2 491 apostas únicas e 1 912 liquidações. Sete regressões sintéticas cobrem intervalos, unidades, emoji duplo, datas inválidas, aliases e notas sem identificação. O próximo gate do parser exige exemplos etiquetados por contrato, mercado, período, marcador e relógio, e validação independente da identidade. [Draft de diagnóstico](https://github.com/nunovinhas-creator/goal-roi/pull/101)

## Pressão real no Git e projeções

Foram encontrados 5 836 commits alcançáveis e zero objetos em falta. A atribuição deduplica o SHA de cada objeto e usa o peso comprimido da cópia local. Uma segunda medição gerou packs thin sobre as mesmas janelas, sem os enviar. O tamanho do pack depende das bases delta e da seleção de objetos, pelo que o peso atribuído representa o pack observado e não uma previsão invariável. Os dois relatórios usam o mesmo blob no corte. [Medição por ficheiro](crescimento-git.json) · [Packs incrementais](packs-incrementais.json)

| Janela | Commits de recolha | Blobs únicos dos relatórios | Peso total | Relatórios |
|---|---:|---:|---:|---:|
| 24 h | 567 | 230 | 51,7 MiB | 36,5 MiB |
| 72 h | 1 703 | 687 | 112,1 MiB | 91,1 MiB |
| 7 dias | 2 764 | 1 627 | 122,2 MiB | 96,6 MiB |
| História alcançável | 4 849 no primeiro pai | 3 258 | 129,7 MiB | 100,3 MiB |

Depois dos relatórios, os maiores grupos históricos são audits/ (8,3 MiB), data/hypotheses.json (7,1 MiB) e data/messages.jsonl (5,3 MiB). O diário e as hipóteses são estados ou fontes a preservar. O relatório e os snapshots são vistas e não precisam de perpetuar cada versão no mesmo Git.

A comparação com 60 revisões reais mediu a inclinação entre 10 e 60. Foram **14,97 KiB por revisão** de relatório, **+1,23 KiB** com snapshots de nome estável e **+16,67 KiB** com nomes com hash. Nomes estáveis reduziram **92,6% do acréscimo** neste ensaio, mantendo a gravação do relatório. A taxa de 229–232 blobs de relatório por dia difere da taxa de commits de recolha. [Comparação de deltas](packs-diferenciais-snapshots.json)

GiB adicionais a taxas constantes. As bandas Git usam os extremos de 7 dias e 24 h e incluem efeitos transitórios. O modelo segmentado abrange apenas o diário, com envelopes distintos e estrutura de dados real reciclada. Nenhuma linha é uma previsão de fatura e os universos não se somam. [Dados da projeção](projecoes-cinco-anos.csv)

| Cenário | 30 d | 90 d | 365 d | 1 825 d |
|---|---:|---:|---:|---:|
| Estimativa anterior do relatório | 0,214–0,304 | 0,641–0,912 | 2,60–3,70 | 13,00–18,50 |
| Git completo, peso atribuído | 0,511–1,514 | 1,534–4,542 | 6,22–18,42 | 31,11–92,11 |
| Git, packs thin | 0,484–1,282 | 1,451–3,847 | 5,89–15,60 | 29,43–78,01 |
| Relatório isolado, 60 revisões | 0,098–0,100 | 0,294–0,299 | 1,19–1,21 | 5,96–6,06 |
| Acréscimo de snapshots estáveis | 0,0081–0,0082 | 0,024–0,025 | 0,098–0,100 | 0,490–0,498 |
| Acréscimo de snapshots com hash no Git | 0,109–0,111 | 0,327–0,333 | 1,33–1,35 | 6,64–6,74 |
| Arquivo segmentado, só diário | 0,0011 | 0,0033 | 0,0133 | 0,0664 |

## Matriz de arquiteturas

| Opção | Custo e limites | Velocidade, disponibilidade e dependência | Integridade, histórico e compatibilidade | Veredito |
|---|---|---|---|---|
| Git atual | Sem novo serviço. GitHub bloqueia ficheiros acima de 100 MiB e recomenda menos de 1 GB, fortemente menos de 5 GB. | Commits identificados de forma atómica. Clones e leituras integrais crescem. Recolha, arquivo e Pages dependem de GitHub. | Preserva o passado e as rotas atuais. A restituição de todo o arquivo depende do mesmo fornecedor. | **NO-GO** como arquivo definitivo. [Limites GitHub](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github) |
| Compressão diferencial | Inclusa no fluxo Git. Ensaio nativo especificado acima. | Reduz transferência e disco conforme base delta. Não muda o acoplamento operacional. | Compatível com os consumidores existentes. A coerência no CDN exige verificação própria. | **GO** como ferramenta transitória. |
| Segmentado portátil | Software livre e disco existente. Cinco anos do diário simulado em 68 MiB. Medir outros estados e cópias antes de compromisso. | Gzip por objeto, troca de HEAD, recuperação de versão anterior, cópia independente necessária contra perda de disco. | Verificação SHA-256 byte a byte, historial anterior conserva-se no Git. Android e V6.1 consomem vistas derivadas. | **GO** para protótipo. |
| GitHub Pages por artefacto | Site até 1 GB, 100 GB/mês de banda como limite suave. Retenção e frequência dependem de configuração. O tar local mede 0,56 MB. | Evita Git para versões derivadas futuras. Pages partilha GitHub com Actions. Sem Actions não há deploy novo. | Permite empacotar um release completo verificável e manter as rotas atuais. O artefacto não substitui a cópia científica. | **GO** para empacotar offline. **NO-GO** para mudar a origem agora. [Limites Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits) · [Deploy por Actions](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages) |
| Cloudflare R2 Standard | Inclui 10 GB-mês, 1 M operações A e 10 M B/mês. Excedentes a US$ 0,015/GB-mês, US$ 4,50/M A e US$ 0,36/M B. Egress direto sem cobrança. | Armazenamento por objeto e leitura independente. Polling e versões múltiplas consomem operações. Depende do fornecedor, budget e mecanismo de proteção. | Guardar os bytes e hashes em formato portátil e exportável. CORS, autenticação e cache exigem testes no Android. Uma réplica no mesmo fornecedor não resolve a sua indisponibilidade. | **Condicional**, sem configuração. [Preço R2](https://developers.cloudflare.com/r2/pricing/) |
| Cloudflare D1 | Free com 5 GB na conta, 500 MB/base, 5 M leituras e 100 mil escritas de linhas/dia. Limite de 2 MB por linha. A quota diária pode bloquear consultas. | SQL serve índice derivado e consultas filtradas. Índices, reconstrução e scans podem consumir a franquia. | SQL exportável e arquivo original mantido à parte. Relatório atual não cabe numa única linha e a V6.1 e Brain não usam esse adaptador. | **NO-GO** como arquivo. [Preço D1](https://developers.cloudflare.com/d1/platform/pricing/) · [Limites D1](https://developers.cloudflare.com/d1/platform/limits/) |
| Objetos genéricos ou ficheiros locais comprimidos | Formato aberto em disco existente. Capacidade, rede e replicação de outro provedor exigem orçamento próprio. | Pode usar espelho independente. Operação local impõe teste de backups e restauração. Índice SQLite reconstruível é opção futura. | Portabilidade, hashes e manifesto com origem confiável. Separar archive privado de vistas públicas. | **GO** offline. **NO-GO** para contratar serviços. |

Nos cenários por fornecedor, Pages mantém **5,37 MB** publicados ao guardar só a versão atual. Um artefacto diário com retenção de sete dias ocupa **3,92 MB**. O mesmo diário segmentado e dois artefactos correntes somam **72,43 MB** em R2 aos 1 825 dias. Guardar cada relatório gzip completo com 231 versões/dia atingiria **133,79 GB** no mesmo período e ultrapassaria 10 GB em cerca de 137 dias. O índice D1 foi modelado com o custo por linha medido em SQLite, resultando em **52,33 MB** para cinco anos. Estes cenários excluem pedidos, leituras na cache, retenção de estados e overhead do fornecedor. [Projeções por alternativa](projecoes-fornecedores.csv)

O Brain usa os caminhos prospetivos atuais para leituras. A sincronização Termux permanece independente. Uma futura migração só pode retirar um caminho após validar os consumidores com autorização específica. A decisão de arquitetura é manter a fonte histórica intacta, arquivar versões futuras em segmentos verificáveis e publicar apenas as vistas derivadas necessárias.

## PR #98 e protótipo

Chromium com CPU 4× mais lenta, rede de 1,6 Mbit/s, atraso de 150 ms e viewport 412×915. Três amostras por variante, medianas num servidor local com ETag e gzip. O corte atual usou o código da PR em cópia separada e o relatório com 3 995 973 bytes. São medições de laboratório, sem latência real do CDN nem p95 robusto. [Corte original](desempenho-pr98-corte-original.json) · [Corte atual](desempenho-pr98-corte-atual.json)

| Corte / modo | Primeiro conteúdo | Completo | Transferência inicial |
|---|---:|---:|---:|
| Original / V6 relatório | 3,799 s | 3,906 s | 341 639 B |
| Original / V6 snapshots | 1,421 s | 1,980 s | 134 892 B |
| Atual / V6 relatório | 3,996 s | 4,112 s | 378 179 B |
| Atual / V6 snapshots | 2,150 s | 2,777 s | 135 898 B |

- Um ficheiro truncado na mesma revisão permaneceu corrompido depois de build devolver unchanged. A verificação de objetos existentes precisa de anteceder a decisão de reutilização.
- Uma interrupção entre renames deixou o manifesto anterior com corpos de revisões diferentes. Um build posterior recuperou os corpos. O caminho desejado mantém cada revisão completa antes de trocar o ponteiro.
- Alterar a entrada prospetiva depois do builder permitiu publicar um manifesto com digest prospetivo obsoleto. A revisão deve fixar relatório, entrada prospetiva e protocolo em conjunto.
- Um corpo alterado com igual comprimento foi recusado por SHA-256 e aceite no caminho que só compara bytes. O cliente deverá recusar esse modo e usar a última revisão verificada.
- O sequenciador recusou respostas antigas. Passaram as regressões de browser da PR #98 e os 19 testes do publicador. O protótipo separado passou concorrência, SIGKILL, reprocessamento, corrupção, fallback e limite mínimo de revisão em cinco testes. [Resultados adversariais](resultados-pr98-adversarial.json) · [Cache e antiguidade](resultados-cache.json)
- O diário real foi reconstituído em 24 segmentos. Diário e relatório passaram round-trip de 10 100 728 B, com 1 252 334 B armazenados. O simulador verificou 1 825 manifestos e 71 311 708 B comprimidos do modelo diário. Um tar local validou os seis snapshots e 18 ficheiros. [Simulação](simulacao-cinco-anos.json) · [Tar Pages](resultados-pages-preview.json)

## Continuidade e recuperação

| Cenário | Evidência | Resposta |
|---|---|---|
| Telegram ou credencial indisponível | Timeout e erro de credencial simulados no backfill deixaram cursor e diário intactos. Testes existentes cobrem publicação e reenvio incerto. | Servir relatório íntegro com antiguidade visível. Corrigir sessão só por operador autorizado. Recuperar páginas em arquivo separado. |
| GitHub Actions indisponível | Watchdog real devolveu erro perante HTTP 503. Atrasos de 24 e 72 h são detetados em modo de leitura, sem dispatch. | Actions, Git e Pages no mesmo fornecedor não são redundância. Conservar cópia independente e arrancar só um escritor autorizado. |
| Cloudflare indisponível | Ausência do cron foi modelada e recuperação em modo observe verificada. Zero dispatches. O ficheiro versionado tem recuperação desligada. | Verificação read-only independente. Não confiar no watchdog durante a indisponibilidade desse fornecedor. |
| Concorrência e resposta ambígua | CAS teve um vencedor. Testes Git preservaram commits concorrentes, recusaram conflito e fizeram retry idempotente. | Verificar ancestry e prova do envio antes de repetir. Colocar conflito em quarentena e recuperar do último HEAD íntegro. |
| Corrupção e cursor interrompido | Página truncada recusada com cursor inalterado. Restaurada a cópia conhecida, o cursor retomou sem refazer o primeiro pedido. | Restaurar por hashes, não reusar corpo corrompido. Conservar manifestos e objetos de evidência. |
| 24 h e 72 h | **670 e 1 497** IDs visíveis recuperados em **7 e 15 páginas**, zero duplicações e diário vivo intacto. 60 IDs recentes fora do backfill em cada caso. | A cadência nominal de 10 min e uma página por execução dão 70/150 min de planeamento, antes de fila e falhas. Sem SLA medido. |

O RPO de zero no ensaio aplica-se aos IDs visíveis na fixture. Telegram pode ter apagado ou editado versões. A sessão é ponto único de recolha. Os bundles de publicação têm retenção de sete dias e dependem de Actions no GitHub. O watchdog depende do cron Cloudflare e também da API GitHub. [Backfill](resultados-continuidade.json) · [Watchdog](resultados-watchdog.json)

## Inventário de prioridades

| Prioridade | Problema | Critério de fecho |
|---|---|---|
| P0, travão de ativação | Revisão misturada e caminho de integridade por comprimento na PR ainda inativa. | Troca atómica de versão completa, SHA-256 obrigatório e última revisão verificada recuperável. |
| P0, invariantes | Fontes, protocolo e registo prospetivo têm de permanecer preservados. | Hashes independentes e nenhuma incorporação retrospetiva nem alteração de algoritmos. |
| P1 | 772 Kalshi, 424 Betfair e 144 rejeições silenciosas Polymarket. | Diagnóstico com regras estruturais e precisão validada por classe antes de qualquer admissão. |
| P1 | Dependência de relatórios completos em Git. | Medir armazenamento e retenção de todas as fontes, restauração e compatibilidade de rotas em paralelo. |
| P1 | Mesma revisão corrompida não reparada, entrada secundária não fixada e limiar 2 s falhado no corte atual. | Reparação, manifesto conjunto, p95 e mediana inferiores a 2 s no perfil aceite. |
| P1 | Falhas correlacionadas GitHub e único transporte Telegram. | Cópia independente verificada e exercício de recuperação com relatórios de frescura e integridade. |
| P2 | Notas sem ID, mercados Stake dependentes de outras provas, aliases e relógios anómalos. | Corpus de referência por variante e manutenção em quarentena quando houver ambiguidade. |
| P3 | Serviço e contexto entram no mesmo contador e o relatório clássico expõe amostras. | Contadores separados e aprovação própria de política de dados exibidos. |

## Migração e reversão

1. **Fixar interfaces.** Inventariar rotas e hashes do diário, projeções, protocolo e estados. Manter históricos no Git. Rever os dois drafts offline.
2. **Sombra após autorização.** Medir bytes de todas as fontes com segmentos por ordem de observação, reconstruir o inventário e comparar relatório a relatório. Provar restauração de uma segunda cópia independente. Sem escrita de sombra no registo científico.
3. **Validação de staging.** Verificar pacote completo e hashes de todas as entradas. Exercitar concorrência, HTTPS, cache e conteúdo truncado. Medir p50/p95 e compatibilidade Android, dashboard clássico, V6.1 e caminhos lidos pelo Brain. Nenhuma configuração externa nesta intervenção.
4. **Decisão de mudança separada.** Confirmar orçamento, retenção, limite de banda, escrita única e reposição. Aprovar um novo deploy só depois dos gates técnicos.
5. **Reversão.** Suspender nova publicação e voltar ao último release íntegro, mantendo rotas e fonte conhecida. Restaurar configuração anterior só com aprovação. Preservar bytes e evidência de falhas. Ausência de force-push, reenvio incerto ou alterações ao diário.

Os vereditos estão no [relatório executivo](relatorio-executivo.md) e os comandos em [continuação](continuacao.md). Custo adicional incorrido pela investigação de 0 €. A V6.1, o Brain V5, a PR #98, o protocolo e o registo prospetivo ficaram fora das mudanças.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
