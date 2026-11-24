# Riscos ainda não resolvidos (K0)

| # | Risco | Estado | Mitigação prevista |
| --- | --- | --- | --- |
| R1 | **Não existe autor dos bancos independente.** | Bloqueante | Designação humana de B fora do repositório (ver [isolation.md](isolation.md)). |
| R2 | **Parâmetros do farol não verificados.** `chain_hash`, `genesis_time` e `period` do drand *quicknet* foram registados de memória. Esta sessão não tem rede para `api.drand.sh` nem biblioteca BLS. | Bloqueante antes do bloqueio K | D obtém `/info` de ≥2 relés e compara. Uma divergência obriga a emenda com nova ronda primária ≥30 dias depois. `parameters_verified=false` até lá. |
| R3 | **A assinatura BLS não é verificada.** Só se verifica `randomness == sha256(signature)` e o acordo entre ≥2 relés. | Aberto | Acrescentar a verificação BLS com a chave pública obtida em R2, antes da ronda. |
| R4 | **Carimbo temporal.** As datas de commit git são falsificáveis. | Mitigado | Conta o carimbo do servidor do GitHub num comentário. A margem mínima é de 7 dias até à ronda. Opcional: OpenTimestamps. |
| R5 | **Elegibilidade depende do código do repositório.** B só tem a descrição em linguagem natural das regras de decisão. | Aberto | Só uma correção permitida. O risco é perder famílias (NO-GO), não favorecer K. |
| R6 | **Semelhança com famílias anteriores.** B não pode consultá-las e pode recriar uma sem querer. | Aberto | D publica uma comparação estrutural (formas de passos e relações) antes da pontuação. Uma família quase idêntica é assinalada, mas não removida. |
| R7 | **A gramática pública orienta o algoritmo.** C conhece os tipos de passos permitidos. | Aceite | A gramática é necessariamente pública. Os mecanismos concretos não. |
| R8 | **Contaminação do desenvolvimento.** Os bancos H, I e J e o diagnóstico pós-hoc de J são conhecidos por C. | Aceite e declarado | Tudo isso é desenvolvimento. Nada entra no teste. |
| R9 | **Operador de D.** Se D correr nesta conta, C poderia ver os bancos antes da pontuação. | Aberto | Usar um *workflow* fixado e pontuar no mesmo *job* em que os bancos são construídos. Os bancos só ficam visíveis com as escolhas seladas. |
| R10 | **Potência estatística.** 10 alvos por banco. Um único falso positivo a mais pode decidir uma célula. | Aceite | Critérios inalterados e exigidos em todas as 72 células. NO-GO em caso de dúvida. |
| R11 | **Indisponibilidade prolongada do farol.** | Coberta | Sequência fixa de 6 rondas de recurso (30 em 30 dias). Esgotada a sequência, é precisa uma emenda e não se escolhe nenhuma semente à mão. |

## Exatamente o que falta para autorizar

**Implementação do algoritmo K** (fase K1–K3 em desenvolvimento):
1. Fazer o bloqueio desta PR (`protocol.json`) com o hash registado. Os passos seguintes dependem dele.
2. Fixar a grelha de ablações {J, J+K1, J+K2, J+K3, K1+K2+K3} e o gerador de desenvolvimento próprio de C, num espaço de sementes separado e publicado antes de correr.
3. Decisão humana a autorizar o início de K, que corre só em bancos de desenvolvimento.

**Geração dos bancos cegos:**
1. Designar B e satisfazer I1–I4 (R1).
2. Verificar os parâmetros do farol e acrescentar a verificação BLS (R2, R3).
3. Exportar o kit e publicar o seu digest (I3).
4. B publica o compromisso. C publica o bloqueio K. A ronda é a primeira da sequência com ≥7 dias de margem.
5. Definir o operador ou o *workflow* de D (R9, I6).

Até lá: **GO só para preparação metodológica. NO-GO para afirmar independência científica.**
