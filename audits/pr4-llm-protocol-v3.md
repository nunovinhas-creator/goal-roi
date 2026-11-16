# PR 4 — Protocolo pré-registado da avaliação da LLM (`llm_incremental_v3`)

Substitui `llm_incremental_v2` (`audits/pr4-llm-protocol-v2.md`, commit `25cd2d2`, sha256 `1f4a1001…1339`), que por sua vez
substituiu o v1 (`audits/pr4-llm-protocol.md`, `9f7b2aa`). O v1 e o v2 ficam no repositório sem alterações, como registo
histórico, e deixam de ser aceites pelo código. A substituição responde à auditoria independente da PR #27
(revisão #5916209217, ponto P1-D: avaliações repetidas e controlo de α). As emendas estão em §0; o resto é igual ao v2.

Registado antes de qualquer implementação das emendas e antes de qualquer avaliação com este protocolo. Este ficheiro é
imutável: o código guarda o seu `sha256` e recusa avaliar com qualquer outra versão. Uma alteração é um protocolo novo.

## 0. Emendas ao v2 (e porquê)

1. **P1-D, um único olhar confirmatório (§0-B):** o v2 permitia correr a avaliação as vezes que se quisesse e parar na
   primeira com p <= 0,05 (paragem oportunista), o que inflaciona o erro de tipo I acima de 0,05. O v3 fixa um único horizonte
   confirmatório; qualquer avaliação anterior é só exploratória e nunca dá uma conclusão de significância.
2. **Calibração do teste (§6):** em simulações preliminares sem utilidade incremental (1 000 a 1 500 repetições por cenário), o
   bootstrap por blocos de dia do v2 rejeitou entre 4,7 % e 6,8 % das vezes a α = 0,05 (anticonservador com efeitos de dia
   fortes e poucos dias). O v3 substitui-o por um teste de inversão de sinais por dia sobre diferenças DENTRO do mesmo dia, que
   elimina o efeito comum do dia e rejeitou entre 4,0 % e 5,2 % nos mesmos cenários. A verificação reprodutível, com sementes
   fixas, fica em `audits/pr4-evidence/calibration.json`.
3. **Mínimos (§7):** mantêm-se os de jogos, grupos e diversidade temporal; acrescenta-se ≥ 14 dias emparelhados (com jogos dos
   dois lados), necessários ao novo teste. O mínimo de réplicas válidas deixa de se aplicar (todas as réplicas são válidas).
4. **Publicação (§0-A):** o v3 exige a sua própria prova de publicação no GitHub; dados anteriores a ela nunca são confirmatórios.

## 0-A. Início P e prova de publicação

- A **primeira publicação verificável** é a hora registada pelo servidor GitHub (não pelo autor) do primeiro registo público que
  contém este ficheiro: o `created_at` da primeira execução de GitHub Actions cujo `head_sha` contém o commit que introduziu
  este ficheiro, ou o `created_at` de um pull request que o contenha, o que for anterior entre os registos apresentados.
- O avaliador recebe essa hora e a ligação para o registo (`--protocol-published-utc`, `--protocol-publication-evidence`) e
  recusa (não verificada) se faltarem ou se a hora for anterior à do commit. A auditoria independente confirma a hora na ligação.
- **P = max(hora do commit que introduziu este ficheiro, primeira publicação verificável)**.

## 0-B. Horizonte confirmatório único

- **Horizonte H = P + 30 dias.** Só contam para a comparação jogos cuja aposta comprometida foi decidida em (t_h, H].
- **Instante confirmatório C = H + 3 dias** (prazo de liquidação, igual a `OPEN_TIMEOUT_DAYS`): os resultados observados até C
  contam; os jogos sem resultado de hora provada até C ficam de fora, como na definição prospetiva.
- **Uma só avaliação confirmatória:** a avaliação com relógio C. Qualquer execução com `as_of >= C` avalia exatamente com o
  relógio C e dá um resultado idêntico, byte a byte; repetir não muda nada. Só hipóteses LLM com nascimento provado até H entram.
- **Antes de C** (`as_of < C`) a avaliação é **exploratória**: mostra os números, mas o veredicto é sempre
  `exploratória — antes do instante confirmatório (sem conclusão)`, qualquer que seja o p.
- **Sem extensão:** se em C algum mínimo falhar, o veredicto do v3 é definitivo (`evidência insuficiente`). Uma nova tentativa
  exige um protocolo novo, com nova publicação e só dados posteriores a ela; os dados do v3 nunca são reutilizados como confirmatórios.

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

## 3. Universo e evidência admitida

- Sinais: só elegíveis (`eligibility.eligible`) na vista `chronology.as_of_view(sinais, as_of)`.
- Instante de início de cada hipótese LLM h: `t_h = max(born_eff(h), P)`, com P definido em §0-A, para a conclusão prospetiva pelo protocolo;
  `born_eff(h)` para a janela descritiva "pós-registo, pré-protocolo" (jogos decididos antes de P), que nunca sustenta conclusões.
- Jogo admitido para a hipótese x (LLM, controlo, mãe ou base da família) com início t: `brain.game_category(jogo, t) == 'prospective'`
  (definição única da PR 3: emitido e lido depois de t, decidível, resultado com hora provada, decidido antes de qualquer
  resultado do jogo, sem jogos mistos).
- Valor do jogo: lucro por unidade à odd anunciada −2 % (`brain.bet_profit`) da aposta comprometida
  (`brain.committed_bet` sobre `brain.game_core`). Um voto por jogo.
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

## 6. Comparação primária (única, α = 0,05, unilateral, só em C)

- Lado LLM: para cada jogo g admitido em algum representante LLM (expectativa `acima`, com controlos), x_g = média dos
  valores dos representantes que o admitem; dia de g = o mais cedo dos dias das apostas comprometidas que o admitem.
- Lado controlo: para cada jogo g admitido em algum controlo, y_g = média ponderada dos valores dos pares (h, c) que o
  admitem, com peso 1/K_h.
- Por dia d com jogos dos dois lados (**dia emparelhado**): m_L,d e m_C,d = médias de x_g e y_g nesse dia; n_L,d e n_C,d = n.º
  de jogos; peso w_d = n_L,d·n_C,d / (n_L,d + n_C,d).
- **Estatística: D = Σ_d w_d (m_L,d − m_C,d) / Σ_d w_d** sobre os dias emparelhados. H0: sem utilidade incremental (as
  diferenças diárias são simétricas em torno de 0); H1: D > 0.
- **Inferência: inversão aleatória de sinais por dia**: D* = Σ_d w_d s_d (m_L,d − m_C,d) / Σ_d w_d com s_d = ±1 equiprováveis e
  independentes, B = 10 000 réplicas, semente 20261115 (`random.Random`). p = (1 + #{D* >= D}) / (1 + B).
- Descritivo (sem inferência): diferença das médias por jogo dos dois lados, como no v2.

## 7. Mínimos (todos obrigatórios para qualquer conclusão, verificados em C)

- ≥ 10 grupos LLM com ≥ 10 jogos admitidos cada;
- ≥ 200 jogos LLM distintos e ≥ 200 jogos de controlo distintos;
- diversidade temporal: ≥ 14 dias distintos com jogos LLM, ≥ 14 dias emparelhados, e nenhum dia com mais de 20 % dos jogos LLM.

## 8. Veredicto (por esta ordem)

1. `não verificada` — prova do nascimento (§2), do protocolo ou da sua publicação (§0-A) falhou.
2. `exploratória — antes do instante confirmatório (sem conclusão)` — `as_of` < C.
3. `evidência insuficiente` — em C, algum mínimo de §7 falhou (definitivo para o v3).
4. `sem utilidade incremental demonstrada` — em C, p > 0,05.
5. `utilidade incremental estatisticamente significativa (não é prova de vantagem nem certificação)` — em C, p <= 0,05.
O veredicto nunca altera estados, certificação, avisos Telegram ou registo de hipóteses.

## 9. Análises adicionais (exploratórias, correção de Holm, m = 7 fixo, α = 0,05 de família)

Mesma estatística, teste e janela; só têm p ajustado em C (antes de C são números sem teste). Uma análise sem dados ou abaixo
dos mínimos de §7 (sem o de grupos, exceto em S6 e S7) conta com p = 1.
- S1: LLM (`acima`) contra a base da família no mesmo intervalo (todos os sinais elegíveis da família, aposta comprometida por jogo).
- S2: só modo `novas` (e sem modo) contra os seus controlos.
- S3: só modo `refinar` contra os seus controlos.
- S4: modo `refinar` contra as hipóteses-mãe (mesma família, menos uma condição, `born_eff` anterior), peso 1/n.º de mães.
- S5: modo `evitar` (expectativa `abaixo`): H1 = base da família − LLM > 0.
- S6: comparação primária com τ = 0,80.
- S7: comparação primária com τ = 0,90.
Nenhuma análise adicional sustenta sozinha um veredicto.

## 10. Reprodutibilidade

- Entrada fixa: um commit do ramo avaliado (dados lidos por `git show`), `as_of` explícito (o relógio efetivo é min(as_of, C)),
  SHA do protocolo, hora e ligação da publicação.
- Saída JSON com `sort_keys`, números arredondados a 6 casas, `sha256` de cada ficheiro de dados, versão do Python e parâmetros.
  Duas execuções com os mesmos argumentos produzem ficheiros iguais byte a byte.
- Sem rede, sem segredos, sem escrita em `data/`.
