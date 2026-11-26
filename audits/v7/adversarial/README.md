# Auditoria adversarial dos snapshots — constatações e correção (V7.2)

Âmbito: protótipo de snapshots da PR #103 (`audits/v7/snapshots/`). A primeira ronda (HEAD `660fa123`) documentou os defeitos com `expectedFailure`/`todo`. Esta ronda acrescenta um publicador e um cliente corrigidos, **sem alterar o protótipo da #103**:

- `audits/v7/snapshots/publisher_v2.py`
- `audits/v7/snapshots/client_integrity_v2.mjs`

Também compara três desenhos. Todas as constatações passam a testes aprovados, com as mesmas asserções. Tudo offline: snapshots inativos, sem produção, sem publicação.

## Constatações e estado

| # | Constatação (ronda 1) | Correção | Prova (passa) |
|---|---|---|---|
| A1 | A publicação reescrevia a ranhura anunciada como `previous` | CAS: objetos imutáveis; nada anunciado é reescrito | `test_a1_…` em cada um dos 7 pontos de interrupção |
| A2 | Com `current` corrompida, a única revisão íntegra era reescrita | Idem; o histórico só mantém revisões verificadas | `test_a2_…` (7 pontos) |
| A3 | Leitor atrasado 2 publicações não lia nada | Retenção contada **desde a substituição** (≥ TTL da CDN) | `test_a3_…` (20 publicações), `test_a3c_…` (período calmo seguido de rajada) |
| A3b | — | Fora da retenção, o leitor deteta e volta a pedir o ponteiro | `test_a3b_…`, cliente C2 |
| A4 | Ponteiro numa linha (proteção do rebase) | Mantido (`separators` compactos) | `test_a4_…`, conflito real entre clones Git |
| A5 | Layout fora da lista publicável; `.lock` na pasta servida | Lista `PUBLISHABLE` exata; lock fora da pasta; temporários interrompidos apagados no início | `test_a5_…`, `test_github_pages_compatible_names` |
| A6 | O recuo tinha de ser distinguível | `inputs` e `published_at` diferentes; o cliente devolve `which` | `test_a6_…`, C3 |
| C1 | O cliente pedia qualquer caminho | Caminho = `objects/<sha256>/dashboard-<nome>.json`, com o SHA igual ao declarado, validado **antes** de qualquer pedido | C1 (URL absoluta, `../`, SHA trocado, query) |
| C2 | O cliente não voltava a pedir o ponteiro | Percorre o histórico e depois volta a pedir o ponteiro uma vez | C2 |
| D2 | `?v=` com caminhos mutáveis envenenava a chave CDN | CAS: o caminho é a versão; não há query | staging (PR #106), cenário "CDN sem query" |

Defeitos que só apareceram nesta ronda, também corrigidos:

- A retenção contada desde a publicação perdia a revisão atual depois de um período calmo (`test_a3c_…`).
- Um relógio atrasado noutro anfitrião fazia adiar a publicação mesmo sem limite configurado (`test_clock_behind_the_last_publication_never_defers`, encontrado pelo teste concorrente).

Outras provas que passam:

- SIGKILL em cada rename (7 pontos).
- 8 publicadores concorrentes no mesmo anfitrião, com entradas diferentes: nenhuma revisão misturada.
- Dois clones Git que publicam a partir da mesma base: conflito no ponteiro, abort e repetição limpa.
- Reparação da revisão atual com as mesmas entradas.
- Objeto partilhado corrompido reescrito.
- Limitação de frequência, que nunca adia uma revisão corrompida.
- Armazenamento limitado ao fim de 100 publicações.
- Cliente: `node --test` corre na CI através de `NodeClient` (`# todo 0`, `# fail 0`).

## Comparação dos três desenhos

| Propriedade | 3 ranhuras (escolha por integridade) | CAS (objetos imutáveis) | Limitação de frequência ≥ 600 s |
|---|---|---|---|
| A1/A2 (interrupção, corrupção) | Corrige | Corrige | Não corrige sozinha |
| Leitor atrasado k publicações | Falha com k ≥ 3 (`test_three_slots_are_not_enough_for_delayed_readers`) | Lê enquanto a retenção cobrir o atraso | Reduz k, não elimina o risco |
| CDN que ignora query strings | Serve corpos antigos pelo caminho → recuo para dados atrasados | Sem efeito: o caminho é a versão | — |
| Cache em níveis diferentes | Ponteiro de um nível com corpos de outro → relatório completo | Sem efeito enquanto retido | — |
| 2 ranhuras com escolha segura | Sem revisão anterior anunciada (`test_two_slots_cannot_announce_a_previous_revision`) | — | — |
| Push Git por publicação (40 relatórios reais, b29f0be4–68f451d9) | **8,6 KB** | 57,0 KB | 67,9 KB (19 de 40 publicadas) |
| Pack após `git gc`, 40 revisões | 177 KB | 206 KB | 136 KB |

O nome `objects/<sha256>/dashboard-<nome>.json` (hash no diretório, nome estável no fim) foi escolhido depois de medir. Com `objects/<nome>/<sha256>.json`, o pack após `gc` era 829 KB. O push do CAS não usa deltas porque o Git só usa como base objetos com o mesmo caminho. É custo de rede, não de armazenamento: ~36 MB/dia de envio a ~640 publicações/dia, ou ~10 MB/dia com o limite de 600 s.

Simulação CDN (`cdn_slot_sim.py`, 1 628 publicações reais em 7 dias, TTL 600 s, pior caso com corpos fora de cache):

| Desenho | Recurso ao relatório completo |
|---|---:|
| 2 ranhuras | 19,0 % |
| 3 ranhuras | 0,8 % |
| 3 ranhuras + limite 300 s | 0 % |
| CAS, retenção 300 s | 3,8 % |
| **CAS, retenção ≥ 600 s (= TTL)** | **0 %** |

## Recomendação

**CAS, com retenção ≥ 1 800 s desde a substituição e limite de publicação de 600 s.** Três ranhuras não bastam: falham com leitores atrasados ≥ 3 publicações e com CDN que ignore query strings. O limite de frequência não corrige A1/A2 sozinho, mas reduz o envio Git e o número de objetos. O porte exige alterar a lista de `publication.py` para `PUBLISHABLE` e a recolha de lixo em `git rm` (fora do âmbito; autorização própria).

## Vereditos

| Item | Veredito |
|---|---|
| A1–A6, C1–C2 no publicador e cliente V7.2 | **Corrigidos offline** (testes aprovados, sem xfail/todo) |
| Três ranhuras como solução | **NO-GO** (insuficientes; provas acima) |
| CAS + retenção + limite | **GO para staging** |
| Ativação dos snapshots / alteração da PR #98 / produção | **NO-GO** (autorização própria; fim do período experimental do Brain V5) |

## Reproduzir

```sh
export PYTHONDONTWRITEBYTECODE=1
python3 -m pytest -q tests/test_v7_adversarial_103.py tests/test_v7_snapshot_publisher.py
node --test tests/test_v7_adversarial_client.mjs tests/test_v7_client_integrity.mjs
python3 audits/v7/adversarial/cdn_slot_sim.py
python3 audits/v7/adversarial/git_layout_cost.py --repo . --ref origin/main --revisions 40
```

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
