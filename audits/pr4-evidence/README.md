# Evidências da PR 4 (protocolo `llm_incremental_v2`)

Código avaliado: commit `9ee9408`. Dados: `main` congelado em `96c176228b00c7d45aead41eb75e0be2992042c0`. Protocolo: `25cd2d2`,
publicado (servidor GitHub) às 2026-11-15T22:36:47Z, Actions run 43770264461. Resposta à revisão: `../pr4-review-response.md`.
Todos os comandos correm na raiz de um clone completo (`git fetch --unshallow`), com Python 3.11, sem rede e sem escrever em `data/`.

PUB = `--protocol-published-utc 2026-11-15T22:36:47Z --protocol-publication-evidence https://github.com/nunovinhas-creator/goal-roi/actions/runs/43770264461`

| Ficheiro | Como reproduzir |
|---|---|
| `../pr4-llm-evaluation.json`, `../pr4-llm-evaluation.md` | `python3.11 scripts/audit_pr4_llm.py --ref 96c176228b00c7d45aead41eb75e0be2992042c0 --protocol-commit 25cd2d2 PUB --out audits` (repetir e comparar com `cmp`) |
| `golden-compare.json` | `python3.11 audits/pr4-evidence/golden_compare.py 96c176228b00c7d45aead41eb75e0be2992042c0 9ee9408 96c176228b00c7d45aead41eb75e0be2992042c0 out.json` |
| `truncation-real-data.json` | `python3.11 audits/pr4-evidence/truncation_check.py 3378115a60edf231d1372f289d52fdad5567e8e3 96c176228b00c7d45aead41eb75e0be2992042c0 25cd2d2 2026-11-15T22:36:47Z https://github.com/nunovinhas-creator/goal-roi/actions/runs/43770264461 out.json` |
| `mutation_check.txt` | `python3.11 audits/pr4-evidence/mutation_check.py` (cerca de 3 minutos) |
| `tests-python3.11.log` | `python3.11 -m unittest discover -s tests -v` (o comando do workflow `tests.yml`) |

As versões anteriores destes ficheiros (protocolo v1, commit `73bdc7c`) ficam no histórico git.
