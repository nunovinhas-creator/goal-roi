# PR 2 — Corte controlado: script, ensaio e procedimento

**Preparação para auditoria independente. Nada foi executado em produção.** Nenhum workflow foi desativado, `main` e os dados de produção não foram alterados, a migração real não foi executada, a PR continua Draft e nenhuma mensagem Telegram foi enviada. Cada ação de produção abaixo exige autorização expressa do proprietário para uma janela concreta.

Segue o modelo do corte da PR 1 (`audits/pr1-cutover-runbook.md`). O objetivo é publicar as regras `independent_games_hysteresis_v1` e criar **silenciosamente** a referência Telegram dessas regras sob a chave própria `status_rules:independent_games_hysteresis_v1`. Preserva os dados atuais, o marcador da PR 1 (`eligible_statuses_v1`), os contadores e o histórico de entregas.

## 1. Ficheiros

| Ficheiro | Papel |
|---|---|
| `scripts/pr2_cutover.py` | Corte da PR 2, transacional e com guardas |
| `scripts/rehearse_pr2_cutover.py` | Ensaio numa worktree temporária fundida com um commit de `main`; mede transições retroativas, risco de código antigo e mudanças legítimas |
| `tests/test_pr2_cutover.py` | 19 regressões offline em repositórios git sintéticos |
| `audits/pr2-cutover-evidence/mutation_check.py` (+ output) | Teste de mutação: desativa cada guarda ou verificação numa cópia e exige um teste a falhar |
| `audits/pr2-cutover-rehearsal.json` | Resultado do ensaio sobre `main` `273a5b722f491a599d80bec1f87dad04a02fb80b` |

Nenhum ficheiro de produção (`data/`, `docs/report.json`, `docs/bets.csv`) nem nenhum workflow foi alterado.

## 2. O que `scripts/pr2_cutover.py` garante

Reutiliza, **sem modificar**, as primitivas de `scripts/pr1_cutover.py` já auditadas (#5909958197 e #5909993975): guardas de snapshot, transação com restauro byte a byte verificado por SHA-256, `CutoverAborted`/`RestoreFailed`, rede bloqueada e fixação de `SCANGOAL_DATA_DIR`.

**Guardas antes de qualquer escrita.** Recusa: código 1, `"restored": false`, ficheiros intactos.

1. `data/` e `docs/` sem alterações nem ficheiros por registar.
2. **Snapshot desatualizado:** o commit congelado de `main` tem de ser antepassado do HEAD, e `data/`, `docs/report.json` e `docs/bets.csv` têm de ser byte a byte iguais aos desse commit.
3. `notify_state.json` é válido e **tem** o marcador `eligible_statuses_v1` (a PR 1 já está integrada).
4. A chave `status_rules:independent_games_hysteresis_v1` **não existe** ainda: a referência da PR 2 só se cria uma vez.
5. **Código antigo vs novo:** o `data/report.json` do commit congelado tem de estar no formato da PR 1 (âmbito elegível e sem `status_rules`). Se algum escritor com código da PR 2 já tiver corrido em produção, o corte recusa.
6. O relatório construído (`register=False`) tem de ter:
   - `brain.evidence_scope` e `operational.scope` elegíveis;
   - `brain.status_rules` exatamente igual à regra deste código;
   - `notify.migration_key()` igual à chave da PR 2, o que garante que `brain` e `notify` concordam;
   - um `status_list` válido.
   - O checkout tem de ficar byte a byte igual depois deste build: nenhum build pode escrever no checkout.
7. **Divergência `register=False` vs `register=True`:** um segundo relatório com `register=True` é construído numa **cópia temporária** de `data/`, como faz o passo de relatório em produção. Tem de passar as mesmas verificações e ter um `status_list` **idêntico**. O checkout tem de ficar byte a byte igual depois dessa construção.

**Trabalho:**
- escreve `data/report.json` e `docs/report.json`;
- corre `notify.migrate_file()` duas vezes, exigindo `NOTIFY_STATUS_MIGRATED` e depois `NOTIFY_STATUS_UNCHANGED`, com o ficheiro byte a byte igual;
- simula o ciclo seguinte com os **dois** relatórios, sem emissor: tem de haver zero transições em cada um.

**Pós-verificações:**
- só mudam os 3 ficheiros previstos e o registo de hipóteses fica inalterado;
- o marcador da PR 1 fica byte a byte igual e as chaves de migração passam a ser as anteriores mais a da PR 2;
- `statuses` e `baseline_statuses` coincidem com o `status_list`, e `previous_statuses` com o estado anterior;
- `watch_sent`, `sent_total`, `hello`, `summary_day`, `last_run_utc`, `regime`, `operational_regime` e restantes campos ficam preservados;
- não aparece nenhum campo novo no topo do estado.

**Escritas parciais e falhas de restauro:**
- qualquer exceção ou verificação falhada depois das guardas repõe todos os ficheiros e confirma por hash;
- `CutoverAborted` indica a fase em que falhou e sai com código 1;
- se o próprio restauro falhar, sai com `RestoreFailed` e código 3: parar e correr `git restore --source=HEAD -- data docs` no checkout de integração.

### Testes e mutações

19 testes em `tests/test_pr2_cutover.py`:
- corte com sucesso;
- silêncio das 11 transições retroativas e notificação única de mudanças legítimas;
- recusas: repetição, sem marcador da PR 1, snapshot desatualizado, sujo ou desconhecido, relatório de M já no formato da PR 2 ou anterior à PR 1, relatório sem as regras novas ou com blocos inválidos, `brain`/`notify` em desacordo sobre a chave;
- divergência `register=True` e escrita no checkout durante qualquer dos builds;
- transições no ciclo simulado;
- migração defeituosa que toca no marcador da PR 1, nos contadores, acrescenta campos, escreve outros ficheiros ou faz crescer o registo;
- segunda migração que não reporta `UNCHANGED`;
- exceções depois das escritas (3 fases);
- restauro falhado e códigos de saída 1, 2 e 3;
- tentativa de rede;
- risco de código antigo documentado.

**Testes de mutação** (`audits/pr2-cutover-evidence/mutation_check.py`, saída versionada ao lado):
- o script começa por exigir que a bateria **não mutada** passe;
- depois desativa, numa cópia temporária, cada uma das 20 guardas, verificações e passos de restauro, e ainda um par: **21 de 21 mutantes apanhados**.

Durante o exercício surgiram vários reforços:
- verificações redundantes e pós-verificações não exercidas levaram a testes novos;
- foram acrescentadas ao script as verificações `no_unexpected_fields` e "nenhum build escreve no checkout";
- uma execução inválida, em que testes novos falhavam na base, foi detetada e motivou a verificação de base obrigatória.

## 3. Ensaio sobre `main` atual

`python scripts/rehearse_pr2_cutover.py origin/main` (Python 3.11.18), `main` = `273a5b7`, HEAD `742dffd`. Todos os `criteria` são verdadeiros.

| Medida | Resultado |
|---|---|
| Fusão com `main` | sem conflitos |
| Corte | `ok: true`; só `data/report.json`, `docs/report.json` e `data/notify_state.json` mudam |
| Referência Telegram | 20 → 13 estados; **11 transições retroativas silenciadas** (4 promissora→observação, 5 abandonada→observação, 2 novas promissoras) |
| Primeiro ciclo (relatório do corte / `register=True` reconstruído) | **0 / 0 transições**; `status_list` idêntico |
| Marcador da PR 1 | byte a byte igual |
| Histórico de entregas | preservado (`sent_total` 270, `watch_sent` 93) |
| Sem a migração da PR 2 (contrafactual) | 11 transições |
| **Código antigo depois do corte** (notificador atual de `main` com relatório da PR 1) | **11 transições falsas**; referência da PR 2 substituída; a execução seguinte com código novo envia **mais 11** (a chave da PR 2 já existe, por isso não há nova migração) |
| Mudança legítima posterior (1 regresso a observação + 1 promoção sintética) | 2 avisos; repetição: 0 |
| Confirmação das 4 promoções pendentes (Kashiwa — Kawasaki) | 4 avisos; repetição: 0 |
| Checkout do ensaio | byte a byte igual |

Os números mudam com cada recolha. O ensaio tem de ser repetido no HEAD a integrar e sobre o `main` congelado (A1).

## 4. Riscos e proteções

1. **Execução de código antigo depois do corte.** É o risco principal, medido com o notificador real de `main` (11 + 11 transições falsas). O script não vê o GitHub Actions: a proteção é o procedimento. É preciso desativar o workflow, deixar a execução em curso terminar naturalmente, confirmar zero execuções ativas e `main` estável, **nunca usar "Re-run"** em execuções anteriores ao merge e não reativar antes de o merge estar verificado. A guarda 5 recusa o corte se já tiver corrido código da PR 2 em produção.
2. **Divergência `register=False` vs `register=True`:** verificada no próprio corte (guarda 7) e de novo no ensaio.
3. **Snapshot desatualizado** (guarda 2) e **proteção natural:** se `main` avançar depois de M, o merge da PR entra em conflito em `data/report.json`.
4. **Escritas parciais e restauro:** transação verificada por hash; código 3 em caso de restauro falhado.
5. **Avisos legítimos depois da retoma:** a confirmação das 4 promoções pendentes produzirá 4 avisos de uma vez, quando chegar o resultado que as confirma. É o comportamento correto pelas regras da PR 2; a redundância fica para as PRs 3 e 5.
6. **Janela:** como na PR 1, preferir 23h–09h UTC. As mensagens emitidas durante a pausa são lidas mais tarde.

## 5. Procedimento (só com autorização expressa)

**Fase A — preparação (sem efeito em produção)**

- A1. No HEAD a integrar: `python scripts/test_offline.py` e `python scripts/rehearse_pr2_cutover.py origin/main`, ambos com código 0 e todos os `criteria` verdadeiros. CI verde na PR.
- A2. O proprietário desativa *Collect ScanGoal*. O operador confirma:
  - o estado `disabled_manually`;
  - que a execução em curso terminou sozinha (≤ 9 min, falhando só o passo "Start the next collection");
  - zero execuções `in_progress`, `queued`, `waiting`, `requested` ou `pending`;
  - `git ls-remote origin main` igual em duas leituras com pelo menos 3 minutos de intervalo. Esse SHA é **M**.
- A3. Cópia de segurança: referência `backup/pre-pr2-cutover` em M (requer autorização). O estado anterior fica também em `previous_statuses` e nos hashes do relatório de corte.

**Fase B — autorização** do proprietário para a janela, para o SHA da PR e para M.

**Fase C — corte**

```bash
git fetch origin main fix/brain-02-independence-stability
git switch --detach origin/fix/brain-02-independence-stability
git merge --no-ff -m "Merge main (M) into PR2 for controlled cutover" <M>   # qualquer conflito: parar
python scripts/test_offline.py
python scripts/rehearse_pr2_cutover.py <M>                                  # criteria todos verdadeiros
python scripts/pr2_cutover.py <M> > audits/pr2-cutover.json
echo $?   # só 0 permite continuar; 1 = parar (ficheiros restaurados e verificados); 3 = parar, ver secção 2
git add data/report.json docs/report.json data/notify_state.json audits/pr2-cutover.json
git commit -m "data: PR2 cutover - status rules independent_games_hysteresis_v1, silent re-baseline"
git push origin HEAD:fix/brain-02-independence-stability
```

- C1. O diff da PR face a `main` contém só código, documentação e os três ficheiros de dados. O CI tem de estar verde, sem `[skip ci]` no commit de corte.
- C2. Confirmar de novo que há zero execuções ativas e que `main` = M. Tirar de Draft e fazer merge com **commit de merge**, fixando o HEAD esperado. Se `main` ≠ M, parar.
- C3. Num checkout novo de `main`:
  - os hashes dos três ficheiros coincidem com `sha256_after` de `audits/pr2-cutover.json`;
  - `python -m scangoal.notify --migrate-statuses` imprime `NOTIFY_STATUS_UNCHANGED` e `git status` fica limpo;
  - as chaves são `eligible_statuses_v1` e `status_rules:independent_games_hysteresis_v1`;
  - o marcador da PR 1 está igual ao de M.
- C4. O proprietário reativa o workflow; é lançada uma única execução.
- C5. Acompanhar as duas primeiras execuções:
  - `NOTIFY_OK sent N` e `sent_total` com +N;
  - os dois marcadores inalterados;
  - zero transições históricas;
  - qualquer aviso de estado corresponde a uma mudança real entre o `status_list` dessa execução e a referência, incluindo a eventual confirmação das 4 pendentes;
  - `report.json` com `status_rules` = `independent_games_hysteresis_v1`.

## 6. Reversão

- **Antes do merge (A–C1):** a produção está intacta; basta reativar o workflow.
- **Depois do merge, antes de reativar:** manter a pausa e reverter o código da PR 2 por commit, preservando os dados atuais. Atenção: o código da PR 1 não conhece a chave da PR 2. Se for reativado com a referência da PR 2, compara `status_list` do formato da PR 1 com essa referência e envia transições, tal como no risco medido acima. A reversão exige por isso, antes de qualquer escritor:
  - (1) gerar com o código da PR 1 o relatório no formato da PR 1, sem emissores;
  - (2) arquivar num registo de reversão a entrada `status_rules:independent_games_hysteresis_v1` e removê-la das migrações ativas, para que uma reinstalação futura volte a migrar em silêncio;
  - (3) estabelecer em silêncio `statuses` a partir desse relatório, preservando o marcador da PR 1, os contadores e o histórico;
  - (4) persistir e só depois retomar um único escritor.

  Este procedimento requer autorização e revisão próprias; não há script automático nesta PR.
- **Depois de reativar:** desativar de novo, confirmar zero execuções ativas, copiar o estado mais recente e seguir o ponto anterior. Nunca fazer reset de `data/` nem restaurar um `notify_state.json` antigo.

## 7. Reprodução

```bash
git fetch origin main fix/brain-02-independence-stability
git switch --detach origin/fix/brain-02-independence-stability
python3.11 scripts/test_offline.py                                # 178 testes
python3.11 -m unittest discover -s tests -p test_pr2_cutover.py -v  # 19 regressões do corte
python3.11 audits/pr2-cutover-evidence/mutation_check.py           # 21 de 21 mutantes apanhados
python3.11 scripts/rehearse_pr2_cutover.py origin/main > /tmp/pr2-rehearsal.json; echo $?   # 0 esperado
```

## 8. Limitações

- O script corre offline e **não vê o GitHub Actions**: a ausência de escritores antigos é garantida pelo procedimento (A2/C2), não pelo código.
- A substituição atómica não é um bloqueio entre processos; mantém-se o pressuposto de um único escritor.
- Uma morte abrupta do processo (SIGKILL, falta de energia) não corre o restauro; recupera-se com `git restore` no checkout limpo, e nada foi publicado.
- A referência da PR 2 silencia, por definição, as diferenças já presentes no relatório do corte.
- O ensaio usa edições sintéticas do `status_list` para demonstrar a notificação de mudanças legítimas; não simula novos resultados.
- O autor preparou e executou o ensaio; não substitui a auditoria independente.
