# PR 4/5 — Procedimento de integração (NÃO executado; exige autorização expressa)

A PR 4 não muda regras de estado, certificação, Telegram nem o formato dos dados de produção, por isso não há migração nem
paragem da recolha. Muda:
- o código da LLM: rejeita redundâncias e guarda a proveniência das propostas;
- o balanço descritivo (`brain.track`);
- a página (`brain.llm_evaluation`, sempre "não verificada" no relatório de produção, que não tem histórico git completo);
- o avaliador, scripts e auditorias, que só correm à mão.

## Pré-condições

1. Auditoria independente aprovada sobre o SHA final da PR #27.
2. Autorização expressa do proprietário para o merge.
3. CI verde nesse SHA.

## Fase A — preflight (só leitura)

1. `git fetch origin main` e congelar o SHA M do `main`.
2. `python3.11 scripts/pr4_preflight.py M <SHA final da PR> audits/pr4-integration-preflight.json`
3. Exigir `ready: true`. O preflight verifica:
   - merge simulado sem conflitos;
   - `data/`, `.github/`, `docs/report.json` e `docs/bets.csv` idênticos aos do `main`;
   - nenhum registo de certificação;
   - testes a passar na árvore resultante;
   - comparação com `main` sobre os dados do `main`: estados, certificação desligada, mensagens Telegram, estado de notificações e
     dados idênticos.

   Último preflight: `main` `31a7988`, PR `cbeb57a`, `ready: true`.

## Fase B — merge

- Merge da PR #27 com **merge commit**: nem *squash*, nem *rebase*. Os commits dos protocolos (`9f7b2aa`, `25cd2d2`, `0766a13`,
  `d48e3b7`, `bb1b07c`) têm de continuar no histórico com os mesmos SHA, porque `llm_eval.protocol_proof` os exige e as provas de
  publicação referem-nos.
- A recolha pode continuar a correr: o passo de commit dos dados faz `git pull --rebase`, e a PR não toca em ficheiros de dados.
- Não ativar a certificação. Não alterar `fetch-depth`. Não enviar mensagens Telegram de teste.

## Fase C — verificação (só leitura, nas duas recolhas seguintes)

1. `python3.11 scripts/post_cutover_check.py <merge> <commit da recolha seguinte>` deve passar:
   - marcas de migração intactas;
   - `sent_total` e `watch_sent` monotónicos;
   - regras de estado da PR 3;
   - certificação desligada;
   - referência Telegram igual ao `status_list`.
2. `docs/report.json` tem `brain.llm_evaluation.protocol = llm_incremental_v5` e `verdict = não verificada`; `brain.track` é descritivo.
3. Depois da primeira chamada à LLM, `data/llm_state.json` tem `rejected_equivalent`. As entradas novas de `data/hypotheses.json`
   têm `provenance`; as antigas ficam iguais.

## Reversão

- `git revert -m 1 <merge>`. Não há dados a restaurar: os campos novos (`provenance`, `rejected_equivalent`) são aditivos e o código
  antigo ignora-os.
- Nunca restaurar `data/notify_state.json` ou `data/hypotheses.json` de uma cópia antiga, porque ambos só crescem.

## Depois da integração

- A avaliação científica corre à mão, com `scripts/audit_pr4_llm.py` num clone completo:
  - P = 2026-11-16T08:56:55Z;
  - C = 2027-11-19T08:56:55Z;
  - conclusão possível em qualquer dia fechado antes de C.
- A melhoria da cobertura de resultados Betfair é uma alteração de dados separada, com autorização própria, e não muda a metodologia.
