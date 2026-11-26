# Cópia independente selada a cada 12 h

Custo: 0 €. Não contrata serviços e não usa credenciais novas. Não escreve no repositório, não faz push e não altera a recolha, o Pages, o protocolo, o registo prospetivo nem o Brain V5. Sem recuperação automática.

## O que é um selo

Cada selo corresponde a **um commit exato** de `origin/main`, nunca à árvore de trabalho:

- `sources.tar.xz`: todos os ficheiros de `data/` exceto a vista `data/report.json`, mais `scangoal/prospective_protocol.json`. Tar determinístico (nomes ordenados, data do commit, sem dono).
- `MANIFEST.json`: seq, commit, datas, SHA-256 e tamanho de cada ficheiro, SHA-256 do tar e **SHA-256 do selo anterior**.
- `SEAL`: SHA-256 do manifesto.

A cadeia de selos deteta selos alterados, removidos ou reescritos. A poda deixa uma lápide (`PRUNED.jsonl`) por selo removido, por isso a cadeia continua verificável. Um commit que não descende do último selado é recusado (ref errada ou história reescrita). Escrita em pasta temporária com fsync e rename atómico; o resultado fica só de leitura.

Medido no corte 9d65d016: **~0,95 MB por selo**, 20 ficheiros, restauração em 0,4 s e **byte a byte igual a `git show`**. Com poda (30 dias completos e depois 1 por semana): ~57 MB no primeiro mês e ~50 MB por ano a seguir. A 5 anos fica perto de 0,3 GB ao tamanho atual; cresce com o diário.

## Comandos

```sh
python3 ops/seal/seal.py seal    --repo <clone> --dest <pasta> --fetch --ref origin/main
python3 ops/seal/seal.py verify  --dest <pasta> --max-age-h 13
python3 ops/seal/seal.py restore --dest <pasta> --out <pasta-nova> [--seq N]
python3 ops/seal/seal.py prune   --dest <pasta> --keep-days 30
```

`ops/seal/run_12h.sh` faz o ciclo completo (fetch, selo, verificação, poda). Termina com código ≠ 0 em qualquer falha: código 2 para integridade ou cadeia, outro para erro de git ou rede.

## Instalação no Termux (escolhida)

Ver [`termux/README.md`](termux/README.md): instalador idempotente, ciclo de 12 h com lock, registo de falhas, retenção e recuperação manual, sem interferir com o Brain, o Obsidian ou o `brain-sync.sh`.

## Onde correr (armazenamento já disponível)

A cópia só é independente se ficar **fora do GitHub**. Por ordem de preferência:

1. **Telemóvel Android com Termux**, já usado pela sincronização do Brain. Pasta em armazenamento interno; requer `python`, `git` e `xz-utils` do Termux (gratuitos) e um clone só de leitura (`git clone https://github.com/nunovinhas-creator/goal-roi`, sem credenciais se o repositório for público). Agendamento com `termux-services` + `cronie`:
   ```
   GOAL_ROI_CLONE=~/goal-roi-ro
   GOAL_ROI_SEALS=~/storage/shared/goal-roi-selos
   17 5,17 * * * sh ~/goal-roi-ro/ops/seal/run_12h.sh >> ~/goal-roi-selos.log 2>&1
   ```
2. **Qualquer computador pessoal** com cron (`17 5,17 * * *`) ou um agendador do sistema, apontando para um disco local ou externo.
3. Não usar: GitHub Actions ou artefactos do GitHub (mesmo fornecedor); os projetos Supabase existentes (um deles pertence a outro projeto; uso só com autorização própria e verificação do limite gratuito).

Nada disto foi instalado nem agendado: exige ação humana no dispositivo escolhido.

## Restauração (procedimento humano)

1. `verify --dest <pasta>`: confirma a cadeia e a idade do último selo.
2. `restore --dest <pasta> --out <pasta-nova>`: repõe os ficheiros numa pasta nova. Recusa se a pasta existir; tudo-ou-nada.
3. Comparar com a última revisão íntegra conhecida (commit no `MANIFEST.json`) antes de qualquer uso. Nunca escrever por cima de `data/` em produção sem decisão humana. O registo prospetivo restaurado serve de prova, não substitui o publicado.

## Testes

`tests/test_seal_copy.py`, 11 testes: cadeia, selo sem alterações, leitura do commit e não da árvore de trabalho, restauração byte a byte, tar adulterado, manifesto reescrito, selo em falta, interrupção sem selo parcial, poda com cadeia verificável, recusa de commit não descendente, idade máxima e códigos de saída.

## Critérios de aprovação

- CI verde.
- Num dispositivo fora do GitHub: dois ciclos consecutivos com 12 h de intervalo, `verify` OK, e restauração do último selo byte a byte igual a `git show <commit>:<ficheiro>`.
- Um ensaio de adulteração no dispositivo (alterar um byte numa cópia da pasta) que dá código 2.

## Reversão

Remover a entrada do cron/termux-services e apagar a pasta de selos. Nada no repositório nem na produção depende disto.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
