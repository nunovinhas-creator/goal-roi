# Evidências da PR 4 (protocolo `llm_incremental_v3`)

Código avaliado: commit `1986596`. Dados: `main` congelado em `d91d839c410992c00551be74c78772e72648b466`. Protocolo: `0766a13`,
publicado (servidor GitHub) às 2026-11-16T07:16:49Z, Actions run 43822686064. H = 2026-12-16T07:16:49Z; C = 2026-12-19T07:16:49Z.
Resposta à revisão e à auditoria: `../pr4-review-response.md`.
Todos os comandos correm na raiz de um clone completo (`git fetch --unshallow`), com Python 3.11, sem rede e sem escrever em `data/`.

PUB = `--protocol-published-utc 2026-11-16T07:16:49Z --protocol-publication-evidence https://github.com/nunovinhas-creator/goal-roi/actions/runs/43822686064`

| Ficheiro | Como reproduzir |
|---|---|
| `../pr4-llm-evaluation.json`, `../pr4-llm-evaluation.md` | `python3.11 scripts/audit_pr4_llm.py --ref d91d839c410992c00551be74c78772e72648b466 --protocol-commit 0766a13 PUB --out audits` (repetir e comparar com `cmp`) |
| `golden-compare.json` | `python3.11 audits/pr4-evidence/golden_compare.py d91d839c410992c00551be74c78772e72648b466 1986596 d91d839c410992c00551be74c78772e72648b466 out.json` |
| `truncation-real-data.json` | `python3.11 audits/pr4-evidence/truncation_check.py 3378115a60edf231d1372f289d52fdad5567e8e3 d91d839c410992c00551be74c78772e72648b466 0766a13 2026-11-16T07:16:49Z https://github.com/nunovinhas-creator/goal-roi/actions/runs/43822686064 out.json` |
| `calibration.json` | `python3.11 audits/pr4-evidence/calibration_check.py out.json` (cerca de 2 minutos; simulação, sem dados de produção) |
| `mutation_check.txt` | `python3.11 audits/pr4-evidence/mutation_check.py` (cerca de 5 minutos) |
| `tests-python3.11.log` | `python3.11 -m unittest discover -s tests -v` (o comando do workflow `tests.yml`) |

A avaliação confirmatória (§0-B do protocolo v3) faz-se uma única vez, com `--as-of` em C ou depois, sobre um commit de `main`
posterior a C; qualquer execução nessas condições dá o mesmo resultado. As versões anteriores destes ficheiros (protocolos v1 e v2)
ficam no histórico git (`73bdc7c`, `ea3b970`).
