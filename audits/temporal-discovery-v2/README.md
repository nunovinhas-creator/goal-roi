# PR C · Descoberta temporal v2

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

A branch parte de `0798e69b27b6d131735737c3a7c2f33a501f8720`, o HEAD corrigido da PR #83. Enquanto a PR #83 estiver aberta, esta revisão aponta para a sua branch. O comando exige `--version 2` para a nova linguagem, descoberta e avaliação. O caminho habitual conserva o DSL, os relógios, o identificador de protocolo e a impressão digital do intérprete da PR #83.

## Linguagem e seleção dos candidatos

O DSL v2 aceita até oito eventos, com até oito predicados tipados em cada passo. Qualquer par de posições crescentes pode ter restrições de tempo de emissão e receção, mudança de linha, diferença no marcador, movimento de odd ou mudança de fonte. A persistência verifica a mesma fase, direção, linha ou estado do marcador nos extremos e nos alertas observados entre eles. Um campo intermédio ausente torna a persistência indeterminada, exceto se outro alerta já provar uma alteração. Uma reversão pode combinar dois movimentos de sinal oposto no mesmo caminho. Os movimentos de preço conservam o requisito de igualdade de seleção, fonte e mercado da v1. Os mesmos predicados em DSL v1 e v2 têm identificadores de versões distintas.

A recolha de candidatos percorre âncoras por estratos de fonte, fase, período e semana. Estende prefixos em três vistas com os alertas disponíveis no próprio instante, mantendo os eventos gerais, os eventos na mesma linha e as cotações da mesma seleção. As projeções usam conjunções categóricas, recorrência quantitativa e trajetórias. A expansão de padrões suportados acrescenta cortes aprendidos e relações de intervalo ou estado, inclusive entre posições não adjacentes. O ranking pondera suporte em jogos e dias distintos, raridade da população, surpresa estrutural e complexidade. A lista final alterna comprimentos, diversifica famílias e corta redundância de apoio (Jaccard amostral ≥ 0,80). Nenhum destes passos consulta FT, acerto ou ROI.

Os limites por execução são 4.096 âncoras amostradas, 236.000 operações de projeção e expansão, 32.000 assinaturas internas, 54 propostas determinísticas, 2.000.000 operações de replay e 300.000 comparações de sobreposição. Uma pesquisa incompleta é reportada. O replay com busca incompleta não emite uma decisão posterior para o jogo afetado. A avaliação só usa matrizes de sobreposição completas. O limite por âncora continua a ser 4.096 operações. A aritmética do ranking é uma heurística estrutural. A comparação de assinaturas amostrais pode agrupar mecanismos distintos, como mostra a perda da reversão multiorigem no ensaio principal.

## Relógio da decisão e protocolo prospetivo proposto

A descoberta avalia a recuperação estrutural pelo primeiro fecho observável em cada jogo. A avaliação prospetiva divide antecipadamente as fixtures em dois braços através do SHA‑256 de um sal fixo e da identidade canónica. Cerca de dois terços são destinados ao braço de casos. Um caso reserva a primeira sequência completa numa âncora admissível e observada. Uma âncora negativa anterior deixa o caso em observação. O braço de controlo reserva a primeira âncora elegível com ausência determinável da sequência. Se a primeira verificação determinável for positiva, a fixture fica de fora. Um controlo já reservado conserva a decisão mesmo que depois complete a sequência. O estimando é a diferença face a um alerta sem a sequência **na hora da sua própria reserva**. Esta alocação impede que a indisponibilidade ou o ganho futuro de um jogo mude o braço a que pertence. Também sacrifica cobertura, especialmente em populações onde praticamente toda oportunidade tem a sequência.

A proposta `temporal-day-contract-v2` tem hash de protocolo `659d7de3788657abfed2b6a07170f16381e3b7acca202084f67a6af18ed98f3a`. Requer registo explícito de definições, hash do intérprete e publicação independentemente testemunhada antes de uma nova janela de 90 dias. A janela começa na primeira meia-noite UTC posterior à publicação comprovada e ao corte de descoberta. Usa apenas jogos novos, com recibos originais concluídos. Nenhuma hipótese desta auditoria tem jogos confirmatórios reais.

- Os blocos são dia UTC do início × contrato, mantendo fonte, fase, mercado nativo, período, linha e seleção. Os dois braços mantêm fixtures distintas. As decisões são reservadas nos seus próprios relógios. Na avaliação, os pesos são calculados a partir das cotações e do estado gravado nesses relógios. O intervalo de 15 minutos da v1 não é imposto.
- Dentro de um bloco, a ponderação linear pela maior distância normalizada exige sobreposição em log da odd (0,35), tempo até início pré-jogo (360 minutos) ou minuto LIVE (30), e golos e diferença de golos LIVE (2). Uma covariável ausente deixa a reserva no denominador de cobertura e fora do contraste. O relatório mostra as diferenças restantes nas covariáveis, frações de sobreposição, cobertura de FT ponderada e amostra efetiva por braço. A reserva anterior ao FT e o desenho da sobreposição têm hashes separados dos desfechos.
- O desfecho primário é o diferencial diário de rendimento teórico ponderado. Aplica uma redução de 2% aos ganhos positivos e um custo fixo de 0,02 por unidade. Fixam-se os FT após 72 h da decisão. FT ausente com odd conhecida recebe limites de perda e ganho. Expõem-se a média observada, limites de identificação e um intervalo de 95% por estatísticas de ordem para a mediana diária, alargado pelos limites de FT ausente.
- O teste unilateral de sinal usa os limites inferiores diários, com pelo menos 40 casos efetivos e 40 controlos efetivos no suporte comum, 20 dias informativos e 80% de peso liquidado por braço. É aplicado uma vez após o fecho da janela e do seguimento. Holm inclui todos os candidatos registados na vaga, com nível `0,05/[n(n+1)]` para a vaga `n`. O intervalo descritivo de 95% não é ajustado à multiplicidade. O teste pressupõe dias suficientemente independentes e a validade do sinal sob a nula. Ponderar estes campos reduz diferenças observadas e deixa confundimento residual.

A avaliação mantém os rótulos `experimental`, `insufficient` e `rejected`. A retenção de um contraste sob este teste continua a ser experimental. A cotação é a anunciada no alerta e a implementação não mede execução, limites, rejeições nem a odd disponível na 1xBet.

## Benchmark v1 e v2

A unidade para recuperação é o conjunto de jogos. Um padrão conta como recuperado se alguma proposta tiver Jaccard ≥ 0,60 com os jogos plantados. A precisão de candidatos é a fração de propostas que atingem esse limiar para um padrão verdadeiro. Propostas sem correspondência são apresentadas como não atribuídas, pois o banco não permite concluir que toda relação inédita é falsa. Os limiares, as etiquetas e as regras-oráculo são usados exclusivamente pelo benchmark. A avaliação prospetiva usa uma janela nova e o desfecho monetário sintético como ensaio de potência distinto.

| Banco | Semente | Jogos | Recuperação v1 → v2 | Precisão por candidato v1 → v2 |
| --- | ---: | ---: | ---: | ---: |
| Referência, 30 dias | 83 | 720 | 1/6 → 5/6 | 5,6% → 55,6% |
| Referência, 30 dias | 84 | 720 | 1/6 → 5/6 | 5,6% → 46,3% |
| Referência, 30 dias | 85 | 720 | 0/6 → 5/6 | 0% → 46,3% |
| Corte curto, 16 dias | 83 | 384 | 1/6 → 5/6 | 11,1% → 66,7% |
| Corte longo, 45 dias | 83 | 1.080 | 1/6 → 5/6 | 5,6% → 53,7% |
| Generalização | 73129 | 550 | 2/4 → 4/4 | 19,4% → 57,4% |
| Generalização | 96403 | 550 | 1/4 → 4/4 | 19,4% → 61,1% |

| Padrão na referência | Melhor Jaccard v2 nas sementes 83 / 84 / 85 | Leitura |
| --- | --- | --- |
| `delayed_confirmation_3` | 1 / 1 / 1 | recuperado |
| `live_context_x_price_2` | 1 / 1 / 1 | recuperado |
| `nonadjacent_delay_4` | 0,981 / 0,984 / 0,986 | recuperado |
| `oscillation_8` | 1 / 1 / 1 | jogos recuperados por sequências por vezes mais curtas |
| `price_up_then_down_3` | 1 / 1 / 1 | recuperado |
| `multisource_reversal_3` | 0,009 / 0,095 / 0,008 | perdido nas três sementes |
| `false_promising_2` | 0,030 / 0,013 / 0,013 | abaixo do limiar estrutural |

Um Jaccard perfeito pode identificar um substituto estrutural que coincide nestes dados. Por exemplo, uma sequência curta de cotações na mesma linha pode localizar os mesmos jogos que a oscilação de oito eventos. Para a generalização, o ficheiro independente `heldout.py` usa novas regras geradoras, mais ruído, preços dispersos, atrasos variáveis e eventos LIVE e de mudança de período. Foi executado depois de congelar o minerador no blob `d282d71af43180fbe18f2cb70bc049e0b22549c3`. O núcleo do minerador conserva esse blob. Este ensaio distingue recuperação no banco deslocado do ajuste à distribuição das três sementes de referência.

Na referência, a v1 fez 23,1–24,8 mil projeções e 180–194 mil operações de replay. A v2 fez 170,7–179,2 mil projeções, 235,4–235,5 mil operações totais de geração e 293–356 mil operações de replay. A matriz de sobreposição acrescentou 3,2–3,8 mil comparações. O benchmark que mede recuperação também recompõe as reservas, separadamente destes contadores. Na primeira execução do ensaio completo em Python 3.12, o tempo por versão e semente da referência foi de 3,65–3,80 s na v1 e 4,49–4,65 s na v2. A procura foi parcial nas expansões da referência e no corte mais longo. O JSON mostra os limites atingidos, as métricas por padrão, propostas sem atribuição e a cobertura da população.

### Potência e falsificação em jogos posteriores

As sete definições-oráculo foram publicadas num diário temporário com testemunho sintético antes de 90 dias independentes. O padrão enganador teve o efeito positivo só durante os primeiros 30 dias de descoberta. No ensaio prospetivo:

| Semente | Verdadeiros retidos v1 | Verdadeiros retidos v2 | Padrão enganador retido v1 / v2 |
| --- | ---: | ---: | ---: |
| 83 | 0/6 | 0/6 | 0 / 0 |
| 84 | 0/6 | 1/6 | 0 / 0 |
| 85 | 0/6 | 1/6 | 0 / 0 |

A v2 formou mais blocos úteis. Por exemplo, a reversão multiorigem do oráculo passou de 4–12 dias emparelhados na v1 para 58–75 dias com suporte na v2. O ensaio continua a mostrar baixa potência após os cortes de cobertura e Holm. A população LIVE plantada não teve um braço negativo comparável, pois o contexto quase identifica a exposição. Num cenário nulo para as sete regras, 0 de 21 testes de três sementes produziram retenção. São ensaios finitos com parâmetros sintéticos. O banco também pode dar o resultado plantado a uma seleção de âncora diferente da aposta implícita no oráculo, o que dilui efeitos e impede interpretar estes números como estimativa de potência universal.

No snapshot real da PR A, a v2 avaliou 54 propostas no corte de 24 de novembro às 08:22 UTC. Vinte e oito tinham ambos os braços e alguma sobreposição histórica, 26 ficaram insuficientes e houve zero jogos confirmatórios. O estudo preserva a descoberta histórica como um exercício separado da validação futura.

## CLI e auditoria

```sh
# Os comandos v1 existentes continuam a usar a política anterior.
python -m scangoal.discovery discover --version 2 --as-of 2026-11-24T08:22:00Z --format summary
python -m scangoal.discovery prompt --version 2 --as-of 2026-11-24T08:22:00Z

# Publicação e avaliação só em diretórios de investigação externos.
python -m scangoal.discovery discover --version 2 --as-of 2026-11-24T08:22:00Z \
  --registry /tmp/goal-roi-c-proposals --commit
python -m scangoal.discovery evaluate --version 2 --as-of "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --registry /tmp/goal-roi-c-proposals --receipts /tmp/goal-roi-c-receipts \
  --evidence-dir /tmp/goal-roi-c-evidence

PYTHONPATH=. python audits/temporal-discovery-v2/benchmark.py --suite all > /tmp/goal-roi-c-benchmark.json
python -m unittest discover -s tests -p test_discovery_v2.py -v
python -m unittest discover -s tests -v
node --test ops/continuity/watchdog.test.mjs
git diff --name-status 0798e69b27b6d131735737c3a7c2f33a501f8720..HEAD
```

O registo usa lotes imutáveis com impressão digital das definições e versão do intérprete. O arquivo opcional de avaliação usa outro diretório e guarda o snapshot de desfechos por conteúdo e hash, incluindo rejeições e a referência ao lote publicado. Alterar o protocolo, código interpretativo ou regra implica novo registo antes de outra janela. A LLM local pode propor relações novas usando o DSL v2 e a mesma fronteira JSON. A descoberta é funcional sem LLM. As fontes de FT e as cotações avaliadas vêm das provas do replay, fora do alcance da resposta do modelo.

Foram testados recibos tardios, persistência indeterminada, reexecução após eventos posteriores, controlo reservado antes de uma exposição tardia, repetição de fixtures, orçamento truncado, alteração do resultado após a reserva, separação de protocolos, prova de publicação e idempotência de arquivo. A CI usa o workflow existente. As alterações ficam no circuito experimental e nos seus testes e documentação.
