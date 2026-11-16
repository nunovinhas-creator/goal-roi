# PR 4/5 — Registo da integração (16/11/2026)

Autorização expressa do proprietário depois da auditoria independente aprovada (revisão #5917720634).

## Preflight final (só leitura)

- **M:** `main` congelado em `0c375a8aab4453dbec031bb23ffa422e8874df32` (09:49:29Z).
- **H:** HEAD da PR `9782e67e8ed29b17622aeca566c4bac03b4c1fa2`, o SHA auditado.
- **`scripts/pr4_preflight.py M H`** → `ready = true` (`preflight-0c375a8.json`):
  - merge simulado sem conflitos;
  - `data/`, `.github/`, `docs/report.json` e `docs/bets.csv` idênticos a M;
  - sem registo de certificação;
  - 343 testes a passar na árvore resultante;
  - comparação operacional idêntica (7 situações, certificação `not_activated`, 0 mensagens Telegram, estado de notificações e dados).
- **Integridade** (`integrity-0c375a8.json`):
  - Telegram: `sent_total` 312, 117 entregas registadas, marcas PR 1–3, referência igual à lista de situações;
  - protocolos v1 a v5 com os SHA originais (`9f7b2aa`, `25cd2d2`, `0766a13`, `d48e3b7`, `bb1b07c`), cada um antecessor de H e
    introdutor do seu ficheiro, sem alterações posteriores.
- `main` não avançou entre o congelamento e o merge (confirmado às 09:51:22Z).

## Merge

- **PR #27:** retirada de Draft e integrada com `merge_method=merge` e `expected_head_sha = 9782e67…`. Sem *squash*, sem *rebase*,
  sem *force-push*.
- **Commit de merge:** `57101133e81a1274e5109ec29c4dd7ae9a40d6c4`, com pais `0c375a8` (M) e `9782e67` (H). A árvore
  `526436b8…` é idêntica à árvore testada no preflight.
- **Ficheiros de produção** alterados pelo merge face ao primeiro pai: nenhum. Os cinco commits de protocolo estão na história de `main`.
- **CI em `main`:** Tests run 43843548103, êxito.

## Recolhas seguintes (código novo)

| Recolha | Commit | `post_cutover_check` | LLM v5 na página | 7 situações | Certificação | Telegram |
|---|---|---|---|---|---|---|
| #650 (recolha) | `42b10df` | código 0 | "não verificada" | iguais | `not_activated` | 312 → 312, 117 → 117, marcas iguais |
| #650 (leituras rápidas) | `9f6bc4f` | código 0 | "não verificada" | iguais | `not_activated` | 312 → 312, sem mensagens |
| #651 (recolha) | `c68dd51` | código 0 | "não verificada" | iguais | `not_activated` | 312 → 312, sem mensagens |

- Nenhuma notificação falsa: zero envios e a referência de estados não mudou.
- A recolha #650 ficou 15 minutos em fila nos runners do GitHub antes de arrancar (criada às 09:57:49, iniciada às 10:13:08). Não é
  um efeito do código: os passos correram normalmente, e o relatório levou 19 s contra 12 s antes, o custo do resumo da página.
- O passo da LLM corre de hora a hora e ainda não tinha corrido com o código novo nestas recolhas. A primeira execução trará
  `rejected_equivalent` em `data/llm_state.json` e `provenance` nas propostas aceites.
