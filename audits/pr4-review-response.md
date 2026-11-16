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

---

# Auditoria independente #5916209217 (SHA `ea3b970`) — P1-D: avaliações repetidas e controlo de α

O v2 é imutável. Foi registado o **protocolo `llm_incremental_v3`** (`audits/pr4-llm-protocol-v3.md`, sha256 `e3190be0…89a4`)
num commit próprio (`0766a13`), com as emendas declaradas em §0. O v1 e o v2 ficam intactos como registo histórico e são recusados pelo código.

**Prova de publicação do v3:** a execução de Actions
[43822686064](https://github.com/nunovinhas-creator/goal-roi/actions/runs/43822686064) tem `head_sha` = `0766a13` e
`created_at` = **2026-11-16T07:16:49Z** (commit às 07:16:42Z). Logo:
- **P** = 2026-11-16T07:16:49Z;
- **H** = 2026-12-16T07:16:49Z;
- **C** = 2026-12-19T07:16:49Z.

## 1. Avaliações repetidas não produzem falsas significâncias

- **Horizonte único:** a janela fecha em H = P + 30 dias. A única avaliação confirmatória é a que tem o relógio em C = H + 3 dias,
  o prazo de liquidação.
- **Antes de C**, o veredicto é sempre `exploratória — antes do instante confirmatório (sem conclusão)`, qualquer que seja o p,
  e as análises adicionais não têm p ajustado.
- **Em C ou depois**, o relógio fica fixado em C e a avaliação ignora tudo o que veio depois:
  - sinais lidos depois de H, mesmo dentro de jogos da janela;
  - resultados observados depois de C;
  - hipóteses registadas depois de H, que nem entram nas contagens; o script indica quantas são, nas entradas.

  Correr outra vez dá exatamente o mesmo resultado, byte a byte.
- **Sem extensão:** se faltar amostra em C, o veredicto `evidência insuficiente` é definitivo para o v3. Uma nova tentativa exige
  outro protocolo, com nova publicação e só dados novos.
- **Mínimos preservados:** os de jogos, grupos e diversidade temporal mantêm-se. Acresce ≥ 14 dias emparelhados, necessário ao novo teste.

## 2. Calibração do teste sob ausência de utilidade incremental

Simulação reprodutível, com sementes fixas e 2 000 repetições por cenário (erro de Monte Carlo 0,0049):
`audits/pr4-evidence/calibration_check.py` → `calibration.json`.

| Cenário nulo | v3: inversão de sinais por dia | v2: bootstrap por blocos de dia |
|---|---|---|
| 14 dias, efeito de dia moderado | 0,0510 | 0,0730 |
| 21 dias, efeito de dia forte | 0,0475 | 0,0695 |
| 30 dias, efeito de dia forte | 0,0540 | 0,0545 |
| 21 dias, LLM em cotações longas e controlos em curtas | 0,0430 | 0,0545 |
| 21 dias, LLM em cotações curtas e controlos em longas | **0,0640** | 0,0705 |

- O bootstrap do v2 era anticonservador; foi substituído no v3 por diferenças dentro do mesmo dia, testadas por inversão aleatória de
  sinais por dia. O efeito comum de cada dia cancela-se.
- **Risco residual, declarado:** no último cenário (lados com perfis de cotação opostos, que tornam assimétricas as diferenças
  diárias), o v3 rejeita 6,4 %. O intervalo de 95 % é 0,054–0,074 e exclui 0,05: é ligeiramente anticonservador nesse caso.
  Fica melhor do que o v2 e dentro do critério de α + 3 erros-padrão que fixei, mas por pouco. Na prática, os controlos são
  emparelhados por família, tamanho e apoio, o que reduz esta assimetria. Corrigi-la exigiria outro teste, portanto um v4; não o
  fiz sem autorização.
- **Poder** (referência, 30 dias): 16,7 % com um efeito de +10 % e 38,5 % com +20 %.

## 3. Paragem oportunista

Sob a hipótese nula, com 600 séries:
- olhar nos dias 14, 16, …, 30 e parar no primeiro p ≤ 0,05 rejeita **12,0 %**;
- o olhar único no horizonte rejeita **5,8 %**.

O v3 só permite o segundo.

## 4. Testes e mutação

- **8 testes novos do P1-D:**
  - antes de C nenhum olhar conclui, mesmo com p < 0,05;
  - o resultado confirmatório é idêntico em C, C + 1 dia e C + 90 dias, mesmo com dados muito favoráveis depois de H;
  - a janela termina em H;
  - hipóteses registadas depois de H não são avaliadas;
  - a paragem oportunista inflaciona α e o olhar único não;
  - o teste está calibrado sob a nula;
  - caso exato pequeno (p = 1/8);
  - o efeito de dia cancela-se, incluindo com tamanhos diários desiguais.
- Mais 1 teste para o mínimo de dias emparelhados.
- **Defeitos encontrados pelos testes novos e corrigidos:** a taxa de acerto descritiva, a contagem de pertença e as
  características dos sinais usavam dados posteriores a H; a contagem de hipóteses registadas depois da janela vinha do registo posterior.
- **Primeira mutação (47 mutantes):** 3 sobreviveram.
  - Dois eram guardas redundantes (os filtros da janela já estavam implicados pela vista lida até H); foram substituídos por uma guarda
    única e testada, `cutoff_of`.
  - Um era uma lacuna de teste: o emparelhamento por dia não se distinguia com efeitos de dia simétricos. Acrescentou-se um caso com
    tamanhos diários desiguais.
- O resultado final está em `audits/pr4-evidence/mutation_check.txt`.

## 5. Resultado com o v3 (`main` congelado em `d91d839`, 07:28:38Z)

- **Veredicto:** `exploratória — antes do instante confirmatório (sem conclusão)`. Ainda não há jogos depois de P.
- 56 hipóteses LLM, 56 grupos (iguais a 0,80 e 0,90), 34 representantes na primária, 17 sem controlos suficientes.
- 27 das 56 propostas eram redundantes no instante da proposta.
- Janela pós-registo e pré-protocolo, só descrição, 5 dias: LLM 108 jogos a −0,061 por jogo; controlos 150 jogos a +0,033.
- **Confirmatório:** a avaliação com o relógio em 2026-12-19T07:16:49Z, uma única vez.
