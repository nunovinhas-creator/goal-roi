# Desenvolvimento de K · ablações K1/K2/K3

**Recomendação: NO-GO para o bloqueio definitivo de K.** A decisão final é humana. A validação independente não foi executada e a independência científica não foi declarada.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

Isto é evidência de desenvolvimento. Os bancos cegos de K0 não foram usados, gerados nem consultados, e não existem. H, I e J estão intactos e verificados por hash.

## Ordem

| Commit | Conteúdo |
| --- | --- |
| `7c3a102` | [plan.json](plan.json): variantes, parâmetros, fontes, sementes, regras L1–L5. Também o [gerador de desenvolvimento](dev_bank.py). Ainda sem código K. |
| seguinte | `scangoal/discovery/experimental_k`, [ablate.py](ablate.py), [ablation.json](ablation.json), testes e este relatório |

Depois de ver os resultados, não alterei variantes, parâmetros nem regras.

## Variantes

Todas mantêm as constantes de J.

| Variante | Alteração |
| --- | --- |
| **J** | Nenhuma. Com os interruptores desligados, K reproduz J (teste `test_all_switches_off_reproduces_j`). |
| **J+K1** | Formas também enumeradas a partir das trajetórias amostradas, até 48 por fase, além da lista curta do gerador. |
| **J+K2** | Consenso medido só nos jogos onde o valor é observável. Exige valores observáveis em pelo menos ½ dos jogos. Os valores desconhecidos não contam como contraexemplo. |
| **J+K3** | Replicação sem a verificação de estabilidade aninhada. |
| **K** | K1 + K2 + K3. |

## Fontes de desenvolvimento

Cada célula é uma combinação família × semente de mineração × semente de validação.

| Fonte | Famílias | Células por variante | Estado |
| --- | ---: | ---: | --- |
| I | 4 | 24 | Conhecida |
| H | 3 | 18 | Conhecida |
| J | 4 | 24 | Conhecida. É a origem das hipóteses. |
| KDEV | 4 | 24 | Nova, escrita pelo autor do algoritmo, que conhece as hipóteses. Favorece K por construção. |

## Portas aprovadas por família

Limiares por omissão: consenso 4/5 e regularidade 3/4. Cada família tem 6 células.

| Fonte · família | J | J+K1 | J+K2 | J+K3 | K |
| --- | ---: | ---: | ---: | ---: | ---: |
| I · queda do bilhete (pré) | 6 | 6 | **0** | 6 | **0** |
| I · intervalo→preço (pré) | 0 | 0 | 0 | 0 | 0 |
| I · âmbito (ao vivo) | 6 | 6 | **3** | 6 | 6 |
| I · confirmação (inelegível) | 0 | 0 | 0 | 0 | 0 |
| H · 3 mundos | 0 | 0 | 0 | 0 | 0 |
| J · surto de contagens (pré) | 0 | 0 | 0 | 0 | 3 |
| J · retransmissão (pré) | 0 | 0 | 0 | 0 | 0 |
| J · 2 famílias ao vivo | 12 | 12 | 12 | 12 | 12 |
| KDEV · liquidez (pré) | 0 | 0 | 6 | 0 | 6 |
| KDEV · bilhete crescente (pré) | 0 | 0 | 0 | 0 | 3 |
| KDEV · deriva do empate (ao vivo) | 0 | 0 | 3 | 0 | 3 |
| KDEV · pressão de duas fontes (ao vivo) | 0 | 0 | 0 | 0 | 0 |
| **Total (de 90)** | **24** | **24** | **24** | **24** | **33** |

## Regras do plano

| Variante | L1 (sem regressão) | L2 (pré J + KDEV completos) | L3 (ao vivo J mantém) | L4 (sensibilidade) | Transporte |
| --- | :---: | :---: | :---: | :---: | :---: |
| J+K1 | ✓ | ✗ (36 células em falta) | ✓ | ✗ | ✓ |
| J+K2 | ✗ (perde 9) | ✗ (27) | ✓ | ✗ | ✓ |
| J+K3 | ✓ | ✗ (36) | ✓ | ✗ | ✓ |
| K | ✗ (perde 6: bilhete I) | ✗ (21) | ✓ | ✗ | ✓ |

**L5 aprovada:** 104 nomes de família e de mecanismo foram procurados em `experimental_k` e nenhum aparece, e os hashes de H, I e J estão intactos. Pela regra de parcimónia, o candidato é J, ou seja, nenhuma alteração. **Nenhuma variante cumpre L1–L4.**

## Resultados negativos e o que mostram

- **K2 desloca a falha.** Admite condições que J rejeitava, e com isso resolve a liquidez KDEV (6/6). Mas a seleção em cada metade do calendário diverge mais: J+K2 tem 42 abstenções por `replicated` em 90 células.
- **K não generaliza ao bilhete I.** Com K2, o bilhete I, aprovado com J, passa a abstenção.
- **K3 sozinho não ajuda.** Elimina abstenções no surto e na retransmissão J, mas a precisão fica em 60/78.
- **K1 sozinho quase não muda nada.** A forma correta passa a estar disponível na retransmissão J, semente 61403 (30/30 caminhos), mas a precisão continua abaixo de 90%.
- **Sensibilidade.** A sensibilidade aos limiares é forte e não é monótona. Nas fontes J e KDEV (48 células), K passa 12–33 conforme o ponto da grelha, e J passa 6–18. Nenhum ponto adjacente ao valor por omissão satisfaz L1–L3. Não se escolheu outro ponto, porque isso seria ajustar ao desenvolvimento.
- **Mundos H.** Continuam a 0 em todas as variantes, por causa do isco omnipresente.

## Conclusão para decisão

Nenhuma das três hipóteses, isolada ou combinada, resolve as famílias pré-jogo sem regressão. **Não recomendo bloquear K.** Uma revisão teria de atacar a instabilidade entre metades criada por K2, por exemplo exigindo que cada condição se replique, e não só a forma. Isso seria uma nova hipótese com um novo plano congelado, e os bancos KDEV atuais contam já como conhecidos.

## Reprodução

```sh
cd audits/k-development
PYTHONPATH=../.. python ablate.py | cmp - ablation.json
cd ../..
python -m unittest tests.test_k_development -v
```
