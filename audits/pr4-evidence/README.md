# Evidências da PR 4 (protocolo `llm_incremental_v4`)

Código avaliado: commit `c99cdf7` (implementação do v4). Dados: `main` congelado em `ab60140c914d386f3ae6e51d0ece0f6252aa7e35`.
Protocolo: `d48e3b7`, publicado (servidor GitHub) às 2026-11-16T08:05:26Z, Actions run 43828482856. H = 2027-11-16T08:05:26Z;
C = 2027-11-19T08:05:26Z. Resposta à revisão e às auditorias: `../pr4-review-response.md`.
Todos os comandos correm na raiz de um clone completo (`git fetch --unshallow`), com Python 3.11, sem rede e sem escrever em `data/`.

PUB = `--protocol-published-utc 2026-11-16T08:05:26Z --protocol-publication-evidence https://github.com/nunovinhas-creator/goal-roi/actions/runs/43828482856`

| Ficheiro | Como reproduzir |
|---|---|
| `../pr4-llm-evaluation.json`, `../pr4-llm-evaluation.md` | `python3.11 scripts/audit_pr4_llm.py --ref ab60140c914d386f3ae6e51d0ece0f6252aa7e35 --protocol-commit d48e3b7 PUB --out audits` (repetir e comparar com `cmp`) |
| `golden-compare.json` | `python3.11 audits/pr4-evidence/golden_compare.py ab60140c914d386f3ae6e51d0ece0f6252aa7e35 c99cdf7 ab60140c914d386f3ae6e51d0ece0f6252aa7e35 out.json` |
| `truncation-real-data.json` | `python3.11 audits/pr4-evidence/truncation_check.py 3378115a60edf231d1372f289d52fdad5567e8e3 ab60140c914d386f3ae6e51d0ece0f6252aa7e35 d48e3b7 2026-11-16T08:05:26Z https://github.com/nunovinhas-creator/goal-roi/actions/runs/43828482856 out.json` |
| `calibration.json` | `python3.11 audits/pr4-evidence/calibration_check.py out.json` (e-processo de produção sob nulas adversariais, fronteira A-sel e poder; cerca de 15 minutos) |
| `p1e_method_comparison.json` | `python3.11 audits/pr4-evidence/p1e_method_comparison.py out.json` (comparação de métodos que motivou o v4; cerca de 10 minutos) |
| `missing_results_rate.txt` | `python3.11 audits/pr4-evidence/missing_results_rate.py ab60140c914d386f3ae6e51d0ece0f6252aa7e35` |
| `mutation_check.txt` | `python3.11 audits/pr4-evidence/mutation_check.py` (64 mutantes) |
| `tests-python3.11.log` | `python3.11 -m unittest discover -s tests -v` (o comando do workflow `tests.yml`) |

A garantia do e-processo é um teorema (§6-A do protocolo v4); as simulações verificam a implementação e o erro empírico, não
substituem a demonstração. As versões anteriores destes ficheiros (protocolos v1, v2 e v3) ficam no histórico git
(`73bdc7c`, `ea3b970`, `9d60503`).
