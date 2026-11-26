# Dropping Odds Handicap

## Auditoria da recolha

A auditoria usou a revisão `f6032e5367420e63fc270dd4edec3607786408b5` de `nunovinhas-creator/goal-roi`. O arquivo `data/messages.jsonl` tinha 9 134 linhas. Preservava 51 mensagens de alerta com o cabeçalho `📉 Odds drop` e dois textos de configuração que apenas mencionavam as quedas. Os alertas incluíam 36 handicaps, 14 totais e um empate. Os três exemplos do pedido estavam no arquivo. [Revisão auditada](https://github.com/nunovinhas-creator/goal-roi/tree/f6032e5367420e63fc270dd4edec3607786408b5)

- `reader.py` lê a conversa ScanGoal com a sessão existente, ignora mensagens de saída e guarda `message_id`, `date_utc`, `edit_date_utc`, texto, botões e hora de receção. Os 51 alertas tinham `receipt_version=1`, que marca o fim da leitura da página.
- `store.py` compara texto, botões e data de edição por ID Telegram. Uma versão diferente fica no arquivo com SHA-256 do texto. O ficheiro conserva as versões anteriores e o leitor relê as mensagens recentes para detetar edições. Os 51 alertas tinham IDs Telegram distintos e nenhuma edição observada.
- `parser.py` não tem um ramo para este cabeçalho. O módulo novo lê diretamente as versões arquivadas.
- `notify.py` já envia pela Bot API usando o par `TG_BOT_TOKEN` e `TG_CHAT_ID` do destino pessoal. O módulo reutiliza a função de transporte. `stake_alerts.py` tem outras regras e outro registo de entregas.
- `collect.yml` tem uma cadência nominal de 10 minutos, com leituras rápidas de aproximadamente 2 minutos dentro da execução. As publicações durante a recolha e no fim das leituras rápidas colocam a fonte no Git. O atraso das 51 receções observadas teve mediana de 1 min 57 s, percentil 95 de 6 min 16 s e máximo de 8 min 19 s.
- O corpo observado contém desporto, competição, contagem decrescente, jogo, mercado, odds, estratégia e endereço `MySignals.ashx?go=…`. Os botões remetiam para a configuração de alertas. Foram observados 51 valores distintos de `go=` nesta conversa e neste intervalo. A amostra não estabelece estabilidade nem unicidade global.

## Implementação

`scangoal/dropping_odds_alerts.py` identifica sinais por regras explícitas. A equipa selecionada tem de corresponder exatamente a uma das duas equipas após normalização. Para o jogo, soma a duração à data original do Telegram em UTC e aplica o calendário civil de `Europe/Lisbon` à hora da avaliação. Uma edição com alteração de conteúdo ou sem original observável torna o sinal ambíguo. Uma edição apenas de formatação ou botões conserva a referência original.

O filtro usa `Decimal` para as cinco linhas, odds e queda. Guarda a percentagem da mensagem e a calculada a partir das odds. A divergência admissível é metade da menor unidade decimal apresentada na percentagem, com os extremos incluídos. A comparação usa multiplicação para evitar arredondar o quociente antes da decisão. Os filtros de produção são independentes da configuração do ScanGoal.

`scangoal/dropping_odds_journal.py` guarda o estado e o JSONL na referência dedicada `refs/notes/goal-roi-dropping-odds`. Cada commit publica ambos atomicamente, com verificação da revisão anterior. O estado valida versão, número de eventos, transições e SHA-256 do registo. O workflow usa um grupo de concorrência próprio. A ausência ou corrupção da referência suspende o módulo. O arquivo é lido e as publicações do módulo só atingem a referência dedicada.

O ID Telegram identifica reprocessamentos e edições. Entre IDs Telegram diferentes, a deduplicação exige competição, equipas, seleção e linha normalizadas, com início até dois minutos de diferença. Dentro desse grupo, o mesmo `go=` ou o mesmo par de odds identifica uma repetição. A janela tolera o minuto inteiro da contagem decrescente. Uma colisão de `go=` em jogos diferentes não basta para excluir o sinal.

A sequência de auditoria regista receção, decisão e, quando aplicável, uma intenção `pending` confirmada no Git antes da Bot API. Depois da publicação, o módulo revalida o início, o dia e a idade imediatamente antes do pedido. Confirmações positivas tornam-se `sent`. A resposta 429 é falha confirmada. Uma interrupção, resposta ambígua, timeout ou 5xx deixa a entrega incerta. Uma intenção sem desfecho passa a `uncertain` na próxima execução. Cada intenção tem no máximo uma tentativa.

`.github/workflows/dropping-odds.yml` usa um job próprio depois de `Collect ScanGoal` e uma janela de execução separada. Lê um instantâneo publicado de `main`, tem checkout esparso, timeout de 90 segundos para o processo e de dois minutos para o job. O modo `off`, incluindo valores ausentes ou desconhecidos, não executa o módulo. `shadow` regista decisões sem aceder à Bot API. `on` exige a antiguidade máxima aprovada e um instante explícito de autorização, ambos configurados independentemente. O envio exige que a data original do sinal seja posterior ao início autorizado e à criação do registo. [Semântica do evento GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run)

O limite proposto para revisão é de **15 minutos** desde a mensagem original. A amostra observada e a cadência nominal deixam margem para publicação e agendamento. O job corre depois da recolha completa e pode acrescentar vários minutos à latência de receção. Convém medir essa diferença em sombra antes de aprovar o limite. O replay aplicou os 15 minutos apenas como cenário offline.

## Replay do arquivo real

O replay usou uma cópia só de leitura dos bytes da revisão auditada, com SHA-256 `504563c6ba07eaa582bb43c52ea24c0e03dd479c990fdadbf26505af9f136f93`. Os relógios de avaliação foram as horas históricas de receção. A reprodução a esse instante é otimista em relação ao agendamento do workflow. Foi uma execução offline de passagem única.

| Métrica | Valor |
| --- | ---: |
| Mensagens Dropping Odds e versões | 51 |
| Jogos distintos do próprio dia em Lisboa | 41 |
| Sinais elegíveis | 14 |
| Excluídos | 37 |
| Duplicados detetados | 0 |
| Versões Telegram adicionais | 0 |
| Mensagens ambíguas | 2 |

Primeiro motivo atribuído por mensagem excluída.

| Motivo | Sinais |
| --- | ---: |
| Mercado fora do handicap entre equipas | 15 |
| Linha de handicap fora da lista | 11 |
| Início noutro dia civil | 6 |
| Queda apresentada fora de 15–19% | 3 |
| Divergência incompatível no arredondamento | 1 |
| Jogo já iniciado à receção | 1 |

Entre os 36 handicaps analisáveis, a distribuição foi a seguinte.

- Linhas de handicap. −4,75 (1), −3 (1), −2,25 (1), −2 (1), −1,75 (1), −1,5 (3), −1 (2), −0,75 (4), −0,25 (6), 0 (3), +0,25 (5), +0,5 (3), +0,75 (1), +1 (1), +1,5 (1), +1,75 (1), +3,5 (1).
- Percentagens de queda apresentadas. 15% (4), 16% (8), 17% (4), 18% (5), 19% (3), 20% (2), 22% (3), 23% (1), 25% (2), 27% (1), 28% (2), 31% (1).
- Odds finais. 1,72 (1), 1,75 (1), 1,78 (1), 1,79 (1), 1,81 (1), 1,82 (4), 1,83 (2), 1,84 (3), 1,85 (3), 1,86 (1), 1,87 (2), 1,89 (1), 1,90 (1), 1,91 (1), 1,92 (3), 1,93 (2), 1,95 (1), 1,96 (1), 1,98 (1), 1,99 (1), 2,00 (1), 2,02 (1), 2,06 (1), 2,10 (1).

Nos 14 elegíveis, as linhas foram −0,75 (2), −0,25 (6), 0 (2), +0,25 (3) e +0,75 (1). As quedas foram 15% (3), 16% (5), 17% (2), 18% (2) e 19% (2). A odd final variou de 1,75 a 2,10. O cenário sem limite de 15 minutos devolveu as mesmas decisões.

Os 14 alertas simulados seriam apresentados com a indicação «Sinal estatístico. Rentabilidade não demonstrada.» As horas estão em Portugal continental no dia 26 de novembro de 2026.

| ID Telegram | Jogo | Competição | Início | Seleção | Odds e queda | Fonte |
| --- | --- | --- | --- | --- | --- | --- |
| 33974 | Udelas FC – Chorrilo | Panama Liga Prom | 00:59 | Chorrilo AH −0.25 | 2.20 → 1.85 · −16% | [ScanGoal](https://scangoal.com/MySignals.ashx?go=22777) |
| 34017 | Olympiacos – AC Milan | Europa League | 17:44 | AC Milan AH −0.25 | 2.53 → 2.10 · −17% | [ScanGoal](https://scangoal.com/MySignals.ashx?go=22849) |
| 34035 | Hapoel Nof HaGalil – Maccabi Kiryat Ata Bialik | Liga Alef | 15:59 | Maccabi Kiryat Ata Bialik AH +0.75 | 2.18 → 1.84 · −16% | [ScanGoal](https://scangoal.com/MySignals.ashx?go=22857) |
| 34046 | Putra Sang Fajar – Persibo Bojonegoro | Indonesia Liga Nusantara | 08:29 | Putra Sang Fajar AH −0.25 | 2.19 → 1.82 · −17% | [ScanGoal](https://scangoal.com/MySignals.ashx?go=22953) |
| 34047 | Kampala Queens Women – Uganda Martyrs High School Women | Uganda Super League Women | 08:59 | Kampala Queens Women AH −0.25 | 2.35 → 1.98 · −16% | [ScanGoal](https://scangoal.com/MySignals.ashx?go=22954) |
| 34056 | Persipal Palu – Persikutim Kutai Timur | Indonesia Liga Nusantara | 08:29 | Persikutim Kutai Timur AH −0.25 | 2.19 → 1.84 · −16% | [ScanGoal](https://scangoal.com/MySignals.ashx?go=23051) |
| 34057 | Persiba Bantul – Persinga Ngawi | Indonesia Liga Nusantara | 08:29 | Persinga Ngawi AH +0.25 | 2.32 → 1.91 · −18% | [ScanGoal](https://scangoal.com/MySignals.ashx?go=23130) |
| 34061 | CSKA Sofia – Trabzonspor | UEFA Conference League | 19:59 | Trabzonspor AH −0.75 | 2.27 → 1.93 · −15% | [ScanGoal](https://scangoal.com/MySignals.ashx?go=23181) |
| 34063 | Uttaradit – Songkhla FC | Division 2 | 10:59 | Uttaradit AH −0.75 | 2.24 → 1.81 · −19% | [ScanGoal](https://scangoal.com/MySignals.ashx?go=23198) |
| 34076 | Paysandu Women – Tiradentes PA Women | Brazil Campeonato Paraense Women | 17:59 | Tiradentes PA Women AH +0.25 | 2.19 → 1.78 · −19% | [ScanGoal](https://scangoal.com/MySignals.ashx?go=23275) |
| 34082 | Al Baqaa – Al Jazeera Amman | Pro League | 14:59 | Al Jazeera Amman AH 0 | 2.08 → 1.75 · −16% | [ScanGoal](https://scangoal.com/MySignals.ashx?go=23297) |
| 34095 | BG Pathum United – Lion City Sailors FC | AFC Champions League Two | 12:14 | Lion City Sailors FC AH −0.25 | 2.18 → 1.85 · −15% | [ScanGoal](https://scangoal.com/MySignals.ashx?go=23380) |
| 34111 | AaB Aalborg – Esbjerg | 1st Division | 17:59 | Esbjerg AH +0.25 | 2.43 → 2.06 · −15% | [ScanGoal](https://scangoal.com/MySignals.ashx?go=23439) |
| 34132 | Al-Mu Aidar – Al Waab FC | League 2 | 14:14 | Al Waab FC AH 0 | 2.46 → 2.02 · −18% | [ScanGoal](https://scangoal.com/MySignals.ashx?go=23543) |

O exemplo A, Al Waab FC, era elegível à receção. O início calculado foi 14:14:40 UTC. O exemplo B foi excluído pelo mercado de totais. O exemplo C foi excluído pelo dia de início. Também apresenta uma divergência entre os 16% declarados e os cerca de 16,52% calculados, contabilizada entre as duas mensagens ambíguas. Os testes adversariais usam dados sintéticos e ficam fora destas contagens.

Estes comandos reproduzem o ensaio numa cópia local do repositório e mostram o texto completo dos 14 alertas.

```bash
git show f6032e5367420e63fc270dd4edec3607786408b5:data/messages.jsonl > /tmp/goal-roi-dropping-odds-replay.jsonl
chmod a-w /tmp/goal-roi-dropping-odds-replay.jsonl
sha256sum /tmp/goal-roi-dropping-odds-replay.jsonl
python -m scangoal.dropping_odds_alerts replay /tmp/goal-roi-dropping-odds-replay.jsonl --max-age-minutes 15
```

## Testes e isolamento

A suite Python da revisão de base passou 1 127 testes. Os 27 testes do módulo passaram em execução isolada. Exercitam os exemplos, mercados, precisão, relógios, edições, concorrência em Git local, corrupção, interrupções e todos os modos. O transporte Telegram foi simulado, incluindo 429, 503, timeout e entrega incerta. A suite Node de continuidade passou 12 testes na base e na branch. O YAML e o Bash do novo workflow passaram validação de sintaxe. O teste da lista exata de workflows inclui agora o novo ficheiro e ganhou uma verificação dedicada das condições de execução, dos limites e do isolamento.

A diferença contém cinco ficheiros novos e uma alteração num teste existente. O arquivo original e a cópia de replay tiveram o mesmo SHA-256 antes e depois do ensaio. A comparação offline, com entradas idênticas e relógio fixo em 26 de novembro de 2026 às 14:20:13 de Lisboa, produziu os mesmos SHA-256 na base e na branch.

| Saída comparada | SHA-256 de ambas as revisões |
| --- | --- |
| Relatório completo | `30dc96a23383b756c0aa32726b80d125640925af6ee4d5859eedadfe22e83952` |
| Simulador | `1b952e7546673657935f1676f7b18e2dc8ceea3af7e18573345e5e9ad2f0a518` |
| Pré-visualização e estado simulado dos alertas gerais | `ef8633695440ff822137d39abc89eb07a26f5e584cc68a936f02226f3c9c79b8` |
| Seleções Stake naquele instante | `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945` |
| Resumo prospetivo | `11ffa14091749516d985ac5de742744f4a0c91c6639de3e2c4eb3854eb08bb05` |
| Registo prospetivo verificado, 444 entradas | `6f8a453950cef1a934bd1ba8bdbca97b6a1c97b5a8d5d7af47843cf5d86cede3` |

- Criado `.github/workflows/dropping-odds.yml`
- Criado `scangoal/dropping_odds_alerts.py`
- Criado `scangoal/dropping_odds_journal.py`
- Criado `tests/test_dropping_odds_alerts.py`
- Criado `docs/dropping-odds-handicap.md`
- Modificado `tests/test_collector_install.py`

## Recursos, custo e riscos residuais

- O replay leu 5,9 MiB em cerca de 0,15 s de CPU, com pico de cerca de 52 MiB de memória no ambiente local. O checkout, Git e Telegram acrescentam latência de I/O. O runner alojado ainda não foi medido.
- Para armazenamento, o registo acrescenta tipicamente centenas de bytes a cerca de 1,5 KiB por decisão, conforme os campos e eventos. O Git conserva o histórico e pode compactar versões. A leitura e publicação do instantâneo corrente crescem com o registo.
- O checkout esparso representa cerca de 1,2 MB de objetos Git compactados nesta revisão. O tráfego inclui metadados, Git notes e, em `on`, dependências Python e até um pedido Telegram por sinal com intenção confirmada. O replay teve zero pedidos Telegram. `shadow` usa apenas o Git do mesmo repositório.
- O custo verificável de serviços adicionais contratados ou recursos de produção utilizados pelo módulo neste trabalho foi de 0 €. O repositório é público e o workflow propõe um runner GitHub standard, gratuito para repositórios públicos. O valor faturado da conta não foi consultado. [Preços e regras do GitHub Actions](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- O arquivo pode ficar desatualizado durante uma falha de publicação. O início é previsto a partir de uma duração em minutos. Adiamentos, eliminações ou edições que ainda não tenham sido arquivados ficam fora da observação.
- O timeout pode sacrificar um alerta para evitar duplicação. A proteção presume que a referência Git se mantém íntegra e que operadores não a apagam, recriam ou rebobinam. A integração de concorrência foi exercitada contra um remoto Git local. O caminho completo e as permissões do `GITHUB_TOKEN` para publicar notes na execução alojada exigem verificação em sombra. O ensaio local não valida a latência alojada nem a equivalência semântica de IDs em outras conversas.

## Operação após aprovação

1. **Ativar sombra.** Depois de revisão e disponibilização do workflow em `main`, definir a variável de repositório `GOAL_ROI_DROPPING_ODDS=shadow`. Na primeira instalação, executar manualmente **Dropping Odds Handicap** com `initialize=true`. A inicialização tem de encontrar a referência ausente e grava o momento a partir do qual se observam sinais novos. As execuções seguintes podem seguir a conclusão da recolha. Acompanhar os resultados do job e a referência Git. Manter a antiguidade como proposta até aprovação própria.
2. **Inspecionar.** Depois de `git fetch origin refs/notes/goal-roi-dropping-odds`, calcular a chave com `printf %s dropping_odds_log.jsonl | git hash-object --stdin` e ler a nota com `git show FETCH_HEAD:<chave>`. Usar `dropping_odds_state.json` no mesmo procedimento para consultar o estado.
3. **Desativar imediatamente.** Definir `GOAL_ROI_DROPPING_ODDS=off` ou remover a variável e cancelar execuções ativas de **Dropping Odds Handicap**. Um pedido já aceite pelo Telegram pode ter sido entregue. Conservar a referência de auditoria.
4. **Reverter.** Se o módulo vier a integrar `main`, reverter a alteração da PR em `main` através da revisão habitual, depois da desativação. Preservar a referência de auditoria para evitar perda do histórico de deduplicação. Após corrupção ou perda de estado, restaurar o último instantâneo íntegro verificável sob revisão e manter `off` até essa verificação.
5. **Envio efetivo.** Requer decisão posterior. Após aprovação, fixar `GOAL_ROI_DROPPING_ODDS_ON_SINCE` em UTC no momento autorizado, incluindo em cada reativação, e definir `GOAL_ROI_DROPPING_ODDS_MAX_AGE_MIN` com o limite aprovado antes de selecionar `on`.

**GO para revisão independente da PR em draft. NO-GO para produção enquanto faltar validação em sombra alojada e aprovação do limite e dos envios.**

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
