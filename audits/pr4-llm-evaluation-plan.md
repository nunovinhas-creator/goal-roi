# PR 4/5 — Aprendizagem e avaliação da LLM (plano técnico)

Estado: plano aprovado a 15/11/2026 com as decisões da secção 9, que prevalecem sobre o texto anterior em caso de conflito.
O protocolo vinculativo é `audits/pr4-llm-protocol.md` (commit próprio).
Base: `main` depois do merge da PR 3 (59bff9a) e das recolhas #561/#562. Certificação: `not_activated` (continua assim).

## 0. Âmbito

Objetivo: medir, com evidência exclusivamente posterior ao registo, se as hipóteses propostas pelo modelo de linguagem
(`source = llm`) acrescentam alguma coisa ao gerador automático (`source = gerador`), sem fuga de informação futura,
sem contar duplicados e sem tratar taxas de acerto acima de 50 % como prova.

Fora de âmbito (não muda nesta PR):
- regras de estado (`STATUS_RULES = chronological_eprocess_v3`), limiares, histerese, e-processo e certificação;
- Telegram (PR 5), workflows, secrets, `data/` de produção;
- o modelo chamado, a frequência e o orçamento (continua no plano gratuito: custo zero).

## 1. Diagnóstico (estado atual, com referências)

Fotografia de `data/` neste ramo: 6095 hipóteses (6044 `gerador`, 51 `llm`: 32 refinar, 11 novas, 5 evitar, 3 sem modo),
todas as `llm` registadas entre 12/11 e 15/11/2026; 4 promissoras, nenhuma forte/confirmada.

| # | Constatação | Onde | Consequência |
|---|---|---|---|
| D1 | O "balanço das propostas" conta como "no sentido esperado" qualquer hipótese com `prob_above > 0.5`. | `brain.track_record` (`brain.py:842`) | Um acerto acima do necessário com probabilidade 51 % conta como êxito; o mesmo critério é enviado ao modelo como "confirmaram-se". |
| D2 | Comparação desigual: o gerador é avaliado em **todos** os dados (incluindo anteriores ao registo, dentro da amostra), a LLM só depois de `date_utc > born`. | `brain.evaluate` (`brain.py:514`) | `gerador 1145/2606` contra `llm 6/14` não é uma comparação de utilidade: períodos e regras diferentes. |
| D3 | O balanço usa `summarize(match)` (sinais com `date_utc` posterior), não a evidência prospetiva da PR 3 (emitido **e lido** depois do registo, decidível, resultado com hora provada). | `brain.track_record`, `brain.prospective_summary` | Pode incluir sinais lidos tarde ou com resultado de hora não provada. |
| D4 | A LLM só é rejeitada por identificador exatamente igual. Não há a regra do gerador "apanha os mesmos sinais que uma combinação mais simples". | `llm.accept` (`llm.py:129`); compare `brain.update_registry` (`brain.py:138`) | Duplicados extensionais entram. Mesmo no gerador, 3 das 4 promissoras atuais são a mesma condição ("diferença de 2 ou mais golos") com condições quase redundantes: 47, 42 e 41 jogos encaixados (Jaccard 0,87–0,89). |
| D5 | O modo `refinar` escolhe as melhores hipóteses (pelo passado) e acrescenta uma condição. | `llm.build_prompt` | A evidência da refinada sobrepõe-se à da hipótese-mãe; não é evidência incremental. |
| D6 | O modo `evitar` conta como êxito ficar abaixo do necessário. | `brain.track_record` | Com a taxa-base negativa da maioria dos tipos, "acertar abaixo" é quase garantido; não mede nada. |
| D7 | `born_utc` é escrito pelo próprio processo e não é confrontado com a publicação no git. | `llm.run`, `chronology.born` | Uma data recuada (edição manual, rebase) faria contar evidência anterior à existência real da hipótese. |
| D8 | O README e o docstring anunciam "30 por dia, 300 no total"; o código não aplica esses limites (só 5 por chamada e o orçamento de neurónios). | `llm.py:21`, README | Documentação incorreta; decisão pedida em §8. |
| D9 | Não há proveniência: não se guarda o que o modelo viu nem o que respondeu. | `llm.run` | Impossível auditar a posteriori se uma proposta foi induzida por informação indevida. |

## 2. Desenho

### 2.1 Equivalência e duplicados — `scangoal/equivalence.py` (novo; partilhado com a PR 5)

Puro, sem rede, **nunca lê resultados** (só pertença de sinais/jogos a hipóteses).
- **Duplicado sintático:** mesmo `hyp_id` canónico (condições já ordenadas) — já rejeitado.
- **Duplicado extensional:** no instante de registo, o conjunto de sinais elegíveis que a hipótese apanha é igual ao de uma
  hipótese existente, ou igual ao de uma sub-combinação sua (mesma regra do gerador, estendida a 4 condições).
- **Estatisticamente equivalente:** conjuntos de jogos com Jaccard ≥ 0,85 e ambos com ≥ 8 jogos (com 0,90 as três promissoras atuais não seriam agrupadas) (parâmetros fixados no
  protocolo, §2.5). Agrupamento por união-disjunta com ordem determinística; representante = registo mais antigo, depois
  identificador lexicográfico.
- O agrupamento usado na **avaliação** é calculado com os dados conhecidos no registo de cada hipótese (as-of), para nunca
  depender de dados futuros. O agrupamento usado na **apresentação** (PR 5) pode usar os dados atuais.

Efeito em produção (decisão pedida em §8): `llm.accept` passa a rejeitar duplicados extensionais, com contador próprio
`rejected_equivalent` em `llm_state.json` (campo novo; os existentes mantêm-se). Só reduz entradas; o registo continua só a crescer.

### 2.2 Avaliação incremental — `scangoal/llm_eval.py` (novo, puro, sem rede)

**Nascimento efetivo** `born_eff(h) = max(born_utc, primeira publicação no git + 0)`. Com histórico completo, a primeira
publicação é o commit mais antigo que contém o identificador em `data/hypotheses.json`; sem histórico (checkout raso do
workflow), `born_eff = born_utc` e a linha leva `birth_proof = "unverified"`. A avaliação científica completa só corre no
script de auditoria (§2.4), com histórico completo. Não se altera o workflow.

**Evidência admitida:** só jogos da categoria `prospective` de `brain.prospective_summary` em relação a `born_eff`
(a definição única da PR 3), e uma aposta por jogo (a primeira decidida, como no e-processo). Tudo o resto é contado por
categoria e excluído.

**Métrica primária (fixada antes de ver dados):** lucro por jogo independente à odd anunciada −2 %.
- Nunca se usa a taxa de acerto, nem `prob_above > 0.5`, como critério de êxito. A taxa de acerto só aparece ao lado da
  taxa necessária, como descrição.

**Termos de comparação no mesmo intervalo de tempo** (elimina o efeito do período):
1. **Base da família:** todos os jogos prospetivos da mesma família no mesmo intervalo `[born_eff, as_of]`.
2. **Controlos do gerador emparelhados:** para cada hipótese LLM, K = 20 hipóteses do gerador já registadas antes de
   `born_eff`, da mesma família, com o mesmo número de condições (3 para as de 4) e apoio no registo entre ½ e 2× o da LLM.
   Escolha pseudoaleatória com semente `sha256(hid)`; a escolha **não lê resultados** (teste T12). Os controlos são
   avaliados no intervalo da hipótese LLM, não no seu.
3. **Hipótese-mãe** (só `refinar`): a sub-combinação existente com menos uma condição; mede-se a diferença sobre os jogos
   em que só a mãe aposta.

**Agregação** (uma unidade por grupo de equivalência, nunca por hipótese):
- Primária, ao nível do portefólio: estratégia LLM = uma aposta por jogo quando algum representante LLM já registado na
  hora da decisão encaixa; estratégia controlo = idem com os controlos. Diferença do lucro médio por jogo, com intervalo
  bootstrap por blocos de dia (semente fixa, 10 000 réplicas) e teste unilateral H0: diferença ≤ 0, α = 0,05.
- Secundárias (descritivas): por hipótese, excesso sobre a base da família e sobre os controlos; `evitar` medido como
  défice face à base da família (não face ao "necessário").

**Veredicto** (texto fixo, nunca "vantagem" nem "validada"):
- `evidência insuficiente` se houver menos de 10 grupos com ≥ 10 jogos prospetivos ou menos de 200 jogos distintos;
- `sem utilidade incremental demonstrada` se o teste não rejeitar H0;
- `utilidade incremental sugerida (exploratória)` se rejeitar. Mesmo assim não muda estados, não certifica e não alimenta o Telegram.

Expectativa honesta: com 3 dias de dados e 51 hipóteses, o resultado esperado é `evidência insuficiente`. O valor da PR está
em fixar o método antes de haver dados, não em produzir um sim.

### 2.3 Fuga de informação futura — medidas

- Invariância por truncagem: a avaliação em `as_of = T` tem de dar o mesmo resultado com ou sem os dados posteriores a T (T1).
- `born_eff` pelo git (D7); `birth_proof` visível em cada linha.
- Vocabulário enviado ao modelo: só dimensões conhecidas no primeiro sinal (exclui `brain.POST`); propostas com
  dimensões `POST` ou valores inexistentes são rejeitadas (já acontece; passa a ter teste dedicado T17).
- Proveniência aditiva em cada **nova** entrada `llm`: `prompt_sha256`, `response_sha256`, `report_generated_at_utc`,
  `registry_size`. Entradas antigas não são reescritas (registo só cresce).
- Retorno ao modelo (D1/D3): o bloco "Confirmaram-se/Falharam" passa a usar só evidência prospetiva, com lucro por jogo e
  intervalo, e o texto "sem evidência suficiente" quando é o caso. Nunca "confirmou-se".
- O retorno dado ao modelo só influencia propostas futuras, avaliadas depois do seu próprio registo: não contamina a avaliação.

### 2.4 Relatório reprodutível

- `scripts/audit_pr4_llm.py --as-of <UTC> [--commit <sha>]`: só leitura, sem rede; lê os dados de um commit fixo por
  `git show`; escreve `audits/pr4-llm-evaluation.json` (com `sha256` de cada ficheiro de entrada, parâmetros, semente,
  versão do Python) e `audits/pr4-llm-evaluation.md` (relatório científico: pergunta, protocolo, população, exclusões por
  categoria, resultados, limitações, ameaças à validade).
- Reprodutibilidade: duas execuções com os mesmos argumentos produzem ficheiros byte-a-byte iguais (T13); evidência de
  reprodução em Python 3.11 guardada em `audits/pr4-evidence/`.
- No relatório da página: secção compacta `brain.llm_evaluation` (veredicto, contagens, intervalo), calculada sem histórico
  git e por isso marcada `birth_proof: unverified`; limite de tempo de cálculo medido (o relatório é refeito de 2 em 2 minutos
  na leitura rápida).

### 2.5 Protocolo pré-registado

`audits/pr4-llm-protocol.md` fixa métrica, parâmetros (Jaccard 0,85, K = 20, mínimos, α, semente, réplicas) e veredictos.
É commitado **antes** de o avaliador correr sobre dados reais; o SHA do commit fica no relatório.
- Jogos entre `born_eff` e o commit do protocolo: "pós-registo, pré-protocolo" (mostrados à parte).
- Jogos depois do protocolo: "prospetivos pelo protocolo". Só estes sustentam a conclusão final.
Qualquer alteração posterior do protocolo é um protocolo novo, com nova data de início.

## 3. Testes adversariais (todos offline, sem rede, sem segredos)

| ID | Ataque / cenário | Resultado exigido |
|---|---|---|
| T1 | Acrescentar sinais e resultados posteriores a T | Avaliação `as_of=T` idêntica byte a byte |
| T2 | `born_utc` recuado 2 dias face à primeira publicação no git | `born_eff` = publicação; jogos intermédios excluídos |
| T3 | Resultado conhecido antes do registo | Categoria `result_known_before_decision`; excluído |
| T4 | Sinal emitido depois do registo mas lido já com resultado | Excluído |
| T5 | 60 % de acerto à odd 1,50 (necessário 66,7 %) | Lucro negativo; nunca êxito |
| T6 | LLM com 70 % de acerto, controlos e base iguais | Incremento ≈ 0; veredicto não positivo |
| T7 | Mesma proposta com condições permutadas | Rejeitada (sintático) |
| T8 | Proposta = hipótese existente + condição redundante | Rejeitada (extensional) |
| T9 | Inundação: 100 clones equivalentes de uma hipótese com sorte | Um grupo; veredicto inalterado |
| T10 | Refinada de uma mãe forte | Medida contra a mãe; sem crédito herdado |
| T11 | `evitar` num tipo com taxa-base negativa | Só conta défice face à base da família |
| T12 | Trocar todos os resultados | Mesmos controlos escolhidos |
| T13 | Duas execuções do script | Ficheiros idênticos |
| T14 | `socket` bloqueado durante avaliação e relatório | Nenhuma tentativa de rede |
| T15 | Relatório antes/depois da PR com os mesmos dados | `status_list`, `counts`, `validation` iguais (sem mudanças no Telegram) |
| T16 | Execução completa | `validation.state = not_activated`; nenhum `certification_ledger.json` criado |
| T17 | Resposta do modelo hostil: dimensões `POST`, valores inexistentes, texto de injeção, 50 itens, 2 MB | Tudo rejeitado ou limpo; ≤ 5 aceites; nada executado |
| T18 | Amostra pequena | `evidência insuficiente` |
| T19 | Hipótese `llm` sem `born_utc` ou malformado | Nunca avaliada (fail-closed) |
| T20 | Texto do relatório | Nunca contém "vantagem", "validada", "confirmada" aplicados à LLM |

Verificação por mutação (`audits/pr4-evidence/mutation_check.py`, como nas PRs 2 e 3): cada guarda crítica
(`born_eff`, filtro prospetivo, agrupamento, critério de lucro, invariância por truncagem) é mutada e pelo menos um teste tem de falhar.

## 4. Critérios de aceitação

1. Os 20 testes adversariais e a bateria existente passam (`python -m unittest discover -s tests`), em Python 3.11.
2. Todas as mutações são apanhadas.
3. Com os dados de `main` congelados: `status_list`, `counts`, `validation`, `statuses` do Telegram e `sent_total` iguais aos de `main` (T15).
4. O relatório científico é reprodutível byte a byte e regista os SHA das entradas e do protocolo.
5. Nenhum critério de êxito usa taxa de acerto ou `prob_above > 0.5`; `track_record` deixa de o fazer.
6. Nenhuma chamada de rede nova, nenhum segredo novo, nenhuma alteração a `.github/workflows/`.
7. Tempo adicional do `report.build` ≤ 2 s com os dados atuais (medido e registado).
8. README corrigido (limites reais da LLM, nova secção de avaliação).

## 5. Riscos

| Risco | Mitigação |
|---|---|
| Amostra curta leva a ler ruído como sinal | Mínimos fixados; veredicto `evidência insuficiente` por defeito; nunca alimenta estados |
| Controlos mal emparelhados favorecem um lado | Emparelhamento por família, tamanho e apoio no registo; sensibilidade com K = 10 e 40 no relatório (descritiva) |
| Jogos partilhados entre hipóteses quebram a independência | Agregação ao nível do portefólio, um voto por jogo, bootstrap por dia |
| Mudança de `llm.accept` altera a produção | Só rejeita mais; contador próprio; decisão explícita em §8 |
| Tempo de cálculo na leitura rápida | Orçamento medido; se exceder, a secção da página fica só com contagens e o cálculo completo fica no script |
| Proveniência aumenta `hypotheses.json` | ~200 bytes por entrada `llm`; ≤ 0,5 % do ficheiro |
| Conflitos com os commits de dados em `main` | Integração por SHA congelado (§6) |

## 6. Estratégia de integração (separada)

1. **Desenvolvimento** (esta sessão): código, testes, mutação, protocolo, relatório gerado sobre um SHA de `main` congelado. Push só para o ramo de desenvolvimento. Sem PR até pedido.
2. **Auditoria independente** (outra sessão, contexto limpo, só leitura): repete os testes, escreve testes adversariais próprios, revê o protocolo, reproduz o relatório byte a byte. Resultado em `audits/pr4-independent-audit.md`.
3. **Integração** (só com autorização expressa): PR aberta só quando pedido; merge sobre SHA congelado de `main`, como na PR 3; sem migração Telegram (as regras de estado não mudam, T15); verificação só de leitura nas duas recolhas seguintes (`scripts/post_cutover_check.py` + verificação PR 4: `status_list` inalterado, `validation` desligada, nenhum aviso Telegram novo atribuível à PR).
   Reversão: `git revert` do merge; o registo continua válido (campos aditivos ignorados pelo código antigo).

## 7. Entregáveis

`scangoal/equivalence.py`, `scangoal/llm_eval.py`, alterações em `scangoal/llm.py` (aceitação, proveniência, retorno) e
`scangoal/brain.py` (`track_record`, secção `llm_evaluation`), `scripts/audit_pr4_llm.py`, `tests/test_llm_evaluation.py`,
`audits/pr4-llm-protocol.md`, `audits/pr4-llm-evaluation.{md,json}`, `audits/pr4-evidence/`, README.

## 8. Decisões pedidas ao proprietário

1. Rejeitar duplicados extensionais na produção (`llm.accept`)? Recomendado: sim.
2. Limites "30/dia, 300 no total": aplicar no código ou corrigir o README para o comportamento real? Recomendado: corrigir o README (sem mudança de comportamento nesta PR).
3. Parâmetros do protocolo (Jaccard 0,85; K = 20; mínimos 10 grupos / 200 jogos; α = 0,05): aceitar ou ajustar antes do registo.

## 9. Decisões do proprietário (15/11/2026) — prevalecem sobre o texto acima

1. Rejeitar só duplicados exatos e redundâncias extensionais demonstradas no instante da proposta (conjunto de sinais igual ao de uma hipótese existente ou de uma sub-combinação). Nunca rejeitar só por Jaccard ≥ 0,85.
2. Sem limites diários ou totais de hipóteses; corrigir o README; manter o orçamento gratuito de neurónios.
3. Jaccard 0,85 só para agrupamento exploratório, com sensibilidade a 0,80 e 0,90; ligação completa e regras de compatibilidade (mesma família, mesma expectativa, nenhuma condição contraditória) impedem agrupamentos transitivos de hipóteses incompatíveis.
4. 20 controlos pré-selecionados, ≥ 10 grupos e ≥ 200 jogos, mais diversidade temporal (≥ 14 dias, nenhum dia com mais de 20 % dos jogos). Nenhuma conclusão positiva com amostra temporal insuficiente.
5. Uma comparação primária e α = 0,05 fixados antes da avaliação; Holm nas 7 análises adicionais.
6. Protocolo registado num commit próprio; só dados posteriores a esse commit sustentam conclusões prospetivas pelo protocolo.
7. Nascimento efetivo pelo histórico git completo; sem prova, avaliação não verificada e nenhuma conclusão científica.
8. `track_record` deixa de apresentar `prob_above > 0.5` como confirmação e deixa de transmitir essa conclusão à LLM.
9. A PR 4 não altera as quatro promissoras atuais, as regras da PR 3 nem a certificação (verificado por comparação com `main` sobre os mesmos dados).
10. Testes adversariais, mutações, avaliação reprodutível e relatório científico.
