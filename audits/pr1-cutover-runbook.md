# PR 1 — Corte controlado: script, ensaio e procedimento

**Preparação para auditoria independente. Nada foi executado em produção.** Nenhum workflow foi desativado ou cancelado, nenhuma branch de backup foi criada, `main` e os dados de produção não foram alterados, a migração real não foi executada, a PR continua Draft e nenhuma mensagem Telegram foi enviada. Cada ação de produção abaixo exige autorização expressa do proprietário para uma janela concreta.

Este documento complementa `audits/pr1-safe-integration.md` (mecanismo e reversão já revistos em #5907675027) com: um script que automatiza os passos 4–6 com verificações, um ensaio reproduzível sobre os dados reais atuais e a análise do risco operacional que falta controlar. A secção 9 regista a correção pedida na revisão #5909958197.

## 1. Ficheiros

| Ficheiro | Papel |
|---|---|
| `scripts/pr1_cutover.py` | Corte: relatório elegível + migração silenciosa, com guardas, verificações e rollback transacional |
| `scripts/rehearse_pr1_cutover.py` | Ensaio do corte numa worktree temporária fundida com um commit de `main`; exige cada critério explicitamente e mede os cenários de risco |
| `tests/test_pr1_cutover.py` | 20 regressões offline: 17 sobre o corte em repositórios git sintéticos e 3 sobre os critérios do ensaio |
| `audits/pr1-cutover-rehearsal.json` | Resultado do ensaio sobre `main` `f12c06b4c5aa7386db312923928be645c99b0cc2` |
| `audits/pr1-cutover-evidence/` | Evidência bruta da primeira passagem e demonstração do defeito P1 (ver README) |

Nenhum ficheiro em `scangoal/`, `data/`, `docs/` ou `.github/` foi alterado.

## 2. O que `scripts/pr1_cutover.py` garante

**Guardas antes de escrever qualquer ficheiro** (recusa → código 1 com `"restored": false`, ficheiros intactos):

1. `data/` e `docs/` sem alterações nem ficheiros por registar.
2. O commit congelado de `main` é antepassado do HEAD e `data/`, `docs/report.json` e `docs/bets.csv` são byte a byte iguais aos desse commit. **Um snapshot antigo nunca pode ser cortado por cima de dados mais recentes.**
3. `notify_state.json` existe, é JSON válido (objeto) e ainda não tem o marcador `eligible_statuses_v1`: a referência só se define uma vez; repetir o corte depois de persistido é recusado.
4. `SCANGOAL_DATA_DIR`, se definido, aponta para o `data/` do checkout.
5. O relatório construído tem `brain.evidence_scope = eligible_bets_only`, `status_list` válido **e `operational.scope = eligible_bets_only`** (bloco que alimenta o dashboard). Esta validação ocorre depois do build e antes de o script escrever qualquer ficheiro.

**Trabalho** (rede bloqueada durante toda a execução; uma tentativa de ligação aborta e restaura):

- `report.build(None, register=False)`: sem crescimento do registo, sem CSV, sem coletores, LLM ou notificações.
- Escreve `data/report.json` (mesmo formato de `report.main()`) e copia para `docs/report.json`.
- `notify.migrate_file()` duas vezes: exige `NOTIFY_STATUS_MIGRATED` e depois `NOTIFY_STATUS_UNCHANGED` com o ficheiro byte a byte igual.
- Simula o ciclo seguinte com `notify.messages()` sobre uma cópia do estado, **sem emissor**: exige zero transições de estado.

**Pós-verificações**: só mudam `data/report.json`, `docs/report.json` e `data/notify_state.json`; registo de hipóteses inalterado; `statuses` e `baseline_statuses` iguais ao `status_list`; `previous_statuses` igual ao estado anterior; `watch_sent`, `sent_total`, `hello`, `summary_day`, `last_run_utc`, `regime`, `operational_regime` e restantes campos preservados.

**Transação.** Tudo o que acontece depois das guardas corre sob um único rollback: build, validação, escrita, migração, leitura do estado migrado, simulação, pós-verificações e construção do registo de auditoria (hashes e resultado). Cada fase fica registada (`build_report`, `validate_report`, `write_report`, `migrate`, `migrate_again`, `read_migrated_state`, `simulate_next_cycle`, `post_checks`, `build_result`). Em qualquer falha, todos os ficheiros de `data/` e os dois de `docs/` são repostos byte a byte, os ficheiros criados são removidos e o restauro é confirmado por SHA-256.

| Código | Significado | Ação do operador |
|---:|---|---|
| 0 | Corte feito; todas as verificações passaram | Continuar o procedimento |
| 1 | Recusa por guarda (`restored: false`); ou verificação falhada (`ok: false, restored: true`); ou exceção (`CutoverAborted`: `restored: true, restore_verified: true`, com `stage` e `cause`) | Parar; checkout byte a byte igual ao inicial; diagnosticar |
| 2 | Uso incorreto | Corrigir o comando |
| 3 | **Restauro falhado** (`RestoreFailed`, que não é `CutoverError`), com `stage`, `trigger` e `restore_error` | Parar; não fazer commit; o checkout pode ter alterações parciais. Como o corte só corre num checkout limpo e commitado, `git restore --source=HEAD -- data docs` repõe-no; confirmar com `git status` |

`KeyboardInterrupt`/`SystemExit` depois das guardas também restauram e só depois se propagam. O script não lê credenciais, não chama o emissor, não faz commit nem push.

## 3. Ensaio sobre os dados reais atuais

`python scripts/rehearse_pr1_cutover.py origin/main` (Python 3.11.18, como em produção), `main` = `f12c06b`, 15/11/2026 09:07 UTC, HEAD `25ef19c`. Fusão sem conflitos. Todos os critérios são exigidos um a um e passaram:

`cutover_ok`, `first_cycle_same_report_no_transitions`, `first_cycle_register_true_no_transitions`, `status_list_identical_register_true_vs_cutover`, `register_true_operational_scope_eligible` e `this_checkout_unchanged`.

| Medida | Snapshot da PR 1 (14/11 22:00) | Ensaio anterior (`2f7a457`, 08:54) | Ensaio atual (`f12c06b`, 09:07) |
|---|---:|---:|---:|
| Hipóteses no registo | 4 789 | 5 163 | 5 164 |
| Promissoras: exploratório → elegível | 33 → 8 | 45 → 11 | 49 → 11 |
| Abandonadas: exploratório → elegível | 6 → 3 | 7 → 5 | 7 → 5 |
| Sinais elegíveis / total | 338 / 450 | 370 / 488 | 370 / 489 |
| Referência Telegram: estados antes → depois | — | 52 → 16 | 56 → 16 |
| Transições silenciadas pela migração | 25 | 38 | 42 (38 promissora→observação, 3 abandonada→observação, 1 nova abandonada) |
| Avisos no 1.º ciclo com o mesmo relatório | 0 | 0 | **0** |
| Avisos no 1.º ciclo com `register=True` | — | 0 | **0** (registo 5 164 → 5 186; `status_list` idêntico; `operational.scope` elegível) |
| Sem migração (contrafactual) | 25 | 38 | 42 transições |
| Walk-forward, ROI seguidas / com odd −2 % | −0,23 % / −1,93 % | −1,82 % / −3,51 % | −1,47 % / −3,16 % |

Mudaram apenas os três ficheiros previstos; o checkout de onde se lançou o ensaio ficou byte a byte igual. Nenhuma tática validada (forte/confirmada) existia, logo nenhum alerta de tática foi silenciado. **Estes números são históricos e mudam com cada recolha; não são validação prospectiva.** As promissoras exploratórias passaram de 33 para 49 em ~11 horas, enquanto as elegíveis ficaram em 11. Isto ilustra a instabilidade das classificações tratada na PR 2.

`register=False` (procedimento) vs `register=True` (produção): o primeiro relatório de produção regista 22 hipóteses novas, porque com sinais elegíveis mudam átomos dependentes do conjunto (`multi_matches`, `earlier_index`). O ensaio exige agora que o `status_list` seja idêntico nos dois modos. Se não for, falha e lista as diferenças (`status_list_differences`), porque o primeiro relatório de produção anunciaria estados que não são evidência nova. O ensaio deve ser repetido imediatamente antes do corte.

## 4. Risco operacional principal: execução antiga depois do corte

`rehearse_pr1_cutover.py` mede o que acontece se o notificador atualmente em `main` (sem migração) correr com o relatório exploratório depois de o estado migrado estar persistido:

| Passo | Resultado medido (`f12c06b`) |
|---|---|
| Execução com código antigo depois do corte | **42 transições falsas** enviadas; a referência elegível é substituída pela exploratória |
| Execução seguinte, já com código novo | **mais 42 transições falsas**: o marcador impede nova migração silenciosa |

A proteção do código novo (relatórios sem `evidence_scope` não alteram a referência) não existe no código antigo. Uma execução em curso pode escrever depois do merge, porque o passo de commit faz `git pull --rebase` antes do push. Por isso:

1. **Não pode existir nenhuma execução com checkout anterior ao merge** quando o estado migrado chega a `main`.
2. **Nunca usar "Re-run" numa execução anterior ao merge**: corre o commit antigo e pode lançar uma nova cadeia.
3. A pausa tem de **impedir o arranque da execução seguinte**, não apenas interromper a atual.

**Porque não cancelar execuções.** Cada execução envia notificações nos primeiros ~20 s e passa ~9 min nas leituras rápidas (`scangoal.watch`), que também notificam e só são registadas no fim. Cancelar a meio perde `watch_sent` (avisos repetidos depois) e as primeiras leituras de mensagens, de que depende a elegibilidade (`had_ft_at_first_sight`, `edited_at_first_sight`). Cancelar uma execução ainda em fila seria inócuo, mas a janela é de poucos segundos.

**Mecanismo de pausa proposto.** O proprietário desativa o workflow *Collect ScanGoal* na interface do GitHub (Actions → Collect ScanGoal → ⋯ → Disable workflow). A execução em curso termina normalmente e regista os seus dados; o passo final `gh workflow run` falha porque o workflow está desativado, e a cadeia pára. O passo de aviso de falha por Telegram está antes desse passo e não corre. As ferramentas GitHub do Claude Code não têm o endpoint de desativação, por isso este passo é do proprietário. A paragem tem de ser confirmada (secção 5, A2) e não presumida.

**Proteção natural adicional.** Se `main` avançar depois do commit congelado, o merge da PR entra em conflito em `data/report.json`, que cada recolha regenera, e o GitHub recusa-o. Isto não substitui a confirmação de que não há execuções ativas: uma execução antiga pode correr *depois* do merge.

**Janela.** Mensagens do bot por hora UTC (12–15/11): 2–11 entre as 23h e as 09h, 24–59 entre as 11h e as 21h. Durante a pausa (~30–40 min) as mensagens são recuperadas depois, mas como vistas tarde, e podem ficar inelegíveis. O corte deve ser feito entre as 23h e as 09h UTC.

## 5. Procedimento proposto (só com autorização expressa)

**Fase A — preparação (sem efeito em produção)**

- A1. Repetir `python scripts/test_offline.py` e `python scripts/rehearse_pr1_cutover.py origin/main` no HEAD a integrar; ambos com código 0 e todos os `criteria` verdadeiros.
- A2. Proprietário desativa *Collect ScanGoal*. Operador confirma: estado do workflow `disabled_manually`; zero execuções `in_progress`, `queued`, `waiting`, `requested` ou `pending` depois de a execução em curso terminar (≤ 9 min); `git ls-remote origin main` igual em duas leituras com pelo menos 3 min de intervalo. Esse SHA é **M**.
- A3. Cópia de segurança: referência `backup/pre-pr1-cutover` em M (requer autorização). O estado anterior fica também no marcador (`previous_statuses`) e nos hashes do relatório de corte.

**Fase B — autorização do proprietário para a janela, SHA da PR e M.**

**Fase C — corte**

```bash
git fetch origin main fix/brain-01-data-eligibility
git switch --detach origin/fix/brain-01-data-eligibility       # checkout limpo da PR
git merge --no-ff -m "Merge main (M) into PR1 for controlled cutover" <M>   # qualquer conflito: parar
python scripts/test_offline.py
python scripts/pr1_cutover.py <M> > audits/pr1-cutover.json
echo $?   # só 0 permite continuar; 1 = parar (ficheiros restaurados e verificados); 3 = parar, ver secção 2
git add data/report.json docs/report.json data/notify_state.json audits/pr1-cutover.json
git commit -m "data: PR1 cutover - eligible report and silent status migration"
git push origin HEAD:fix/brain-01-data-eligibility
```

- C1. Confirmar que o diff da PR face a `main` contém só código, documentação e os três ficheiros de dados.
- C2. Confirmar de novo zero execuções ativas e `main` = M. Retirar de Draft e fazer merge com **commit de merge** (preserva os commits auditados). Código, relatório elegível e estado migrado chegam a `main` num só commit, e o dashboard publica o relatório no formato que lê. Se `main` ≠ M, parar.
- C3. Num checkout novo de `main`: os hashes de `data/report.json`, `docs/report.json` e `data/notify_state.json` coincidem com `sha256_after` de `audits/pr1-cutover.json`; `python -m scangoal.notify --migrate-statuses` imprime `NOTIFY_STATUS_UNCHANGED` e `git status` fica limpo.
- C4. Proprietário reativa o workflow; uma única execução é lançada. O grupo de concorrência `scangoal-write` mantém uma só cadeia.
- C5. Acompanhar as duas primeiras execuções: `NOTIFY_OK sent N`; `sent_total` aumenta exatamente N; marcador inalterado; qualquer aviso de estado corresponde a uma diferença real entre o `status_list` dessa execução e a referência. Podem surgir sinais, resumo diário ou a primeira referência `operational_regime` (silenciosa). Transições históricas: zero.

## 6. Reversão

- **Durante o script:** automática e verificada (secção 2); com código 3, `git restore --source=HEAD -- data docs` no checkout de integração.
- **Antes do merge (A–C1):** produção intacta; basta reativar o workflow. Os commits na branch da PR não afetam `main`.
- **Depois do merge, antes de reativar:** manter a pausa; aplicar a secção "Reversão futura" de `audits/pr1-safe-integration.md` com nova autorização. Reverter o código por commit, preservando os dados atuais; não fazer reset de `data/` nem restaurar o `notify_state.json` antigo.
- **Depois de reativar:** desativar de novo, confirmar zero execuções ativas, copiar o estado mais recente e seguir o mesmo procedimento.

## 7. Reprodução

```bash
git fetch origin main fix/brain-01-data-eligibility
git switch --detach origin/fix/brain-01-data-eligibility
python3.11 scripts/test_offline.py                              # 132 testes
python3.11 -m unittest tests.test_pr1_cutover -v                # só as 20 regressões do corte e do ensaio
python3.11 scripts/rehearse_pr1_cutover.py origin/main > /tmp/rehearsal.json; echo $?   # 0 esperado
# Defeito P1 de #5909958197 no script auditado vs corrigido (repositórios sintéticos):
git show 426489e:scripts/pr1_cutover.py > /tmp/pr1_cutover_426489e.py
python3.11 -I audits/pr1-cutover-evidence/p1-rollback-demo.py /tmp/pr1_cutover_426489e.py .   # 3 ficheiros alterados
python3.11 -I audits/pr1-cutover-evidence/p1-rollback-demo.py scripts/pr1_cutover.py .        # nenhum
```

O ensaio não precisa de credenciais nem de rede além do `git fetch`. Cria e remove uma worktree temporária; o commit de fusão temporário não fica referenciado por nenhuma branch e nada é publicado. Com outro commit de `main` os números mudam; os critérios e os zeros devem manter-se.

## 8. Limitações

- O script corre offline e **não vê o GitHub Actions**: a ausência de execuções antigas é garantida pelo procedimento A2/C2, não pelo código.
- A substituição atómica não é um bloqueio entre processos; mantém-se o pressuposto de um único escritor.
- O rollback cobre o processo do script; uma morte abrupta do processo (SIGKILL, falta de energia) não corre código de restauro. Nesse caso, o checkout limpo e commitado permite `git restore --source=HEAD -- data docs`, e nada foi publicado.
- A primeira referência silencia, por definição, qualquer mudança já presente no relatório do corte.
- Mantém-se a limitação já conhecida de entrega em lote: uma falha a meio pode repetir mensagens legítimas.
- Python 3.11 arredonda um valor de referência do walk-forward de forma diferente (−2,78 vs −2,77 un.); sem efeito em classificações.
- O autor preparou e executou o ensaio; não substitui a auditoria independente.

## 9. Correção após a revisão #5909958197 (SHA auditado `426489e`)

**P1 — exceções nas pós-verificações deixavam alterações parciais.** No commit auditado, o rollback cobria build e migração, mas não `after = contents(root)`, as verificações, os hashes nem a construção do resultado. Demonstração reproduzível (`audits/pr1-cutover-evidence/p1-rollback-demo-output.jsonl`): com uma exceção injetada nas pós-verificações ou no cálculo dos hashes, o script auditado terminava com `OSError` e deixava `data/notify_state.json` (já migrado), `data/report.json` e `docs/report.json` alterados. O corrigido termina em `CutoverAborted` e nenhum ficheiro fica alterado.

Alterações:

- `execute()` contém todo o trabalho depois das guardas, incluindo pós-verificações e resultado; `cutover()` envolve-o num único `try`.
- `restore()` confirma por SHA-256 e lança `RestoreFailed` (código 3, distinto de `CutoverError`) se não conseguir repor ou se os hashes diferirem, com `stage`, `trigger` e `restore_error`.
- `CutoverAborted` (código 1) indica a fase e a causa e garante que o restauro foi verificado.
- Validação de `operational.scope = eligible_bets_only` antes de qualquer escrita.
- `rehearse_pr1_cutover.py`: a função `criteria()` exige explicitamente a igualdade do `status_list` entre o relatório do corte e o primeiro com `register=True`, além do âmbito operacional e da ausência de transições.

Regressões novas (9), todas offline:

- exceção na leitura do estado migrado, na simulação, nas pós-verificações e na construção do resultado; em cada caso o teste prova que os três ficheiros tinham sido escritos e que ficam byte a byte iguais aos originais;
- interrupção depois de escrita parcial;
- falha de restauro distinguida (escrita que falha e escrita silenciosamente ignorada detetada por hash);
- relatório sem bloco operacional elegível;
- códigos de saída 1 e 3 da linha de comandos;
- três testes dos critérios do ensaio, incluindo `status_list` diferente com zero transições, que tem de falhar.

Contra os scripts `426489e`, 8 das 9 regressões novas falham e as 11 anteriores passam. A de interrupção já passava, porque o rollback antigo cobria o build, onde a interrupção é injetada. Parte das falhas resulta de a API nova (`CutoverAborted`, `RestoreFailed`, `criteria`) não existir no código antigo. A prova comportamental do defeito é `p1-rollback-demo`, que usa a mesma API nos dois scripts. Total: **132 testes**.
