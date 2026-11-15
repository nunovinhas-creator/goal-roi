# PR 1 — Corte controlado: script, ensaio e procedimento

**Preparação para auditoria independente. Nada foi executado em produção.** Nenhum workflow foi desativado ou cancelado, nenhuma branch de backup foi criada, `main` e os dados de produção não foram alterados, a migração real não foi executada, a PR continua Draft e nenhuma mensagem Telegram foi enviada. Cada ação de produção abaixo exige autorização expressa do proprietário para uma janela concreta.

Este documento complementa `audits/pr1-safe-integration.md` (mecanismo e reversão já revistos em #5907675027) com: um script que automatiza os passos 4–6 com verificações, um ensaio reproduzível sobre os dados reais atuais e a análise do risco operacional que falta controlar.

## 1. Ficheiros

| Ficheiro | Papel |
|---|---|
| `scripts/pr1_cutover.py` | Corte: relatório elegível + migração silenciosa, com guardas, verificações e restauro |
| `scripts/rehearse_pr1_cutover.py` | Ensaio do corte numa worktree temporária fundida com um commit de `main`; mede os cenários de risco |
| `tests/test_pr1_cutover.py` | 11 regressões offline em repositórios git sintéticos |
| `audits/pr1-cutover-rehearsal.json` | Resultado do ensaio sobre `main` `2f7a4578bcdec2a127db3ec4b60b4960dd24d8e4` |
| `audits/pr1-cutover-evidence/` | Evidência bruta da primeira passagem (ver README) |

Nenhum ficheiro em `scangoal/`, `data/`, `docs/` ou `.github/` foi alterado.

## 2. O que `scripts/pr1_cutover.py` garante

**Guardas antes de escrever qualquer ficheiro** (falha → código 1, ficheiros intactos):

1. `data/` e `docs/` sem alterações nem ficheiros por registar.
2. O commit congelado de `main` é antepassado do HEAD e `data/`, `docs/report.json` e `docs/bets.csv` são byte a byte iguais aos desse commit. **Um snapshot antigo nunca pode ser cortado por cima de dados mais recentes.**
3. `notify_state.json` existe, é JSON válido (objeto) e ainda não tem o marcador `eligible_statuses_v1`: a referência só se define uma vez; repetir o corte depois de persistido é recusado.
4. `SCANGOAL_DATA_DIR`, se definido, aponta para o `data/` do checkout.

**Trabalho** (rede bloqueada durante toda a execução; uma tentativa de ligação aborta e restaura):

- `report.build(None, register=False)`: sem crescimento do registo, sem CSV, sem coletores, LLM ou notificações. O relatório tem de ter `brain.evidence_scope = eligible_bets_only` e `status_list` válido **antes** de ser escrito.
- Escreve `data/report.json` (mesmo formato de `report.main()`) e copia para `docs/report.json`.
- `notify.migrate_file()` duas vezes: exige `NOTIFY_STATUS_MIGRATED` e depois `NOTIFY_STATUS_UNCHANGED` com o ficheiro byte a byte igual.
- Simula o ciclo seguinte com `notify.messages()` sobre uma cópia do estado, **sem emissor**: exige zero transições de estado.

**Pós-verificações**: só mudam `data/report.json`, `docs/report.json` e `data/notify_state.json`; registo de hipóteses inalterado; `statuses` e `baseline_statuses` iguais ao `status_list`; `previous_statuses` igual ao estado anterior; `watch_sent`, `sent_total`, `hello`, `summary_day`, `last_run_utc`, `regime`, `operational_regime` e restantes campos preservados.

**Restauro**: qualquer verificação falhada ou exceção depois das guardas repõe byte a byte todos os ficheiros de `data/` e os dois de `docs/` (e remove ficheiros criados), confirmando por hash. O script não lê credenciais, não chama o emissor, não faz commit nem push.

## 3. Ensaio sobre os dados reais atuais

`python scripts/rehearse_pr1_cutover.py origin/main` (Python 3.11.18, como em produção), `main` = `2f7a457`, 15/11/2026 08:54 UTC. Fusão sem conflitos.

| Medida | Snapshot da PR 1 (14/11 22:00) | Ensaio (15/11 08:54) |
|---|---:|---:|
| Hipóteses no registo | 4 789 | 5 163 |
| Promissoras: exploratório → elegível | 33 → 8 | 45 → 11 |
| Abandonadas: exploratório → elegível | 6 → 3 | 7 → 5 |
| Sinais elegíveis / total | 338 / 450 | 370 / 488 |
| Referência Telegram: estados antes → depois | — | 52 → 16 |
| Transições silenciadas pela migração | 25 | 38 (34 promissora→observação, 3 abandonada→observação, 1 nova abandonada) |
| Avisos no 1.º ciclo com o mesmo relatório | 0 | **0** |
| Avisos no 1.º ciclo com `register=True` | — | **0** (registo 5 163 → 5 185; `status_list` idêntico) |
| Sem migração (contrafactual) | 25 | 38 transições |
| Walk-forward, ROI seguidas / com odd −2 % | −0,23 % / −1,93 % | −1,82 % / −3,51 % |

Todas as verificações do script passaram; mudaram apenas os três ficheiros previstos; o checkout de onde se lançou o ensaio ficou byte a byte igual. Nenhuma tática validada (forte/confirmada) existia, logo nenhum alerta de tática foi silenciado. **Estes números são históricos e mudam com cada recolha; não são validação prospectiva.** O aumento de 33 para 45 promissoras exploratórias em ~11 horas ilustra a instabilidade das classificações tratada na PR 2.

`register=False` (procedimento) vs `register=True` (produção): o primeiro relatório de produção regista 22 hipóteses novas, porque com sinais elegíveis mudam átomos dependentes do conjunto (`multi_matches`, `earlier_index`). Medido: nenhuma nasce fora de observação e o `status_list` fica idêntico, por isso não há avisos. Isto vale para estes dados; o ensaio deve ser repetido imediatamente antes do corte.

## 4. Risco operacional principal: execução antiga depois do corte

`rehearse_pr1_cutover.py` mede o que acontece se o notificador atualmente em `main` (sem migração) correr com o relatório exploratório depois de o estado migrado estar persistido:

| Passo | Resultado medido |
|---|---|
| Execução com código antigo depois do corte | **38 transições falsas** enviadas; a referência elegível é substituída pela exploratória |
| Execução seguinte, já com código novo | **mais 38 transições falsas**: o marcador impede nova migração silenciosa |

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

- A1. Repetir `python scripts/test_offline.py` e `python scripts/rehearse_pr1_cutover.py origin/main` no HEAD a integrar; ambos com código 0.
- A2. Proprietário desativa *Collect ScanGoal*. Operador confirma: estado do workflow `disabled_manually`; zero execuções `in_progress`, `queued`, `waiting`, `requested` ou `pending` depois de a execução em curso terminar (≤ 9 min); `git ls-remote origin main` igual em duas leituras com pelo menos 3 min de intervalo. Esse SHA é **M**.
- A3. Cópia de segurança: referência `backup/pre-pr1-cutover` em M (requer autorização). O estado anterior fica também no marcador (`previous_statuses`) e nos hashes do relatório de corte.

**Fase B — autorização do proprietário para a janela, SHA da PR e M.**

**Fase C — corte**

```bash
git fetch origin main fix/brain-01-data-eligibility
git switch --detach origin/fix/brain-01-data-eligibility       # checkout limpo da PR
git merge --no-ff -m "Merge main (M) into PR1 for controlled cutover" <M>   # qualquer conflito: parar
python scripts/test_offline.py
python scripts/pr1_cutover.py <M> > audits/pr1-cutover.json   # código 0 e "ok": true; caso contrário parar (ficheiros já restaurados)
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

- **Antes do merge (A–C1):** produção intacta; basta reativar o workflow. Os commits na branch da PR não afetam `main`.
- **Depois do merge, antes de reativar:** manter a pausa; aplicar a secção "Reversão futura" de `audits/pr1-safe-integration.md` com nova autorização. Reverter o código por commit, preservando os dados atuais; não fazer reset de `data/` nem restaurar o `notify_state.json` antigo.
- **Depois de reativar:** desativar de novo, confirmar zero execuções ativas, copiar o estado mais recente e seguir o mesmo procedimento.

## 7. Reprodução

```bash
git fetch origin main fix/brain-01-data-eligibility
git switch --detach origin/fix/brain-01-data-eligibility
python3.11 scripts/test_offline.py                              # 123 testes
python3.11 -m unittest tests.test_pr1_cutover -v                # só as 11 regressões do corte
python3.11 scripts/rehearse_pr1_cutover.py origin/main > /tmp/rehearsal.json; echo $?   # 0 esperado
```

O ensaio não precisa de credenciais nem de rede além do `git fetch`. Cria e remove uma worktree temporária; o commit de fusão temporário não fica referenciado por nenhuma branch e nada é publicado. Com outro commit de `main` os números mudam; as verificações e os zeros devem manter-se.

## 8. Limitações

- O script corre offline e **não vê o GitHub Actions**: a ausência de execuções antigas é garantida pelo procedimento A2/C2, não pelo código.
- A substituição atómica não é um bloqueio entre processos; mantém-se o pressuposto de um único escritor.
- A primeira referência silencia, por definição, qualquer mudança já presente no relatório do corte.
- Mantém-se a limitação já conhecida de entrega em lote: uma falha a meio pode repetir mensagens legítimas.
- Python 3.11 arredonda um valor de referência do walk-forward de forma diferente (−2,78 vs −2,77 un.); sem efeito em classificações.
- O autor preparou e executou o ensaio; não substitui a auditoria independente.
