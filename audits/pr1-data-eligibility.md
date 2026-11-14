# PR 1 — Integridade e elegibilidade dos dados

Base: `0a4423983bee954beb5ed247349e85c7df47eee4`. Relógio fixado: `2026-11-14T22:00:00+00:00`.
Comparação feita no mesmo snapshot de dados, identificado pelos SHA-256 em `pr1-metrics.json`. Nenhuma escrita em `data/`, `docs/report.json` ou `docs/bets.csv`.

## Estado inicial e testes

Antes das alterações: 92 testes, 91 passaram; 1 falhou (`Settlement.test_manual_result_settles_and_missing_matches_are_listed_with_a_reason`). A fixture de 11/11/2026 ultrapassou os três dias de espera; o teste usava o relógio real. Corrigido apenas o relógio da fixture, sem alterar a regra de produção.

Depois: 97 testes passaram, incluindo 5 novos testes de regressão. Execução com ligações de rede bloqueadas: `python scripts/test_offline.py`. Os testes de Telegram usam um emissor simulado. Nenhuma chamada de API real.

Os novos testes cobrem: metadados ausentes/incoerentes e odds inválidas; primeiro sinal inválido seguido de reforço válido; vitórias excluídas incapazes de promover hipóteses ou apoiar registo; exclusão do treino walk-forward; exclusão de agenda, watch, quadro e pontos de entrada.

## Ficheiros e justificação

- `scangoal/eligibility.py`: política comum de elegibilidade, fechada por defeito quando falta a confirmação `in_roi`.
- `scangoal/brain.py`: aplica o filtro a geração, avaliação, comparação, agenda e walk-forward; separa contagens exploratórias sem alterar o registo.
- `scangoal/report.py`: anota elegibilidade/motivos e fornece à LLM sinais elegíveis; mantém o significado legado de `in_roi` e a taxa exploratória.
- `tests/test_eligibility.py`: cinco regressões; `tests/test_scangoal.py`: fixtures explícitas e relógio determinístico.
- `scripts/test_offline.py`: execução sem rede; `scripts/audit_data_eligibility.py`: comparação da implementação base e corrigida, relógio comum, leitura dos mesmos dados e verificação dos hashes.
- `README.md` e `audits/`: contrato, evidência e limitações.

## Antes/depois

| Medida | Antes | Depois |
|---|---:|---:|
| em observação | 4750 | 4778 |
| promissora | 33 | 8 |
| forte | 0 | 0 |
| confirmada | 0 | 0 |
| abandonada | 6 | 3 |
| Hipóteses preservadas | 4789 | 4789 |
| Apostas elegíveis / liquidadas | 338 / 270 | 338 / 270 |
| Walk-forward: apostas seguidas / liquidadas | 192 / 157 | 123 / 103 |
| Walk-forward: ROI das seguidas | +4,43% | −0,23% |
| Walk-forward: ROI com perda de odd de 2% | +2,65% | −1,93% |
| Walk-forward: saldo das seguidas | +6,95 un. | −0,24 un. |

O ROI estrito por grupo e a tabela completa de acerto exploratório são exatamente iguais antes/depois (assertions no script). Dos 450 sinais exploratórios, 338 são elegíveis e 112 excluídos. Os motivos sobrepõem-se; ver JSON. A redução de 33 para 8 promissoras não é uma métrica de melhoria preditiva.

## Hipóteses que motivaram a auditoria

| Hipótese | n antes → depois | Estado antes → depois | ROI antes → depois |
|---|---:|---|---:|
| Stake — Número de apostas = uma | 31 → 24 | abandonada → em observação | -22.66% → -14.20% |
| Stake — Dia = fim de semana | 33 → 33 | em observação → em observação | -17.54% → -17.54% |
| Jogo aberto — Confiança indicada pelo bot = menos de 95 % & Minuto = 31 a 60 | 30 → 25 | promissora → em observação | 45.52% → 36.71% |
| Jogo aberto — Faixa de odd = odd 1,50 a 2,00 & Golos em falta para ganhar = até 1 golo | 31 → 24 | promissora → em observação | 23.43% → 11.91% |
| Jogo aberto — Golos até ao sinal = 1 ou 2 & Minuto = 31 a 60 | 30 → 25 | promissora → em observação | 45.52% → 36.71% |

## Avaliação crítica e limitações

Conclusão técnica: a evidência sem proveniência válida deixa de alimentar decisões nos caminhos testados, preservando a leitura exploratória e o histórico. Conclusão científica: INCONCLUSIVO. O resultado histórico mais fraco demonstra sensibilidade à seleção de dados; não mede o desempenho futuro.

Esta auditoria compara o código base e corrigido em execuções separadas, mas foi preparada pelo autor da implementação. Não substitui revisão independente por outro revisor; a PR permanece Draft para essa revisão.

- PR 2 pendente: limiares ainda usam número de sinais; o exemplo Stake/fim de semana conserva 33 resultados mas apenas 14 jogos. Não há mecanismo novo de estabilidade nesta PR.
- PR 3 pendente: redundância, múltiplos testes e ativação cronológica. O walk-forward continua a usar o registo atual e a aproximação de resultado conhecido aos 150 minutos. Não é validação prospectiva.
- PR 4 pendente: referências e avaliação científica da LLM. Só se filtra aqui a evidência e o universo entregue ao modelo; nenhuma API foi chamada.
- PR 5 pendente: consolidação de mensagens e revisão do vocabulário de validação. Uma eventual integração poderá gerar mudanças de estado com o mecanismo antigo; nenhuma notificação foi enviada nesta execução.
- A ausência de uma marca de edição não prova que a odd era executável: recolha tardia e identidade/cronologia real dos resultados precisam de análise adicional. A PR aplica a política de proveniência já existente; não inventa evidência de captura.
- A avaliação exploratória adicional aumenta o trabalho local do relatório. Não se alteram frequências, modelos, quotas ou workflows; não foi medida a duração em produção nem se garante custo de execução idêntico após eventual integração.
- O filtro remove também sinais sem mercado/odd da evidência de apostas, mantendo-os nos relatórios exploratórios. Chamadores com sinais sintéticos precisam de declarar `in_roi` explicitamente.

## Reprodução e preservação

`python scripts/test_offline.py`

`python scripts/audit_data_eligibility.py`

`git diff --check`

O script lê a base com `git show`, sem checkout nem execução de coletores. Para reproduzir os mesmos números, usar os ficheiros `data/` do commit base (a recolha em main pode continuar). O JSON inclui hashes de todos os ficheiros de dados. Nenhum secret, workflow ou histórico é alterado. Commit com `[skip ci]` para evitar execução automática de Actions ao abrir a PR. Sem merge ou deploy.

PRs 2–5 aguardam integração autorizada da anterior, conforme a condição final do pedido.
