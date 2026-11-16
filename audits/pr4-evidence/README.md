# Evidências da PR 4 (protocolo `llm_incremental_v5`)

Código avaliado: commit `cbeb57a` (implementação do v5). Dados: `main` congelado em `31a7988716172c00ba76a008acd90676cfd87b6e`.
Protocolo: `bb1b07c`, publicado (servidor GitHub) às 2026-11-16T08:56:55Z, Actions run 43835421997. H = 2027-11-16T08:56:55Z;
C = 2027-11-19T08:56:55Z. Resposta à revisão e às auditorias: `../pr4-review-response.md`. Integração: `../pr4-integration-runbook.md`.
Todos os comandos correm na raiz de um clone completo (`git fetch --unshallow`), com Python 3.11, sem rede e sem escrever em `data/`.

PUB = `--protocol-published-utc 2026-11-16T08:56:55Z --protocol-publication-evidence https://github.com/nunovinhas-creator/goal-roi/actions/runs/43835421997`

| Ficheiro | Como reproduzir |
|---|---|
| `../pr4-llm-evaluation.json`, `../pr4-llm-evaluation.md` | `python3.11 scripts/audit_pr4_llm.py --ref 31a7988716172c00ba76a008acd90676cfd87b6e --protocol-commit bb1b07c PUB --out audits` (repetir e comparar com `cmp`) |
| `../pr4-integration-preflight.json` | `python3.11 scripts/pr4_preflight.py 31a7988716172c00ba76a008acd90676cfd87b6e cbeb57a out.json` (merge simulado, testes e comparação com `main`) |
| `golden-compare.json` | `python3.11 audits/pr4-evidence/golden_compare.py 31a7988716172c00ba76a008acd90676cfd87b6e cbeb57a 31a7988716172c00ba76a008acd90676cfd87b6e out.json` |
| `truncation-real-data.json` | `python3.11 audits/pr4-evidence/truncation_check.py 3378115a60edf231d1372f289d52fdad5567e8e3 31a7988716172c00ba76a008acd90676cfd87b6e bb1b07c 2026-11-16T08:56:55Z https://github.com/nunovinhas-creator/goal-roi/actions/runs/43835421997 out.json` |
| `a_sel_analysis.json` | `python3.11 audits/pr4-evidence/a_sel_analysis.py 87a5bd8534e2eb2c6f407719f2758628186b2b19 out.json` (dados reais e simulação do mecanismo; cerca de 20 minutos) |
| `calibration.json` | `python3.11 audits/pr4-evidence/calibration_check.py out.json` (e-processo de produção sob nulas adversariais, fronteira A-sel, poder; intervalos exatos de Clopper-Pearson) |
| `p1e_method_comparison.json` | `python3.11 audits/pr4-evidence/p1e_method_comparison.py out.json` (comparação de métodos que motivou o v4) |
| `missing_results_rate.txt` | `python3.11 audits/pr4-evidence/missing_results_rate.py 31a7988716172c00ba76a008acd90676cfd87b6e` |
| `mutation_check.txt` | `python3.11 audits/pr4-evidence/mutation_check.py` (68 mutantes) |
| `tests-python3.11.log` | `python3.11 -m unittest discover -s tests -v` (o comando do workflow `tests.yml`) |

A garantia do e-processo é um teorema (§6-A do protocolo); as simulações verificam a implementação e o erro empírico, não substituem
a demonstração. As versões anteriores destes ficheiros (protocolos v1 a v4) ficam no histórico git (`73bdc7c`, `ea3b970`, `9d60503`,
`a12a087`).
