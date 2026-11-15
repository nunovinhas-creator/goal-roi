# PR 3 — Plano da ativação científica (passo separado, NÃO autorizado)

Este documento descreve o que terá de acontecer **depois** do corte da PR 3 (`audits/pr3-cutover-runbook.md`) para ativar a certificação. Nada aqui foi executado:
- não existe `data/certification_ledger.json`;
- o workflow não foi alterado;
- `scangoal.certification --activate` nunca correu sobre dados de produção.

A ativação exige uma autorização expressa própria, posterior ao corte e às duas primeiras execuções verificadas.

## 1. Porque é separada

O corte integra o código com a certificação desligada (`validation.state = not_activated`), e o sistema fica operacional assim. A ativação cria, uma única vez, o registo *append-only* de hipóteses selecionadas e as suas α online (α_j = 0,05 / (j(j+1))). A partir desse momento:
- a ordem e a hora de cada seleção ficam fixadas para sempre;
- um erro na ativação (relatório velho, escritor concorrente, legadas incompletas, publicação fora da janela) invalida toda a certificação (fail-closed) e não se corrige sem reiniciar o registo.

Por isso exige um passo próprio, com a recolha parada.

## 2. Pré-requisito: checkout completo

`certification.history_proof` precisa do histórico git do registo:
- num checkout raso devolve `history unavailable: shallow repository`;
- a certificação fica então `history_unverified`.

O workflow de recolha tem de passar a usar `fetch-depth: 0`. A alteração está preparada em `audits/pr3-cutover-evidence/collect-fetch-depth-0.patch` e **não foi aplicada**.

Custo medido num clone do GitHub:
- 1,63 s e 4,31 MiB (1076 commits), contra 0,84 s e 0,56 MiB do checkout raso;
- continua gratuito e cresce cerca de 144 commits por dia.

Ordem recomendada:
1. aplicar o patch numa PR própria, com o workflow ainda a correr sem registo;
2. confirmar numa execução que `git rev-parse --is-shallow-repository` = `false` e que o relatório continua `not_activated`;
3. só então ativar.

## 3. Pré-condições verificadas pelo código (`certification.activation_plan`)

A ativação recusa (`CERTIFICATION_NOT_ACTIVATED`, código 1) se faltar qualquer uma:

1. `--confirm-collection-paused`: confirmação explícita de que não há escritores.
2. Nenhum registo existente.
3. `report.json` publicado gerado sob `chronological_eprocess_v3`, com no máximo 20 minutos de idade.
4. `status_list` do relatório igual a uma avaliação fresca.
5. Migração Telegram `status_rules:chronological_eprocess_v3` já feita, ou seja, o corte já foi aplicado.
6. `validation.state = not_activated`.
7. **Legadas completas:** todas as hipóteses que estão, estiveram ou estão a caminho de ser promissoras ficam legadas e nunca são certificáveis.

## 4. Pré-condições do procedimento (fora do código)

- **Pausa real:** workflow desativado e zero execuções ativas e pendentes, verificadas no GitHub; `main` estável em duas leituras.
- **Publicação na janela de 30 minutos:** o génesis tem de ser publicado entre `activated_utc` e 30 minutos depois, pela data de *committer*. Ensaio: 5 e 29 min aceites, 31 min recusado.
- **Compatibilidade com o `git pull --rebase` da recolha:**
  - um rebase reescreve a data de committer;
  - um rebase dentro da janela continua válido (ensaio: 12 min aceite, 40 min recusado);
  - os commits do registo devem por isso ser publicados de imediato e nunca ficar pendentes.
- **Histórico linear, sem `force-push`:** `verify_history` exige que cada versão publicada prolongue a anterior. Um force-push ou squash que apague versões invalida a certificação.
- **Mesmas entradas na avaliação, na migração e na escrita do registo:**
  - registar os SHA-256 de `data/report.json`, `data/notify_state.json`, `data/hypotheses.json` e dos sinais usados na avaliação;
  - confirmar que são iguais imediatamente antes de escrever o registo e no commit que o publica.

## 5. Procedimento proposto (a autorizar mais tarde)

1. Patch `fetch-depth: 0` integrado e verificado (§2).
2. Pausa verificada; M congelado; cópia de segurança `backup/pre-pr3-activation`.
3. No checkout completo de M, gerar um relatório fresco com `python -m scangoal.report`, sem notificar, e registar os hashes de entrada.
4. Correr `python -m scangoal.certification --activate --confirm-collection-paused`. Exige `CERTIFICATION_ACTIVATED`; qualquer outra saída obriga a parar.
5. Confirmar que os hashes de entrada não mudaram. Commit apenas de `data/certification_ledger.json`, push em menos de 30 minutos, com `push_preflight`.
6. Num checkout completo novo, confirmar:
   - `verify` e `history_proof` sem problemas;
   - `validation.state = active` com 0 certificadas;
   - nenhuma mensagem `✅ Tática validada`.
7. Reativar a recolha; acompanhar as duas primeiras execuções (estado `active`, sem certificações espúrias, registo só a crescer).

## 6. Reversão

Antes do push, apagar o ficheiro local. Depois do push, o registo não deve ser editado: para desativar, remove-se o ficheiro num commit próprio. Sem ficheiro, a certificação passa a `not_activated` e o notificador deixa de anunciar táticas validadas. Uma reativação futura cria um registo novo, com outra autorização, e as hipóteses já selecionadas antes devem entrar como legadas.
