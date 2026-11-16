# PR31 — Pacote de decisão administrativa (revisão final, 2026-11-16)

Estado: PREPARADO, NADA EXECUTADO. Nenhuma ref, ruleset, ambiente, secret,
permissão, workflow ou `main` alterado. Zero pedidos BSD/HL. Genesis NÃO construído
(ver F). Repositório `nunovinhas-creator/goal-roi`: **público**, conta **pessoal**.

**Parecer: GO para preparar a instalação dos dois patches inertes. NO-GO para o
primeiro pedido BSD real** (faltam G.1–G.4).

## A. Orçamento temporal

- Watchdog `SIGALRM` de 150 s sobre todo o tail (Git local/rede, replay, persistência,
  conclusão). Cortes por operação Git e proibição de envio a `tail_end − 80 s` mantêm-se.
  Testado: bloqueio antes e depois da reserva, thread não principal, neutralidade do patch.
- Limite residual: uma syscall em estado D (ex.: `fsync` num disco bloqueado) só
  devolve o controlo ao terminar.

## B. Margem do coletor — 650 s é ESTIMATIVA, não garantia

Passos depois do BSD e o seu limite real (código em `main` 17a3a5e):

| Passo | Limite | Pior caso analítico |
|---|---|---|
| LLM (`scangoal.llm`) | `urlopen(timeout=60)` por modelo, até 3 modelos; `report.build()` | ~180 s de rede + CPU; o timeout é por operação de socket, não total |
| Relatório | nenhum | CPU (16–22 s observados) |
| Telegram (`notify`, `NOTIFY_DURABLE=git`) | `timeout=20` por mensagem; Git sem timeout | ilimitado |
| Commit data | nenhum (`git pull --rebase`, `git push`) | ilimitado |
| Leituras rápidas | `timeout 540s` + 10 s; duração fixa `WATCH_SECONDS=480` | 550 s |
| Commit + `gh workflow run` | nenhum | ilimitado |

Amostra real (4 execuções de hoje): BSD termina a +9–12 s; depois disso 504–565 s
(leituras rápidas 481–503 s, restantes ≤ 62 s). O valor de 650 s dá folga sobre a
amostra, mas não há limite analítico. Conclusão: **não é um limite operacional seguro.**

As leituras rápidas têm duração fixa e não encurtam se o tail atrasar o passo; a
recolha normal não perde funcionalidade. Efeito colateral: a cadeia "Start the next
collection" atrasa ≤ 150 s em no máximo 5 execuções.

**Garantia proposta (sem reduzir trabalho normal):** `timeout-minutes: 15 → 18` em
`collect-yml-proposal.patch`. O código continua a exigir que o tail caiba nos 15 min
originais (gate `650 + 150` contra `início + 870 s`), e os 180 s extra (≥ 150 s do
watchdog) asseguram que uma execução que terminaria em 15 min sem o tail nunca é
cortada por causa dele. Custo: um job pendurado leva +3 min a ser terminado; minutos
de Actions são gratuitos em repositório público. Com esta alteração, 650 s passa a
ser apenas um filtro de oportunidade e deixa de ser condição de segurança.

## C. Consumidores BSD

| # | Consumidor | Estado | Evidência |
|---|---|---|---|
| 1 | `results_bsd` no passo BSD de `collect.yml` (`main`) | ATIVO, ≤ 12 pedidos/execução, intervalo ≥ 50 min | código + workflow; único workflow com `secrets.BSD_API_KEY` |
| 2 | `scangoal.recovery --allow-live` (PR29, não está em `main`) | manual; só com chave no ambiente local | código; sem workflow que o execute |
| 3 | `PreparedBSD`/`PreparedTail` (PR31) | 0 pedidos reais (`OfflineWire`) | testes |
| 4 | **asianodds-h1-lab** | **NÃO VERIFICÁVEL** | ver abaixo |
| 5 | Outros repositórios do titular: ET-spotter, Monte-Carlos, tens-direito, tensdireito-content-engine (públicos); comparador, TurnoAL, OBRA-LEVE (privados) | **NÃO VERIFICADO** | o âmbito desta sessão não permite lê-los; a pesquisa de código do GitHub nem encontra `BSD_API_KEY` no próprio goal-roi, por isso a ausência de resultados não prova nada |

asianodds-h1-lab: aparece em dois comentários do commit inicial importado
(`results_bsd.py`: "padrão já usado no asianodds-h1-lab" a propósito da paginação
`limit/offset` da BSD; `llm.py`: mesmo serviço Cloudflare). Não existe com esse nome
entre os repositórios visíveis do titular (pesquisa de repositórios: 0 resultados) e
não pode ser anexado a esta sessão. Pode ser privado, de outra conta, local ou já
apagado. O comentário indica que o projeto usou a paginação da API BSD, o que é um
**indício de que usou a BSD**, mas não prova que o faça hoje nem com que conta.

Não se assume que o mesmo nome de variável (`BSD_API_KEY`) noutro projeto seja a
mesma conta: só o titular pode ligar chaves a contas.

### Declaração de exclusividade — só o que precisa da confirmação do titular

> 1. A chave no secret `BSD_API_KEY` de goal-roi pertence à conta BSD \_\_\_ (identificador ou e-mail da conta, a não publicar se for sensível).
> 2. Chaves ativas nessa conta: \_\_\_ (número). Locais onde cada uma está guardada: \_\_\_.
> 3. asianodds-h1-lab: [ ] nunca usou a BSD [ ] usou outra conta [ ] usa esta conta; neste caso está [ ] desativado desde \_\_\_ / [ ] ativo com ≈ \_\_\_ pedidos/dia.
> 4. Algum dos repositórios ET-spotter, Monte-Carlos, tens-direito, tensdireito-content-engine, comparador, TurnoAL, OBRA-LEVE, ou outra máquina ou script, usa esta conta? [ ] não [ ] sim: \_\_\_.
> 5. Pedidos BSD já feitos para 2026-11-13..15 com os 10 IDs do ensaio, fora do passo normal (ex.: `scangoal.recovery --allow-live`): \_\_\_ (número; 0 se nenhum). → `prior_debits`.
> 6. Comprometo-me a não usar esta conta fora do coletor enquanto o ensaio estiver ativo.
> Data / assinatura: \_\_\_

O revisor confere 2–5 com o painel do fornecedor e com o histórico do asianodds-h1-lab
antes de citar a declaração no genesis. A deteção nos cabeçalhos só cobre a janela
do passo BSD.

## D. Registo remoto: integridade do orçamento ≠ autenticidade dos resultados

Factos confirmados: os rulesets estão disponíveis em repositórios públicos no GitHub
Free ([about rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)).
Os ambientes, os secrets de ambiente e as regras de branch de deployment estão
disponíveis em repositórios públicos de todos os planos ([deployments and environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)).
Por confirmar: que atores de bypass (deploy keys) uma conta pessoal oferece. A
recomendação abaixo NÃO depende disso.

**Integridade do orçamento** (5 débitos irreversíveis, âncora única, sem reescrita ou
recriação). Já é garantida com rulesets de Free e o código atual:
- âncora fixada em `PROVISIONED_ANCHOR` (o código não aceita outra; um novo genesis
  não dá mais slots);
- ruleset: Restrict creations + Restrict deletions + Block force pushes, bypass vazio;
- cada `reserve` acrescentado, legítimo ou não, conta para o cap. Um evento forjado
  não pode reembolsar nem desfazer `halted`. Quem tem escrita só consegue gastar
  slots ou parar o ensaio (fail-closed), nunca obter mais de 5.

**Autenticidade dos resultados** (respostas aceites como evidência). NÃO está garantida
hoje: quem tem `contents: write` (o titular, sessões de agentes, qualquer workflow de PR
do mesmo repositório que declare escrita) pode acrescentar um `reserve` + `complete`
bem formados com bytes inventados, e o replay aceitá-los-ia. "Restrict updates" sem
bypass impediria também o coletor de escrever.

**Solução mínima recomendada: atestação OIDC do GitHub Actions** (gratuita, sem
secrets, sem ambiente, sem risco de bloquear o coletor):
- `collect.yml`: `permissions: id-token: write` (acrescentado às permissões atuais).
- Cada evento `reserve`/`complete` passa a incluir um JWT OIDC pedido pelo próprio
  passo, com `audience = sha256(evento canónico sem o JWT)`.
- O replay/load verifica, para cada evento: assinatura RS256 com o JWKS de
  `token.actions.githubusercontent.com`; `repository = nunovinhas-creator/goal-roi`;
  `job_workflow_ref = nunovinhas-creator/goal-roi/.github/workflows/collect.yml@refs/heads/main`;
  `run_id:run_attempt == owner`; `aud` = digest do evento. Falha => o journal pára e
  nada é aceite como evidência.
- Implementação estimada em ~60 linhas stdlib (JWT, verificação PKCS#1 v1.5 com `pow`,
  pedido do token pelas variáveis `ACTIONS_ID_TOKEN_REQUEST_*`). NÃO está implementada
  nem auditada; é condição G.2.
- Riscos residuais: (1) a confiança é o código em `main`: quem consegue fazer push
  para `main` consegue alterar o coletor; (2) se o GitHub rodar a chave OIDC e a
  antiga sair do JWKS, o replay de eventos antigos falha e o ensaio pára (fail-closed);
  (3) um append forjado pode parar o ensaio permanentemente (DoS), porque a ref não
  pode ser reescrita; (4) nem o OIDC nem nada no GitHub prova que os bytes vieram do
  servidor BSD: isso assenta no TLS e no código auditado do coletor, e não é
  verificável por terceiros.

Alternativa (se o titular preferir não usar OIDC): ambiente `bsd-trial-journal` com
regra de branch de deployment = só `main`, sem revisores nem espera, secret de
ambiente `BSD_JOURNAL_HMAC_KEY` (32 bytes aleatórios gerados pelo titular) e HMAC em
cada evento. Desvantagens: o job `collect` inteiro passa a depender do ambiente (uma
configuração errada bloqueia a recolha), cada execução cria um registo de deployment,
e é preciso criar um secret.

## E. Configuração exata recomendada (por autorizar; nada foi feito)

1. **Ruleset** (Settings → Rules → Rulesets → New branch ruleset): nome
   `bsd-trial-journal`; Enforcement **Active**; Bypass list **vazia**; Target →
   Include by pattern `bsd-trial-journal` (+ `bsd-ruleset-probe` só durante o teste);
   regras **Restrict creations**, **Restrict deletions**, **Block force pushes**;
   **sem** Restrict updates.
2. **Workflow permissions** (Settings → Actions → General): "Read repository contents
   and packages permissions". `collect.yml` declara `contents: write` e `actions: write`
   explicitamente e não muda; `tests.yml` e outros workflows sem bloco passam a só
   leitura. Confirmar a seguir uma execução verde do coletor.
3. **Genesis**: construir e validar localmente (comandos em F), e fazer push só da âncora
   e da ref de prova antes de ativar o ruleset; testar eliminação/force-push SÓ na ref de
   prova; confirmar `git ls-remote` == âncora; depois retirar e apagar a prova.
4. **`PROVISIONED_ANCHOR`**: commit revisto com o SHA.
5. **OIDC** (D): commit revisto com o código de atestação e `id-token: write`.

## F. Genesis — NÃO construído

Campos obrigatórios e estado:

| Campo | Estado |
|---|---|
| `mode`, `trial_id`, `cap`, `reserve` | conhecidos (`inline-five-slots`, `goal-roi-bsd-20261113-15-v1`, 5, 100) |
| `prior_debits` | **não comprovado**: o repositório mostra 0 pedidos de ensaio, mas usos manuais fora do repositório não são verificáveis (declaração, ponto 5) |
| `exclusive_account_review_ref` | **não existe** (declaração por assinar) |
| `predecessor_budget_review_ref` | depende de `prior_debits` |
| `snapshot_sha` | só deve ser fixado na revalidação final dos 10 IDs, imediatamente antes |

Construir agora exigiria inventar valores; por isso não foi feito. Comandos para quando
houver dados (máquina do titular):

```sh
W=$(mktemp -d); git init --bare -q "$W/j.git"
cat > "$W/genesis.json" <<'JSON'
{"kind":"genesis","config":{"mode":"inline-five-slots",
 "trial_id":"goal-roi-bsd-20261113-15-v1","cap":5,"reserve":100,
 "prior_debits":<N>,"snapshot_sha":"<SHA main revalidado>",
 "predecessor_budget_review_ref":"<link reconciliação>",
 "exclusive_account_review_ref":"<link declaração revista>"}}
JSON
python3 -c 'import json,sys;sys.stdout.write(json.dumps(json.load(open(sys.argv[1])),sort_keys=True,separators=(",",":"))+"\n")' "$W/genesis.json" > "$W/msg"
TREE=$(git --git-dir="$W/j.git" mktree </dev/null)
ANCHOR=$(git --git-dir="$W/j.git" commit-tree "$TREE" < "$W/msg")
git --git-dir="$W/j.git" update-ref refs/heads/bsd-trial-journal "$ANCHOR"
python3 -c 'import sys;from scangoal.recovery_inline import InlineSlots;from scangoal.recovery_trial_journal import OfflineGitStore;print(InlineSlots(OfflineGitStore(sys.argv[1]),sys.argv[2],sys.argv[3]).load()[1]["config"])' "$W/j.git" "$ANCHOR" "$W/prepared-fixture-trial"
# só depois de aprovado:
PROBE=$(echo '{"kind":"ruleset-probe"}' | git --git-dir="$W/j.git" commit-tree "$TREE")
git --git-dir="$W/j.git" push https://github.com/nunovinhas-creator/goal-roi.git "$ANCHOR:refs/heads/bsd-trial-journal" "$PROBE:refs/heads/bsd-ruleset-probe"
# ruleset ativo -> estes dois TÊM de ser rejeitados:
git --git-dir="$W/j.git" push https://github.com/nunovinhas-creator/goal-roi.git :refs/heads/bsd-ruleset-probe
OTHER=$(echo '{"kind":"rewrite-probe"}' | git --git-dir="$W/j.git" commit-tree "$TREE")
git --git-dir="$W/j.git" push --force https://github.com/nunovinhas-creator/goal-roi.git "$OTHER:refs/heads/bsd-ruleset-probe"
```

## G. Patches e condições

| Patch | Alteração | Aplica em `main` 17a3a5e |
|---|---|---|
| `collect-yml-proposal.patch` | `timeout-minutes` 15 → 18; 1.º passo grava `GOAL_ROI_JOB_STARTED_UTC` | sim |
| `collector-tail-proposal.patch` | `results_bsd.py` +18/−3: `request_json(observe=None)`, `run(after_bsd=None)` | sim |

Zero linhas tocam em Telegram, notify, relatório, agendamento, encadeamento ou
certificação. Sem callback, os patches são inertes (testado byte a byte). Riscos: o
passo novo pode, em teoria, falhar antes da recolha; a variável fica visível nos
passos seguintes (não é secret); um job pendurado leva +3 min a terminar; o teste de
integridade que fixa o blob do `collect.yml` tem de ser atualizado na PR de instalação.

Garantias obrigatórias em falta para o primeiro pedido (NO-GO até todas existirem):
1. Declaração C assinada e revista; `prior_debits` comprovado.
2. Atestação OIDC (D) implementada, auditada e testada.
3. Ruleset, permissões, genesis e `PROVISIONED_ANCHOR` (E.1–E.4) concluídos e verificados.
4. Patches instalados (incluindo `timeout-minutes: 18`) e terceiro commit auditado
   (wiring no `main()` e ponte HTTP ≤ 20 s, namespace real separado de `synthetic_wire`).

Primeira execução, depois de tudo isso: recolha normal guardada → cabeçalho < 60 s
sem queda inexplicada → saldo ≥ 100 + slots restantes → tail cabe nos 15 min originais
→ watchdog → reserva atestada e confirmada → UMA página → bytes/hash/UTC duráveis →
`complete` atestado → staging com revisão manual. Qualquer incerteza gasta o slot, sem
reenvio; reverter = remover o callback.
