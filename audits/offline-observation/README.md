# PR G · Auditoria executável e piloto offline

**GO para preparação offline. NO-GO para pedir autorização de execução do piloto real com os artefactos atuais. NO-GO confirmatório.**

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

A base é a PR #87 em `6df43dfb4e6be285600fa54ec601f6cd0976be81`. A matriz executável traduz os riscos apontados na revisão independente #6041697734 em casos testáveis. O relatório verifica o SHA-256 de todos os 31 ficheiros Python de descoberta na base, incluindo os intérpretes dos protocolos congelados. Os casos novos foram gerados em bancos próprios, distintos do banco da PR #87. [Revisão independente](https://github.com/nunovinhas-creator/goal-roi/pull/87#pullrequestreview-6041697734) · [Hashes congelados](frozen.json)

## Resultados da auditoria

Os desafios executáveis distinguem o controlo com inventário selado e vazio, o controlo com antecedente conhecido após a cotação e quatro situações indetermináveis. Estas cobrem uma interrupção de alertas, um recibo de revisão em falta, a prova de golos recebida tarde e um recibo assinado que omite um alerta presente no inventário independente. Para cada situação, quatro permutações com novos IDs, linhas, duplicados e FT inertes preservam o estado da fita, as arestas e a reserva. As capturas seladas só entram no motor quando a assinatura e o recibo estão disponíveis. A verificação de contrato distingue localmente período, fonte, linha e seleção.

Há dois defeitos novos reproduzidos no código congelado e tratados no percurso experimental:

| Defeito confirmado | Tratamento em PR G |
| --- | --- |
| Duas cotações do mesmo contrato e segundo podiam concordar no preço e contradizer-se no volume. A v3.2 preservava um dos volumes na última cotação por desempate determinístico. | O adaptador experimental retira os campos contraditórios e insere uma lacuna de conhecimento. Um inventário completo pode conter factos incompatíveis. A consulta anterior ao segundo recibo conserva o estado então observável. |
| Uma trajetória com golo podia chegar à projeção herdada da v2 sem `signal_type`. A entrada v3.2 abortou com `invalid_literal` na geração de candidatos do novo banco. | O caminho experimental passa apenas alertas e cotações com formato legado à projeção v2. O minerador temporal enriquecido continua a receber toda a fita tipada. O teste conserva a reprodução do erro na entrada congelada e avalia a alternativa do piloto separadamente. |

A verificação de durabilidade injeta uma falha de disco durante a publicação, volta a abrir o diário e compara a cabeça da cadeia com uma execução limpa. Um checkpoint retido deteta a truncagem do sufixo. Uma assinatura forjada é recusada. Os testes adicionais verificam relógios falsos, falhas de identidade, catálogo parcial, preço cruzado e snapshots sem liquidez bilateral. [Relatório executável](report.json)

## Provas de cobertura

O manifesto é selado por uma autoridade de plano e os recibos por uma autoridade de captura distinta. Cada slot fixa jogo, categoria, modo, intervalo e identidade do objetivo. Para a fita de eventos sintéticos completa, o plano inclui também o inventário esperado. A aceitação requer assinatura, domínio, encadeamento, integridade do corpo, identidade, relógio e watermark de fim. Um recibo vazio só cobre a categoria se a ausência corresponder ao inventário selado. Perdas, recibos contraditórios, atrasos e incoerências ficam no relatório. A reconstrução por instante usa apenas o prefixo conhecido. A vista do plano é só de leitura e alterar uma projeção em memória deixa intacto o prefixo verificado. As chaves publicadas nas fixtures são de laboratório e os selos simulam papéis de confiança. A prova de enumeração completa é aplicável ao escritor sintético que conhece esse inventário.

- **Livro**. O manifesto `sampled_clob` prova que uma resposta ocorreu num slot. Nem uma sequência total de slots gera um intervalo contínuo `Coverage` para a v3.2.
- **Fita sintética de eventos**. Há selos independentes para a cotação decimal sintética, alertas, revisões, golos e marcadores. A cotação dessa fita pertence ao cenário sintético do motor científico. O preço de token do livro mantém unidade própria.
- **Teste de isolamento**. O jogo `gap-ctf` tem **todos os slots do livro observados**, enquanto a categoria de alertas falha. O antecedente continua indeterminável. Em `pair-v2`, a prova de golos chega depois de `t` e o controlo também fica indeterminável. A única ausência comprovada neste ensaio CTF pertence à fita sintética com os cinco selos no prazo.

## Piloto de catálogo e livro

As respostas usam dois envelopes de laboratório. O CTF exige `condition_id` e `asset_id`. O V2 exige `v2_market_ref`, `token_id` e `position_id`. A correspondência valida identidade formal do jogo, equipas ordenadas, início, competição, período, liquidação, linha, seleção, lado YES/NO, token e versão de mercado. As duas versões mantêm espaços de identidade próprios. Um catálogo parcial é recusado para preservar o denominador planeado. Estas fixtures exercitam a rejeição de misturas e ainda carecem de validação contra capturas autênticas dos esquemas reais. [Respostas sintéticas gravadas](recorded-books.json)

| Indicador | Resultado |
| --- | ---: |
| Jogos admitidos | 5 |
| Slots previstos por token | 24 |
| Slots planeados / tentados / observados / perdidos | 720 / 717 / 706 / 14 |
| Slots com recibo ou falha registada | 719/720 |
| CTF observados / previstos | 418/432 |
| V2 observados / previstos | 288/288 |
| Comparações de livro válidas / excluídas / indetermináveis | 324 / 987 / 64 |
| Chamadas de rede, ordens e notificações | 0 / 0 / 0 |
| Pares confirmatórios | 0 |

A grelha começa em T−6 h, repete a cada 15 minutos e termina antes do início. O universo inclui os dois limites de ±45 min e exclui um jogo a 46 min. O relatório detalhado escreve o plano, as observações ou motivos de perda, a identidade, o relógio da fonte, pedido e disponibilidade, latência, hash e níveis por lado em unidades nativas. A melhor oferta de venda é o melhor bid e a de compra é o melhor ask. A profundidade é registada em shares. O replay exerce timeout, 429, desligamento, falha de disco, recibo perdido, suspensão e reabertura, token trocado, alteração do início, livro parcial, livro cruzado, ausência de liquidez e relógio fora da tolerância. Um conflito recebido posteriormente deixa de qualificar o slot nesse instante e a comparação anterior continua no relatório.

As comparações válidas na tabela significam observações de livro comparáveis pela identidade e relógio. A disponibilidade de contrapartida, a prova de ausência, o emparelhamento científico e a execução são medidas separadas. A latência máxima de 3 s e a cobertura são propriedades desta simulação. A potência para dados reais fica indeterminada.

## Descoberta autónoma em bancos separados

O minerador recebe só a trajetória observada. O motivo plantado tem uma descida de preço no mesmo contrato, um alerta de volume elevado, um alerta de direção oposta, um golo e uma mudança de marcador. Os motivos falsos incluem ordem invertida, subida de preço, alerta recebido tarde, período errado e marcador em conflito. A regra de referência é usada apenas pelo avaliador. O treino e os dois bancos de seguimento têm datas, jogos e sementes distintos.

O caminho experimental produziu 47 candidatos distintos com dois a quatro passos. A escolha pelo treino ficou congelada antes de calcular as métricas de seguimento. O candidato escolhido acertou **6 de 6 terminais** em cada banco, mas a precisão foi **6/18** e recuperou **zero caminhos completos**. Os falsos positivos foram seis subidas de preço e seis casos de período incompatível em cada banco. A referência recuperou seis terminais e seis caminhos completos sem marcar motivos falsos em cada banco. **Nenhum dos 47 candidatos** cumpriu os mínimos nos dois bancos, de 90% de precisão no terminal, 80% de recuperação do terminal e 75% de recuperação do caminho completo. O sistema procura automaticamente um runtime e modelo GGUF locais. Nesta execução a comparação com a LLM ficou sem métricas por falta de um par runtime–modelo disponível.

## Critérios de decisão

| Autorização em causa | Critério verificável | Situação |
| --- | --- | --- |
| Continuar laboratório offline | Proteger hashes congelados, reproduzir a matriz e o relatório byte a byte, contabilizar ≥95% dos slots com recibo ou motivo certificado, respeitar fronteiras de dados e zero operações externas. | **GO**. O replay registou 719 de 720 slots com recibo ou motivo e as falhas injetadas foram classificadas de forma conservadora. |
| Pedir autorização para executar o piloto observacional real | Além dos gates offline, verificar catálogo e livro contra os envelopes efetivamente autorizados, identidade e liquidação em cada versão, termos aplicáveis e restrições geográficas. | **NO-GO**. Os dois envelopes exercitados são sintéticos. A verificação de contrato real e o preflight de termos e geografia continuam por fazer. |
| Usar observações para confirmação v3.2 | Provar cobertura real das categorias exigidas, medir suporte com dados prospetivos e passar o gate de transferência do minerador num banco independente. | **NO-GO**. Falta atestação real de continuidade e o benchmark de descoberta ficou abaixo dos mínimos. |

O desenho de autorização usa o preflight previsto na revisão para endpoints de leitura. [Documentação de livros da Polymarket](https://docs.polymarket.com/market-data/prices-order-books) · [Termos de uso](https://polymarket.com/tos)

## Reprodução

Na raiz do repositório:

```sh
PYTHONPATH=. python audits/offline-observation/run.py --deterministic-only > /tmp/goal-roi-g.json
cmp /tmp/goal-roi-g.json audits/offline-observation/report.json
PYTHONPATH=. python audits/offline-observation/run.py --deterministic-only --details > /tmp/goal-roi-g-details.json
python -m unittest discover -s tests -p test_offline_observation.py -v
node --test ops/continuity/watchdog.test.mjs
```

O comando sem `--deterministic-only` usa o modelo local se o encontrar. O replay cria o diário numa diretoria temporária e usa fixtures versionadas ou geradas automaticamente. A suite completa é descoberta pelo workflow de testes existente.
