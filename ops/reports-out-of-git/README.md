# Retirar os relatórios gerados do histórico Git futuro (sem reescrever o histórico)

Investigação e plano por fases. Esta PR só acrescenta `scripts/build_pages_site.py`, um teste e este documento; **não muda a produção**. Nenhuma fase abaixo é executada sem autorização própria, e nenhuma antes do fim do período experimental do Brain V5.

## Problema

Os relatórios gerados (`data/report.json`, `docs/report.json`, `docs/bets.csv`) são reescritos a cada recolha e ficam na história do `main`. No pack Git autocontido de 24 h (PR #103, `packs-24h-por-classe.json`), as **vistas ocupam 38,1 MB/dia de 47,6 MB (80 %)**; as fontes `data/*` ocupam 2,6 MB/dia. As vistas não têm valor científico histórico: derivam das fontes e do código de cada commit.

`data/report.json` e `docs/report.json` são o **mesmo blob** (o workflow copia um para o outro; verificado: bytes iguais). Retirar só um deles não poupa nada. A poupança vem de deixar de os versionar a ambos, ou de os versionar com muito menor frequência.

## Consumidores (inventário de 26/11/2026)

| Consumidor | O que lê | Como | Efeito se o ficheiro sair do Git |
|---|---|---|---|
| Pages: painel clássico (`docs/dashboard.js`) | `report.json` | `fetch('report.json')` relativo ao site | Nenhum, se o site publicar o mesmo caminho |
| Pages: V6.1 (`docs/v6/v6-app.js`) | `../report.json`, `snapshots/…` | idem | Nenhum, idem |
| Android | o site Pages | HTTP | Nenhum, idem (validar no dispositivo) |
| Brain V5: `ciclo.py`, `sincronizar.py` | relatório **prospetivo** | Git/raw | Nenhum (confirmado pelo responsável: não usam o relatório geral) |
| Brain V5: restantes scripts e `brain-sync.sh` (repositório privado `goal-roi-brain`) | desconhecido | — | **Por confirmar.** A leitura do repositório foi recusada nesta sessão. Correr `check_consumers.sh` no repositório e na pasta Termux (abaixo) |
| Avisos Telegram (`scangoal/notify.py`, `PAGE`) | ligação para a raiz do site | HTTP | Nenhum, se o site mantiver o endereço |
| `docs/index.html` (botões "Descarregar CSV" e "Abrir JSON") | `bets.csv`, `report.json` | ligações relativas | Nenhum: o site publica os mesmos caminhos |
| `audits/dashboard-ux/capture.cjs`, `audits/v6-intelligence/measure.cjs` | `docs/report.json` | ficheiro local num checkout | Ferramentas de auditoria: gerar o relatório localmente (`python -m scangoal.report`) ou descarregá-lo do site |
| Repositórios públicos `Monte-Carlos`, `ET-spotter`, `tens-direito`, `tensdireito-content-engine` | — | — | Nenhuma referência ao goal-roi (pesquisa de código a 26/11/2026) |
| Terceiros que leiam o site público | `/report.json`, `/bets.csv` | HTTP | Nenhum, se o site mantiver os caminhos; desconhecidos que usem `raw.githubusercontent.com` partem na fase 2 (a opção B mitiga) |
| `raw.githubusercontent.com/…/main/data/prospective_report.json` (V6) | registo prospetivo | raw | Nenhum: não é vista, fica no Git |
| `scangoal/report.py` | escreve `data/report.json` e `docs/bets.csv` | ficheiro local | Nenhum (continua a escrever localmente) |
| `scangoal/watch.py` | escreve `docs/report.json` | ficheiro local | Precisa de publicar o site no fim do ciclo |
| `scangoal/notify.py` | lê `data/report.json` | ficheiro local, na mesma execução | Nenhum |
| `scangoal/certification.py --activate` | lê `data/report.json` "publicado" | ficheiro local num checkout manual | **Muda:** sem ficheiro no Git, o operador tem de gerar ou obter o relatório publicado (do site, com o SHA do manifesto) |
| `scangoal/publication.py` | `PATHS` inclui `docs/report.json`, `docs/bets.csv` | lista de caminhos permitidos | Retirar da lista na fase 2 |
| `.github/workflows/collect.yml` | `cp data/report.json docs/report.json` | passo do workflow | Substituir pela publicação do site |
| `scripts/*cutover*.py`, `post_cutover_check.py` | `git show <commit>:data/report.json` | história | Continuam a funcionar para commits antigos; deixam de servir para commits futuros |

### Verificação do Brain e do Termux (só leitura, a correr pelo responsável)

```sh
sh ops/reports-out-of-git/check_consumers.sh ~/goal-roi-brain ~/caminho-do-termux
```

Lista cada referência a `report.json` ou `bets.csv` (o relatório prospetivo é excluído), ao site ou ao `raw` do goal-roi. Código 0 = nenhuma; 1 = rever antes da fase 2. Não altera nada; não toca no `brain-sync.sh`.

`tests/test_pages_site.py` fixa este inventário: uma referência nova em `scangoal/` ou `.github/` faz o teste falhar e obriga a rever a tabela.

## Opções

| Opção | Reescreve história? | Crescimento das vistas | Consumidores por Git/raw | Mudança de definições |
|---|---|---|---|---|
| A. **Pages por artefacto do Actions** + deixar de versionar as vistas | Não | ~0 | Partem (precisam do site) | Pages: origem "GitHub Actions" |
| B. Versionar as vistas só a cada 12 h; o site é publicado por artefacto a cada recolha | Não | ~1/70 do atual | Ficam, com até 12 h de atraso | Igual a A |
| C. Ramo separado para as vistas | Não, mas o ramo cresce para sempre; limpá-lo seria reescrita | Igual ao atual no repositório | Mudam de ramo | Nenhuma |
| D. Ativos de release sobrescritos | Não | ~0 | Partem; URL diferente | Nenhuma |

Recomendação: **A, com B como passo intermédio**. A preserva os caminhos públicos do site (`/report.json`, `/bets.csv`, `/v6/`), que servem o painel, a V6.1 e o Android. B dá tempo para validar o Brain sem o partir.

## Fases

**Fase 0 (esta PR, inativa).** `scripts/build_pages_site.py` monta o site numa pasta: estáticos de `docs/` + relatório + CSV nos mesmos caminhos; valida o JSON e as referências locais; escreve `site-manifest.json` com SHA-256. Teste: o site é **byte a byte igual a `docs/`** em todos os caminhos públicos.

**Fase 1 (dupla publicação; exige autorização).**
1. Settings → Pages → origem "GitHub Actions" (gratuito em repositório público).
2. No fim de `collect.yml`, depois do último `publication publish`: `python3 scripts/build_pages_site.py --out _site`, `actions/upload-pages-artifact` e `actions/deploy-pages` (fixar por SHA, como os outros passos), com `permissions: pages: write, id-token: write` e `concurrency` próprio. Os commits das vistas **continuam** nesta fase.
3. Critério: durante 7 dias, o `report.json` servido tem o SHA-256 igual ao do commit da mesma recolha (`site-manifest.json`), e o painel clássico, a V6.1 e o Android funcionam.

**Fase 2 (deixar de versionar; exige autorização e validação do Brain).**
1. Confirmar no Brain V5 e no Termux, com `check_consumers.sh` (código 0), que nenhum lê `data/report.json`, `docs/report.json` ou `docs/bets.csv` por Git ou raw. `ciclo.py` e `sincronizar.py` já estão confirmados.
2. Um commit normal: `git rm --cached data/report.json docs/report.json docs/bets.csv` e `.gitignore`. A história anterior fica intacta e consultável.
3. Retirar os caminhos de `publication.py` (`PATHS`) e o `cp` de `collect.yml`. `watch.py` volta a publicar o site no fim do ciclo.
4. `certification --activate`: documentar a obtenção do relatório publicado a partir do site com conferência do SHA do manifesto.
5. Opcional (opção B): um commit das vistas a cada 12 h para consumidores por Git, se o Brain precisar.

## Critérios de aprovação

- Fase 0: CI verde; `tests/test_pages_site.py` passa.
- Fase 1: 7 dias de igualdade de SHA-256 entre o site e o commit; zero regressões nos três clientes.
- Fase 2: Brain validado por escrito; crescimento diário do pack medido com `audits/v7/storage/pack_split.py` abaixo de 10 MB/dia; nenhuma alteração ao protocolo nem ao registo prospetivo.

## Reversão

- Fase 1: Pages volta à origem "branch `main` /docs" (os commits das vistas nunca pararam).
- Fase 2: `git revert` do commit que deixou de versionar as vistas; a recolha seguinte volta a escrevê-las. Nada é perdido, porque as vistas derivam das fontes.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
