# Modelo de isolamento K0

**Estado: independência NÃO demonstrada.** Este documento define o que tem de ser verdade e como se verifica. Enquanto uma das atestações abaixo faltar, nenhum resultado pode ser chamado independente.

## Papéis e permissões

| Papel | Quem | Pode ler | Não pode ler | Pode publicar |
| --- | --- | --- | --- | --- |
| A · Protocolo | Esta PR (K0) | Tudo o que já está publicado | — | `protocol.json` e as suas emendas versionadas |
| B · Autor dos bancos | **Por designar.** Uma pessoa ou um processo fora do repositório | O kit exportado: `bank_author_kit/`, `protocol.json`, `tools/commitment.py` | Repositório `goal-roi`, histórico, PR, relatórios, código H/I/J/K, esta conversa | Compromisso, revelação, correção única |
| C · Autor do algoritmo | Sessão de desenvolvimento K | Bancos de desenvolvimento (H, I, J e um gerador de desenvolvimento próprio) | Gerador B, mecanismos, sal, sementes finais antes da ronda | Bloqueio K |
| D · Avaliador | Pipeline determinístico num ambiente limpo, operado por alguém que não é C | Tudo depois da revelação | — | Elegibilidade, escolhas seladas, relatório, reprodução |

## O que NÃO conta como independência

- Uma sessão separada (Claude ou humana) com acesso ao mesmo repositório, mesmo com *checkout* esparso. O histórico git e a API do GitHub continuam acessíveis.
- Esta sessão. Escreveu J, os bancos J e o diagnóstico pós-hoc, e por isso conhece os modos de falha. Só pode atuar como A ou como C, nunca como B.
- Qualquer agente cujo único limite seja uma instrução de texto ("não leias X").

## Atestações exigidas antes de declarar independência

| # | Atestação | Verificação |
| --- | --- | --- |
| I1 | Identidade de B registada (pessoa, ou responsável pelo processo) e assinada por B | Comentário de B na PR de acompanhamento |
| I2 | B trabalha num ambiente sem credenciais nem rede para `github.com/nunovinhas-creator/goal-roi` | Declaração de B. Se B for um processo: configuração do ambiente publicada (lista de *hosts* permitidos sem `github.com`) e registo do *proxy* |
| I3 | B recebeu só o kit, com SHA-256 publicado | `tools/commitment.tree_digest` do kit é igual ao valor desta PR |
| I4 | B não teve contacto com C sobre mecanismos | Declaração de B e de C |
| I5 | O compromisso de B foi publicado antes do bloqueio K e antes da ronda | Carimbos de servidor do GitHub |
| I6 | D é operado por alguém diferente de C, ou corre num CI cujo *workflow* foi fixado no protocolo | Registo do *workflow* e do operador |
| I7 | A revelação reproduz o compromisso | `tools/commitment.py --verify` |

Se I1–I4 não puderem ser satisfeitas de forma verificável, a avaliação pode ainda correr como **cega para C**, mas o relatório tem de declarar "independência não demonstrada" e o veredito permanece NO-GO quanto à independência científica.

## Bloqueio atual

Ainda não há nenhum B designado. Daqui não é possível criar um processo sem acesso ao repositório e comprová-lo: as sessões remotas disponíveis herdam o acesso ao GitHub desta conta, e o *proxy* de rede não é auditável a partir de B. **É necessária uma decisão humana:** quem é B, e em que ambiente trabalha.
