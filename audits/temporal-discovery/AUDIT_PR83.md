# Auditoria independente pré-merge · PR #83

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

HEAD auditado: `117145000ba7818cc47db9d46f7634fe81850c6c`. Snapshot congelado: `main` em `2b1f593793a02e5c317c1a7770e9b43b3d1eb90c`, corte `2026-11-24T08:22:00Z`. Todas as experiências são offline. Nenhuma aposta, alerta, estratégia, workflow, variável ou ficheiro de `data/` foi alterado.

## 1. Decisão

| Âmbito | Decisão |
| --- | --- |
| Merge do código experimental, inativo e offline, com as correções desta auditoria | **GO condicional**. O módulo é isolado: a auditoria normal da PR #81 dá o mesmo hash que na `main` (`963c1bf7…`) e a branch não toca em workflows, `data/`, `ops/` nem `scripts/`. Condições: a PR continua marcada como experimental e nenhum processo agendado chama `scangoal.discovery`. |
| Afirmar que o sistema "aprende autonomamente em produção" | **NO-GO**. Não há execução agendada, testemunho automático, persistência de veredictos nem inferência LLM real. |
| Ativar o protocolo prospetivo v1 tal como está | **NO-GO**. Com os dados atuais, o emparelhamento estrito não atinge os 50 pares exigidos. Mesmo regras verdadeiras plantadas acabam `rejected · no_forward_support_at_close`. Ver §4. |

## 2. Estado real da autonomia

| Capacidade | Estado | Evidência |
| --- | --- | --- |
| Descoberta determinística de padrões | **Operacional offline** e com cobertura limitada | CLI reproduzida byte a byte: `b479e6d3…` no HEAD auditado e `6ffa61ba…` depois das correções. Só mudam `protocol_sha256` e `interpreter_sha256`. No banco sintético recupera 0 ou 1 de 6 padrões verdadeiros (§5). |
| Hipóteses inéditas geradas pela LLM | **Só simulada** | Nunca correu com um modelo real. O proxy bloqueia o HuggingFace e não há GGUF nem binário do llama.cpp. Todos os testes usam respostas fixas ou subprocessos simulados. |
| Registo imutável | **Operacional localmente** | Cadeia de hashes, `flock` e publicação atómica. A truncagem do último lote só é detetada com um checkpoint externo, como `--expect-head`. Não existe publicação pública. |
| Avaliação com resultados novos | **Implementada e exercitada só com dados sintéticos** | Não existe nenhum lote testemunhado. Antes desta auditoria, nenhuma hipótese podia ser rejeitada pela evidência (P1-1). |
| Funcionamento contínuo sem intervenção | **Não implementado** | Não há agendador, pedido TSA automático, diário de veredictos nem relatórios periódicos. O plano está no §6. |

## 3. Defeitos e correções

| ID | Sev. | Defeito | Correção nesta branch | Teste |
| --- | --- | --- | --- | --- |
| P1-1 | P1 | `evaluate` nunca atribuía `rejected`. Qualquer hipótese com p calculado ficava `experimental`, mesmo com p = 0,9. O Holm e o α da vaga eram calculados mas não decidiam nada. | Adicionado um veredicto terminal após o fecho da janela e das 72 h de liquidação: `experimental · forward_difference_survived_holm` ou `rejected` por `no_forward_support_at_close`, `no_forward_difference_at_close` ou `adverse_forward_difference`. Com a janela aberta, o estado fica `insufficient · forward_window_open`. O protocolo passa à v2 com a regra declarada. A v1 nunca foi testemunhada. | `test_closed_window_verdicts…`, `test_open_window_never_has_a_verdict` |
| P1-2 | P1 | Um único lote com protocolo desconhecido lançava `LedgerError` e bloqueava a avaliação de todas as vagas. Qualquer revisão futura do protocolo tornaria o histórico inavaliável. | Cada lote afetado é reportado como `unsupported_forward_protocol` sem prova nem veredicto, e as restantes famílias continuam a ser avaliadas. | `test_unknown_protocol_is_reported…` |
| P1-3 | P1 | O filtro de alegações da LLM aceitava textos como "+12% ROI… bet now", "profitable edge", "sure win", "aposte" ou "lucrativo e comprovado". Esses textos ficavam no registo e no painel. | O filtro passa a cobrir retorno, certeza, validação e instruções de aposta em PT e EN. Os nomes de fontes, como Stake, continuam permitidos. | `test_claims_of_return…` |
| P1-4 | P1 | A geração local não tinha gramática. Respostas reais com cercas Markdown ou com o marcador `[end of text]` seriam todas rejeitadas, por isso a integração era inviável na prática. | Passa a ser usado `--json-schema` do llama.cpp para fixar o envelope, e o marcador final é removido. As cercas Markdown continuam inválidas. O validador DSL continua a ser a única autoridade sobre o conteúdo. | `test_local_generation_is_grammar_constrained`, `test_duplicates_fences_and_end_marker` |
| P1-5 | P1 (metodológico, sem alteração de código) | O emparelhamento v1 não tem potência. Ver §4. | Não houve relaxamento silencioso. Foram feitas análises de sensibilidade e há uma proposta de v2 para registo prospetivo separado. | `pairing_sensitivity.py`, `adversarial_bench.py --oracle-sensitivity` e `--v2-power` |
| P2-1 | P2 | A semântica do intérprete não tinha impressão digital. Uma alteração de um getter mantinha o mesmo `td1:` com um significado diferente. | `interpreter_sha256` (bytes de `dsl.py` e `replay.py`) passa a ficar no lote. A avaliação marca `changed_since_registration` e retém o veredicto. | `test_interpreter_change_withholds_any_verdict` |
| P2-2 | P2 | O orçamento determinístico encolhia quando a LLM fazia propostas, por isso uma resposta não confiável podia mudar a descoberta determinística. | Cada origem passa a ter um medidor separado. O teto total é `work + min(work/4, 50 000)`. | `test_model_reply_cannot_change_deterministic_discovery` |
| P2-3 | P2 | `--commit` aceitava um registo dentro da árvore do repositório, por exemplo em `audits/…`. O teste criou mesmo um lote nesse local no código original. | Qualquer caminho dentro do repositório passa a ser recusado. | `test_cli_refuses_a_registry_inside_the_working_tree` |
| P2-4 | P2 | Propostas LLM duplicadas na mesma resposta contavam duas vezes. | Os duplicados são rejeitados com `duplicate_model_proposal`. | `test_duplicates_fences_and_end_marker` |
| P2-5 | P2 (documentado) | A reserva da primeira âncora elegível por jogo impõe um teto à recolha de cadeias cuja âncora também satisfaz passos anteriores. | É uma escolha correta contra look-ahead. O efeito medido é uma recolha do oráculo de 0,13 a 0,49 nos padrões `oscillation_8` e `price_up_then_down_3`. | `adversarial_bench.py` |
| P2-6 | P2 (documentado) | A gramática de projeção usa um campo por passo, por isso não gera conjunções como fonte∧direção. O ranking por suporte com teto de 36 e a amostra de 256 âncoras perdem padrões gerados. | Fica para a DSL v2 e para o plano de pesquisa, sem alterar a saída congelada. | §5 |

Ataques à memória tentados, sem defeito novo: FT conhecido antes da decisão (invariante verificado em todas as âncoras reais), edição retroativa de um lote anterior (a cadeia quebra), substituição de comparadores (os hashes de reserva e emparelhamento são estáveis), duplicação de jogos (`duplicate_body`, uma decisão por jogo), reutilização da amostra histórica (exclusão por `discovery_cutoff`), falha a meio da publicação (o lote anterior fica intacto e não há lote parcial), processos concorrentes (um único lote com `flock`), prova anterior à publicação local ou com digest errado (recusada) e dados tardios depois do fecho (FT corrigido após o prazo e jogos novos não alteram evidência nem veredicto). As rejeições LLM e as hipóteses `insufficient` ficam no registo.

Limitações que se mantêm: a truncagem do último lote só é detetada com um checkpoint externo. Um operador também pode testemunhar vários registos alternativos e mostrar apenas um. As duas coisas só se resolvem com publicação pública append-only (§6).

## 4. Porque há zero pares estritos

Reproduzir com `PYTHONPATH=. python audits/temporal-discovery/pairing_sensitivity.py <snapshot>/data`.

1. **O estrato é quase único por jogo.** O v1 exige igualdade em 11 componentes: dia do início, fonte, fase, mercado nativo, período, linha, seleção, contexto (faixa de tempo até ao início, ou minuto e marcador exato), faixa de odd, preço pontual e densidade do prefixo. Entre as primeiras âncoras de 770 jogos há 635 estratos distintos e 559 são singulares. Nos 21 candidatos seguidos, 1.049 dos 1.075 casos têm um estrato próprio.
2. **Mesmo sem limite de tempo quase não há comparáveis.** Nos 21 candidatos, só 11 combinações caso–controlo partilham o estrato completo em qualquer instante. Dentro de 15 minutos há **0**, dentro de 60 minutos há 2 e dentro de 6 horas há 10. Os componentes que mais falham face ao controlo mais próximo são o dia (743), a seleção (602), o contexto (598), a linha (438) e a faixa de odd (395).
3. **A janela de 15 minutos não é a causa principal, mas agrava.** O limite superior da oferta, contando pares disjuntos entre as primeiras âncoras e ignorando a divisão caso/controlo, é este (11,8 dias):

| Estrato | 15 min (v1) | 60 min | 6 h | qualquer |
| --- | ---: | ---: | ---: | ---: |
| v1 completo | 21 | 40 | 81 | 98 |
| sem densidade nem faixa de odd | 23 | 48 | 105 | 128 |
| contrato e dia | 49 | 118 | 211 | 255 |
| só contrato | 49 | 120 | 222 | 343 |

Projetado para 90 dias, isto dá cerca de 160 pares v1 para todos os jogos. Depois de filtrar pela regra e de exigir braços opostos, ficam perto de zero por hipótese, contra o mínimo de 50 pares com preço em 20 dias informativos.

4. **Confirmação sintética.** O ciclo completo (descoberta → publicação testemunhada sintética → 90 dias futuros → veredicto) foi corrido com as 7 regras do oráculo registadas diretamente. Há 6 padrões verdadeiros, com acerto de 78% contra 48%. O máximo foi 7 pares em 15 minutos e as 7 regras acabaram `rejected · no_forward_support_at_close`. Com uma janela de 24 h, o máximo sobe para 91 pares, mas nenhuma regra verdadeira sobrevive, porque o estrato fino dispersa os pares por poucos dias (`--oracle-sensitivity`).
5. **Uma alternativa viável, que não foi aplicada.** Ver `--v2-power`. Com contrastes por blocos dia × contrato sem emparelhamento temporal, o desfecho é o seguinte:

| Regra do oráculo | braço caso vs. zero (p) | dia × contrato, caso − controlo (p) |
| --- | ---: | ---: |
| nonadjacent_delay_4 | 9e-15 | 1,4e-8 |
| multisource_reversal_3 | 1,3e-12 | 0,029 |
| delayed_confirmation_3 | 5,8e-5 | 0,015 |
| live_context_x_price_2 | 1e-13 | sem controlos |
| oscillation_8 | 9,6e-8 | 0,23 |
| price_up_then_down_3 | 0,061 | 0,052 |
| **false_promising_2 (falso)** | **0,61** | **0,58** |

**Proposta v2, para registar antes de qualquer ativação e sem tocar na v1.** O contraste principal é dia × contrato (fonte, fase, mercado, período, linha, seleção), sem a janela de 15 minutos. Cada decisão continua causal no seu próprio relógio. O teste de sinal é feito sobre os dias, com Holm e alfa por vaga. O contraste secundário compara o braço caso com zero após o haircut de 2% e 0,02. A faixa de odd e o contexto ficam como covariáveis reportadas. A v2 exige um novo `protocol_sha256` e publicação testemunhada antes do início da janela. Os lotes v1 continuam a ser avaliados com a regra v1.

## 5. Capacidade de descoberta medida

O banco `adversarial_bench.py` corre 720 jogos sintéticos (30 dias), com ruído de 1 a 5 alertas por jogo de Polymarket, Stake e ScanGoal. Os padrões plantados estão só no gerador. O motor recebe a auditoria sintética sem alterações.

| Padrão plantado (eventos) | Característica adversarial | Oráculo DSL (precisão/recolha) | Melhor das 36 propostas (Jaccard) sementes 83/84/85 | Onde se perde |
| --- | --- | --- | --- | --- |
| live_context_x_price_2 | contexto do jogo × preço, LIVE | 1,0/1,0 | **1,0 / 1,0** / 0,09 | descoberto em 2 de 3 sementes; na semente 85 perde-se pela amostra de 256 âncoras |
| nonadjacent_delay_4 | relação não adjacente, atraso >90 min, distratores intermédios | 1,0/0,81 | 0,14 / 0,15 / 0,22 | gerado (0,88), perdido no ranking/teto de 36 ou na amostragem |
| multisource_reversal_3 | três fontes e mudança de direção | 0,76/0,92 | 0,17 / 0,20 / 0,14 | a gramática não gera fonte∧direção |
| delayed_confirmation_3 | confirmação atrasada de 2 a 3 h | 1,0/0,53 | 0,19 / 0,31 / 0,13 | gramática ou amostragem |
| oscillation_8 | 8 eventos com reversões alternadas | 1,0/0,35 | 0,48 / 0,42 / 0,49 | teto da reserva da primeira âncora |
| price_up_then_down_3 | odd sobe e depois desce na mesma seleção | 0,92/0,15 | 0,26 / 0,23 / 0,23 | teto da reserva da primeira âncora |
| false_promising_2 (falso) | ganha só no período de descoberta | — | 0,17 / 0,09 / 0,16 | não seguido; no `--v2-power` dá p = 0,61 |

Resumo: entre 0 e 1 de 6 padrões verdadeiros são recuperados pelas 36 propostas, e só 7 a 9 das 36 propostas se relacionam com algum padrão plantado (Jaccard ≥ 0,3). A descoberta é uma enumeração de padrões frequentes e o ranking nunca usa o desfecho. Isto evita sobreajuste, mas significa que a relevância preditiva fica inteiramente para a fase prospetiva. No ciclo completo (`--lifecycle`), as 19 hipóteses seguidas foram corretamente rejeitadas, sem falsos positivos, mas o motor também nunca confirmaria uma verdadeira (§4).

## 6. Plano para autonomia operacional futura

Este plano é separado desta PR. Não liga nada ao collector e não altera workflows.

1. **Repositório público próprio** (por exemplo `goal-roi-research`), com um workflow `schedule` diário que corre no GitHub Actions. Os minutos são gratuitos em repositórios públicos. O workflow faz checkout só de leitura da `main` do goal-roi num commit fixado e copia `data/messages.jsonl` e os ficheiros de resultados. O token só tem permissão de escrita no repositório de investigação.
2. **Descobrir e publicar.** `discover --commit` grava o lote num diretório `ledger/` desse repositório. O mesmo job pede um timestamp RFC 3161 a duas TSAs gratuitas independentes, por exemplo FreeTSA e DigiCert, com `openssl ts -query … | curl --data-binary`. Junta também um recibo OpenTimestamps, que é gratuito e ancorado em Bitcoin. Depois faz commit do lote e dos `.tsr` e `.ots`, e publica o checkpoint (o head da cadeia) numa *release* ou *issue* do repositório. A visibilidade pública impede registos alternativos escondidos e a truncagem silenciosa.
3. **Verificação independente.** Um segundo job, ou qualquer terceiro, verifica a cadeia, os recibos contra raízes TSA fixadas e o `--expect-head` do checkpoint anterior publicado. Nunca usa as credenciais do primeiro job.
4. **Avaliar e lembrar.** Um job semanal corre `evaluate` e grava um diário append-only de veredictos, testemunhado da mesma forma, com estados `rejected` e `experimental` e respetivas razões. A memória da LLM passa a incluir esses veredictos e as definições já testadas.
5. **Relatórios.** O job gera Markdown no próprio repositório ou numa página do GitHub Pages. Não envia Telegram, alertas nem certificação. Fica sempre o veredicto padrão.
6. **LLM opcional e gratuita.** O mesmo job pode usar o binário do llama.cpp e um GGUF pequeno de cerca de 1 GB em `actions/cache`, só em CPU, com `--json-schema` e um timeout próprio desse job. Se falhar, a descoberta continua determinística. A LLM nunca vê FT, não escreve código nem regista nada diretamente.
7. **Pré-condições.** Registar o protocolo v2 (§4), aceitar a DSL v2 com conjunções por passo e pesquisa estratificada sem teto fixo de 36, e manter o ensaio sintético como teste de aceitação. Por exemplo, recolher pelo menos 4 de 6 padrões e não confirmar `false_promising`.

O utilizador não precisa de introduzir odds, resultados, jogos ou hipóteses. Tudo vem do diário já recolhido pelo collector. A 1xBet não é usada.

## Reprodução

```sh
mkdir -p /tmp/in && git archive 2b1f593793a02e5c317c1a7770e9b43b3d1eb90c data/messages.jsonl \
  data/results_hl.json data/results_bsd.json data/details_hl.json | tar -x -C /tmp/in
python -m scangoal.discovery discover --data-dir /tmp/in/data --as-of 2026-11-24T08:22:00Z > /tmp/d.json
PYTHONPATH=. python audits/temporal-discovery/pairing_sensitivity.py /tmp/in/data > /tmp/pairing.json
PYTHONPATH=. python audits/temporal-discovery/adversarial_bench.py 83 > /tmp/bench.json   # also 84, 85
PYTHONPATH=. python audits/temporal-discovery/adversarial_bench.py --lifecycle
PYTHONPATH=. python audits/temporal-discovery/adversarial_bench.py --oracle-sensitivity
PYTHONPATH=. python audits/temporal-discovery/adversarial_bench.py --v2-power
python -m unittest discover -s tests -v
node --test ops/continuity/watchdog.test.mjs
```

Resultados locais: 1.115 testes Python OK (1.097 existentes e 18 novos em `tests/test_discovery_audit.py`), watchdog Node 12/12, descoberta real em 1,9 s e auditoria da PR #81 com o mesmo hash que na `main`. Contra o código original, 13 dos 18 testes novos falham. Sete detetam defeitos reais: veredicto, protocolo, alegações, gramática, duplicados, registo dentro do repositório e intérprete. Os outros seis falham só porque o atributo novo `INTERPRETER_SHA256` não existe, e servem como regressões.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
