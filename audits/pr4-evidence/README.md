# Evidências da PR 4

Código avaliado: commit `918a18c`. Dados: `main` congelado em `68348cbae0fa7435dce8cf36828ac6e8ace6e150`. Protocolo: `9f7b2aa`.
Todos os comandos correm na raiz de um clone completo (`git fetch --unshallow`), com Python 3.11, sem rede e sem escrever em `data/`.

| Ficheiro | Como reproduzir |
|---|---|
| `../pr4-llm-evaluation.json`, `../pr4-llm-evaluation.md` | `python3.11 scripts/audit_pr4_llm.py --ref 68348cbae0fa7435dce8cf36828ac6e8ace6e150 --protocol-commit 9f7b2aa --out audits` (repetir e comparar com `cmp`) |
| `golden-compare.json` | `python3.11 audits/pr4-evidence/golden_compare.py 68348cbae0fa7435dce8cf36828ac6e8ace6e150 918a18c 68348cbae0fa7435dce8cf36828ac6e8ace6e150 out.json` |
| `truncation-real-data.json` | `python3.11 audits/pr4-evidence/truncation_check.py 3378115a60edf231d1372f289d52fdad5567e8e3 68348cbae0fa7435dce8cf36828ac6e8ace6e150 9f7b2aa out.json` |
| `mutation_check.txt` | `python3.11 audits/pr4-evidence/mutation_check.py` (cerca de 2–3 minutos) |
| `tests-python3.11.log` | `python3.11 -m unittest discover -s tests -v` (o comando do workflow `tests.yml`) |
