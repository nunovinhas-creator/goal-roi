# K0 · Protocolo de validação independente

**GO só para preparação metodológica. NO-GO para afirmar independência científica até o isolamento estar demonstrado.**

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

Esta fase não implementa K1, K2 ou K3, nem gera, revela ou avalia bancos. A PR #91, o seletor J, os seus limiares, sementes e bancos ficam intactos, e o veredito de J continua NO-GO.

## Conteúdo

| Ficheiro | Função |
| --- | --- |
| [protocol.json](protocol.json) | Critérios, matriz, regras de pontuação, elegibilidade, farol, compromissos e ordem. É o objeto bloqueado. |
| [isolation.md](isolation.md) | Papéis A/B/C/D, permissões, atestações I1–I7 e bloqueio atual |
| [risks.md](risks.md) | Riscos por resolver e o que falta para autorizar K e os bancos cegos |
| [tools/beacon.py](tools/beacon.py) | Calendário de rondas drand, escolha determinística da ronda, verificação `sha256(signature)`, derivação HMAC das sementes |
| [tools/commitment.py](tools/commitment.py) | Compromisso SHA-256 com sal sobre o digest canónico de uma árvore de ficheiros |
| [tools/guard.py](tools/guard.py) | Execução única, reprodução byte a byte, pontuação congelada das células e decisão |
| [bank_author_kit/](bank_author_kit) | O único material entregue a B: interface dos eventos, modelo sem mecanismos, regras |

## Critérios congelados

- **Limiares:** precisão ≥ 90%, recuperação de terminais ≥ 80% e caminhos exatos ≥ 75%, em **cada** célula família × semente de mineração × semente de validação.
- **Matriz:** 8 famílias inéditas (F1–F4 pré-jogo, F5–F8 ao vivo), 3 sementes de mineração e 3 de validação, ou seja, 72 células por motor. H, I, J e K são comparados nas mesmas condições.
- **Abstenções e ausência de deteções:** uma abstenção conta como zero deteções e falha a célula. Sem deteções, a precisão fica indefinida e falha. Os alvos ficam sempre no denominador. Um banco inelegível falha as suas células e a família nunca sai das tabelas. Uma célula em falta é uma célula falhada. O melhor candidato retrospetivo é só diagnóstico. `guard.gate` e `guard.decide` implementam estas regras e os testes verificam-nas.
- **GO** exige as oito famílias elegíveis, nenhuma abstenção de K, as 72 células aprovadas, invariância ao transporte, procura completa e reprodução idêntica. Qualquer outro resultado é NO-GO e os bancos passam a desenvolvimento.

## Sementes por farol público

- **Ronda primária:** drand *quicknet* ronda **36 226 612** (2027-02-01 12:00 UTC).
- **Recurso:** rondas fixas de 30 em 30 dias, no máximo seis.
- **Escolha da ronda:** a primeira da sequência com pelo menos 7 dias de margem sobre o último compromisso, de B ou do bloqueio K, contado pelo carimbo do servidor do GitHub.
- **Ronda indisponível:** só quando, em 7 dias, menos de 2 relés concordam em `randomness == sha256(signature)`. Nesse caso passa-se à ronda seguinte da sequência.
- **Derivação:** HMAC-SHA256 com a aleatoriedade como chave e o hash do protocolo, a família, o papel e o índice na mensagem.
- **Proibido:** escolher outra ronda, outros relés ou outra derivação.
- Os parâmetros da cadeia **ainda não foram verificados** (R2).

## Contra acesso antecipado, repetição seletiva e alteração de critérios

- **Acesso antecipado:** o gerador fica só com B até depois do bloqueio K e da ronda. Antes disso publica-se apenas o compromisso com sal. Os bancos dependem de sementes que ninguém conhece antes da ronda.
- **Repetição seletiva:** `guard` grava `runs/run-001.json`. Qualquer execução posterior é uma reprodução e tem de ser byte a byte igual, senão a avaliação fica inválida (NO-GO).
- **Alteração de critérios:** o hash de `protocol.json` entra em cada compromisso, em cada semente e no arranque do `guard`. Uma emenda é um ficheiro novo versionado e nunca pode mexer em limiares, matriz ou pontuação depois de existir algum compromisso.

## Verificação local

```sh
python -m unittest tests.test_k0_protocol -v
python audits/independent-k0/tools/beacon.py --schedule
```

## Hashes deste bloqueio

| Objeto | SHA-256 |
| --- | --- |
| `protocol.json` | `99611ec64891c7e11b9f0252fb7297d074f70765964f3a77dde68cd2562901f3` |
| `bank_author_kit/` (digest canónico de `tools/commitment.tree_digest`) | `93656c1cedc9c6823bfa9cd644d525578cf44b1adbfbfbda728fe947f5757985` |
| `tools/commitment.py` | `1aacbffec0e4c41f8f8948ab9d0e10cad99216e97b7800b508285a97f0dde943` |

O kit exportado para B contém exatamente `bank_author_kit/`, `protocol.json` e `tools/commitment.py`, com estes hashes.
