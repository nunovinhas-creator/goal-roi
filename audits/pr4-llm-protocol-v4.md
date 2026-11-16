# PR 4 — Protocolo pré-registado da avaliação da LLM (`llm_incremental_v4`)

Substitui `llm_incremental_v3` (`audits/pr4-llm-protocol-v3.md`, commit `0766a13`, sha256 `e3190be0…89a4`). O v1
(`9f7b2aa`), o v2 (`25cd2d2`) e o v3 ficam no repositório sem alterações, como registo histórico, e deixam de ser aceites pelo
código. A substituição responde à auditoria independente da PR #27 (revisão #5916502096, ponto P1-E: o teste do v3 não controla
o erro de tipo I com cotações diferentes e diferenças diárias assimétricas) e às decisões do proprietário de 16/11/2026: teste
por apostas como único critério confirmatório, bootstrap-t só como diagnóstico, horizonte de 365 dias. As emendas estão em §0;
§1, §2, §4 e §5 são iguais ao v3.

Registado antes de qualquer implementação das emendas e antes de qualquer avaliação com este protocolo. Este ficheiro é
imutável: o código guarda o seu `sha256` e recusa avaliar com qualquer outra versão. Uma alteração é um protocolo novo.

## 0. Emendas ao v3 (e porquê)

1. **P1-E, teste (§6):** a inversão de sinais do v3 só é exata se as diferenças diárias forem simétricas; com perfis de cotação
   opostos e poucos jogos rejeitou até 9,2 % sob ausência de utilidade (`audits/pr4-evidence/p1e_method_comparison.py`). O v4
   usa um **teste por apostas (e-processo)** cujo erro de tipo I é limitado por um teorema (§6-A), sem simetria, sem
   independência e sem forma da distribuição. O bootstrap-t fica só como diagnóstico, sem efeito no veredicto.
2. **Horizonte (§0-B):** 365 dias. O e-processo é válido em qualquer instante (desigualdade de Ville): olhar repetidamente e
   parar quando a riqueza passa 1/α não aumenta o erro, por isso o v4 permite concluir antes do horizonte (resolve o P1-D de
   outra forma, mais forte do que o olhar único do v3).
3. **Jogos sem resultado (§3):** deixam de ser excluídos (a exclusão era uma seleção pela disponibilidade do resultado). Um jogo
   LLM sem resultado comprovado em 3 dias conta como perda total; um jogo de controlo, como ganho máximo (imputação desfavorável
   à hipótese em teste, que preserva a garantia).
4. **Revisões retroativas dos dados (§0-C):** prova de publicação de cada observação e fixação do commit de dados da avaliação final.
5. **Publicação (§0-A):** o v4 exige a sua própria prova de publicação no GitHub; dados anteriores a ela nunca são confirmatórios.

## 0-A. Início P e prova de publicação

- A **primeira publicação verificável** é a hora registada pelo servidor GitHub (não pelo autor) do primeiro registo público que
  contém este ficheiro: o `created_at` da primeira execução de GitHub Actions cujo `head_sha` contém o commit que introduziu
  este ficheiro, ou o `created_at` de um pull request que o contenha, o que for anterior entre os registos apresentados.
- O avaliador recebe essa hora e a ligação para o registo (`--protocol-published-utc`, `--protocol-publication-evidence`) e
  recusa (não verificada) se faltarem ou se a hora for anterior à do commit. A auditoria independente confirma a hora na ligação.
- **P = max(hora do commit que introduziu este ficheiro, primeira publicação verificável)**.

## 0-B. Horizonte, dias fechados e conclusão a qualquer momento

- **Horizonte H = P + 365 dias.** Só contam jogos cuja aposta comprometida foi decidida em (t_h, H].
- **Instante final C = H + 3 dias.** O relógio efetivo de uma avaliação é min(as_of, C).
- **Dia fechado:** o dia UTC d está fechado quando o relógio >= fim do dia d + 3 dias. Nessa altura todos os jogos decididos nesse
  dia têm resultado comprovado ou imputado (§3); o valor do dia nunca mais muda. Só dias fechados entram no e-processo, pela ordem
  das datas, e por isso a trajetória da riqueza já calculada nunca é reescrita por avaliações posteriores.
- **Conclusão:** no primeiro dia fechado em que a riqueza K >= 1/α = 20 **e** os mínimos de §7 se cumprem sobre os dias fechados
  até esse dia, a conclusão é `utilidade incremental estatisticamente significativa`, com essa data, e mantém-se em todas as
  avaliações posteriores. Se isso nunca acontecer até C, o veredicto final em C é `sem utilidade incremental demonstrada` (ou
  `evidência insuficiente` se os mínimos falharem sobre todos os dias fechados). Antes disso, `em curso (sem conclusão)`.
- **Sem extensão:** depois de C, o v4 termina. Uma nova tentativa exige outro protocolo, com nova publicação e só dados novos.

## 0-C. Revisões retroativas dos dados

- **Prova de publicação das observações:** pela história de primeiro-pai do commit avaliado, `data/messages.jsonl` só cresce
  (nenhuma linha removida ou reescrita); nos ficheiros de resultados (`results_hl.json`, `results_bsd.json`, `details_hl.json`,
  `manual_results.json`) nenhuma entrada é removida ou alterada depois de publicada; e cada observação publicada a partir de P (mensagem
  recolhida, resultado associado, minutos dos golos, resultado manual) foi publicada no git entre o seu carimbo e 30 minutos
  depois. Qualquer falha torna a avaliação **não verificada**. Isto impede que um resultado seja acrescentado ou corrigido mais tarde
  com uma data antiga.
- **Commit de dados da avaliação final:** a avaliação em C usa sempre o primeiro commit da história de primeiro-pai de `main` com
  hora >= C, e nunca um posterior; o script escolhe-o sozinho e regista o seu SHA. Revisões feitas depois desse commit não lhe chegam.
- Uma conclusão antecipada (§0-B) é calculada com o commit avaliado nesse momento; a prova de publicação garante que avaliações
  posteriores, com mais dados, reproduzem a mesma trajetória da riqueza sobre os mesmos dias fechados.

## 1. Pergunta

As hipóteses propostas pelo modelo de linguagem (`source = llm`) escolhem jogos com mais lucro por jogo, depois do seu
registo, do que hipóteses comparáveis do gerador automático já existentes no mesmo instante, avaliadas no mesmo intervalo?

Não é uma pergunta sobre vantagem contra o mercado, nem certifica nenhuma tática.

## 2. Nascimento efetivo (prova pelo histórico git)

- `born_eff(h) = max(born_utc(h), hora do primeiro commit da história de primeiro-pai do ramo avaliado em que h aparece em
  data/hypotheses.json)`.
- Exige histórico completo (repositório não *shallow*) e prova de registo só a crescer: em cada versão publicada, todas as
  entradas das versões anteriores existem com conteúdo idêntico; horas dos commits não decrescentes.
- Um `born_utc` anterior à publicação é corrigido pelo máximo; posterior não é problema.
- Sem prova (histórico raso, ficheiro apagado ou ilegível numa versão, entrada removida ou alterada, horas de commit
  decrescentes, hipótese LLM nunca publicada, `born_utc` ausente ou malformado): avaliação **não verificada** e nenhuma
  conclusão científica, qualquer que seja o resultado numérico.

## 3. Universo, evidência admitida e imputação

- Sinais: só elegíveis (`eligibility.eligible`), lidos (hora de leitura provada) até min(relógio, H), com os resultados observados
  até ao relógio.
- Instante de início de cada hipótese LLM h: `t_h = max(born_eff(h), P)`; `born_eff(h)` para a janela descritiva "pós-registo,
  pré-protocolo", que nunca sustenta conclusões.
- **Jogo admitido** para a hipótese x (LLM, controlo, mãe ou base da família) com início t: a aposta comprometida do jogo (a
  primeira decidida entre os sinais de x nesse jogo) é emitida e lida depois de t e decidível (`brain.operational_bet` sem a
  exigência do resultado), e nenhum resultado do jogo tinha sido observado antes da sua decisão. A admissão não depende do valor
  nem da existência do resultado.
- **Valor do jogo:** lucro por unidade à odd anunciada −2 % (`brain.bet_profit`) da aposta comprometida, se o seu resultado foi
  observado com hora provada até 3 dias depois da decisão. Caso contrário, depois desses 3 dias, **imputa-se**: −1 (perda total) no
  lado LLM; o ganho máximo da aposta (odd·0,98 − 1) no lado de comparação. Uma devolução vale 0. Um voto por jogo.
- **Ganho máximo do jogo** (para a normalização de §6): odd·0,98 − 1 da aposta comprometida (no lado de comparação, a média
  ponderada sobre os pares que o admitem).
- Dia do jogo: data UTC da hora de decisão da aposta comprometida.
- A taxa de acerto, `prob_above` e qualquer percentagem acima de 50 % são apenas descritivas e nunca entram em critério algum.

## 4. Duplicados e equivalência

- **Rejeição na proposta (produção):** só duplicados exatos (identificador canónico) e redundâncias extensionais
  demonstradas no instante da proposta: o conjunto de sinais elegíveis abrangidos é igual ao de uma hipótese existente da
  mesma família, ou igual ao de uma sub-combinação das suas condições (incluindo a família inteira). Nunca se rejeita só por Jaccard.
- **Agrupamento exploratório (avaliação):** as hipóteses LLM são consideradas pela ordem (`born_eff`, id). Para a hipótese h que
  chega, no instante `born_eff(h)`: conjunto de pertença de cada hipótese = jogos de todos os sinais elegíveis abrangidos **lidos
  até `born_eff(h)`** (com ou sem resultado; não lê resultados). Duas hipóteses são compatíveis se têm a mesma família, a mesma
  expectativa (`acima`/`abaixo`) e nenhuma dimensão com valores diferentes. Ligação completa: h entra no primeiro grupo em que é
  compatível com **todos** os membros e tem Jaccard ≥ τ com **todos** (ambos com ≥ 8 jogos nesse instante); senão abre grupo
  novo. A decisão nunca é revista. Não há agrupamentos transitivos. Representante = primeiro membro. τ = 0,85; sensibilidade a 0,80 e 0,90.

## 5. Controlos pré-selecionados

Para cada representante LLM h com expectativa `acima`:
- candidatos: hipóteses `gerador` com `born_eff < born_eff(h)`, mesma família, número de condições = min(n.º de condições de h, 3),
  sem dimensões de `brain.POST`, e com conjunto de sinais lidos até `born_eff(h)` diferente do de h;
- apoio s(x) = número de sinais elegíveis abrangidos **lidos (hora de leitura provada) até `born_eff(h)`**; admitido se
  s(h)/2 <= s(c) <= 2·s(h);
- ordem pseudoaleatória por `sha256(id(h) + "\n" + id(c))`; ficam os primeiros K = 20; com menos de 5, h fica fora da análise
  primária (contado como "sem controlos"). A seleção não lê resultados.
- Cada controlo é avaliado no intervalo de h (início t_h), não no seu.

## 6. Comparação primária: e-processo (α = 0,05, unilateral)

- Lado LLM: para cada jogo g admitido em algum representante LLM (expectativa `acima`, com controlos), x_g = média dos
  valores dos representantes que o admitem; dia de g = o mais cedo dos dias das apostas comprometidas que o admitem.
- Lado controlo: para cada jogo g admitido em algum controlo, y_g = média ponderada dos valores dos pares (h, c) que o admitem,
  com peso 1/K_h; u_g = média ponderada dos ganhos máximos correspondentes.
- Por dia fechado j com jogos dos dois lados (**dia emparelhado**), pela ordem das datas: m_L,j e m_C,j = médias de x_g e de y_g;
  L_j = 1 + média de u_g; **X_j = (m_L,j − m_C,j) / L_j**. Como x_g >= −1 e y_g <= u_g, **X_j >= −1 sempre**.
- **Aposta:** λ_1 = 1/8; para j >= 2, λ_j = min(1/2, max(0, μ_j / (v_j + μ_j²))), com μ_j e v_j a média e a variância (divisor
  n) de X_1, …, X_{j−1} (λ_j = 1/8 se v_j + μ_j² = 0). λ_j depende só dos dias anteriores.
- **Riqueza:** K_0 = 1; K_j = K_{j−1} · (1 + λ_j X_j). Rejeição de H0 no primeiro j com K_j >= 20 e os mínimos de §7 cumpridos
  sobre os dias fechados até j.
- **Diagnóstico (sem efeito no veredicto):** bootstrap-t por dias da média ponderada das diferenças diárias (pesos
  n_L·n_C/(n_L+n_C)), 999 réplicas, semente 20261115.

## 6-A. Hipótese nula e garantia (teorema)

**H0 (hipótese científica de ausência de utilidade incremental):** para cada dia emparelhado e fechado j, a esperança de X_j dado o
passado é <= 0: E[X_j | X_1, …, X_{j−1}] <= 0.

Condição suficiente, mais interpretável (**H0-dia**): para cada dia j, condicionalmente aos resultados de todos os dias anteriores
e à composição do dia j (que jogos entram em cada lado e com que cotações e pesos), a esperança da diferença verdadeira m_L,j − m_C,j
é <= 0. H0-dia implica H0: L_j é função da composição; a imputação só baixa X_j; aplica-se a propriedade da torre.

**Teorema.** Sob H0, P(existe j com K_j >= 20) <= 0,05. Logo a probabilidade de uma conclusão falsa de utilidade, em qualquer
avaliação e em qualquer instante até ao fim do v4, é <= 0,05.

**Demonstração.** λ_j ∈ [0, 1/2] é função de X_1..X_{j−1}; X_j >= −1, por isso 1 + λ_j X_j >= 1/2 > 0 e K_j >= 0. Pela linearidade da
esperança condicional, E[K_j | X_<j] = K_{j−1} (1 + λ_j E[X_j | X_<j]) <= K_{j−1}. (K_j) é uma supermartingala não negativa com
K_0 = 1, e a desigualdade de Ville dá P(sup_j K_j >= 1/α) <= α. Exigir também os mínimos de §7 só reduz o acontecimento de rejeição. ∎

**O que a garantia cobre:**
- qualquer dependência entre jogos do mesmo dia (golos, mercados, efeitos do dia) e entre dias, porque só a esperança
  condicional de cada dia entra;
- quaisquer cotações e misturas diferentes nos dois lados, e assimetria arbitrária das diferenças, desde que os lucros sejam
  limitados, o que acontece por construção;
- qualquer seleção adaptativa que dependa de dias anteriores: hipóteses, controlos, quais jogos e quantos;
- jogos sem resultado (imputação desfavorável);
- olhares repetidos e paragem em qualquer instante.

**Fronteira explícita, fora da garantia:** se a composição de um dia dependesse dos resultados de jogos desse mesmo dia (por exemplo,
entrarem mais jogos depois de uma perda), a média diária podia ser positiva mesmo com apostas individualmente justas. Nesse caso
H0-dia (condicional à composição) deixa de coincidir com "cada aposta é justa". O teorema continua a valer para H0 tal como está
definida, mas a interpretação "por aposta" exige a suposição **A-sel**: a entrada de um jogo num dia não depende dos resultados
de outros jogos desse dia. No sistema, a entrada depende só das mensagens do bot e de hipóteses fixadas antes do dia; A-sel é
plausível mas não verificável pelos dados e é declarada em cada relatório.

## 7. Mínimos (obrigatórios para a conclusão positiva, sobre os dias fechados até à data da conclusão)

- ≥ 10 grupos LLM com ≥ 10 jogos admitidos cada;
- ≥ 200 jogos LLM distintos e ≥ 200 jogos de controlo distintos;
- diversidade temporal: ≥ 14 dias distintos com jogos LLM, ≥ 14 dias emparelhados, e nenhum dia com mais de 20 % dos jogos LLM.

## 8. Veredicto (por esta ordem)

1. `não verificada`: prova do nascimento (§2), do protocolo, da sua publicação (§0-A) ou das observações (§0-C) falhou.
2. `utilidade incremental estatisticamente significativa (e-processo, α = 0,05; não é prova de vantagem nem certificação)`: houve
   conclusão (§0-B), com a data do dia fechado em que a riqueza passou 20.
3. `em curso — sem conclusão (e-processo abaixo de 20)`: relógio < C e ainda sem conclusão.
4. `evidência insuficiente`: em C, sem conclusão e com algum mínimo de §7 falhado sobre todos os dias fechados.
5. `sem utilidade incremental demonstrada`: em C, sem conclusão.

Cada relatório indica a suposição A-sel (§6-A). O veredicto nunca altera estados, certificação, avisos Telegram ou registo de hipóteses.

## 9. Análises adicionais (exploratórias)

Mesmo e-processo, mesma janela e mesmos dias fechados. Para cada análise, p_e = min(1, 1/sup K) (p-valor válido a qualquer
momento), com correção de Holm, m = 7 fixo. Uma análise sem dias emparelhados conta com p_e = 1. Nenhuma sustenta sozinha um veredicto.
- S1: LLM (`acima`) contra a base da família no mesmo intervalo (todos os sinais elegíveis da família, aposta comprometida por jogo).
- S2: só modo `novas` (e sem modo) contra os seus controlos.
- S3: só modo `refinar` contra os seus controlos.
- S4: modo `refinar` contra as hipóteses-mãe (mesma família, menos uma condição, `born_eff` anterior), peso 1/n.º de mães.
- S5: modo `evitar` (expectativa `abaixo`): H1 = base da família − LLM > 0 (a imputação troca de lados com os papéis).
- S6: comparação primária com τ = 0,80.
- S7: comparação primária com τ = 0,90.

## 10. Reprodutibilidade

- Entrada fixa: um commit do ramo avaliado (dados lidos por `git show`; em C, o commit de §0-C), `as_of` explícito (relógio
  efetivo min(as_of, C)), SHA do protocolo, hora e ligação da publicação.
- Saída JSON com `sort_keys`, números arredondados a 6 casas, `sha256` de cada ficheiro de dados, versão do Python e parâmetros.
  Duas execuções com os mesmos argumentos produzem ficheiros iguais byte a byte.
- Sem rede, sem segredos, sem escrita em `data/`.
