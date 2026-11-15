# PR 4/5 — Auditoria adversarial própria (desenvolvimento)

> **Atualização:** o protocolo v1 foi substituído pelo `llm_incremental_v2` depois da revisão independente preliminar
> (P1-A, P1-B, P2-C). Ver `audits/pr4-review-response.md`; as evidências em `audits/pr4-evidence/` foram regeneradas com o v2.
> O texto abaixo descreve a auditoria do v1 (`73bdc7c`).

Auditoria do próprio programador, **não independente**. A auditoria independente deve repetir tudo isto sobre o SHA
final do ramo `claude/prs-4-5-llm-telegram-faxwbm`, com contexto limpo e só leitura.

## 1. Commits

| Commit | Conteúdo |
|---|---|
| `be36529` | Planos técnicos das PRs 4 e 5 |
| `8362eb5` | Plano da PR 4 com as decisões do proprietário (15/11/2026) |
| `9f7b2aa` | **Protocolo pré-registado** `llm_incremental_v1`, commit próprio; P = 2026-11-15T22:03:16Z; sha256 do ficheiro `8b108bf9…5cb0` |
| `918a18c` | Implementação, testes e scripts (código avaliado por todas as evidências) |
| seguinte | Evidências, relatório científico e esta auditoria (sem alterações de código) |

Dados avaliados: `main` congelado em `68348cbae0fa7435dce8cf36828ac6e8ace6e150` (2026-11-15T22:12:11Z).

## 2. Decisões do proprietário: verificação

| # | Decisão | Como está cumprida | Evidência |
|---|---|---|---|
| 1 | Rejeitar só duplicados exatos e redundâncias extensionais demonstradas na proposta | `equivalence.Redundancy` (mesmos sinais que uma hipótese existente ou que uma sub-combinação, incluindo a família); nunca por Jaccard | T7, T8 (Jaccard 17/18 aceite), gémeos na mesma resposta |
| 2 | Sem limites diários/totais; README corrigido; orçamento de neurónios | Código inalterado nesses limites; README e docstring corrigidos; `DAILY_NEURONS` intacto | `git diff` de `llm.py` |
| 3 | Jaccard 0,85 só exploratório, sensibilidade 0,80/0,90, sem transitividade | Ligação completa + compatibilidade (família, expectativa, condições não contraditórias); S6/S7 | testes de agrupamento; mutações "complete linkage", "same expectation", "no contradictory condition" |
| 4 | 20 controlos; ≥ 10 grupos, ≥ 200 jogos, diversidade temporal | K = 20 sem ler resultados; ≥ 14 dias e ≤ 20 % num dia; cada mínimo isolado num teste | T12, `test_each_minimum_fails_on_its_own`, T18 |
| 5 | Comparação primária e α fixados; Holm nas adicionais | §6 e §9 do protocolo; `holm` com m = 7 fixo | `test_holm`, mutação "holm" |
| 6 | Protocolo num commit próprio; só dados posteriores sustentam conclusões | `protocol_proof` exige o conteúdo registado e o commit que o introduziu; início = max(nascimento, P) | testes GitProofs; `test_pre_protocol_evidence_never_supports_conclusions` |
| 7 | Nascimento pelo histórico git completo; sem prova, sem conclusões | `registry_history` (registo só a crescer, horas monótonas, repositório não raso); veredicto `não verificada` com qualquer falha | T2, GitProofs; página sempre `não verificada` |
| 8 | `track_record` sem `prob_above > 0.5` e sem o transmitir à LLM | Balanço só descritivo, só evidência prospetiva; prompt sem "confirmaram-se/falharam" | `test_track_record_is_descriptive_and_never_confirms`, `test_prompt_carries_the_track_record_only_as_description` |
| 9 | Não alterar as 4 promissoras, as regras da PR 3 nem a certificação | Comparação com `main` sobre os mesmos dados: relatório igual exceto `brain.track` e `brain.llm_evaluation` | `pr4-evidence/golden-compare.json` (`ok: true`) |
| 10 | Testes adversariais, mutações, avaliação reprodutível, relatório científico | 30 testes novos; 31/31 mutantes apanhados; relatório igual byte a byte em duas execuções | secção 3 |

## 3. Resultados das verificações

- **Testes:** 315 passam em Python 3.11 com o comando do workflow `tests.yml` (`python -m unittest discover -s tests -v`;
  `pr4-evidence/tests-python3.11.log`) e com a rede bloqueada (`scripts/test_offline.py`). 285 já existiam; 3 foram adaptados
  porque codificavam o comportamento corrigido (o critério `prob_above > 0.5`; uma proposta agora reconhecida como redundante).
- **CI do GitHub:** o workflow `tests.yml` só corre em pull requests e em `main`; sem PR aberta, não correu. A execução local
  usa o mesmo comando e a mesma versão do Python.
- **Mutação** (`pr4-evidence/mutation_check.txt`): 31 de 31 mutantes apanhados. Na primeira execução sobreviveram 2, ambos
  corrigidos: faltava um teste que isolasse o mínimo de 200 jogos LLM; e a exclusão de `evitar` da primária estava duplicada
  (mutar uma das guardas não mudava nada), passando a ter uma só guarda, testada.
- **Comparação com `main`** (`pr4-evidence/golden-compare.json`): mesmos dados, relógio fixado; relatório idêntico fora das
  duas chaves permitidas; `status_list` igual (4 promissoras e 1 abandonada); `validation.state = not_activated` nos dois;
  nenhum certificado nem registo de certificação; mensagens Telegram calculadas idênticas (0); estado de notificações e ficheiros de dados idênticos.
- **Invariância por truncagem em dados reais** (`pr4-evidence/truncation-real-data.json`): avaliação em 2026-11-15T11:59:17Z com os
  dados desse instante e com os dados de `main` congelado: idêntica; só difere a contagem de 7 hipóteses registadas depois (excluídas).
- **Reprodutibilidade:** `audits/pr4-llm-evaluation.json` (sha256 `93f6221d…2424`) e `.md` (`42c7b9a3…6e20`) iguais byte a byte em execuções repetidas.
- **Prova de nascimento:** 284 versões publicadas de `data/hypotheses.json` lidas pela história de primeiro-pai; nenhuma entrada
  removida ou reescrita; as 51 hipóteses LLM foram publicadas 5 a 48 s depois do seu `born_utc`.
- **Custos:** sem chamadas de rede novas; o resumo da página custa 11 ms e o verificador de redundância 14 ms por execução.
  `llm.run` de ponta a ponta sobre uma cópia dos dados reais, com modelo simulado: entradas antigas intactas, prompt sem linguagem de confirmação.

## 4. Defeitos encontrados e corrigidos durante a auditoria própria

1. A taxa necessária descritiva estava dividida duas vezes pelo número de jogos (só afetava uma coluna descritiva).
2. A redundância à nascença usava a hora de envio dos sinais; passou a usar o que o sistema já tinha lido nesse instante (`as_of_view`).
3. As duas lacunas de testes reveladas pela mutação (secção 3).
4. A comparação com `main` revelou dois campos que dependem do relógio da máquina (`brain.agenda.generated_utc`, `per_day`),
   em código não alterado; a comparação passou a fixar o relógio nas duas execuções em vez de ignorar campos.

## 5. Resultado científico

**Veredicto: evidência insuficiente** (`audits/pr4-llm-evaluation.md`). Às 22:12:11Z só havia 9 minutos de dados depois do protocolo:
nenhum jogo admitido. Nenhum mínimo cumprido.
- 51 hipóteses LLM, todas com nascimento provado; 50 grupos de equivalência (iguais a 0,80 e 0,90); 29 representantes na primária
  (17 sem 5 controlos comparáveis, sobretudo refinadas de 3–4 condições).
- **24 das 51 propostas eram redundantes no instante da proposta** (19 com uma condição que não acrescenta nada; 5 com os mesmos
  sinais de uma hipótese existente). Com a PR 4 teriam sido rejeitadas.
- Janela pós-registo e pré-protocolo (só descrição, 4 dias): LLM 99 jogos a −0,032 por jogo; controlos 140 jogos a +0,047.
  Não é um teste nem uma conclusão.
- Pelos mínimos (≥ 14 dias, ≥ 200 jogos), a primeira conclusão possível exige pelo menos 14 dias de dados posteriores a P.

## 6. Riscos residuais e limitações

- A hora de publicação é a hora do commit; um commit com data falsificada e monótona não seria detetado.
- O workflow usa checkout raso: a página mostra sempre `não verificada`. A avaliação científica só existe pelo script, num clone completo
  (a alteração `fetch-depth: 0` não foi aplicada, por instrução).
- Os controlos vêm do gerador; com hipóteses LLM de 3–4 condições há poucos controlos comparáveis (17 sem controlos).
- Ordem das rejeições em `llm.accept`: uma proposta para lá do limite de 5 conta como `over_limit` mesmo que também fosse redundante.
- A redundância usa a elegibilidade conhecida na proposta; se a elegibilidade de sinais antigos mudar mais tarde, a decisão não é revista (o registo só cresce).

## 7. Restrições

`main`, `data/`, `.github/workflows/` e secrets sem alterações (`git diff 72418c7..HEAD -- data .github` vazio); nenhuma mensagem
Telegram; certificação não ativada; `fetch-depth` não aplicado; sem merge nem PR; custo zero.
