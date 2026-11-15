# PR 4/5 — Resposta à revisão independente preliminar (PR #27)

Os três pontos confirmaram-se com testes adversariais **antes** de qualquer correção. Como as correções mudam regras escritas
no protocolo v1, o v1 não foi editado: foi registado o protocolo `llm_incremental_v2` num commit próprio, com as emendas
declaradas, e publicado de imediato para obter uma prova de publicação do servidor GitHub.

| Commit | Conteúdo |
|---|---|
| `25cd2d2` | Protocolo `audits/pr4-llm-protocol-v2.md` (sha256 `1f4a1001…1339`), só esse ficheiro. O v1 (`9f7b2aa`) fica intacto e deixa de ser aceite |
| `9ee9408` | Implementação das emendas e testes de regressão |
| seguinte | Evidências regeneradas (sem código) |

## P1-A — apoio dos controlos pela hora de emissão

- **Reprodução no código anterior (`73bdc7c`):** 20 sinais da liga de controlo B0, emitidos antes do nascimento das LLM mas lidos
  3 dias depois, mudavam os controlos escolhidos (6 → 5): o apoio "no nascimento" contava sinais que o sistema ainda não conhecia.
- **Correção:** `controls()` usa um retrato `snap(t)` só com os sinais elegíveis **lidos** até `born_eff(h)`
  (`chronology.as_of_view`, hora de leitura provada), para o apoio e para a exclusão de controlos com os mesmos sinais.
- **Regressão:** `test_P1A_signal_sent_before_birth_but_read_after_never_affects_controls`. A leitura tardia não muda nada; os
  mesmos sinais lidos antes do nascimento excluem B0, como devem.

## P1-B — grupos dependentes de jogos posteriores

- **Reprodução no código anterior:** duas hipóteses sem nenhum jogo em comum no nascimento da segunda; 200 jogos posteriores
  fundiam-nas num grupo e a segunda perdia o lugar de representante.
- **Correção:** `equivalence.groups_at_birth`. Cada hipótese é comparada no seu nascimento com os jogos dos sinais já lidos
  nesse instante (os seus e os de cada membro). A decisão nunca é revista, por isso a pertença e os representantes são fixos.
  A página usa a mesma regra.
- **Regressões:** os jogos posteriores não juntam grupos separados no nascimento, não separam grupos formados no nascimento, e os
  sinais emitidos antes mas lidos depois não contam.
- **Efeito nos dados reais:** 51 grupos em vez de 50. O v1 fundia um par com jogos posteriores ao nascimento.

## P2-C — hora do commit versus publicação verificável

- **Problema:** P era a hora do commit, escrita pelo autor. A primeira prova do servidor GitHub para o v1 é posterior: a execução de
  Actions 43770023387, de 2026-11-15T22:34:15Z, contra o commit às 22:03:16Z.
- **Correção (protocolo §0-A):** P = max(hora do commit, primeira publicação verificável no GitHub). O avaliador exige a hora do
  servidor e uma ligação `https://github.com/…`. Sem elas, ou com uma hora anterior ao commit, a avaliação sai **não verificada**.
  Dados anteriores a P nunca são prospetivos.
- **Prova do v2:** a execução de Actions [43770264461](https://github.com/nunovinhas-creator/goal-roi/actions/runs/43770264461)
  tem `head_sha` = `25cd2d2` (o próprio commit do protocolo) e `created_at` = **2026-11-15T22:36:47Z**. O commit é de 22:36:33Z,
  logo **P = 22:36:47Z**. A auditoria independente deve confirmar esta hora na ligação.
- **Regressões:** sem prova de publicação, ligação fora do GitHub ou publicação anterior ao commit levam a "não verificada"; um
  commit com data recuada fica com P na publicação; os dados entre o commit e a publicação não são prospetivos; o v1 é recusado.

## Evidências regeneradas (dados de `main` congelado em `96c1762`, 23:30:46Z)

- 321 testes em Python 3.11 (36 adversariais da PR 4, 6 deles novos); 37 de 37 mutantes apanhados, 8 deles para as emendas.
- Avaliação v2: **evidência insuficiente**, ainda sem jogos depois de P. Reprodutível byte a byte. Sem os argumentos de publicação: "não verificada".
- Comparação com `main` nos mesmos dados: estados (4 promissoras e 1 abandonada), certificação desligada, mensagens Telegram (0),
  estado de notificações e dados idênticos.
- Truncagem em dados reais (avaliação às 11:59:17Z com os dados dessa hora e com os de `96c1762`): idêntica.

## Limitações que se mantêm

- O nascimento das hipóteses continua provado pela hora dos commits de dados em `main` (feitos pelo coletor em GitHub Actions).
  Não se cruza com os registos de execução do servidor. É uma melhoria possível, fora destes três pontos.
- A hora de publicação é indicada ao avaliador e confirmada pela auditoria: o script não tem rede e não a consulta sozinho.
