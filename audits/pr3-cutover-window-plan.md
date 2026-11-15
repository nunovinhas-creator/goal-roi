# PR 3 — Janela de corte por etapas: checklist com autorizações separadas

O plano operacional foi aceite pelo proprietário depois da revisão #5912630773, com duas exigências:
1. a referência `backup/pre-pr3-cutover` em M é **obrigatória**;
2. `scripts/post_cutover_check.py` fica preservado, documentado e testado, sem execução em produção antes da fase D.

O corte faz-se em **quatro fases**, cada uma com uma **autorização separada** do proprietário. Nenhuma fase começa sem a sua autorização, e a autorização de uma fase não cobre a seguinte. O procedimento técnico detalhado está em `audits/pr3-cutover-runbook.md`.

**Em todas as fases:**
- a certificação continua desligada: sem `data/certification_ledger.json`, sem `--activate`, sem patch `fetch-depth: 0`;
- sem alterações a workflows ou secrets; o operador não desativa nem reativa workflows, isso é feito pelo proprietário;
- sem force-push; sem PR 4.

**Durante a pausa:**
- O coletor fica parado entre A e D, incluindo o tempo de revisão entre B e C.
- As mensagens do bot são lidas depois (o leitor relê as últimas 400). Os sinais lidos com mais de 30 minutos de atraso ficam "não reconstituíveis".
- Se a revisão de B se prolongar, o proprietário pode desistir da janela: aplica-se R0, reativa-se o coletor e mais tarde repete-se tudo com um novo M.

## Fase A — pausa, congelamento e cópia de segurança

**Começa só depois de** o proprietário confirmar expressamente a janela (UTC) e desativar *Collect ScanGoal*.

- [ ] A0. O proprietário indica a janela e o SHA da PR a integrar (HEAD da PR #26 revisto).
- [ ] A1. O proprietário desativa *Collect ScanGoal* (Actions → `···` → *Disable workflow*) e regista T0.
- [ ] A2. A execução em curso termina sozinha (≤ 15 min).
  - Não cancelar, não usar *Re-run*, não usar *Run workflow*.
  - O passo final *Start the next collection* falha; é esperado e não envia aviso Telegram.
  - Uma execução `queued` lançada antes de T0 é cancelada pelo proprietário antes de começar.
- [ ] A3. O operador confirma pela API do GitHub:
  - o workflow está em `disabled_manually`;
  - zero execuções de `collect.yml` em `in_progress`, `queued`, `waiting`, `requested` ou `pending`.
- [ ] A4. `git ls-remote origin refs/heads/main` dá o mesmo valor em duas leituras com pelo menos 3 minutos de intervalo, repetindo A3 entre elas. Esse SHA é **M**.
  - O último commit de M é do `scangoal-collector` e é anterior ao fim da última execução.
- [ ] A5. **Cópia de segurança obrigatória:** criar `backup/pre-pr3-cutover` em M.
  - Comando: `git push origin "$M:refs/heads/backup/pre-pr3-cutover"`.
  - Se o push for recusado, a referência é criada a partir de `main` (= M, congelado) pela interface do GitHub ou pela API.
  - Verificação: `git ls-remote origin refs/heads/backup/pre-pr3-cutover` = M. Sem esta verificação, a fase B não começa.
  - A referência só se cria; nunca se apaga nem se move nesta janela.

## Fase B — validação sobre M (local, sem push)

**Começa quando** A3 a A5 estiverem verificados. Tudo é local; nada é publicado.

- [ ] B1. `origin/main` = M; HEAD remoto da PR = SHA indicado em A0; checkout limpo.
- [ ] B2. `git switch --detach <SHA da PR>` e `git merge --no-ff <M>`, sem conflitos.
- [ ] B3. `python3.11 scripts/test_offline.py`: **285 testes**, todos OK.
- [ ] B4. `python3.11 audits/pr3-cutover-evidence/mutation_check.py`: **32 de 32**.
- [ ] B5. `python3.11 scripts/rehearse_pr3_cutover.py <M>` com código 0, `ok: true` e os **18 critérios** verdadeiros. Inclui:
  - nenhuma reclassificação;
  - ciclos 1 e 2 com 0 transições e 0 mensagens validadas;
  - marcadores das PRs 1 e 2 byte a byte iguais;
  - `validation_state = not_activated`;
  - `ledger_created = false`.
- [ ] B6. `python3.11 scripts/pr3_cutover.py <M>` com código 0, `ok: true` e todas as `checks` verdadeiras. Commit **local** dos 3 ficheiros e dos JSON de evidência; esse commit é H.
- [ ] B7. `git diff --name-only <M> H -- .github data docs/report.json docs/bets.csv` devolve exatamente:
  - `data/notify_state.json`, `data/report.json`, `docs/report.json`;
  - sem registo de certificação.
- [ ] B8. `push_preflight` = GO: `main` ainda é M, fast-forward e checkout limpo.

**Resultados a apresentar ao proprietário:**
- M e H, e o SHA da PR integrado;
- a saída dos testes, das mutações e dos 18 critérios;
- `sha256_before` e `sha256_after` dos ficheiros (de `audits/pr3-cutover.json`);
- contadores de entrega em M (`sent_total`, `watch_sent`) e chaves de migração antes e depois;
- o diff B7.

Depois disto, **parar** e pedir a segunda autorização.

## Fase C — push e merge (segunda autorização)

**Começa só com autorização expressa** depois da revisão dos resultados de B. Antes de começar, repetir A3 e B8.

- [ ] C1. `git push origin HEAD:fix/brain-03-chronological-validity`, só em fast-forward.
- [ ] C2. O CI *Tests* fica verde em H; `mergeable_state: clean`; os ficheiros da PR coincidem com B7.
- [ ] C3. Repetir A3 e confirmar `main` = M e HEAD da PR = H. Tirar a PR de Draft.
- [ ] C4. Merge com **commit de merge** e `expectedHeadSha = H`. Se o GitHub recusar, parar.
- [ ] C5. Integridade, num clone novo de `main`:
  - os pais do merge são M e H, e a árvore é igual à de H;
  - os hashes dos 3 ficheiros = `sha256_after`;
  - `python3.11 -m scangoal.notify --migrate-statuses` imprime `NOTIFY_STATUS_UNCHANGED` e o checkout fica limpo;
  - não existe registo de certificação;
  - o CI em `main` fica verde.

**Resultados a apresentar:** C1 a C5. Depois disto, **parar** e pedir a autorização da fase D. O coletor continua parado.

## Fase D — reativação e duas execuções (terceira autorização)

**Começa só com autorização expressa** depois de confirmada a integridade (C5).

- [ ] D1. O proprietário reativa *Collect ScanGoal* e lança **uma** execução: *Run workflow*, ramo `main`, `force_results` desligado.
- [ ] D2. Depois de cada uma das duas primeiras execuções, correr `python3.11 scripts/post_cutover_check.py <antes> <depois>`:
  - **antes** = o merge (1.ª execução) ou o último commit da execução anterior (2.ª);
  - **depois** = o último commit da execução verificada;
  - tem de dar código 0.
- [ ] D3. Logs: `NOTIFY_OK sent N` e nenhum `NOTIFY_STATUS_MIGRATED`.
- [ ] D4. No Telegram: nenhuma mensagem `✅ Tática validada` nem `🟢 ALERTA`. Cada aviso de estado tem de corresponder a uma entrada de `status_list_changes`; uma mudança real não é um erro.

## `scripts/post_cutover_check.py`

Só leitura. Lê os ficheiros dos commits com `git show`, sem tocar no checkout, sem rede e sem Telegram. Não é chamado por nenhum workflow nem por nenhum módulo de produção. Usa-se só na fase D. Código de saída: 0 = GO, 1 = falha (parar, R3), 2 = uso errado ou ficheiro ilegível.

| Verificação | Falha quando |
|---|---|
| `markers_unchanged` | alguma das 3 chaves de migração (PRs 1, 2 e 3) falta ou muda |
| `delivery_monotonic` | `sent_total` ou o número de `watch_sent` diminui |
| `status_rules` | o relatório não é de `chronological_eprocess_v3` |
| `certification_off` | `validation.state` ≠ `not_activated`, há `certified` ou `ledger_valid`, ou existe o registo |
| `no_validated_statuses` | alguma tática `forte` ou `confirmada` |
| `statuses_follow_report` | a referência Telegram difere do `status_list` |

`status_list_changes` é informativo: lista as mudanças reais de estado, para correlacionar com os avisos recebidos.

**Testes:** `tests/test_post_cutover_check.py`, 10 testes em repositórios git sintéticos.
- Passa: execução normal com mudança real; commits idênticos.
- Falha: cada verificação com a sua causa, incluindo 4 variantes de certificação e o registo presente.
- Código 2: JSON ilegível, commit inexistente, uso errado.
- Prova de que só lê: o checkout, o HEAD e os ficheiros ficam iguais.

Sobre `main` atual, que ainda está antes do corte, a verificação devolve, como esperado, código 1 com `markers_unchanged`, `status_rules` e `certification_off` falsos.

## Reversão

As regras completas estão no runbook, secção 6, e no plano operacional.

| Fase da falha | Ação | Autorização |
|---|---|---|
| A ou B | **R0**: abandonar o checkout local (código 3 do corte: `git restore --source=HEAD -- data docs`). Nada foi publicado; o proprietário reativa o coletor. A referência de backup fica. | pré-autorizada |
| C, antes do merge | **R1**: `main` intacto. Numa nova tentativa, `git revert` do commit de corte, fundir o novo M e repetir B. Sem force-push. | pré-autorizada |
| C, depois do merge (C5 falha) | **R2**: manter a pausa; `git revert -m 1 <merge>`; confirmar 0 transições com o código da PR 2 antes de reativar. | no momento |
| D (D2–D4 falha) | **R3**: pausar de novo; reverter só o código, mantendo os dados mais recentes. Nunca restaurar um `notify_state.json` antigo, nunca fazer reset de `data/`, nunca fazer force-push. | no momento |

A referência `backup/pre-pr3-cutover` serve de prova e de ponto de comparação. **Não** é usada para repor `main` por cima de dados mais recentes.
