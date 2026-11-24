# PR J · Seleção autónoma, robustez e abstenção

**GO para investigação offline. NO-GO para seleção autónoma independente e confirmação prospetiva.**

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

J mantém o miner I congelado como gerador. Acrescenta uma camada de seleção que não lê rótulos e pode abster-se. Na avaliação independente, a escolha automática de J cumpriu os três limiares em todas as avaliações das duas famílias ao vivo. Não cumpriu em nenhuma avaliação das duas famílias pré-jogo. Pela regra pré-registada, o veredito é NO-GO. O algoritmo não foi reajustado depois da avaliação.

Uma nota sobre o que as tabelas medem: a *primeira escolha automática* é a regra que o motor escolhe sem rótulos. O *melhor candidato retrospetivo* é a regra da carteira que melhor se saiu depois de conhecidos os rótulos. Só a primeira mede seleção autónoma.

## Cronologia

| Commit | Conteúdo | Rótulos novos vistos? |
| --- | --- | --- |
| `f1a7600` | Auditoria do ranking, seletor J, testes, diagnóstico nos bancos já conhecidos ([known.json](known.json)) | Não. Só bancos H e I já publicados |
| `4d14cc8` | [Bloqueio](freeze.json): hashes do algoritmo, limiares, sementes, regras de elegibilidade e de decisão | Não. `bank_j.py` ainda não existia |
| `0f31bfe` | [Bancos J](bank_j.py), [avaliador](evaluate.py) e [elegibilidade](eligibility.json) | Contagens de alvos observáveis. Nenhuma regra avaliada |
| `bed4311` | [Relatório](report.json), primeira e única execução | Sim |
| seguinte | [Diagnóstico pós-hoc](posthoc.json) e este README | Sem alteração de veredito |

O mesmo agente escreveu o seletor e os bancos novos. Para limitar o enviesamento, as perturbações de cada banco são sorteadas pelo gerador e não escolhidas por família. São elas: taxa de iscos, população de jogos sem relação, escala de preço e volume, e atraso terminal. Os mecanismos das quatro famílias são novos.

## 1. Auditoria do ranking da PR I

Os números vêm dos bancos de desenvolvimento das três famílias avaliáveis da PR I, com duas sementes cada.

| Causa | Evidência |
| --- | --- |
| **Ordenação por comprimento antes da pontuação** | `_shortlist` percorre as faixas 8→2 passos e só depois intercala canais. A primeira proposta é sempre a da faixa mais longa disponível, qualquer que seja a pontuação. O teste `test_i_shortlist_puts_longest_lane_first_whatever_its_score` reproduz o defeito. |
| Primeira escolha com pontuação baixa | Nas seis carteiras, a primeira regra ocupou as posições 23, 16, 19, 19, 21 e 15 (de 0 a 23) na própria pontuação de I. |
| Primeira escolha com suporte baixo | O suporte foi de 3, 8, 7, 7, 7 e 8 jogos. Os candidatos que recuperavam todos os caminhos tinham entre 14 e 26. |
| Preferência por comprimento | `quality()` soma 95 por passo e desconta só 21 por condição. Recompensa 34 por jogo rejeitado, até 14. Regras longas e muito específicas sobem. |
| Passos acessórios | As primeiras escolhas tinham 7 ou 8 passos, contra 6 ou 7 nas trajetórias rotuladas. Incluíam iscos que só alguns jogos tinham. A recuperação foi 0/180. |
| Redundância | Entre 0 e 6 candidatos por carteira repetem o mesmo conjunto de testemunhas. As inversões entre posições adjacentes da pontuação foram 9 a 11 por carteira. |
| Coincidências do desenvolvimento | Nenhum candidato I com ≥75% de caminhos em desenvolvimento caiu abaixo de 25% nos outros bancos. Em H, quatro caíram. Os dois do pré-jogo da PR H tinham um predicado de nível absoluto. |

## 2. Seletor J

O trabalho tem quatro etapas, mantidas separadas:

1. **Geração.** O miner I corre inalterado (hash verificado) e a sua carteira de 24 propostas é preservada. J acrescenta propostas refinadas por consenso e não remove nenhuma.
2. **Seleção autónoma** ([selector.py](../../scangoal/discovery/experimental_j/selector.py)). Usa só a fita observada, sem resultados nem rótulos.
   - **Trajetória regular.** Uma forma é regular quando a regra estrutural dispara em pelo menos ¾ dos jogos da sua melhor subforma com um passo a menos. Isto corresponde a um padrão sequencial aproximadamente fechado. Escolhe-se a forma regular mais longa.
   - **Regime dominante** ([refine.py](../../scangoal/discovery/experimental_j/refine.py)). Só entram condições sem escala: sinais de variações, booleanos e categorias. Cada condição tem de valer em pelo menos 4/5 dos jogos onde a forma dispara e falhar em pelo menos um. As condições que valem em todos ficam de fora, porque nada observado mostra que sejam necessárias. Os níveis absolutos de preço, volume, minuto e intervalo ficam excluídos. São feitas no máximo duas rondas.
   - **Comparação semântica.** As condições com o mesmo contraste observado agrupam-se. Fica um representante e as alternativas são registadas.
3. **Refutação.** Cada verificação, se falhar, leva à abstenção:
   - menos de 5 jogos ou de 2 dias;
   - mais de 1,1 terminais por jogo;
   - procura incompleta;
   - menos de ¼ do suporte numa metade do calendário;
   - forma concorrente com o mesmo comprimento e jogos diferentes;
   - **replicação**, ou seja, uma nova seleção independente em cada metade do calendário tem de chegar à mesma forma.
4. **Avaliação científica.** Fica só no avaliador, que é o único com rótulos. Um teste AST verifica que o pacote J não importa bancos nem avaliadores.

## 3. Desenvolvimento nos bancos já conhecidos (não independente)

Estes bancos foram inspecionados antes de J ser escrito, incluindo os de validação da PR I. Servem de diagnóstico.

| Família PR I | Caminhos exatos H / I / J | Precisão J | Portas J |
| --- | ---: | ---: | ---: |
| Queda do maior bilhete | 43/60 · 0/60 · 60/60 | 60/60 | 6/6 |
| Intervalo para preço pontual | 60/60 · 0/60 · 60/60 | 60/78 | 0/6 |
| Mudança de âmbito ao vivo | 48/60 · 0/60 · 60/60 | 60/60 | 6/6 |
| Confirmação de marcador (não avaliável) | alvos inelegíveis | J abstém-se em 2/2 | — |

Há duas limitações:

- **Mundos H.** Um alerta-isco está presente em todos os jogos. Sem rótulos é indistinguível de um passo verdadeiro, e J incorpora-o. Na pressão ao vivo, por exemplo, J obteve 54/54 terminais corretos com 0/54 caminhos exatos.
- **Sensibilidade.** Na grelha consenso {3/4, 4/5, 5/6} × regularidade {2/3, 3/4, 4/5}, só o consenso 4/5 passa nas duas famílias I aprovadas. Com 3/4, a replicação falha. Com 5/6, perde-se precisão. É um indício de ajuste ao desenvolvimento, registado antes do holdout ([known.json](known.json), `sensitivity`).

## 4. Metodologia de avaliação

**Quarto cenário da PR I.** O relatório anterior fica intacto e verificado por hash no bloqueio. A causa reproduz-se: nos 10 alvos de cada banco, três golos visíveis resolvem a linha Over 2,5, e a guarda congelada devolve `line_already_decided_by_score`. Esta auditoria acrescenta dois factos:

- **Contraexemplos.** Também nenhum dos 18 contraexemplos era elegível, porque todos os jogos chegam a três golos.
- **Falsas deteções excluídas.** A primeira escolha I fez 100 deteções, todas falsas, nas seis avaliações desse cenário. Ao excluir o cenário, a PR I deixou essas deteções fora dos denominadores de precisão.

A exclusão foi decidida depois da primeira execução e está documentada como tal.

**Correção pré-registada.** A elegibilidade passou a ser uma porta calculada e publicada antes de qualquer pontuação. Exige ≥8 alvos com decisão observável e ≥5 mecanismos de contraexemplo observáveis por banco. Uma família inelegível não é retirada das tabelas e torna o veredito NO-GO. Era permitida uma única correção do gerador antes da pontuação, mas não foi necessária: as quatro famílias passaram com 10/10 alvos em todos os 20 bancos. Na família do total em aberto, 5 de 6 mecanismos eram observáveis.

## 5. Avaliação independente

O protocolo usa 4 famílias novas, 2 sementes de mineração e 3 sementes de validação. As escolhas de H, I e J ficaram seladas por hash antes de construir qualquer banco de validação. Uma abstenção conta como zero deteções, e os alvos permanecem no denominador.

| Família | Precisão H / I / J | Terminais H / I / J | Caminhos exatos H / I / J | Falsas H / I / J | Portas J | Abstenções J |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Golo visitante e deriva ao vivo | 19/56 · 17/34 · **60/60** | 19 · 17 · **60**/60 | 2 · 0 · **60**/60 | 37 · 17 · **0** | **6/6** | 0/6 |
| Total em aberto ao vivo | 37/70 · 24/38 · **60/60** | 37 · 24 · **60**/60 | 23 · 0 · **60**/60 | 33 · 14 · **0** | **6/6** | 0/6 |
| Surto de contagens pré-jogo | 34/69 · 8/26 · 30/39 | 34 · 8 · 30/60 | 19 · 0 · 30/60 | 35 · 18 · 9 | 0/6 | 3/6 |
| Retransmissão entre fontes pré-jogo | 46/88 · 8/20 · 30/39 | 46 · 8 · 30/60 | **46** · 0 · 0/60 | 42 · 12 · 9 | 0/6 | 3/6 |
| **Total** | 48,1% · 48,3% · **90,9%** | 56,7% · 23,8% · 75,0% | 37,5% · 0% · 62,5% | 147 · 61 · **18** | 12/24 | 6/24 |

Os limiares eram 90% de precisão, 80% de recuperação dos terminais e 75% de recuperação dos caminhos, por família, semente de mineração e semente de validação. Nas famílias ao vivo, J escolheu a mesma regra nas duas sementes de mineração. As escolhas mantiveram-se invariantes ao transporte em todas as avaliações, e a procura do gerador ficou completa nos oito bancos de mineração. As primeiras escolhas I tiveram 7 ou 8 passos em todas as famílias e voltaram a recuperar 0/240 caminhos. O melhor candidato retrospetivo da carteira I chegava a 10/10 em 21 de 24 avaliações, o que confirma que o problema de I estava na seleção e não na geração. A exceção foi uma semente da retransmissão.

## 6. Abstenções e falhas (pós-hoc, sem reajuste)

[posthoc.py](posthoc.py) foi escrito depois do relatório e não altera o veredito.

| Caso | Causa | Contrafactual |
| --- | --- | --- |
| Retransmissão, semente 61403 | A forma rotulada de seis passos não estava na carteira do gerador I. A forma regular mais longa tinha cinco passos, por isso houve 0 caminhos exatos. | — |
| Retransmissão e surto, escolha feita | O near-miss que J não excluiu foi `relay_volume_stalls` ou `surge_counts_fall`. A condição discriminante partilha o conjunto excluído com outro mecanismo cujo valor fica indisponível (volume mascarado, unidade de contagem diferente). Esse grupo junto ultrapassa 1/5 dos jogos e falha o consenso. A precisão ficou em 10/13. | — |
| Retransmissão e surto, abstenção `replicated` | A seleção na metade inicial do calendário abstém-se pela verificação de estabilidade aninhada (amostra de 17–18 jogos). A metade final chega à forma correta. | Sem replicação, J teria escolhido a forma correta: 30/30 caminhos e 30/39 de precisão. A família falharia na mesma. |

A abstenção evitou 18 falsas deteções e custou 60 caminhos recuperáveis. Não mudou o resultado de nenhuma porta. Fica por resolver, para uma revisão com um bloqueio novo e bancos novos:

- enumerar formas independentemente da lista curta do gerador;
- tratar exclusões partilhadas entre mecanismos;
- remover a estabilidade aninhada da replicação.

Nada disto foi aplicado aqui.

## 7. Regressões

- `scangoal/discovery/experimental_h`, `experimental_i` e os relatórios das PR H e I estão verificados por hash no bloqueio.
- `audits/robust-generalization/evaluate.py` continua a reproduzir `report.json` byte a byte.
- [known.json](known.json) e [report.json](report.json) reproduzem-se byte a byte.
- Não há rede, recolha, apostas ou introdução manual de dados. Os testes bloqueiam sockets e DNS.

## Reprodução

Na raiz do repositório:

```sh
PYTHONPATH=. python audits/selective-abstention/known.py > /tmp/j-known.json
cmp /tmp/j-known.json audits/selective-abstention/known.json
cd audits/selective-abstention
PYTHONPATH=../.. python evaluate.py --eligibility | cmp - eligibility.json
PYTHONPATH=../.. python evaluate.py | cmp - report.json
PYTHONPATH=../.. python posthoc.py | cmp - posthoc.json
cd ../..
python -m unittest tests.test_selective_abstention -v
```
