# Processo B isolado — teste técnico

**Isolamento técnico: PASS. Independência científica: NÃO DEMONSTRADA.** O executor e a sonda foram escritos pela mesma sessão que escreveu J e K, e continua a não existir um autor dos bancos externo. Não há atestações I1–I4.

`run_b.py` cria, com `unshare` do util-linux e como root, um ambiente efémero:

| Exigência | Implementação | Resultado em `runs/sandbox.json` |
| --- | --- | --- |
| Sem repositório nem histórico Git | Nova raiz tmpfs com `chroot`. Só `/usr` (interpretador e biblioteca padrão), `/kit` e `/out` | Raiz `bin kit lib lib64 out usr`. Nenhum `.git`, `/home`, `/root` nem `/tmp` |
| Sem credenciais | `env -i`, com apenas `PATH` e `LANG`, e `python -I` | 0 variáveis sensíveis (o anfitrião tem 31) |
| Sem rede | Novo espaço de nomes de rede, só com `lo` desligado | DNS falha, TCP para um IP literal falha, proxy local falha |
| Só leitura, salvo a saída | Raiz, `/usr` e `/kit` montados só de leitura, e `/out` com escrita, sem `exec` | Só a escrita em `/out` é aceite |
| Kit mínimo | `bank_author_kit/`, `protocol.json`, `tools/commitment.py` | Digest do kit igual antes e depois |
| Registos verificáveis | SHA-256 do executor, do script do ambiente, da sonda, do kit, das saídas e do stdout. Comando, tempos e código de saída. Hash do próprio registo | `record_sha256` em cada registo |
| Não gerar bancos | A sonda substitui o gerador. O modelo do kit recusa (`NotImplementedError`) | `refused_no_mechanism` |

**Controlo negativo** (`runs/control.json`): a mesma sonda, sem isolamento, vê o repositório, dois diretórios `.git`, DNS, TCP e o proxy, e 31 variáveis sensíveis. Só se registam contagens, nunca os nomes. A sonda não é vazia: deteta as fugas quando elas existem. No controlo, as escritas fora de `/out` são saltadas.

**Limites:**
- O operador é root no mesmo contentor e poderia alterar o executor ou os registos. Os hashes só permitem que um terceiro repita a execução e compare.
- O kernel e o hipervisor não são atestados.
- Ainda falta a parte humana: um autor B que nunca tenha visto H, I, J ou K.

**Incidente:** a primeira versão do controlo escreveu `/probe-root` e `/usr/probe` (5 bytes cada) no contentor da sessão. Esta sessão não os conseguiu remover, porque a verificação de segurança bloqueou o `rm`. A versão atual nunca escreve fora de `/out` no controlo.

```sh
sudo python audits/independent-k0/isolation/run_b.py   # escreve runs/{sandbox,control,verdict}.json
```
