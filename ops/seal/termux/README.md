# Cópia selada no Termux (Android)

Custo 0 €. Não usa credenciais e nunca faz push. Não lê, não escreve e não agenda nada do Brain, do Obsidian ou do `brain-sync.sh`.

## Instalação (uma vez)

No Termux:

```sh
pkg install -y git
git clone --depth 1 https://github.com/nunovinhas-creator/goal-roi.git ~/goal-roi-instalador
sh ~/goal-roi-instalador/ops/seal/termux/install.sh
sv-enable crond
```

O instalador:

1. Instala `python git xz-utils cronie termux-services util-linux` (pacotes gratuitos do Termux).
2. Cria `~/goal-roi-selos/`, uma pasta própria no armazenamento privado do Termux, fora de qualquer pasta partilhada do Obsidian:
   - `repo/`: clone **só de leitura** (`pushurl` desativado; `--filter=tree:0`, sem descarregar o histórico de ficheiros);
   - `bin/goal-roi-selo`: um ciclo completo; `bin/seal.py`, copiado de `origin/main`;
   - `selos/`: os selos;
   - `logs/selo.log` (roda a 1 MB) e `logs/falhas.jsonl`.
3. **Recusa instalar** se a pasta ficar dentro, ou por cima, de uma pasta com `brain-sync.sh` ou `.obsidian`.
4. Acrescenta **uma** linha ao crontab, marcada `# goal-roi-selos`, às 05h e às 17h. Escolhe um minuto a pelo menos 20 min do minuto do `brain-sync`. Confirma que as outras linhas ficaram exatamente iguais; caso contrário, aborta.

Repetir a instalação é seguro (idempotente) e atualiza `seal.py`.

## O que cada ciclo faz

`bin/goal-roi-selo`:

1. Lock próprio (`~/goal-roi-selos/.lock`). Se o ciclo anterior ainda estiver a correr, regista e sai sem fazer nada.
2. Corre com `nice` (prioridade baixa).
3. `seal`: lê `origin/main` e sela um commit exato. Guarda o tar e um manifesto com o SHA-256 de cada ficheiro e do selo anterior (cadeia).
4. `verify`: verifica todos os selos, a cadeia e a idade do último (≤ 13 h).
5. `prune`: 30 dias completos; depois, 1 selo por semana (com lápides; a cadeia continua verificável).

Uma falha acrescenta a `logs/falhas.jsonl` uma linha `{"utc", "passo", "codigo"}`, com código 2 para integridade ou cadeia. Se o Termux:API estiver instalado, mostra também uma notificação. Não há recuperação automática.

## Verificação e recuperação manual

```sh
python3 ~/goal-roi-selos/bin/seal.py verify --dest ~/goal-roi-selos/selos
```

```sh
python3 ~/goal-roi-selos/bin/seal.py restore --dest ~/goal-roi-selos/selos --out ~/goal-roi-restauro-$(date +%Y%m%d)
```

```sh
python3 ~/goal-roi-selos/bin/seal.py restore --dest ~/goal-roi-selos/selos --out ~/goal-roi-restauro-seq12 --seq 12
```

A restauração recusa uma pasta que já exista e é tudo-ou-nada. O commit de origem está no `MANIFEST.json` do selo. Os ficheiros restaurados são prova; não são copiados para a produção sem decisão humana.

## Desinstalação

```sh
sh ~/goal-roi-instalador/ops/seal/termux/uninstall.sh
```

Remove só a linha `# goal-roi-selos`; os selos ficam. Com `--purge`, apaga também `~/goal-roi-selos` (só se for uma instalação reconhecida).

## Testes

`tests/test_seal_termux_install.py` (6 testes, HOME falso, crontab simulado, remoto Git local):

- crontab existente com `brain-sync` intacto; a nossa linha só uma vez e a ≥ 20 min;
- recusa de pastas dentro do Brain/Obsidian;
- ciclo real com selo verificado e restauração byte a byte; clone sem push;
- falha registada com passo e código;
- ciclo sobreposto ignorado;
- desinstalação só da nossa linha.

`tests/test_seal_copy.py` (11 testes) cobre o `seal.py`.

## Critérios de aprovação no telemóvel

- Dois ciclos agendados seguidos (05h e 17h) com `verify` OK e `falhas.jsonl` vazio.
- Restauração do último selo igual a `git show <commit>:<ficheiro>`.
- Uma adulteração de ensaio, numa cópia da pasta, dá código 2.
- `crontab -l` igual ao anterior mais uma linha; `brain-sync.sh` e o Obsidian intactos.

## Reversão

`uninstall.sh` (e `--purge` se quiser apagar os selos).

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
