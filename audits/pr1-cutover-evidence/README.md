# Evidência bruta da preparação do corte (15/11/2026)

Ficheiros preservados da área temporária do autor (Claude Code), tal como foram produzidos na primeira passagem, antes de existir o script versionado. **Não são ferramentas de produção.** A versão auditável é `scripts/pr1_cutover.py` + `scripts/rehearse_pr1_cutover.py`, com o resultado em `audits/pr1-cutover-rehearsal.json`.

| Ficheiro | O que é |
|---|---|
| `tests-112-pr-head-dad9279.log` | `python3.11 scripts/test_offline.py` no HEAD `dad9279`: 112 testes, OK |
| `tests-112-merged-main-f94b193.log` | Mesma bateria na fusão local (não publicada) de `dad9279` com `main` `f94b193`: 112, OK |
| `repro-pr1-metrics-python3.11.json` | Reprodução de `scripts/audit_data_eligibility.py` em Python 3.11 |
| `reproduction-python-versions.json` | Única diferença dessa reprodução face ao publicado (−2,78 vs −2,77 un., arredondamento do Python 3.11); 3.12 e 3.13 reproduzem exatamente; `audit_notify_migration.py` reproduz exatamente em 3.11 e 3.12 |
| `dry_run_cutover.v0.py` | Primeiro ensaio, numa cópia dos dados de `main` `f94b193` |
| `dry-run-main-f94b193.json` | Resultado desse ensaio (inclui hashes SHA-256 dos dados de produção lidos) |
| `pr1_cutover.v0.py` | Primeira versão do script de corte (sem restauro automático nem testes); substituída |
| `rehearsal-v0-main-bba39f6.json` | Ensaio geral com essa primeira versão sobre `main` `bba39f6` |
| `p1-rollback-demo.py` | Demonstração do defeito P1 da revisão #5909958197: injeta uma exceção nas pós-verificações e no cálculo dos hashes e lista os ficheiros que ficam alterados (repositórios sintéticos) |
| `p1-rollback-demo-output.jsonl` | Resultado: script `426489e` deixa 3 ficheiros alterados (`OSError`); script corrigido deixa 0 (`CutoverAborted`) |

Todos os ensaios correram com a rede bloqueada, sem credenciais e sem emissor Telegram, em cópias ou worktrees temporárias. Os ficheiros de dados do checkout lido tinham os mesmos hashes antes e depois (`production_data_unchanged: true`); nada foi escrito em `main` nem no GitHub. Os números variam entre ensaios apenas porque `main` recebe dados novos de 8 em 8 minutos; nenhum deles é validação prospectiva.
