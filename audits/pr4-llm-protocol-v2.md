# PR 4 — Protocolo pré-registado da avaliação da LLM (`llm_incremental_v2`)

Substitui `llm_incremental_v1` (`audits/pr4-llm-protocol.md`, commit `9f7b2aa`, sha256 `8b108bf9…5cb0`), que fica no
repositório sem alterações e deixa de ser aceite pelo código. A substituição responde à revisão independente preliminar da
PR 4 (PR #27): P1-A, P1-B e P2-C. As emendas estão listadas em §0; tudo o resto é igual ao v1.

Registado antes de qualquer implementação das emendas e antes de qualquer avaliação com este protocolo. Este ficheiro é
imutável: o código guarda o seu `sha256` e recusa avaliar com qualquer outra versão. Uma alteração é um protocolo novo.

## 0. Emendas ao v1 (e porquê)

1. **P1-A, apoio dos controlos (§5):** o apoio à data do nascimento conta só sinais **lidos** (hora de leitura provada) até
   `born_eff(h)`, e não sinais emitidos até lá. Um sinal emitido antes e lido depois não era conhecido no nascimento. A
   exclusão de controlos com os mesmos sinais que h usa também só os sinais lidos até `born_eff(h)`.
2. **P1-B, grupos (§4):** cada decisão de agrupamento é tomada **no nascimento** da hipótese que chega, com os jogos dos
   sinais já lidos nesse instante (para ela e para todos os membros com quem é comparada), e nunca é revista. A pertença aos
   grupos e os representantes ficam assim invariantes a jogos posteriores.
3. **P2-C, início P (§0-A):** P deixa de ser a hora do commit (escrita pelo autor) e passa a ser
   `max(hora do commit, primeira publicação verificável no GitHub)`. Sem prova de publicação, a avaliação é **não verificada**.
   Dados anteriores à publicação comprovada nunca são evidência prospetiva pelo protocolo.

## 0-A. Início P e prova de publicação

- A **primeira publicação verificável** é a hora registada pelo servidor GitHub (não pelo autor) do primeiro registo público que
  contém este ficheiro: o `created_at` da primeira execução de GitHub Actions cujo `head_sha` contém o commit que introduziu
  este ficheiro, ou o `created_at` de um pull request que o contenha, o que for anterior entre os registos apresentados.
- O avaliador recebe essa hora e a ligação para o registo (`--protocol-published-utc`, `--protocol-publication-evidence`) e
  recusa (não verificada) se faltarem ou se a hora for anterior à do commit. A auditoria independente confirma a hora na ligação.
- **P = max(hora do commit que introduziu este ficheiro, primeira publicação verificável)**.

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

## 6. Comparação primária (única, α = 0,05, unilateral)

- Lado LLM: para cada jogo g admitido em algum representante LLM (expectativa `acima`, com controlos), x_g = média dos
  valores dos representantes que o admitem. Ḡ_LLM = média de x_g sobre os jogos.
- Lado controlo: para cada jogo g admitido em algum controlo, y_g = média ponderada dos valores dos pares (h, c) que o
  admitem, com peso 1/K_h. Ḡ_CTRL = média de y_g sobre os jogos.
- Estatística: D = Ḡ_LLM − Ḡ_CTRL. H0: D <= 0; H1: D > 0.
- Inferência: bootstrap por blocos de dia (dias com observações em qualquer dos lados, reamostrados com reposição),
  B = 10 000 réplicas, semente 20261115 (`random.Random`); réplicas sem jogos num dos lados são descartadas e contadas.
  p = (1 + #{D* <= 0}) / (1 + réplicas válidas). Limite inferior unilateral a 95 % = percentil 5 de D*.

## 7. Mínimos (todos obrigatórios para qualquer conclusão)

- ≥ 10 grupos LLM com ≥ 10 jogos admitidos cada;
- ≥ 200 jogos LLM distintos e ≥ 200 jogos de controlo distintos;
- diversidade temporal: ≥ 14 dias distintos com jogos LLM e nenhum dia com mais de 20 % dos jogos LLM;
- ≥ 9 000 réplicas válidas.

## 8. Veredicto (por esta ordem)

1. `não verificada` — prova do nascimento (§2), do protocolo ou da sua publicação (§0-A) falhou.
2. `evidência insuficiente` — algum mínimo de §7 falhou.
3. `sem utilidade incremental demonstrada` — p > 0,05.
4. `utilidade incremental estatisticamente significativa (não é prova de vantagem nem certificação)` — p <= 0,05.
O veredicto nunca altera estados, certificação, avisos Telegram ou registo de hipóteses.

## 9. Análises adicionais (exploratórias, correção de Holm, m = 7 fixo, α = 0,05 de família)

Mesma estatística e bootstrap; uma análise sem dados ou abaixo dos mínimos de §7 conta com p = 1.
- S1: LLM (`acima`) contra a base da família no mesmo intervalo (todos os sinais elegíveis da família, aposta comprometida por jogo).
- S2: só modo `novas` (e sem modo) contra os seus controlos.
- S3: só modo `refinar` contra os seus controlos.
- S4: modo `refinar` contra as hipóteses-mãe (mesma família, menos uma condição, `born_eff` anterior), peso 1/n.º de mães.
- S5: modo `evitar` (expectativa `abaixo`): H1 = base da família − LLM > 0.
- S6: comparação primária com τ = 0,80.
- S7: comparação primária com τ = 0,90.
Nenhuma análise adicional sustenta sozinha um veredicto.

## 10. Reprodutibilidade

- Entrada fixa: um commit do ramo avaliado (dados lidos por `git show`), `as_of` explícito, SHA do protocolo, hora e ligação da publicação.
- Saída JSON com `sort_keys`, números arredondados a 6 casas, `sha256` de cada ficheiro de dados, versão do Python e parâmetros.
  Duas execuções com os mesmos argumentos produzem ficheiros iguais byte a byte.
- Sem rede, sem segredos, sem escrita em `data/`.
