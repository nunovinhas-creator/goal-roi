# Staging HTTPS dos snapshots

Custo: 0 €. Não contrata serviços, não usa credenciais e não publica nada. Não toca em `docs/`, `data/`, Pages, workflows nem no Brain V5. Snapshots inativos.

Este ramo inclui a PR #105 (publicador e cliente V7.2) e repete os cenários sobre HTTPS para os três desenhos.

## 1. Staging local (executado)

| Peça | Função |
|---|---|
| `https_cdn.cjs` | CA e certificado efémeros; origem HTTPS com os cabeçalhos do Pages (`max-age=600`, ETag); CDN simulada com chave com ou sem query string, relógio controlado e contagem de chaves cujo corpo não tem o SHA prometido. |
| `publish_rev.py` | Publica uma revisão com o builder real: `--design v1` (PR #103), `slots3` ou `cas` (PR #105). Recusa escrever em `docs/` ou `data/`. |
| `drill.mjs` | Cenários S1–S5 para os três desenhos. |
| `tests/test_v7_staging_https.py` | 7 testes sobre os resultados; corre na CI. |

### Resultados (`resultados-staging.json`)

| Cenário | v1 (#103) | 3 ranhuras | **CAS** |
|---|---|---|---|
| S1 TLS sem a CA | recusado | recusado | recusado |
| S2 ponteiro em cache, 1 publicação | atual | atual | atual |
| S3 mesma cache, 2 publicações | atual | atual | atual |
| S3b ponteiro num nível, corpos noutro, 3 publicações | **relatório completo**; 2 chaves envenenadas | **relatório completo** | **atual**, 0 chaves envenenadas |
| S4 CDN com query na chave | atual | atual | atual |
| S4 CDN **sem** query na chave | **anterior (dados atrasados)** | atual (com mais publicações falharia) | **atual** |
| S5 corpo atual corrompido na origem | anterior | anterior | relatório completo*; a publicação seguinte repara → atual |

\* No CAS, a revisão atual e a anterior partilhavam o objeto corrompido (conteúdo igual). O recuo não ajuda e o cliente usa o relatório completo, nunca dados errados. A publicação seguinte reescreve o objeto. Nas ranhuras, cada revisão tem cópia própria; é a única vantagem delas aqui.

Os três desenhos deixam na pasta servida só caminhos da lista `PUBLISHABLE` (compatível com o Pages: sem `_` nem `.` iniciais).

## 2. Staging remoto — opção A (preparado, **não publicado**)

GitHub Pages num repositório separado, **só com dados sintéticos**.

| Peça | Função |
|---|---|
| `remote/synthetic.py` | Relatório e entrada prospetiva 100 % sintéticos ("Equipa A01…", ano 2030). Não lê dados reais. |
| `remote/prepare_site.py` | Monta o site numa pasta nova fora do repositório: K revisões CAS, cliente V7.2, página `index.html` + `probe.mjs`, `.nojekyll` e `SITE-MANIFEST.json`. |
| `remote/measure_remote.mjs` | Depois de publicado: a cada minuto regista `age`, `x-cache`, `x-served-by`, o resultado do cliente e se a CDN separa uma query string nova. Só aceita `https://<utilizador>.github.io/…`. |
| `tests/test_v7_staging_remote_package.py` | Só dados sintéticos (nenhum nome de `team_aliases.json`, nem "scangoal" ou "Betfair"); layout publicável; hashes do manifesto; recusa de URL fora do Pages. |

Verificação local: `prepare_site.py` + servidor local + Chromium. O cliente leu a revisão atual em 39 ms, sem erros.

### Passos depois de autorização explícita (nenhum foi executado)

1. Criar um repositório **público** novo e vazio, por exemplo `goal-roi-staging`, sem segredos nem Actions. O Pages é gratuito em repositórios públicos.
2. `python3 ops/staging/remote/prepare_site.py --out ../goal-roi-staging-site --revisions 6`; fazer commit do conteúdo no repositório novo.
3. Settings → Pages → origem "branch `main` / root".
4. Durante 2 h, publicar uma revisão sintética nova a cada 2–10 min (mesmo script, `--revisions` crescente) e correr `node ops/staging/remote/measure_remote.mjs --base https://<utilizador>.github.io/goal-roi-staging --minutes 120`.
5. p95 completo com `audits/v7/snapshots/perf_p95.cjs` (20 repetições) contra o URL de staging.
6. No fim: desativar o Pages e arquivar ou apagar o repositório. Nada toca no repositório de produção.

### Critérios de aprovação

- TLS válido; leitura `current` na primeira visita.
- A chave da CDN inclui ou não a query string (registar); o CAS não depende disso.
- Em 2 h com publicações a cada 2–10 min: zero leituras com dados errados; recurso ao relatório completo abaixo de 1 %.
- p95 completo ≤ 2 s.

### Reversão

Desativar o Pages e apagar o repositório de staging. Não há estado em produção.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
