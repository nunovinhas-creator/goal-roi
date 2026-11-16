# PR 5 — P0: intenção de envio publicada no GitHub antes de contactar o Telegram

Revisão #5920565094, P0. **Atualização (revisão #5921136533):** o proprietário autorizou as duas linhas
`NOTIFY_DURABLE: git` **só no ramo da PR #28**. Estão aplicadas nesse ramo (secção 5); o `main` não foi alterado e o merge
continua por autorizar.

## 1. O problema (reproduzido)

O workflow `collect.yml` corre `scangoal.notify` (e, nas leituras rápidas, `scangoal.watch` → `notify.run`) **antes** do passo
que faz commit e push de `data/`. A PR 5 grava a intenção (`in_flight`) e o registo localmente, mas só o push os torna
visíveis ao runner seguinte. Se o runner A enviar e morrer, ou se o push final falhar, o runner B parte de um checkout limpo
com o estado antigo e volta a enviar.

O teste `test_notify_durability.test_without_the_barrier_a_dead_runner_causes_a_duplicate` reproduz exatamente isto com
repositórios git reais: um remoto *bare* e um clone limpo por runner.

Sem a barreira, a garantia "no máximo uma vez" vale só **dentro do mesmo runner**. É assim que a PR a descreve enquanto a
barreira não estiver ativa.

## 2. A barreira (implementada, inativa por omissão)

`notify.GitPublisher`, ativada por `NOTIFY_DURABLE=git` com `GITHUB_REF_NAME` definido. Para cada aviso:

1. Grava a intenção: efeitos + `in_flight` no estado (atómico) e a linha `intent` no registo, como antes.
2. **Publica** só esses dois ficheiros: `git add` + `git commit -- data/notify_state.json data/notify_log.jsonl`, com a
   mensagem `notify: intent <hash> [skip ci]`.
3. Sincroniza com `git pull --rebase --autostash origin <ramo>` e faz `git push origin HEAD:<ramo>`. Repete até 3 vezes.
4. **Só se o push for aceite** contacta o Telegram e grava o resultado localmente. O resultado chega ao remoto no commit
   seguinte: o do próximo aviso ou o passo "Commit data" do workflow.
5. Se a publicação falhar (comando, conflito, tempo esgotado de 60 s, push recusado):
   - a intenção é **retirada**: efeitos desfeitos, aviso de novo `pending`, linha `withdrawn` no registo;
   - nada é enviado e esta execução pára de enviar;
   - o aviso pode sair uma vez numa execução seguinte, se ainda for válido.

Com o código novo, sem a variável o comportamento é o atual. O registo de execução mostra `NOTIFY_DURABILITY local`
(barreira inativa) ou `NOTIFY_DURABILITY git` (ativa). Uma configuração inválida (`NOTIFY_DURABLE` com outro valor, ou sem
`GITHUB_REF_NAME`) não envia nada.

## 3. Porque cobre todos os casos

| Momento da falha do runner A | Remoto depois | Runner B (checkout limpo) |
|---|---|---|
| Antes de publicar (morte, push recusado, conflito, tempo esgotado sem efeito) | sem intenção | envia uma vez (nada tinha saído) |
| Push aceite mas resposta perdida (A julga que falhou e não envia) | `in_flight` | marca como **incerta**, não envia (perde-se, nunca duplica) |
| Idem, mas A sobrevive e faz o commit final | `pending` + `withdrawn` | envia uma vez |
| Depois de publicar, antes, durante ou depois do envio | `in_flight` | marca como **incerta**, nunca reenvia |
| Depois do envio e do resultado local, sem commit final | `in_flight` | incerta (a mensagem saiu, mas nunca é contada nem repetida) |
| Leituras rápidas: várias execuções no mesmo runner, morte na n-ésima | intenções de todas publicadas | só o que nunca foi publicado volta a ser tentado |
| Outro commit no remoto (p. ex. merge) | `pull --rebase --autostash` integra-o | — |
| Commit no remoto que mexe no estado (conflito) | rebase abortado, intenção retirada, nada enviado | trata como "antes de publicar" |

A regra de recuperação (`reconcile`) mantém-se: uma intenção sem resultado e não retirada passa a incerta e nunca é
reenviada.

## 4. Testes adversariais (`tests/test_notify_durability.py`, git real, offline)

1. Sem barreira: o runner A envia e morre → o runner B **duplica**. Caracteriza o defeito.
2. Com barreira: A envia e morre → B não reenvia; `sent_total` conta só a entrega confirmada.
3. A morre em cada ponto de uma entrega (`after_intent_state`, `after_intent`, `after_publish`, `after_response`,
   `after_record_state`, `after_record`). Em três runners seguidos, nenhum texto sai duas vezes e `sent_total` está certo.
4. Push recusado (hook `pre-receive`): nada enviado. Depois, com A sobrevivente e o seu commit final aceite, sai uma vez.
5. Push recusado e A morre: B envia uma vez.
6. Push aceite mas resposta perdida, com e sem sobrevivência de A: no máximo uma vez. Sem sobrevivência, fica incerta.
7. Remoto movido por outro commit, com a árvore de trabalho suja: rebase com *autostash*, envio feito, ficheiros locais
   intactos.
8. Remoto com o estado alterado por terceiros (conflito): nada enviado, nenhum rebase pendurado.
9. Leituras rápidas: duas execuções no mesmo runner e morte na segunda. Cada aviso sai uma vez, por A ou por B.
10. Configuração inválida: nada enviado.
11. A barreira só faz commit dos dois ficheiros do notificador, com `[skip ci]`, e deixa intacto o resto da árvore.
12. Queda entre a retirada gravada no estado e a linha `withdrawn` no registo: o aviso nunca saiu, por isso continua pendente
    e sai uma vez depois. Nunca fica "incerto" por engano.

As mutações M44–M48 removem cada peça da barreira e são todas apanhadas.

## 5. Alteração ao workflow (autorizada e aplicada só no ramo da PR #28)

```diff
       - name: Telegram notices through the user's bot (skipped without keys)
         env:
           TG_BOT_TOKEN: ${{ secrets.TG_BOT_TOKEN }}
           TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
+          NOTIFY_DURABLE: git
         run: python -u -m scangoal.notify
@@
       - name: Fast reads every 2 minutes until the next collection (read-only)
@@
           TG_BOT_TOKEN: ${{ secrets.TG_BOT_TOKEN }}
           TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
+          NOTIFY_DURABLE: git
         run: timeout --signal=TERM --kill-after=10s 540s python -u -m scangoal.watch
```

O resto já existe no workflow atual:
- `permissions: contents: write`;
- `actions/checkout@v4`, que guarda a credencial do `GITHUB_TOKEN` para `git push`;
- `GITHUB_REF_NAME`, definido pelo Actions;
- o grupo de concorrência `scangoal-write`, que garante um só escritor.

O passo `--failure` não é afetado.

Efeitos operacionais:
- um push por aviso, uns 1–3 s cada; numa recolha típica, 0 a 3 avisos;
- os commits `notify: intent … [skip ci]` aparecem no histórico do `main`;
- o workflow de testes ignora `data/**`;
- um push com o `GITHUB_TOKEN` não lança workflows.

## 6. Riscos que ficam com a barreira ativa

- **Aviso perdido:** se o push for aceite mas a resposta se perder e o runner morrer, esse aviso perde-se. Fica incerto e
  aparece no resumo, mas nunca é duplicado.
- **Conflito com um commit manual:** um conflito com um commit manual em `data/notify_state.json` impede os envios até ser
  resolvido. É visível como `NOTIFY_PUBLISH_FAILED`.
- **Nada evita o reenvio se o remoto perder commits:** se alguém reescrever o histórico do `main` com force-push e apagar
  intenções publicadas, um aviso já enviado pode sair outra vez.

## 7. Integração proposta

1. As duas linhas seguem na mesma PR. Depois do merge não há nenhuma janela com a barreira inativa.
2. Verificar nas duas recolhas seguintes:
   - `NOTIFY_DURABILITY git` aparece nos registos;
   - cada aviso tem um commit `notify: intent` **anterior** ao commit com o seu resultado;
   - `post_cutover_check` está verde.
3. Reversão: retirar as duas linhas `NOTIFY_DURABLE: git` (perde-se a garantia entre runners) ou reverter a PR inteira (runbook).
   Nenhum dado a restaurar.

## 8. Validação com a barreira ligada (terceira ronda)

- Estrutura (`WorkflowActivation`): `NOTIFY_DURABLE: git` aparece exatamente duas vezes, no `env` dos passos "Telegram
  notices" e "Fast reads", e não no passo `--failure`. `permissions: contents: write`, o grupo `scangoal-write` com
  `cancel-in-progress: false` e `actions/checkout@v4` estão presentes. `watch.refresh()` chama `notify.run()` sem
  argumentos, e por isso lê o `NOTIFY_DURABLE` do passo.
- Ordem (`BarrierOrdering`): no instante em que cada texto chega ao emissor, o remoto já tem a intenção desse mesmo texto
  (`in_flight` com o mesmo `sha256`). Vale também com o emissor real (`send`, `urlopen` simulado) e pelo caminho das leituras
  rápidas (`watch.refresh`). `NOTIFY_DURABILITY git` e `NOTIFY_DURABILITY local` aparecem nos registos.
- Produção: `post_cutover_check` tem a verificação `pr5_intent_published_before_outcome`. Para cada entrega confirmada, o
  commit da intenção é anterior ao do resultado. Falha se a barreira estiver desligada, o que está testado.
- Ensaio de corte: fase `durable_*` com um remoto *bare* e um runner limpo, que corre o notificador duas vezes com a barreira:
  migração dentro da execução, zero mensagens, zero commits de intenção, contadores e marcas iguais.

O passo `--failure` (aviso de recolha falhada, no máximo um por hora) fica **fora da barreira** com o comportamento anterior:
não é um acontecimento com chave nem conta em `sent_total`.
