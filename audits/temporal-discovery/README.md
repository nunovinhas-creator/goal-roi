# PR B · Descoberta temporal autónoma

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

A auditoria pré-merge está em [AUDIT_PR83.md](AUDIT_PR83.md). Inclui o estado real da autonomia, os defeitos corrigidos, a explicação dos zero pares e o banco adversarial. O motor é uma ferramenta offline. Não corre de forma contínua nem aprende sozinho em produção.

O ponto de entrada é `python -m scangoal.discovery`. O motor lê o histórico recolhido pelo Goal-ROI e usa a projeção de cotações da PR A para obter identidade de partida, corpo original, recibo, decisão admissível e provas de FT. A projeção evita materializar todos os pares quando só são precisos os relógios das âncoras. A auditoria habitual da PR A conserva a saída original. O painel de investigação em Markdown é servido pela CLI. O JSON contém a análise completa, com exemplos e provas de proveniência.

## Descobrir e acompanhar

Os comandos de descoberta funcionam com a instalação normal do repositório. Use a data de corte com fuso explícito. Os dois primeiros comandos mostram o mesmo replay num painel e em JSON.

```sh
python -m scangoal.discovery discover --as-of 2026-11-24T08:22:00Z --format summary
python -m scangoal.discovery discover --as-of 2026-11-24T08:22:00Z > /tmp/temporal-discovery.json

python -m scangoal.discovery discover --as-of 2026-11-24T08:22:00Z \
  --registry /tmp/goal-roi-research-ledger --commit

python -m scangoal.discovery registry --registry /tmp/goal-roi-research-ledger \
  --as-of "$(date -u +%Y-%m-%dT%H:%M:%SZ)"

python -m scangoal.discovery evaluate --registry /tmp/goal-roi-research-ledger \
  --receipts /tmp/goal-roi-research-receipts --as-of "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --format summary
```

`--commit` publica automaticamente o lote derivado dos dados. Use um diretório de investigação separado, fora da árvore do repositório. A CLI recusa qualquer caminho dentro do repositório. A publicação é a única operação de escrita do motor. O registo conserva o esquema e o SHA da definição, a origem, o suporte observado, a alternativa, o motivo da decisão, o instante e a impressão do snapshot conhecido. `registry` dá o digest de cada lote. Guarde um checkpoint fora do registo e use `--expect-head` para detetar a sua perda. Por omissão, o leitor aceita lotes posteriores que sejam extensões válidas da cadeia. A opção `--wave N` permite avaliar uma família de publicação em separado.

O digest de cada lote pode receber um recibo RFC 3161 no domínio `temporal-discovery-publication-v1`. O verificador offline já existente procura `<digest>.tsr` em `--receipts` e verifica o lote integral. Para preparar um pedido de timestamp ao operador já autorizado, use `openssl ts -query -digest <digest> -sha256 -cert -out batch.tsq`. Um recibo independente válido tem de ser posterior à publicação local. Um relógio local ou um commit Git isolado só demonstra a existência no registo local. As propostas ficam com zero jogos prospetivos até à prova independente. O motor envia zero pedidos de timestamp e aceita um recibo por lote inteiro. O recibo prova os bytes e o instante, e a retenção pública de checkpoints permite verificar que o registo apresentado é o mesmo. O ficheiro de protocolo proposto na PR A conserva o seu estatuto original.

## Hipóteses e pesquisa

O DSL v1 descreve uma sequência com dois a oito eventos distintos e um limite de tempo total. Cada posição é uma conjunção de predicados tipados. Uma relação pode ligar quaisquer duas posições em ordem crescente, incluindo eventos não adjacentes. A última posição fixa a oportunidade que reserva o jogo. A prova precisa de emissão estritamente crescente e de todos os recibos disponíveis no instante dessa última posição.

```json
{
  "version": 1,
  "within_seconds": 7200,
  "steps": [
    [{"field": "phase", "op": "eq", "value": "pre"}],
    [{"field": "source", "op": "eq", "value": "Polymarket"}],
    [{"field": "prior_sources", "op": "ge", "value": 2}]
  ],
  "relations": [
    {"from": 0, "to": 2, "field": "same_direction", "op": "eq", "value": true},
    {"from": 1, "to": 2, "field": "odds_delta_bp", "op": "lt", "value": -200}
  ]
}
```

O catálogo em `dsl.py` define o tipo e a função de leitura de cada primitiva. Registar outra primitiva exige uma função pura e uma revisão do esquema se mudar a semântica existente. Há direção, linha, fonte, fase, mercado nativo, período, estado e minuto do jogo, volume, quota, confirmação entre fontes, contagem do prefixo, intervalo de emissão ou leitura, magnitude e velocidade de odds. Uma alteração de preço requer duas odds pontuais do mesmo jogo, fonte, mercado nativo, período e seleção. As igualdades com dados ausentes ficam indeterminadas. Substituições de fonte, mudanças de preço e variações de fase podem combinar-se num único caminho. O modelo pode formular relações novas entre primitivas sem executar código livre.

O gerador determinístico usa uma amostra de âncoras espalhada pelo intervalo observado. Projeta subsequências com eventos contíguos e intervenientes em diferentes campos. Aprende cortes numéricos na amostra de descoberta, cruza trajetórias relacionais e contexto da âncora e alterna famílias e comprimentos. O primeiro filtro usa jogos distintos. O segundo reaplica a regra inteira, reserva a primeira oportunidade elegível por jogo e procura comparadores. O seguimento exige pelo menos cinco jogos em cada braço na descoberta. O ranking depende de suporte e emparelhamentos, sem seleção por lucro. As mesmas oportunidades são descritas por fonte, mercado, fase, tempo, resultado e cobertura. Uma alternativa inverte um antecedente ou relação mantendo a população da âncora, e é testada separadamente. O relatório apresenta perdas dos casos, ganhos dos controlos e casos incompletos.

A deduplicação normaliza ordem, repetições, tipos numéricos equivalentes, condições booleanas e intervalos redundantes. Alterar a substância produz um novo ID e uma nova janela. Regras diferentes que reproduzam a mesma reserva em descoberta ficam sinalizadas como uma repetição empírica desse corte. Uma definição determinável com casos e controlos pode ficar em seguimento prospetivo como `insufficient` quando a descoberta carece de pares. As propostas obtidas por busca incompleta, com um braço demasiado pequeno ou redundância no corte ficam em memória para investigação, sem uma janela de confirmação própria nesse lote.

### Investigadora LLM opcional

```sh
python -m scangoal.discovery prompt --as-of 2026-11-24T08:22:00Z
python -m scangoal.discovery discover --as-of 2026-11-24T08:22:00Z \
  --llm-model /caminho/modelo-local.gguf
```

A integração local usa `llama-completion` do llama.cpp e um GGUF já disponível. `--llm-reply` permite reproduzir uma resposta JSON guardada para auditoria. A investigadora recebe exemplos alinhados ao relógio e um resumo dos motivos de rejeição anteriores. Usa até cinco propostas com explicação e condição de refutação. A seleção, a liquidação, os custos e os estados são decididos pelo motor. JSON malformado, chaves duplicadas, ciclos, primitivas desconhecidas, campos de resultado, alegações de validação, excesso de passos e de orçamento dão erros ou rejeições com hash. Uma resposta rejeitada conserva apenas o digest e o motivo. A descoberta continua deterministicamente quando o modelo ou o executável local estão indisponíveis. A execução local usa uma tentativa, dois threads, 45 segundos, 6.144 tokens de contexto e até 1.536 tokens de geração. A saída é restringida por `--json-schema` ao envelope de propostas. Explicações com alegações de retorno, certeza, validação ou instruções de aposta são rejeitadas, tal como propostas repetidas. A integração ainda não foi exercitada com inferência real. O prompt tem até 4.300 bytes e a resposta aceita tem até 12.000 bytes. O modelo tem um medidor próprio com no máximo um quarto do trabalho de replay e até 50.000 operações. O trabalho determinístico não é reduzido por uma resposta do modelo. A integração é local e independe da política Free existente.

## Avaliação prospetiva

A janela começa na primeira meia-noite UTC após o maior instante entre publicação testemunhada e corte de descoberta, com o corte da PR A como mínimo. Dura 90 dias. Tanto a emissão como a primeira disponibilidade e o início comprovado do jogo têm de pertencer à janela. São exigidos recibos de página concluída. Jogos já visíveis na descoberta ficam excluídos. O primeiro alerta com os predicados da âncora reserva o jogo mesmo sem preço utilizável. Uma exposição precisa de um testemunho completo. Um controlo precisa de uma busca concluída com resultado negativo. Ausência de prova, conflito de odds ou procura truncada não criam controlos.

O emparelhamento exige os mesmos dia UTC do jogo, fonte, fase, mercado nativo, período, seleção, linha, faixa de preço e densidade do prefixo. Em pré-jogo exige a mesma faixa de tempo até ao início. Em LIVE exige a mesma faixa de minuto e o mesmo marcador. É escolhido o último jogo de braço oposto já reservado e ainda livre, dentro de 15 minutos estritos. A margem operacional da PR A assegura que essa diferença cabe antes do fecho da oportunidade anterior. Cada jogo participa uma vez por hipótese. O relógio da primeira reserva e os hashes dos casos e comparadores são independentes da leitura de FT.

A avaliação fixa a liquidação até 72 horas após cada decisão. Reporta taxa de acerto, perdas, devoluções e meias liquidações, odds ausentes, FT em falta, conflitos e mercados cuja liquidação exige outros dados. O ROI teórico usa a odd pontual anunciada, com unidade fixa, e a sensibilidade usa uma perda de 2% do ganho positivo mais 0,02 unidades de custo. A execução real continua por demonstrar. Os contrastes usam o par e os blocos de dia de início. A estabilidade é mostrada nas duas metades da janela. Só depois do fim e dos 72 h de seguimento é mostrado o teste bilateral de sinal dos dias, quando há pelo menos 50 pares com preço e resultado, 20 dias informativos e 80% de cobertura de liquidação e preço por braço emparelhado. Holm considera todos os candidatos da família, inclusive os insuficientes, e cada lote `n` recebe nível `0,05/(n(n+1))`. Simetria sob a hipótese nula e dependência entre dias são limitações da análise observacional. A rotina atualiza evidência futura sem substituir regras. Enquanto a janela está aberta, o estado é `insufficient · forward_window_open`. Depois do fim e das 72 h, o protocolo v2 decide: `experimental` só se o p ajustado por Holm for ≤ ao α da vaga e a diferença diária média for positiva. Caso contrário, `rejected` com `no_forward_support_at_close`, `no_forward_difference_at_close` ou `adverse_forward_difference`. Um lote com protocolo desconhecido é reportado sem bloquear os restantes. Se a impressão `interpreter_sha256` mudar desde o registo, o veredicto fica retido. Com os dados atuais, o emparelhamento estrito quase nunca atinge os 50 pares. A análise e a proposta de uma versão prospetiva separada estão na auditoria.

## Auditoria de implementação

O snapshot de referência é a `main` em `2b1f593793a02e5c317c1a7770e9b43b3d1eb90c`, com corte em **2026-11-24T08:22:00Z**. A mesma entrada e o mesmo corte produzem JSON idêntico. O digest de snapshot contém a projeção visível e vem acompanhado dos hashes dos ficheiros de entrada.

| Verificação | Resultado |
| --- | --- |
| Descoberta real, sem LLM | 256 de 1.816 âncoras amostradas, 17.766 padrões observados, 3.003 definições projetadas e 36 propostas de dois a cinco eventos |
| Suporte | 36 insuficientes na descoberta. 21 com ambos os braços e elegíveis para seguimento mediante publicação testemunhada. Zero pares estritos e zero jogos confirmatórios |
| Operações e tempo | 163.603 operações de replay, 4,82 s de ponta a ponta e 81.364 KiB de pico no ensaio final local. Os tempos e memória variam com CPU e dados |
| Reexecução | Duas execuções com saída idêntica em bytes. A auditoria normal da PR A também produziu bytes iguais antes e depois da projeção adicionada |
| Caso para inspeção | `td1:24864709c1865738f95e2ae9c2bbb5302587b76ebab5f63e518da3f00c00d4eb` liga cinco alertas Polymarket no mesmo mercado nativo. Nove casos, 63 controlos candidatos e zero emparelhamentos. A âncora 28105 é um contraexemplo com perda |

As regressões adversariais cobrem leituras tardias, empate na emissão, cotações incompatíveis ou contraditórias, limite de busca, oito alertas distintos, um jogo reservado uma vez, comparadores sem suporte, edições de FT, devolução asiática sem preço pontual, exclusão de jogo antigo e recibo legado da janela futura, rejeições LLM e publicações concorrentes. Um replay sintético viu o mesmo par antes do FT e depois da liquidação. Os hashes das reservas e do emparelhamento coincidiram. A prova criptográfica foi exercitada com uma TSA efémera e um timestamp RFC 3161 genuíno no teste. Foram verificadas alterações no digest, prova anterior à publicação, ausência de recibo e preservação do checkpoint. O ambiente de teste não tinha um modelo GGUF com llama.cpp instalado. O caminho LLM foi exercitado com respostas controladas e falhas de subprocesso.

A descoberta está limitada por defeito a 256 âncoras, 60.000 emissões de padrões, 36 propostas determinísticas, 350.000 operações de replay e 4.096 expansões por âncora. A busca de propostas pode reduzir `--emissions` e `--work`. Na avaliação, o teto de trabalho é 400.000 operações e até 12 lotes por execução. Um limite atingido adia o trabalho e os comparadores truncados ficam indeterminados. A projeção inicial ainda lê o diário e os fornecedores de resultados. O custo depende do volume do snapshot e não está incluído nos contadores de busca. Um teste com 320 alertas num jogo verifica que a projeção não expande todos os pares.

Para reproduzir com o histórico congelado e verificar o diff:

```sh
mkdir -p /tmp/goal-roi-discovery-input
git archive 2b1f593793a02e5c317c1a7770e9b43b3d1eb90c \
  data/messages.jsonl data/results_hl.json data/results_bsd.json data/details_hl.json \
  | tar -x -C /tmp/goal-roi-discovery-input
python -m scangoal.discovery discover --data-dir /tmp/goal-roi-discovery-input/data \
  --as-of 2026-11-24T08:22:00Z > /tmp/temporal-discovery.json
python -m unittest discover -s tests -p test_autonomous_discovery.py -v
python -m unittest discover -s tests -v
node --test ops/continuity/watchdog.test.mjs
git diff --name-status 2b1f593793a02e5c317c1a7770e9b43b3d1eb90c..HEAD
```

A cobertura depende das cotações e resultados que já chegam do collector. O volume anunciado é um campo do alerta. As hipóteses são associações observacionais sujeitas a confundimento, preços intermitentes e suporte desigual. A integração de modelos e timestamp é explícita e local. O estudo não usa 1xBet e nenhum ficheiro de históricos, regras congeladas, certificação, alertas, configuração ou workflow de produção faz parte do circuito de escrita.
