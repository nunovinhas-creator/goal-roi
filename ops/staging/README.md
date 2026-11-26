# Staging HTTPS isolado para os snapshots V7

Custo: 0 €. Não contrata serviços, não usa credenciais, não publica nada e não toca em `docs/`, `data/`, no Pages, nos workflows ou no Brain V5.

## 1. Staging local (pronto, executado)

| Peça | Função |
|---|---|
| `https_cdn.cjs` | CA e certificado efémeros (openssl, 1 dia, só `localhost`/`127.0.0.1`), origem HTTPS com os cabeçalhos do Pages (`max-age=600`, ETag) e uma cache partilhada que imita a CDN: chave com ou sem query string, relógio simulado e contagem de chaves envenenadas. |
| `publish_rev.py` | Publica uma revisão com o publicador V7 (PR #103) e o builder real, a partir de cópias do relatório. Recusa escrever em `docs/` ou `data/`. |
| `drill.mjs` | Cenários S1–S5 com o cliente `client_integrity.mjs` (SHA-256 obrigatório). |
| `tests/test_v7_staging_https.py` | Asserções sobre os cenários; corre na CI (`node` e `openssl` existem no ubuntu-24.04). |

```sh
node ops/staging/drill.mjs            # escreve ops/staging/resultados-staging.json
python3 -m pytest -q tests/test_v7_staging_https.py
```

### Resultados (`resultados-staging.json`)

| Cenário | Resultado |
|---|---|
| S1 TLS e leitura base | Sem a CA, o TLS é recusado (`UNABLE_TO_VERIFY_LEAF_SIGNATURE`); com a CA, lê `current`. |
| S2 1 publicação dentro do TTL | `current` (atrasada, coerente). |
| S3 ponteiro e corpos na mesma cache, 2 publicações | `current` atrasada e coerente: os corpos com `?v=` ficaram em cache ao mesmo tempo que o ponteiro e duram mais do que ele. Zero chaves envenenadas. |
| S3b ponteiro num nível de cache, corpos noutro | **Relatório completo** para este leitor e para o seguinte; 2 chaves `?v=` passam a guardar bytes de outra revisão até ao fim do TTL. Recupera quando o ponteiro expira. |
| S4 CDN com query string na chave | `current`. |
| S4 CDN sem query string na chave | **`previous`** (dados atrasados) apesar de `current` existir: a CDN devolve corpos antigos pelo caminho. |
| S5 corpo atual corrompido na origem | `previous`, como previsto. |

Isto corrige o modelo da auditoria adversarial (PR #105): os 19 % de recurso ao relatório completo assumiam corpos sempre fora de cache. Com uma única cache partilhada, um ponteiro atrasado continua coerente (S3). O risco real existe quando o ponteiro e os corpos ficam em níveis diferentes: cache do navegador, outro nó da CDN ou despejo LRU (S3b). A frequência desse caso só se mede numa CDN real.

## 2. Staging HTTPS remoto (preparado, não executado — exige autorização)

O que só uma CDN real responde:

1. A chave de cache do Pages/Fastly inclui a query string? (S4)
2. Com que frequência há ponteiro e corpos em níveis ou nós diferentes? (S3b)
3. p95 do primeiro conteúdo e da conclusão sobre HTTPS real (meta 2 s; laboratório 1,79 s).

Opções sem custo, por ordem de preferência. Todas **publicam** conteúdo e por isso exigem autorização explícita:

| Opção | Custo | Isolamento | Ressalva |
|---|---|---|---|
| A. Repositório público novo só com dados sintéticos e GitHub Pages | 0 € (plano gratuito) | Total: outro repositório, sem dados reais | Publica uma página pública com dados fictícios. |
| B. Implantação de pré-visualização Vercel (conta já ligada) só com dados sintéticos | 0 € no plano Hobby, se a conta for Hobby | Projeto novo, sem domínio de produção | Confirmar plano e limites antes; não é a mesma CDN do Pages (só responde a 3). |
| C. Repositório privado com Pages | Exige plano pago | — | Excluída (custo). |

Procedimento para A, a executar só depois de autorização:

1. Criar o repositório de staging vazio (nome sugerido `goal-roi-staging`), público, sem segredos nem Actions.
2. Gerar a origem com dados sintéticos: `python3 ops/staging/publish_rev.py --out <dir> --rev 1 --report <relatório-sintético>` (relatório sintético: cópia com equipas, mensagens e identificadores substituídos; nunca o relatório real).
3. Publicar `<dir>` e uma página mínima com o cliente; ativar Pages na branch.
4. Medir com `drill.mjs` adaptado a uma URL real (sem relógio simulado: intervalos reais de 2–10 min durante 2 h) e com `perf_p95.cjs` (20 repetições).
5. Registar cabeçalhos `age`, `x-cache`, `x-served-by` e o resultado S3b/S4.
6. Desativar o Pages e arquivar o repositório no fim. Nenhum passo toca no repositório de produção.

### Critérios de aprovação

- TLS válido; S1, S2 e S5 iguais ao local.
- A chave da CDN inclui a query string (S4 com query = `current`).
- S3b medido: fração de recursos ao relatório completo abaixo de 1 % com a correção de 3 ranhuras ou limite ≥ 600 s.
- p95 completo ≤ 2 s em 20 repetições.

### Reversão

Local: apagar as pastas temporárias (o ensaio já o faz). Remoto: desativar o Pages e arquivar ou apagar o repositório de staging. Não há estado em produção a reverter.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
