# PR 3 — Corte controlado: script, ensaios e procedimento

**Preparação para auditoria independente (revisão #5912461690). Nada foi executado em produção.**
- Nenhum workflow foi desativado nem alterado.
- `main`, `data/` de produção e os secrets não foram tocados.
- A migração real não foi executada e nenhuma mensagem Telegram foi enviada.
- A certificação não foi ativada e a PR continua Draft.

Cada ação de produção abaixo exige autorização expressa do proprietário para uma janela concreta.

Segue o modelo dos cortes das PRs 1 e 2 (`audits/pr1-cutover-runbook.md`, `audits/pr2-cutover-runbook.md`). O corte faz três coisas:
- publica as regras `chronological_eprocess_v3`;
- cria **silenciosamente** a referência Telegram dessas regras sob a chave própria `status_rules:chronological_eprocess_v3`;
- preserva os dados atuais, os marcadores das PRs 1 (`eligible_statuses_v1`) e 2 (`status_rules:independent_games_hysteresis_v1`), os contadores e o histórico de entregas.

**Integração e certificação são passos separados.** O corte integra o código da PR 3 com a certificação **desligada**: não existe `data/certification_ledger.json` e o estado de validação é `not_activated`. Nenhuma tática é anunciada como validada. A ativação científica tem um plano próprio (`audits/pr3-certification-activation-plan.md`) e exige outra autorização, posterior a este corte.

## 1. Ficheiros

| Ficheiro | Papel |
|---|---|
| `scripts/pr3_cutover.py` | Corte da PR 3: idempotente, transacional e com guardas; inclui `push_preflight` |
| `scripts/rehearse_pr3_cutover.py` | Ensaio numa worktree temporária fundida com um commit de `main`: corte, Telegram, falhas, janela de publicação e checkout |
| `tests/test_pr3_cutover.py` | 22 regressões offline em repositórios git sintéticos |
| `audits/pr3-cutover-evidence/mutation_check.py` (+ `mutation_check.txt`) | Teste de mutação: desativa cada guarda ou verificação numa cópia e exige um teste a falhar |
| `audits/pr3-cutover-evidence/collect-fetch-depth-0.patch` | Alteração **proposta, não aplicada** ao workflow (checkout completo), só para a ativação |
| `audits/pr3-cutover-rehearsal.json` | Resultado do ensaio sobre `main` real |
| `audits/pr3-certification-activation-plan.md` | Plano da ativação científica (passo separado) |
| `audits/pr3-cutover-window-plan.md` | Checklist da janela por fases A–D com autorizações separadas (plano aceite depois da revisão #5912630773) |
| `scripts/post_cutover_check.py` (+ `tests/test_post_cutover_check.py`, 10 testes) | Verificação só de leitura das duas primeiras execuções depois do corte (fase D) |

## 2. O que `scripts/pr3_cutover.py` garante

Reutiliza, **sem modificar**, as primitivas já auditadas de `scripts/pr1_cutover.py`:
- guardas de snapshot;
- transação com restauro byte a byte verificado por SHA-256;
- `CutoverAborted` e `RestoreFailed`;
- rede bloqueada durante o corte;
- fixação de `SCANGOAL_DATA_DIR`.

### Guardas antes de qualquer escrita

Em caso de recusa: código 1, `"restored": false`, ficheiros intactos.

1. **Regras fixadas:** `brain.STATUS_RULES` tem de ser `chronological_eprocess_v3`. O script só faz este corte.
2. **Checkout limpo e snapshot fresco:**
   - `data/` e `docs/` sem alterações;
   - o commit congelado M é antepassado do HEAD;
   - `data/`, `docs/report.json` e `docs/bets.csv` são byte a byte iguais aos de M.
3. **Idempotência:** se a chave da PR 3 já existir, o script não escreve nada.
   - Se o estado for coerente (relatório com as regras da PR 3, certificação desligada, `statuses` = `status_list`), devolve `already_applied` com código 0.
   - Se não for coerente (marcador presente com relatório ou estados diferentes), recusa.
4. **PRs anteriores integradas:** os marcadores da PR 1 e da PR 2 têm de existir.
5. **Código antigo vs novo:** o `data/report.json` de M tem de estar no formato da PR 2 (`status_rules` = `independent_games_hysteresis_v1`). Se algum escritor com código da PR 3 já tiver corrido, o corte recusa.
6. **Sem registo de certificação:** recusa se `data/certification_ledger.json` existir em M ou no checkout.
7. **Relatório construído** (`register=False`):
   - `status_rules` exato;
   - `notify.migration_key()` = chave da PR 3, o que prova que `brain` e `notify` concordam;
   - âmbito operacional elegível e `status_list` válido;
   - `validation.state = not_activated`, sem `certified` e sem `ledger_valid`;
   - nenhum estado validado (`forte`/`confirmada`);
   - o checkout fica byte a byte igual depois do build.
8. **`register=True` numa cópia** de `data/` (como o passo de relatório em produção):
   - mesmas verificações e `status_list` **idêntico**;
   - **nenhum registo de certificação criado**;
   - o checkout fica intacto.

### Trabalho

- Escreve `data/report.json` e `docs/report.json`.
- Corre `notify.migrate_file()` duas vezes: exige `NOTIFY_STATUS_MIGRATED` e depois `NOTIFY_STATUS_UNCHANGED`, com o ficheiro byte a byte igual.
- **Simula os dois ciclos seguintes** sobre o mesmo estado, sem emissor:
  - ciclo 1 com o relatório do corte;
  - ciclo 2 com o relatório `register=True`.
  
  Em cada ciclo têm de dar **zero transições** e **zero mensagens de tática validada** (`🟢 ALERTA`, `✅ Tática validada`).

### Pós-verificações

- Só mudam os 3 ficheiros previstos; o registo de hipóteses fica inalterado; não aparece o registo de certificação.
- Os marcadores das PRs 1 e 2 ficam byte a byte iguais; as chaves passam a ser as anteriores mais a da PR 3.
- `statuses` e `baseline_statuses` coincidem com o `status_list`, e `previous_statuses` com o estado anterior.
- `watch_sent`, `sent_total`, `hello`, `summary_day`, `last_run_utc`, `regime` e restantes campos de entrega ficam preservados; não aparece nenhum campo novo no topo do estado.

### Interrupção e falhas de restauro

- Qualquer exceção (incluindo `KeyboardInterrupt`) ou verificação falhada depois das guardas repõe todos os ficheiros e confirma por hash. `CutoverAborted` indica a fase em que falhou; código 1.
- A seguir, uma nova execução corre de raiz.
- Se o restauro falhar: `RestoreFailed`, código 3. Parar e correr `git restore --source=HEAD -- data docs` no checkout de integração.

### `push_preflight(remote_main, frozen)` antes de qualquer push ou merge

Devolve NO-GO se:
- o `main` remoto deixou de ser M (commit concorrente);
- o HEAD não é um fast-forward desse `main`;
- o checkout não está limpo.

### Testes e mutações

**22 testes** em `tests/test_pr3_cutover.py`:
- corte com sucesso: marcadores, histórico, dois ciclos e uma mudança legítima notificada uma vez;
- repetição idempotente e marcador incoerente recusado;
- marcadores das PRs 1 e 2 obrigatórios;
- relatório de M no formato da PR 2;
- registo de certificação recusado em M, no checkout e quando criado por `register=True`;
- relatório com certificação ativada, certificado, `forte` ou regras da PR 2 recusado;
- snapshot desatualizado ou sujo; regras fixadas;
- mensagem validada ou transição em qualquer dos ciclos leva a restauro;
- execução interrompida (`KeyboardInterrupt`, `RuntimeError`, `OSError`) com restauro e nova execução;
- tentativa de rede; restauro falhado com código 3;
- migração defeituosa (marcadores, contadores, campos novos, outros ficheiros, registo, ledger);
- segunda migração diferente de `UNCHANGED`;
- builds que escrevem ou divergem; desacordo sobre a chave; âmbito ou estados inválidos;
- `push_preflight` com `main` movido ou sem fast-forward;
- risco de código antigo documentado.

**Teste de mutação** (`audits/pr3-cutover-evidence/mutation_check.py`):
- exige primeiro que a bateria **não mutada** passe;
- desativa numa cópia cada uma das 29 guardas, verificações, passos de restauro e verificações do preflight, mais 3 pares redundantes;
- resultado: **32 de 32 mutantes apanhados**.

## 3. Ensaios sobre `main` real

`python3.11 scripts/rehearse_pr3_cutover.py origin/main` (Python 3.11.18), `main` = `f71b34f`, HEAD `bc813aa` (o commit seguinte só acrescenta este resultado e números de documentação). Todos os `criteria` são verdadeiros e `ok: true`.

### Corte e Telegram (item 3 da revisão)

| Medida | Resultado |
|---|---|
| Fusão com `main` | sem conflitos |
| Corte | `ok: true`, código 0; só `data/report.json`, `docs/report.json` e `data/notify_state.json` mudam |
| Referência Telegram | 4 estados, todos iguais aos da PR 2: 0 removidos, 0 acrescentados, 0 mudados |
| Ciclo simulado 1 (relatório do corte) | **0 transições, 0 mensagens validadas** |
| Ciclo simulado 2 (`register=True` reconstruído) | **0 transições, 0 mensagens validadas**; `status_list` idêntico; estado `not_activated`; nenhum registo criado |
| Marcadores das PRs 1 e 2 | byte a byte iguais |
| Histórico de entregas | preservado (`sent_total` 303, `watch_sent` 112) |
| Sem a migração da PR 3 (contrafactual) | 0 transições |
| Código antigo depois do corte | 0 transições; a referência da PR 3 não é substituída |
| Mudança legítima posterior (1 saída + 1 promoção sintética) | 2 avisos; repetição: 0 |

Neste momento as regras da PR 3 não mudam nenhum estado em produção (as 4 promissoras mantêm-se). A migração silenciosa protege, ainda assim, contra qualquer diferença que surja entre o ensaio e a janela.

### Falhas (item 6 da revisão)

| Ensaio | Resultado |
|---|---|
| Execução interrompida a meio (fase `simulate_next_cycles`) | abortada e restaurada, hashes verificados; nova execução `ok` |
| Repetição depois do sucesso | `already_applied`, nada escrito |
| Integridade de dados | o commit do corte só toca nos 3 ficheiros; `data/`, `docs/report.json` e `docs/bets.csv` do commit de integração iguais a `main`; mensagens Telegram idênticas |
| Preflight com `main` congelado | GO (`[]`) |
| Commit concorrente em `main` depois de M | NO-GO: `remote main moved…` e `HEAD is not a fast-forward…` |
| Escritor antigo (PR 2) com `git pull --rebase` sobre o corte | conflito em `data/report.json`: o push falha e nada se perde (falha segura) |
| Reversão por `git revert` do merge | ficheiros de `main` repostos; marcador da PR 3 removido; marcador da PR 2 mantido |

### Janela de publicação de 30 minutos e histórico git (itens 4 e 6)

Num repositório sintético com registo ativo, `certification.history_proof` usa a data de *committer*:

| Situação | Resultado |
|---|---|
| Registo publicado 5 min depois da ativação | aceite |
| Publicado 29 min depois | aceite |
| Publicado 31 min depois | `genesis published out of time` |
| Commit refeito por `git pull --rebase` 12 min depois | aceite (a data de committer passa a ser a do rebase) |
| Rebase 40 min depois | recusado (fora da janela) |

### Checkout (item 4)

Clone a partir do GitHub, medido:

| Checkout | Tempo | `.git` | Commits visíveis | `history_proof` sem registo |
|---|---|---|---|---|
| raso (`actions/checkout` atual, profundidade 1) | 0,96 s | 0,56 MiB | 1 | `history unavailable: shallow repository` |
| completo (`fetch-depth: 0`) | 1,87 s | 4,32 MiB | 1078 | `ledger never published` |

Conclusões:
- Com o checkout raso, a certificação fica sempre bloqueada (fail-closed): é compatível com a integração sem ativação.
- O checkout completo é necessário só para a ativação; custa cerca de 1 s e 4 MiB por execução, a crescer com a recolha.
- A alteração está preparada em `collect-fetch-depth-0.patch` (`git apply --check` passa) e **não foi aplicada**.

Os números mudam com cada recolha. O ensaio tem de ser repetido no HEAD a integrar e sobre o `main` congelado (A1 e C).

## 4. Riscos e proteções

1. **Código antigo depois do corte.** Medido: 0 transições falsas, porque os estados da PR 3 coincidem hoje com os da PR 2. A proteção continua a ser o procedimento: workflow desativado, zero execuções ativas e pendentes, **nunca "Re-run"** de execuções anteriores ao merge. A guarda 5 recusa o corte se já tiver corrido código da PR 3.
2. **Commits concorrentes:** guarda 2, `push_preflight` (C2) e conflito natural em `data/report.json`.
3. **Execução interrompida:** transação com restauro verificado; nova execução de raiz; idempotência.
4. **Ativação acidental da certificação:** guardas 6–8; com checkout raso, `history_proof` falha de qualquer modo; o notificador só anuncia táticas com estado `active`, `ledger_valid` e certificado.
5. **Sinais sem prova de leitura** (P2-K): não existem na vista `as_of`. Impacto atual: 0 dos 565 sinais de produção.
6. **Janela:** preferir 23h–09h UTC. As mensagens emitidas durante a pausa são lidas mais tarde; o corte não recolhe nem envia.

## 5. Procedimento (só com autorização expressa)

### Fase A — preparação (sem efeito em produção)

**A1.** No HEAD a integrar, correr:
- `python3.11 scripts/test_offline.py`;
- `python3.11 audits/pr3-cutover-evidence/mutation_check.py`;
- `python3.11 scripts/rehearse_pr3_cutover.py origin/main`.

Os três têm de sair com código 0 e todos os `criteria` têm de ser verdadeiros. O CI da PR tem de estar verde.

**A2.** O proprietário desativa *Collect ScanGoal*. O operador confirma **externamente** (GitHub):
- o estado `disabled_manually`;
- que a execução em curso terminou sozinha (≤ 15 min);
- zero execuções `in_progress`, `queued`, `waiting`, `requested` ou `pending`;
- `git ls-remote origin main` igual em duas leituras com pelo menos 3 minutos de intervalo. Esse SHA é **M**.

**A3.** Cópia de segurança **obrigatória**: referência `backup/pre-pr3-cutover` em M, verificada com `git ls-remote` antes da fase B (`audits/pr3-cutover-window-plan.md`, A5).

### Fase B — autorização

O proprietário autoriza a janela, o SHA da PR e M.

### Fase C — corte

```bash
git fetch origin main fix/brain-03-chronological-validity
git switch --detach origin/fix/brain-03-chronological-validity
git merge --no-ff -m "Merge main (M) into PR3 for controlled cutover" <M>   # qualquer conflito: parar
python3.11 scripts/test_offline.py
python3.11 scripts/rehearse_pr3_cutover.py <M>                              # criteria todos verdadeiros
python3.11 scripts/pr3_cutover.py <M> > audits/pr3-cutover.json
echo $?   # 0 = continuar; 1 = parar (restaurado e verificado); 3 = parar (restauro falhado, ver secção 2)
git add data/report.json docs/report.json data/notify_state.json audits/pr3-cutover.json
git commit -m "data: PR3 cutover - status rules chronological_eprocess_v3, silent re-baseline, certification off"
python3.11 -c "import sys; sys.path.insert(0,'scripts'); import pr3_cutover as c, subprocess as s; \
r=s.run(['git','ls-remote','origin','main'],capture_output=True,text=True).stdout.split()[0]; \
p=c.push_preflight(c.ROOT, r, '<M>'); print(p or 'GO'); sys.exit(1 if p else 0)"   # NO-GO: parar
git push origin HEAD:fix/brain-03-chronological-validity
```

**C1.** Verificar o diff e o CI:
- o diff face a `main` contém só código, documentação e os três ficheiros de dados;
- **não** contém `data/certification_ledger.json` nem alterações a `.github/workflows/`;
- o CI está verde, sem `[skip ci]` no commit de corte.

**C2.** Repetir o preflight: zero execuções ativas e `main` = M. Tirar de Draft e fazer merge com **commit de merge**, fixando o HEAD esperado. Se `main` ≠ M ou houver NO-GO, parar.

**C3.** Num checkout novo de `main`, confirmar:
- os hashes dos três ficheiros coincidem com `sha256_after`;
- `python -m scangoal.notify --migrate-statuses` imprime `NOTIFY_STATUS_UNCHANGED` e `git status` fica limpo;
- existem as três chaves de migração e os marcadores das PRs 1 e 2 estão iguais aos de M;
- não existe registo de certificação.

**C4.** O proprietário reativa o workflow; é lançada uma única execução. Só com a autorização da fase D (`audits/pr3-cutover-window-plan.md`).

**C5.** Acompanhar as duas primeiras execuções com `python3.11 scripts/post_cutover_check.py <antes> <depois>` (código 0 obrigatório):
- `NOTIFY_OK sent N` e `sent_total` com +N;
- marcadores inalterados;
- zero transições históricas e zero mensagens de tática validada;
- `report.json` com `status_rules = chronological_eprocess_v3` e `validation.state = not_activated`.

## 6. Reversão

- **Antes do merge (A–C1):** a produção está intacta; basta reativar o workflow.
- **Depois do merge, antes de reativar:** manter a pausa e reverter por commit (`git revert -m 1 <merge>`), preservando os dados posteriores. O ensaio confirma:
  - os ficheiros de `main` são repostos;
  - o marcador da PR 3 desaparece e o da PR 2 fica;
  - por isso o código da PR 2 volta a comparar com a sua própria referência.
  
  Como hoje os estados coincidem, isso não produz transições. Se divergirem na altura, aplica-se o procedimento de reversão silenciosa descrito no runbook da PR 2 (§6), com autorização própria.
- **Depois de reativar:** desativar de novo, confirmar zero execuções ativas e seguir o ponto anterior sobre o estado mais recente. Nunca fazer reset de `data/` nem restaurar um `notify_state.json` antigo.
- **Certificação:** não há nada a reverter, porque não foi ativada.

## 7. Reprodução

```bash
git fetch origin main fix/brain-03-chronological-validity
git switch --detach origin/fix/brain-03-chronological-validity
python3.11 scripts/test_offline.py                                    # 285 testes
python3.11 -m unittest discover -s tests -p test_pr3_cutover.py -v    # 22 regressões do corte
python3.11 audits/pr3-cutover-evidence/mutation_check.py              # 32 de 32 mutantes apanhados
python3.11 scripts/rehearse_pr3_cutover.py origin/main > /tmp/pr3-rehearsal.json; echo $?   # 0 esperado
git apply --check audits/pr3-cutover-evidence/collect-fetch-depth-0.patch                   # só verifica, não aplica
```

## 8. Limitações

- O script corre offline e **não vê o GitHub Actions**: a ausência de escritores é garantida pelo procedimento (A2/C2), não pelo código.
- O preflight reduz, mas não elimina, a corrida entre a leitura de `main` e o merge; o commit de merge com HEAD fixado e o conflito em `data/report.json` são as barreiras finais.
- A substituição atómica não é um bloqueio entre processos; mantém-se o pressuposto de um único escritor.
- Uma morte abrupta (SIGKILL) não corre o restauro; recupera-se com `git restore` no checkout de integração, e nada foi publicado.
- Os ensaios do ciclo simulado usam os relatórios do corte; não simulam novos resultados reais. As mudanças legítimas são sintéticas.
- A janela de 30 minutos foi ensaiada num repositório sintético; o tempo real entre a ativação e a publicação depende da execução do workflow.
- O autor preparou e executou os ensaios; não substituem a auditoria independente.
