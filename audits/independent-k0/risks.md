# Riscos ainda não resolvidos (K0)

| # | Risco | Estado | Mitigação prevista |
| --- | --- | --- | --- |
| R1 | **Não existe autor dos bancos independente.** | Bloqueante | Designação humana de B fora do repositório (ver [isolation.md](isolation.md)). |
| R2 | **Parâmetros do farol.** | **Verificado em 2026-11-24** | Os quatro relés devolvem o mesmo `/info`, com cadeia, génese e período iguais aos de `protocol.json` e o esquema `bls-unchained-g1-rfc9380` ([beacon-verification.json](beacon-verification.json)). O `protocol.json` não foi editado. A verificação repete-se antes de cada compromisso. |
| R3 | **Verificação BLS.** | **Preparado** | `tools/bls.py` usa `py_ecc` 8.0.0 (`hash_to_G1` RFC 9380), com verificação de subgrupo. Testes offline com rondas 1, 1 000 000 e 23 456 789, cada uma obtida de dois relés diferentes, e com rejeição de ronda trocada, bit alterado, chave alterada, esquema errado e `randomness` falsa. Falta repeti-lo na ronda real. |
| R4 | **Carimbo temporal.** As datas de commit git são falsificáveis. | Mitigado | Conta o carimbo do servidor do GitHub num comentário. A margem mínima é de 7 dias até à ronda. Opcional: OpenTimestamps. |
| R5 | **Elegibilidade depende do código do repositório.** B só tem a descrição em linguagem natural das regras de decisão. | Aberto | Só uma correção permitida. O risco é perder famílias (NO-GO), não favorecer K. |
| R6 | **Semelhança com famílias anteriores.** B não pode consultá-las e pode recriar uma sem querer. | Aberto | D publica uma comparação estrutural (formas de passos e relações) antes da pontuação. Uma família quase idêntica é assinalada, mas não removida. |
| R7 | **A gramática pública orienta o algoritmo.** C conhece os tipos de passos permitidos. | Aceite | A gramática é necessariamente pública. Os mecanismos concretos não. |
| R8 | **Contaminação do desenvolvimento.** Os bancos H, I e J e o diagnóstico pós-hoc de J são conhecidos por C. | Aceite e declarado | Tudo isso é desenvolvimento. Nada entra no teste. |
| R9 | **Operador de D.** Se D correr nesta conta, C poderia ver os bancos antes da pontuação. | Aberto | Usar um *workflow* fixado e pontuar no mesmo *job* em que os bancos são construídos. Os bancos só ficam visíveis com as escolhas seladas. |
| R10 | **Potência estatística.** 10 alvos por banco. Um único falso positivo a mais pode decidir uma célula. | Aceite | Critérios inalterados e exigidos em todas as 72 células. NO-GO em caso de dúvida. |
| R12 | **User-Agent bloqueado.** `drand.cloudflare.com` respondia 403 ao `urllib` com o User-Agent por omissão. | Corrigido antes de qualquer compromisso | `beacon.fetch` envia `goal-roi-k0-verifier/1`. O teste verifica o cabeçalho. Com o defeito, uma ronda poderia ficar com menos relés do que o quórum. |
| R11 | **Indisponibilidade prolongada do farol.** | Coberta | Sequência fixa de 6 rondas de recurso (30 em 30 dias). Esgotada a sequência, é precisa uma emenda e não se escolhe nenhuma semente à mão. |

## Exatamente o que falta para autorizar

**Implementação do algoritmo K** (fase K1–K3 em desenvolvimento):
1. Fazer o bloqueio desta PR (`protocol.json`) com o hash registado. Os passos seguintes dependem dele.
2. Fixar a grelha de ablações {J, J+K1, J+K2, J+K3, K1+K2+K3} e o gerador de desenvolvimento próprio de C, num espaço de sementes separado e publicado antes de correr.
3. Decisão humana a autorizar o início de K, que corre só em bancos de desenvolvimento.

**Geração dos bancos cegos:**
1. Designar B e satisfazer I1–I4 (R1).
2. ~~Verificar os parâmetros do farol e acrescentar a verificação BLS~~ (feito: R2, R3, R12). Na ronda real, a BLS verifica-se com o quórum.
3. Exportar o kit e publicar o seu digest (I3).
4. B publica o compromisso. C publica o bloqueio K. A ronda é a primeira da sequência com ≥7 dias de margem.
5. Definir o operador ou o *workflow* de D (R9, I6).

Até lá: **GO só para preparação metodológica. NO-GO para afirmar independência científica.**
