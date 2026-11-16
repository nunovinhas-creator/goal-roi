# PR31 — Pacote de decisão administrativa (2026-11-16)

Estado: PREPARADO, NADA EXECUTADO. Nenhuma referência criada, nenhum ruleset,
secret, definição do repositório, workflow ou `main` alterado. Zero pedidos BSD/HL.
Repositório: `nunovinhas-creator/goal-roi`, **público**, conta **pessoal**.

## A. Orçamento temporal (resolvido offline)

Antes desta revisão o limite de 150 s só cortava as operações Git de rede e o
instante de envio. Git local, replay (CPU), persistência e conclusão/replay final
não tinham limite global. Agora:

- `PreparedTail.__call__` arma um watchdog `SIGALRM` (`setitimer`) de
  `TAIL_BUDGET_S=150` que cobre TODO o trabalho do tail. A exceção é
  `BaseException`, portanto nenhum `except Exception` interno a engole; é apanhada
  só no topo e o resultado é `trial_deadline_normal_preserved`.
- O temporizador é desarmado e o handler anterior restaurado em `finally`
  (testado). Fora da thread principal não há watchdog => skip sem reserva.
- Mantêm-se como defesa em profundidade: corte de cada operação Git de rede ao fim
  do tail e proibição de envio depois de `tail_end − 80 s`.
- Limite residual que nenhum código Python resolve: uma syscall em estado D
  (ex.: `fsync` num disco bloqueado) só devolve o controlo ao terminar; o
  `timeout-minutes: 15` do job continua a ser o último recurso.

Testes novos: trabalho bloqueado antes da reserva (recolha normal guardada, zero
reservas, < 5 s); envio bloqueado depois da reserva (slot fica `reserved` e nunca é
reenviado; a execução seguinte salta); thread não principal; patch instalado sem
callback produz estado e ficheiro byte-a-byte iguais ao coletor atual.

### Margem empírica (só leitura da API do GitHub, 4 execuções de 2026-11-16)

| run | fim do passo BSD após início do job | depois do BSD até fim | leituras rápidas | outros passos |
|---|---|---|---|---|
| 43931508179 | +11 s | 522 s | 498 s | 24 s |
| 43926766931 | +12 s | 565 s | 503 s | 62 s |
| 43913419359 | +10 s | 504 s | 481 s | 23 s |
| 43921443044 | +9 s | 522 s | — | — |

Leituras rápidas têm limite rígido 540+10 s. LLM, relatório, notify, commits e
"Start the next collection" NÃO têm timeout de passo (máximo observado conjunto: 62 s).
Proposta para aprovação: `downstream_bound = 650` (550 rígidos + 100 para os
restantes, ~1,6× o máximo observado). Isto é EMPÍRICO, não prova. Com prazo
`início+870 s`, o ensaio só arranca se restarem ≥ 800 s (650 + 150); na amostra
restavam ~858 s. Uma execução em que o leitor demore >60 s salta o ensaio.
Não se propõe encurtar nenhum passo normal.

## B. Consumidores da conta BSD

Comprovado por inspeção (main `17a3a5e` e esta branch):

| # | Consumidor | Chave | Volume | Onde corre |
|---|---|---|---|---|
| 1 | `scangoal.results_bsd` no passo BSD de `collect.yml` | `secrets.BSD_API_KEY` | ≤ `BSD_MAX_REQUESTS`=12/execução, intervalo ≥50 min (0 com `force_results`) | GitHub Actions, `main`, grupo `scangoal-write` |
| 2 | `scangoal.recovery` CLI `--allow-live` (PR29, NÃO está em `main`) | `BSD_API_KEY` do ambiente local | manual | só se alguém o correr com a chave fora do Actions |
| 3 | `PreparedBSD`/`PreparedTail` (PR31) | nenhuma real | 0 (só `OfflineWire`) | testes |
| — | `recovery-trial.yml` (PR30, não em `main`) | não recebe a chave | 0 | — |

`collect.yml` é o ÚNICO workflow que recebe o secret. Nenhum outro ficheiro do
repositório lê a variável.

NÃO comprovável por código:
- outras máquinas, scripts ou repositórios com a mesma chave ou outra chave da mesma
  conta. O comentário de `results_bsd.py` refere o projeto **asianodds-h1-lab**
  ("padrão já usado no asianodds-h1-lab"): é um candidato concreto a consumidor
  externo e tem de ser incluído na declaração;
- uso pelo painel do fornecedor ou por cópias antigas do secret;
- se a quota `q=7500` é por chave ou por conta.

A deteção nos cabeçalhos (queda ≠ 1 por pedido entre respostas normais) só observa a
janela do passo BSD. Não substitui a declaração.

### Declaração para o titular da conta (preencher; NÃO é tida como comprovada)

> Eu, titular da conta BSD (sports.bzzoiro.com) cuja chave está no secret
> `BSD_API_KEY` de `nunovinhas-creator/goal-roi`, declaro que, de \_\_\_ até ao fim
> do ensaio `goal-roi-bsd-20261113-15-v1`:
> 1. Esta conta tem as seguintes chaves ativas: \_\_\_ (n.º e onde estão guardadas).
> 2. Os únicos consumidores são: o passo BSD de `collect.yml` em `main`
>    [ ] e também: \_\_\_ (ex.: asianodds-h1-lab — desativado em \_\_\_ / ativo com \_\_\_ pedidos/dia).
> 3. Não corri nem vou correr `python -m scangoal.recovery --allow-live` com esta
>    chave, exceto: \_\_\_ (data, n.º de pedidos, datas consultadas).
> 4. Pedidos BSD já feitos ao abrigo deste ensaio (datas 2026-11-13..15, os 10 IDs):
>    \_\_\_ (este número é `prior_debits`).
> 5. Comprometo-me a não usar a conta noutro lado enquanto o ensaio estiver ativo e a
>    remover o callback se isso mudar.
> Data / assinatura: \_\_\_

O revisor confirma os pontos 1–4 contra evidência (painel do fornecedor, histórico
local, repositório asianodds-h1-lab) antes de os citar no genesis.

## C. Âncora Git protegida — procedimento exato

O que o código já exige (`InlineSlots.load`, `RemoteGitStore`): commit sem pais,
árvore vazia, mensagem = JSON canónico `{"config":{...},"kind":"genesis"}`; remoto
exatamente `https://github.com/nunovinhas-creator/goal-roi.git`; ref
`refs/heads/bsd-trial-journal`; âncora == `PROVISIONED_ANCHOR` (hoje `None` =>
bloqueado); ≤32 commits até à âncora; push nunca forçado.

Disponibilidade no GitHub: `docs.github.com` não é acessível desta sessão, por isso
NÃO verifiquei a documentação aqui. Pelo que conheço, os rulesets estão disponíveis
em repositórios públicos no plano Free e, numa conta pessoal, a lista de bypass é
limitada (sem equipas/apps). O procedimento abaixo usa lista de bypass VAZIA e não
depende disso. Passo 0 confirma na interface. A leitura da proteção da ref por esta
integração devolveu 403: requer o titular.

0. **Verificar disponibilidade (só leitura).** Settings → Rules → Rulesets: existe
   "New branch ruleset" com "Restrict creations", "Restrict deletions", "Block force
   pushes"? Se não existir, PARAR: o ensaio continua bloqueado.
1. **Pré-requisitos.** Declaração B assinada e revista (link estável, ex.: issue ou
   ficheiro revisto). Reconciliação dos débitos: evidência do repositório
   (PR29/30/31) = **0 pedidos BSD de ensaio**; `prior_debits` = valor do ponto 4 da
   declaração. Se for desconhecido, não provisionar. `snapshot_sha` = SHA do `main`
   usado na revalidação final dos 10 IDs.
2. **Construir o genesis localmente (máquina do titular, sem push).**
   ```sh
   W=$(mktemp -d); git init --bare -q "$W/j.git"
   cat > "$W/genesis.json" <<'JSON'
   {"kind":"genesis","config":{"mode":"inline-five-slots",
    "trial_id":"goal-roi-bsd-20261113-15-v1","cap":5,"reserve":100,
    "prior_debits":0,"snapshot_sha":"<SHA main revalidado>",
    "predecessor_budget_review_ref":"<link reconciliação>",
    "exclusive_account_review_ref":"<link declaração revista>"}}
   JSON
   python3 -c 'import json,sys;sys.stdout.write(json.dumps(json.load(open(sys.argv[1])),sort_keys=True,separators=(",",":"))+"\n")' "$W/genesis.json" > "$W/msg"
   TREE=$(git --git-dir="$W/j.git" mktree </dev/null)
   ANCHOR=$(git --git-dir="$W/j.git" commit-tree "$TREE" < "$W/msg")
   git --git-dir="$W/j.git" update-ref refs/heads/bsd-trial-journal "$ANCHOR"
   echo "$ANCHOR"
   ```
3. **Validar com o código do projeto (offline)**, a partir da checkout da PR31:
   ```sh
   python3 -c 'import sys;from scangoal.recovery_inline import InlineSlots;from scangoal.recovery_trial_journal import OfflineGitStore;print(InlineSlots(OfflineGitStore(sys.argv[1]),sys.argv[2],sys.argv[3]).load()[1]["config"])' "$W/j.git" "$ANCHOR" "$W/prepared-fixture-trial"
   ```
   Tem de imprimir a configuração; qualquer `TrialBlocked` => corrigir e repetir.
   Rever `git --git-dir="$W/j.git" cat-file -p "$ANCHOR"` (sem `parent`).
4. **Criar a ref e uma ref de prova (única escrita na produção).**
   ```sh
   PROBE=$(echo '{"kind":"ruleset-probe"}' | git --git-dir="$W/j.git" commit-tree "$TREE")
   git --git-dir="$W/j.git" push https://github.com/nunovinhas-creator/goal-roi.git \
       "$ANCHOR:refs/heads/bsd-trial-journal" "$PROBE:refs/heads/bsd-ruleset-probe"
   ```
5. **Ruleset (Settings → Rules → Rulesets → New branch ruleset).**
   Nome `bsd-trial-journal`; Enforcement **Active**; Bypass list **vazia**;
   Target branches → Include by pattern: `bsd-trial-journal` e `bsd-ruleset-probe`;
   regras: **Restrict creations**, **Restrict deletions**, **Block force pushes**.
   NÃO ativar "Restrict updates" (ver D). Guardar.
6. **Testar a proteção só na ref de prova.**
   ```sh
   git --git-dir="$W/j.git" push https://github.com/nunovinhas-creator/goal-roi.git :refs/heads/bsd-ruleset-probe      # tem de ser REJEITADO
   OTHER=$(echo '{"kind":"rewrite-probe"}' | git --git-dir="$W/j.git" commit-tree "$TREE")
   git --git-dir="$W/j.git" push --force https://github.com/nunovinhas-creator/goal-roi.git "$OTHER:refs/heads/bsd-ruleset-probe"  # REJEITADO
   git ls-remote https://github.com/nunovinhas-creator/goal-roi.git refs/heads/bsd-trial-journal   # == $ANCHOR
   ```
   Se algum push for aceite: desativar o callback (nunca instalado) e rever o
   ruleset. Nunca testar eliminação na ref do journal.
   Depois: remover o padrão `bsd-ruleset-probe` do ruleset e apagar essa branch.
7. **Restringir escrita (Settings → Actions → General → Workflow permissions):**
   "Read repository contents and packages permissions". `collect.yml` declara
   `contents: write` e `actions: write` explicitamente, por isso não muda;
   `tests.yml` (sem bloco `permissions`, corre em PRs) passa a só leitura e deixa de
   poder escrever na ref. Confirmar a seguir que uma execução do coletor termina verde.
8. **Fixar a âncora:** commit revisto na PR31 com `PROVISIONED_ANCHOR = '<ANCHOR>'`,
   CI verde. Concorrência, timeouts incertos, recusa de eliminação/recuo e perda do
   remoto já estão testados offline (servidor bare + dois clientes); não repetir
   contra a produção.

## D. Escrita restrita: risco residual e alternativa

Sem "Restrict updates", qualquer credencial com `contents: write` pode ACRESCENTAR
commits (fast-forward) à ref: o titular, `collect.yml` (incluindo despachos manuais
noutros refs pelo titular) e sessões de agentes com acesso de escrita. Um evento
inválido bloqueia o ensaio (fail-closed); não cria slots (o cap conta todas as
reservas e a âncora é fixa). Mas um `complete` forjado e bem formado poderia
introduzir uma resposta falsa no staging. Mitigação: o staging não é publicado; cada
evento é um commit auditável; regra operacional de nenhum agente escrever nessa ref.

Alternativa forte (não preparada): "Restrict updates" com bypass para uma deploy key
usada só pelo coletor. Exige um secret novo, um remoto SSH (o código só aceita HTTPS
hoje) e confirmar que contas pessoais permitem deploy keys como bypass. Decisão do
titular: aceitar o risco residual (recomendado para 5 pedidos) ou pedir esta
alternativa.

## E. Patches (não instalados)

| Patch | Alteração | Aplica em `main` 17a3a5e |
|---|---|---|
| `collect-yml-proposal.patch` | +2 linhas: 1.º passo grava `GOAL_ROI_JOB_STARTED_UTC` | sim |
| `collector-tail-proposal.patch` | `results_bsd.py` +18/−3: `request_json(observe=None)`, `run(after_bsd=None)` com try/except | sim |

Zero linhas tocam em Telegram, notify, relatório, `schedule`/cron, cadeia de
encadeamento ou certificação (verificado no diff aplicado). Instalados, são
INERTES: sem `after_bsd`, o estado e o ficheiro são byte-a-byte iguais (testado).
A ativação exige um TERCEIRO commit, por auditar: construir `PreparedTail` no `main()`
do coletor, passar `github.token` ao `RemoteGitStore` e a ponte HTTP real.

Riscos de instalar os dois patches:
- `request_json` passa a ler o corpo para uma variável antes do `json.loads`: mesmo
  comportamento e mesmos erros (testado).
- Novo passo no workflow: se falhar (só se `$GITHUB_ENV` não for gravável), o job
  falha antes da recolha. Probabilidade muito baixa, mas não nula.
- A variável de ambiente fica visível a todos os passos seguintes (não é secret).
- Qualquer alteração ao `collect.yml` muda o blob auditado; os testes de integridade
  que fixam o blob antigo têm de ser atualizados na mesma PR de instalação.

## F. Procedimento mínimo para a primeira consulta BSD real

1. A–E aprovados; passos C.0–C.8 concluídos; `PROVISIONED_ANCHOR` em `main`.
2. Auditoria e autorização expressa do terceiro commit (wiring + ponte HTTP
   ≤20 s, sem redirects/retries, namespace real separado de `synthetic_wire`).
3. Execução elegível (automática; caso contrário, skip silencioso):
   recolha normal concluída e guardada → recibo do último cabeçalho <60 s, sem queda
   inexplicada → saldo ≥ 100 + slots restantes → prazo ≥ 650 + 150 s → watchdog
   armado → replay do journal → reserva confirmada por push não forçado →
   UMA página BSD → bytes/hash/UTC duráveis → `complete` no journal → replay/staging.
4. Qualquer erro/incerteza: o slot fica gasto, sem reenvio. O abandono só com prova
   autenticada da tentativa terminal. Reverter = remover o callback; não tocar na ref.
5. Rever o staging manualmente antes de qualquer uso; nada é publicado.
