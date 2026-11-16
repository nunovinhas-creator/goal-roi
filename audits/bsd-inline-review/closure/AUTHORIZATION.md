# PR31 — Pacote de decisão administrativa (auditoria OIDC e plano de instalação, 2026-11-16)

Estado: PREPARADO, NADA EXECUTADO. Nenhuma ref, ruleset, ambiente, secret,
permissão, workflow ou `main` alterado. Zero pedidos BSD/HL. Genesis NÃO construído
(ver F). Repositório `nunovinhas-creator/goal-roi`: **público**, conta **pessoal**.

**Parecer: GO para a instalação controlada dos quatro patches, depois de autorização
expressa do titular (são inertes sem callback). NO-GO para o primeiro pedido BSD
real.** As garantias em falta estão listadas em H.5.

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

**Autenticidade dos resultados** (respostas aceites como evidência). Sem atestação,
quem tem `contents: write` (o titular, sessões de agentes, qualquer workflow de PR do
mesmo repositório que declare escrita) podia acrescentar um `reserve` + `complete` bem
formados com bytes inventados, e o replay aceitá-los-ia. "Restrict updates" sem bypass
impediria também o coletor de escrever. Com a atestação abaixo, esses eventos são
recusados (testado). Fica a fronteira de confiança descrita nos riscos residuais.

**Implementado: atestação OIDC do GitHub Actions** (`scangoal/recovery_oidc.py`,
integrada em `InlineSlots.load/write`; gratuita, sem secrets, sem ambiente).

Correção do desenho anterior: este documento propunha verificar `job_workflow_ref`.
Essa claim é só de workflows reutilizáveis. `collect.yml` é um workflow normal, por
isso a claim verificada é `workflow_ref`; um `job_workflow_ref` presente e diferente
é rejeitado (indicaria código de outro workflow).

Escrita (`InlineSlots.write`, genesis com `"attestation":"github-oidc-v1"`):
1. o evento recebe `attested_utc`;
2. `aud = "goal-roi-bsd-journal-v1:" + sha256(canónico{trial_id, parent, evento sem
   atestação})`, em que `parent` é o commit do journal sobre o qual o evento vai assentar;
3. o passo pede o token ao runner (`ACTIONS_ID_TOKEN_REQUEST_URL/TOKEN`); o bearer só
   existe em memória e nunca é registado nem persistido;
4. o token é verificado como fresco ANTES do CAS; se falhar, nada é escrito
   (uma reserva anterior continua gasta);
5. o evento guarda `{"mode":"github-oidc-v1","jwt":...}`.

Leitura (`InlineSlots.load`, em cada execução e em cada replay), por evento
`reserve`, `complete` e `abandon`:
- JWT compacto estrito; cabeçalho só com `alg/typ/kid/x5t`; `alg == "RS256"`
  (rejeita `none`, `HS256`/confusão de algoritmos, `jwk`/`jku`/`x5u`/`x5c`/`crit`);
- assinatura RSA PKCS#1 v1.5/SHA-256 verificada com pyca/cryptography; só depois disso
  as claims são consideradas;
- `iss = https://token.actions.githubusercontent.com`; `aud` = digest acima (string,
  não lista); `sub = repo:nunovinhas-creator/goal-roi:ref:refs/heads/main`;
  `repository` e `repository_owner` exatos; `ref = refs/heads/main`, `ref_type = branch`;
  `workflow_ref = nunovinhas-creator/goal-roi/.github/workflows/collect.yml@refs/heads/main`;
  `event_name` ∈ {schedule, workflow_dispatch}; `runner_environment = github-hosted`;
  sem claim `environment`;
- `run_id:run_attempt` = dono da reserva (`reserve` e `complete`). Num `abandon` basta
  qualquer execução de `collect.yml@main`, porque a prova da tentativa terminal está
  dentro do evento atestado;
- tempo: `nbf ≤ iat + 60`, `iat ≤ exp`, duração ≤ 1 h, e `attested_utc` entre
  `iat − 300 s` e `iat + 60 s`;
- qualquer falha => o journal não carrega, nada é aceite como evidência e o ensaio
  pára (fail-closed). O genesis continua a ser a âncora administrativa revista.

Ligação de cada token ao conteúdo e à posição: o mesmo token não serve para outro
evento, nem para o mesmo evento noutra posição da cadeia (testado). O journal remoto
GitHub exige genesis com atestação (`attestation_required`).

Expiração e rotação de chaves:
- tokens expiram ~5 min após a emissão. Na escrita exige-se frescura; no replay
  histórico a expiração posterior NÃO é tratada como fraude (verifica-se a coerência
  temporal do próprio token e a ligação ao `attested_utc`);
- chaves confiáveis: (a) `PINNED_JWKS`, fixadas num commit revisto a partir do JWKS
  oficial; (b) o documento de descoberta oficial e o JWKS obtidos por TLS no momento,
  só se o `kid` não estiver fixado. Exige-se `issuer` igual e `jwks_uri` dentro do
  emissor; sem redirects; uma tentativa limitada por processo;
- chave histórica removida do JWKS e não fixada => `oidc_key_unknown_unverifiable`:
  o ensaio pausa. É "não verificável", não "fraude", e não é corrigido
  automaticamente: exige fixar a chave por revisão;
- fornecedor indisponível => `oidc_keys_unavailable`: pausa sem esperas repetidas;
- chave, URL ou JWKS introduzidos no journal pelo atacante: nunca consultados (testado);
- evidência histórica incompleta (evento sem atestação) => rejeitada.
- Não consegui ler o documento de descoberta nem o JWKS a partir desta sessão (o proxy
  bloqueia o host); por isso `PINNED_JWKS` está vazio. Fixar as chaves é um passo
  administrativo (E.6).

Dependência: `cryptography==44.0.3` (pyca, Apache-2.0/BSD, wheels abi3; testado com
44.0.3 e 50.0.1). Importada só dentro do tail: sem ela o ensaio salta e a recolha
normal não muda. Custo: gratuito; ~4 MB no `pip install` do coletor (com cache).
No CI, `tests.yml` passa a instalá-la (edição de um workflow existente, não um novo).

Garantias demonstradas (22 testes em `tests/test_recovery_oidc.py`, assinaturas RSA
reais): evento legítimo e replay noutro verificador; assinatura forjada com `kid`
genuíno; `aud` errado; evento alterado depois do token; evento genuíno copiado para
outra posição; repositório, branch, workflow de PR, `workflow_ref` de outro ficheiro;
`run_id`/`run_attempt` errados; claims em falta ou inesperadas (`environment`,
`job_workflow_ref`, `aud` em lista, self-hosted, emissor); `alg=none`, HS256, `jwk`/`jku`
no cabeçalho; evento sem atestação; token expirado válido no histórico mas recusado
na escrita; rotação (fixada passa sem rede, retirada → não verificável, fornecedor em
baixo → pausa, chave do atacante ignorada); `complete` forjado com bytes inventados
(replay recusa e não escreve staging); `abandon` forjado; `abandon` genuíno mantém o
débito; falha de atestação depois da reserva gasta o slot sem reenvio; sexta tentativa
bloqueada; OIDC em baixo sem afetar a recolha normal; JWKS pendurado cortado pelo
watchdog; bearer fora de resultados e exceções; JWKS só do emissor oficial; chaves
fracas ou de outro tipo recusadas.

Riscos residuais:
1. **Fronteira de confiança = código a correr no job `collect` em `main`.** Com
   `id-token: write`, qualquer passo do job (leitor Telegram, LLM, notify, ações
   `actions/checkout@v4` e `setup-python@v5` fixadas por tag, dependências pip) pode
   pedir um token com qualquer `aud` e atestar eventos. É a mesma fronteira que já
   existe: o `checkout` deixa o `GITHUB_TOKEN` de escrita na configuração Git, ao
   alcance de todos os passos. Reduzir: fixar as ações por SHA (alteração de produção,
   não incluída).
2. Quem faz push para `main` pode alterar o coletor e atestar o que quiser.
3. OIDC prova a identidade da execução, não a origem dos bytes. A proveniência BSD
   continua a depender do TLS e do código auditado da ponte HTTP (ainda por fazer).
4. Um append forjado não é aceite, mas pára o ensaio de vez (DoS), porque a ref não
   pode ser reescrita.
5. Os JWT ficam guardados num ref público. Expiram em ~5 min e o `aud` é próprio deste
   journal, por isso nenhum serviço os aceita.
6. Personalizar o template `sub` do OIDC no repositório faria rejeitar todos os tokens
   (fail-closed). Não personalizar.

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
5. **Permissões do job**: `id-token: write` acrescentado (patch `collect-yml`).
   `contents: write` e `actions: write` mantêm-se; `id-token` não dá acesso ao
   repositório, só a pedir tokens OIDC.
6. **`PINNED_JWKS`**: procedimento em H.2. Não há chaves reais nesta PR.

## F. Genesis — NÃO construído

Campos obrigatórios e estado:

| Campo | Estado |
|---|---|
| `mode`, `trial_id`, `cap`, `reserve`, `attestation` | conhecidos (`inline-five-slots`, `goal-roi-bsd-20261113-15-v1`, 5, 100, `github-oidc-v1`) |
| `prior_debits` | **não comprovado**: o repositório mostra 0 pedidos de ensaio, mas usos manuais fora do repositório não são verificáveis (declaração, ponto 5) |
| `exclusive_account_review_ref` | **não existe** (declaração por assinar) |
| `predecessor_budget_review_ref` | depende de `prior_debits` |
| `snapshot_sha` | só deve ser fixado na revalidação final dos 10 IDs, imediatamente antes |

Construir agora exigiria inventar valores; por isso não foi feito. Comandos para quando
houver dados (máquina do titular):

```sh
W=$(mktemp -d); git init --bare -q "$W/j.git"
cat > "$W/genesis.json" <<'JSON'
{"kind":"genesis","config":{"mode":"inline-five-slots","attestation":"github-oidc-v1",
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

| Ordem | Patch | Alteração |
|---|---|---|
| 1 | `requirements-proposal.patch` | `cryptography==44.0.3` |
| 2 | `collector-tail-proposal.patch` | `results_bsd.py` +18/−3: `request_json(observe=None)`, `run(after_bsd=None)` |
| 3 | `collect-yml-proposal.patch` | `id-token: write`; `timeout-minutes` 15 → 18; 1.º passo grava `GOAL_ROI_JOB_STARTED_UTC` |
| 4 | `actions-pin-proposal.patch` | `checkout`/`setup-python` fixados por SHA; ajuste mínimo de `tests/test_notify_durability.py` |

Os quatro aplicam em sequência sobre `main` `7e349c5` e a suite do próprio `main`
(433 testes) passa, com 1–3 e com 1–4 (detalhe em H.4).

Zero linhas tocam em Telegram, notify, relatório, agendamento, encadeamento ou
certificação. Sem callback, os hooks são inertes (testado byte a byte). Riscos de
instalar:
- uma falha no `pip install` (agora com mais um pacote) impede a recolha, tal como
  hoje acontece com o Telethon;
- o passo novo pode, em teoria, falhar antes da recolha;
- `id-token: write` alarga a todos os passos a capacidade de pedir tokens OIDC (D, risco 1);
- um job pendurado leva +3 min a terminar;
- o teste que fixa o blob do `collect.yml` tem de ser atualizado na PR de instalação.

Tempo (watchdog total de 150 s, inalterado):
- todas as operações de rede novas (pedido do token, descoberta e JWKS) estão
  limitadas a ≤ 10 s cada e cortadas ao fim do tail;
- a verificação RSA custa milissegundos por evento e cada commit é verificado uma vez
  por processo;
- a reserva depois do envio subiu de 80 s para 90 s (um token para o `complete`).

No pior caso, as operações antes do envio (~6 fetch/push Git, token e JWKS) podem
passar a janela de envio (150 − 90 = 60 s). Nesse caso a reserva confirmada não envia
e o slot fica gasto: é fail-closed, nunca ultrapassa o tempo. Fixar `PINNED_JWKS`
retira o JWKS do caminho. A garantia `timeout-minutes: 18` mantém-se válida (≥ 150 s).

Garantias obrigatórias em falta para o primeiro pedido (NO-GO até todas existirem):
1. Declaração C assinada e revista; `prior_debits` comprovado.
2. ~~Atestação OIDC~~: implementada e testada offline; falta auditoria humana do código
   e fixar `PINNED_JWKS` (E.6).
3. Ruleset, permissões, genesis atestado e `PROVISIONED_ANCHOR` (E.1–E.4) concluídos e verificados.
4. Patches instalados e terceiro commit auditado (wiring no `main()` com `ActionsAttestor`
   e `Verifier`, ponte HTTP ≤ 20 s, namespace real separado de `synthetic_wire`).

Primeira execução, depois de tudo isso: recolha normal guardada → cabeçalho < 60 s
sem queda inexplicada → saldo ≥ 100 + slots restantes → tail cabe nos 15 min originais
→ watchdog → reserva atestada e confirmada → UMA página → bytes/hash/UTC duráveis →
`complete` atestado → staging com revisão manual. Qualquer incerteza gasta o slot, sem
reenvio; reverter = remover o callback.

## H. Auditoria independente e instalação controlada (sobre `1af3c75`)

### H.1 Auditoria criptográfica

Li `recovery_oidc.py`, `recovery_inline.py`, `recovery_remote.py`, `recovery_tail.py`
e os testes. Procurei cada via pedida e mais sondagens adversariais (script
descartável, não versionado):

| Tentativa | Resultado |
|---|---|
| Evento adulterado depois do token | recusado (`aud` = digest do evento) |
| Reutilizar um JWT noutro evento ou noutra posição | recusado (`aud` inclui o commit pai) |
| Substituir uma chave confiável (`kid` genuíno com outra chave; `jwk`/`jku` no cabeçalho; chave no journal) | recusado |
| Eventos de outra execução (`run_id`/`run_attempt`/workflow/branch/repositório) | recusado |
| Resultados falsos em staging (`complete` forjado) | recusado; o replay não escreve `engine-state.json` |
| Aumentar ou reiniciar os 5 débitos (genesis novo, `genesis` acrescentado, `abandon` forjado) | recusado; âncora fixa; `abandon` mantém o débito |
| Contornar por exceções ou caches | sem bypass: a cache guarda só commits já verificados (o SHA liga conteúdo e pai); a falha do JWKS fica em cache como indisponível, nunca como válida; exceções propagam e param o journal |
| JWT malformado (padding, ponto final, corpo re-serializado) | recusado |
| Campo extra na atestação; `attested_utc` em falta | recusado |

**Vulnerabilidades demonstradas: nenhuma.** Não alterei a lógica OIDC.

Observações (não exploráveis):
- um commit cujo JSON não é objeto termina em `AttributeError` em vez de
  `TrialBlocked`. Continua fail-closed (o journal não carrega e o `PreparedTail` e o hook
  apanham `Exception`);
- `InlineSlots` aceita journals sem atestação quando o remoto não é o GitHub (fixtures
  `file://`). O terceiro commit (wiring) TEM de construir o store com `GITHUB_REMOTE`;
  caso contrário, um journal local daria 5 slots novos. Este é um requisito de auditoria
  desse commit;
- se o GitHub reutilizar um `kid` fixado com outra chave, os tokens novos falham como
  `signature_invalid` (fail-closed). É improvável.

### H.2 Chaves públicas oficiais (`PINNED_JWKS`)

Estado: **não fixadas**. O host `token.actions.githubusercontent.com` está bloqueado
tanto para o proxy desta sessão (CONNECT 403) como para o agente de leitura web (DNS).
Não inventei chaves.

Acrescentado nesta revisão:
- cada chave fixada tem de trazer `rfc7638_sha256` (thumbprint RFC 7638), conferido no
  arranque do `Verifier` (`validate_pinned`). Um `n` trocado ou um campo em falta dá
  `oidc_pinned_key_corrupt` (testado);
- `scripts/oidc_jwks_evidence.py` (só leitura, para o titular correr): obtém descoberta e
  JWKS por TLS (CA do sistema, só o host do emissor), valida `issuer`/`jwks_uri` e cada
  chave com as mesmas regras do verificador, e regista hora UTC, URL, SHA-256 de cada
  resposta, certificado TLS folha (SHA-256, sujeito, emissor, validade) e, por chave,
  `kid`, bits, thumbprint RFC 7638 e SHA-256 do SPKI. Imprime a proposta de
  `PINNED_JWKS` mas não edita código (testado offline).

Procedimento proposto:
1. O titular corre o script em duas redes independentes (ex.: casa e rede móvel) e os
   `kid`/thumbprints têm de coincidir.
2. Commit revisto: `PINNED_JWKS` = proposta, mais os ficheiros de evidência em
   `audits/bsd-inline-review/oidc-keys/<data>.json` (chaves públicas, sem segredos).
   O revisor confere os thumbprints contra a evidência.
3. Rotação: repetir o script antes de cada janela de ensaio e sempre que o verificador
   reportar `oidc_key_unknown_unverifiable`; acrescentar o `kid` novo por commit revisto.
4. Retenção: NUNCA remover uma chave fixada enquanto existirem eventos assinados com ela;
   só depois de o ensaio ser encerrado e arquivado.
5. Com o fornecedor inacessível: não fixar nada; o ensaio fica bloqueado.

Alternativa: permitir esse host no ambiente desta sessão cloud deixaria o Claude
recolher uma segunda observação independente. Não foi pedido nem feito.

### H.3 Segurança dos workflows

- Ações externas em `collect.yml`: `actions/checkout@v4` e `actions/setup-python@v5`
  (tags móveis). Confirmado em 2026-11-16T19:48:42Z por `git ls-remote` e `git fetch`
  nos repositórios oficiais: `v4` = `11d5960a326750d5838078e36cf38b85af677262`
  (= v4.4.0, commit) e `v5` = `a26af69be951a213d495a4c3e4e4022e16d87065` (= v5.6.0,
  commit). Evidência em `actions-pin-evidence.txt`. Fixar no SHA que as tags móveis já
  usam hoje não muda o comportamento.
- Achado: o `main` tem um teste (`test_notify_durability`) que exige o texto literal
  `actions/checkout@v4`, porque a barreira de durabilidade do notify depende das
  credenciais persistidas do checkout. Fixar a ação sem o ajustar partiria o CI do
  `main`. O patch 4 inclui o ajuste mínimo: aceita a tag ou este SHA e passa a exigir
  explicitamente que não exista `persist-credentials: false`, preservando a garantia
  original.
- `id-token: write` vale para o job inteiro; o GitHub não permite restringi-lo a um passo.
  Fica ao alcance do leitor Telegram, do LLM, do notify, das duas ações e das
  dependências pip. A fronteira já existia (o `GITHUB_TOKEN` de escrita persistido pelo
  checkout está ao alcance de todos os passos). Os riscos novos ficam reduzidos ao fixar
  as ações (patch 4). Isolar num job próprio seria uma mudança de arquitetura, fora de
  âmbito. `tests.yml` (só CI de PR) continua com tags móveis.
- Permissões necessárias à recolha preservadas: `contents: write` e `actions: write`.

### H.4 Integração e impacto

- `main` mais recente `7e349c5`. `results_bsd.py`, `requirements.txt`, `collect.yml` e
  `test_notify_durability.py` têm blobs idênticos aos desta branch, por isso o teste de
  neutralidade byte a byte vale para o `main` atual.
- Patches 1–3 e 1–4 aplicados ao `main`: a suite do `main` passa (433/433 em ambos).
  Sem os ajustes do patch 4, a fixação isolada falhava 1 teste (achado acima).
- `cryptography==44.0.3` para CPython 3.11 em Linux x86_64 instala 3 wheels binárias:
  `cryptography` (4,2 MB), `cffi` e `pycparser` (versões não fixadas: as mais recentes
  compatíveis). Nenhuma dependência é partilhada com o Telethon (`pyaes`, `rsa`). A
  importação só acontece dentro do tail.
- Nenhum patch toca em Telegram, relatório, calendário/cron, cadeia de encadeamento ou
  certificação; o ajuste de teste do patch 4 só muda a forma de verificar o checkout.

### H.5 Pendentes (bloqueiam o primeiro pedido real)

1. Declaração de exclusividade da conta BSD (C), por assinar.
2. `prior_debits` por comprovar.
3. Referência Git remota protegida (ruleset), por criar.
4. `PROVISIONED_ANCHOR` = `None`.
5. Chaves OIDC reais verificadas (H.2), por fixar.
6. Autorização expressa dos 4 patches, por dar.
7. Auditoria da ponte HTTP real (TLS, ≤ 20 s, sem redirects), por fazer.
8. Namespace de evidência real separado de `synthetic_wire`/`prepared-fixture-trial`,
   por implementar.

### H.6 Instalação controlada (quando autorizada)

1. PR separada a partir do `main`, com os patches 1–4 por esta ordem e sem nenhum código
   do ensaio ativo (sem callback, sem `PreparedTail` no `main()`).
2. CI verde no `main` patcheado (inclui os 433 testes atuais).
3. Merge e observação de pelo menos 3 execuções do coletor: passos verdes, duração e
   `results_bsd.json` sem diferenças de comportamento, `GOAL_ROI_JOB_STARTED_UTC` presente,
   cadeia de encadeamento intacta.
4. Reversão: revert do merge. Nada fica ativo, por isso não há estado a limpar.
5. Só depois disso: genesis, ruleset, chaves e terceiro commit (H.5), cada um com
   autorização própria.
