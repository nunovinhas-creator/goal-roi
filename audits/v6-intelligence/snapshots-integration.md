# Integração operacional dos snapshots V6

Esta PR é separada e está empilhada sobre a PR #97. **Fica inativa por omissão:** nada muda em produção enquanto a variável do repositório `GOAL_ROI_V6_SNAPSHOTS` não for `on`.

## Caminhos de publicação analisados

| Caminho | Escreve `docs/report.json` | Snapshots |
| --- | --- | --- |
| `collect.yml` → passo "Report (fixed rules)" | `cp data/report.json docs/report.json` | passo novo "V6 dashboard snapshots", **depois** do registo prospetivo e **antes** de "Publish the report before sending notices" |
| `scangoal.watch.refresh` (leituras rápidas de 2 em 2 minutos) | cópia atómica para `docs/report.json` | `dashboard_snapshots.publish_quietly()` logo a seguir, antes de `publisher.publish()` |
| `scangoal.publication` (commit e push com retoma) | `PATHS` | `docs/v6/snapshots` acrescentado; `check_paths` aceita só o prefixo `docs/v6/snapshots/` |
| `Commit data`, `Commit data from the fast reads`, `resume` | — | os snapshots seguem no mesmo commit da revisão do relatório; a retoma repete o pacote completo |

## Garantias

- **Coerência:** o manifesto regista o SHA-256 de `docs/report.json`. Se o relatório mudar durante a construção, o resultado é descartado.
- **Atomicidade por revisão:**
  - os ficheiros têm nomes endereçados por conteúdo (`dashboard-<nome>.<sha256[:16]>.json`);
  - o manifesto é escrito por último, com um rename atómico;
  - a geração anterior é mantida, para que um manifesto em cache no CDN continue a resolver;
  - o cliente recusa qualquer ficheiro cujo hash não coincida.
- **Concorrência:** um lock de ficheiro serializa a recolha e as leituras rápidas. A publicação git é fast-forward com retoma; os dados de outro escritor mantêm-se (testado).
- **Falhas:** o builder falha, excede o tempo ou devolve um ficheiro adulterado → mantém-se o manifesto anterior. O dashboard deteta snapshots atrás do relatório e usa o relatório completo (PR #97). O passo tem `continue-on-error` e nunca bloqueia a recolha, os avisos nem o registo prospetivo.
- **Ciência:** só lê `data/prospective_report.json` e o protocolo. Nenhum dos ficheiros alterados está em `prospective.CODE_V2` (os hashes do protocolo congelado não mudam).

## Ativação e reversão (decisão humana)

- **Ativar:** criar a variável do repositório `GOAL_ROI_V6_SNAPSHOTS=on`.
- **Reverter:** remover a variável. Os últimos snapshots ficam atrás do relatório e o dashboard volta ao relatório completo no ciclo seguinte.

## Testes

`tests/test_dashboard_snapshots.py` (12 testes) cobre:
- inativo por omissão;
- coerência e nomes endereçados por conteúdo;
- entradas protegidas intactas;
- rotação de gerações;
- falha do builder;
- relatório alterado durante a construção;
- saída adulterada;
- timeout e ausência de `node`;
- 4 construções concorrentes;
- caminhos de publicação;
- posição e inatividade do passo no workflow;
- publicação git real com um escritor concorrente.
