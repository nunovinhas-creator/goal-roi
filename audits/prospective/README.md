# B1 + B4 · Resultados independentes e registo prospetivo

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.** O registo mede o resultado desportivo confirmado e, na H1, a capacidade preditiva. Não mede rentabilidade: sem preços executáveis e custos conhecidos, nenhum ROI é validado.

## B1 — associação de resultados Highlightly/BSD

- **Liga.** `results_bsd.competition_ok` aceita nomes de liga escritos de forma diferente quando as palavras do nome mais curto aparecem todas no mais longo. Por exemplo, "La Liga" e "Spanish La Liga Primera" são compatíveis. Uma única palavra genérica ("league", "liga") nunca chega.
- **Mantém-se sem alteração:** equipas iguais depois de normalizar (ou alias revisto à mão), início a ±15 min, um só candidato e a regra auditada N03 (sem liga do fornecedor, só um id já associado identifica o jogo).
- **O registo do fornecedor guarda:** liga, equipas e id do fornecedor, e também liga, equipas, início e fase do alvo (`target_*`) e o método de identidade. As versões continuam *append-only* (`ft_versions`, `first_results`).
- **Jogos do registo prospetivo:** entram nas consultas mesmo quando o bot já deu o resultado. A consulta é por dia, por isso não gasta pedidos extra e as quotas mantêm-se.
- **Limite conhecido:** a BSD não devolve liga, por isso só associa por id já ligado.

## B4 — registo prospetivo

- **Protocolo congelado:** [`scangoal/prospective_protocol.json`](../../scangoal/prospective_protocol.json) define população, exclusões, unidade (jogo), horizonte, resultados em falta, H1, H2 e D1.
- **Registo:** `data/prospective_ledger.jsonl`, só acrescenta. Cada entrada tem `seq`, `prev_sha256` e `sha256`. A primeira entrada é a ativação, com o SHA-256 do protocolo e do código. Uma alteração do protocolo para o registo.
- **Tipos de entrada:** `signal` (no primeiro ciclo de recolha que o vê, com `result_known_at_registration`), `outcome` (primeira confirmação por fornecedor, nunca substituída), `outcome_conflict`, `outcome_missing` (72 h), `diagnostic` (D1) e `code_version`.
- **Resumo derivado:** `data/prospective_report.json`, reconstruído em cada ciclo.
- **No workflow:** passo `Prospective ledger (append-only)` logo depois do relatório, com `continue-on-error` e timeout de 3 min. Não envia avisos, não lê fontes novas e não faz ordens.

## Verificação

```sh
python -m scangoal.prospective verify      # cadeia, sequência e duplicados; código 3 se estiver partida
python -m unittest tests.test_prospective_b1_b4 -v
```

## Reversão

1. Remover o passo `Prospective ledger (append-only)` do `collect.yml` e as duas linhas de proteção correspondentes nos testes. O registo fica como evidência e nunca é editado.
2. Para B1, reverter `competition_ok` para a igualdade exata e retirar a inclusão dos jogos registados em `pending_targets`. Os registos de fornecedor já guardados mantêm-se.
3. Efeito visível de B1 no relatório: jogos com resultado de fornecedor passam a poder ser liquidados por essa fonte, o que muda os totais do resumo diário. Os avisos de sinais não mudam.
