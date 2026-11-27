# Auditoria P0 — Invalidação recorrente da TG_SESSION

Data: 2026-11-27 · Âmbito: leitura de produção (logs/metadados do GitHub Actions e histórico Git); sem ligações ao
Telegram, sem acesso a segredos, sem alteração de dados, protocolos ou workflows. Branch de trabalho: `ccr-4bb8f38c-b288jh`.

Convenção: **[D]** demonstrado por evidência verificável · **[P]** hipótese plausível, não demonstrada · **[R]** rejeitada
com a evidência indicada · **[?]** não verificável a partir daqui.

## 1. Relatório forense

### 1.1 Cronologia dos incidentes (UTC)

| | Incidente A (19/11) | Incidente B (27/11) |
|---|---|---|
| Último ciclo bom | #1153 (run 44581691209); última leitura rápida OK às 18:01:25 | #2155 (run 46046060507); leituras OK às 18:00:32, 18:08:15, 18:11:12, 18:12:14 |
| Primeiro sinal | #1154, 18:01:44: `SCANGOAL_READ_FAILED SESSION_NOT_AUTHORIZED` (19 s após a última leitura boa) | #2155, 18:14:14: `WATCH_READ_FAILED AuthKeyUnregisteredError`, numa ligação já aberta e usada com sucesso 2 min antes |
| Ciclos falhados | 7 seguidos, #1154–#1160 (todos `SESSION_NOT_AUTHORIZED`) | 7 seguidos, #2156–#2162 (todos `SESSION_NOT_AUTHORIZED`) |
| Retoma da recolha | #1161, primeira leitura 18:59:34, `outside_window False` e 5 novas versões | #2163, início 19:54:51, 400 mensagens, 121 novas versões e `outside_window False`. #2163 e #2164 concluíram com sucesso, segundo a validação independente |
| Duração sem leitura | ≈ 58 min | ≈ 100 min (histórico Git: 18:14 → 19:58) |
| Mudança de sessão | substituição do Secret (intervenção humana) | idem |

Outros factos verificados:
- **[D]** `outside_window False` indica que o controlo da janela não sinalizou um intervalo de IDs por cobrir. Quando há
  IDs de referência, o controlo compara o ID mais antigo lido com o último ID de referência da recolha mais um. Este
  controlo não confirma a presença de todos os IDs nem a recuperação de versões intermédias de mensagens editadas, de
  mensagens entretanto apagadas ou de outros dados que o Telegram já não disponibilizasse. Os 121 registos de #2163 são
  versões novas observadas nessa execução.
- **[D]** Lacunas de recolha desde 12/11 (histórico de `data/state.json`): 15/11 95 min; **19/11 254 min (03:00→07:15)**;
  19/11 60 min (18:01→19:01); **27/11 104 min**. A de 19/11 03:00 **não** foi de sessão: a leitura Telegram esteve OK até
  ao fim (#1082, 03:00:57) e falhou o passo *Start the next collection* às 03:00:58, partindo a cadeia até às 07:13.
  Este é um modo de falha distinto (P1, ver §3).
- **[D]** Das 23 execuções falhadas desde 11/11, 15 têm `SESSION_NOT_AUTHORIZED`: #1 (11/11, arranque), #1154–1160, #2156–2162.
  As restantes (publicação, `pip install`, dispatch) não envolvem a sessão.
- **[D]** Entre os primeiros sinais registados nos dois incidentes decorreram 8 d 0 h 12 min 30 s (18:01:44 → 18:14:14).
  As horas de criação das chaves não foram confirmadas. O intervalo observado não estabelece a duração das sessões nem um
  prazo de validade imposto pelo Telegram.

### 1.2 Código auditado — achados

| # | Ficheiro | Achado |
|---|---|---|
| F1 | `scangoal/reader.py` (`collect`) | `client.is_user_authorized()` do Telethon 1.45.0 faz `except errors.RPCError: self._authorized = False` (verificado no código-fonte). Os RPCError capturados por essa verificação ficam como `SESSION_NOT_AUTHORIZED`. Os logs consultados de 19/11 não identificam o erro RPC original. Em 27/11, a classe do erro foi registada no `watch`. |
| F2 | `scangoal/watch.py` (`rounds`) | Um único `read()` falhado só incrementa `fails`; com 4 leituras por execução, o erro em 18:14:14 terminou o passo com `WATCH_DONE reads 3` e código 0. O primeiro sinal da morte da sessão foi **mascarado como sucesso**. Quando falha por `RuntimeError('session not authorized')` o tipo real também se perde. |
| F3 | `collect.yml` | `concurrency: scangoal-write` com `cancel-in-progress: false` garante um job a correr e no máximo um pendente. Os passos Telegram são sequenciais (reader → … → watch), cada um com `disconnect()` em `finally`. |
| F4 | `continuity.yml` | O job `restart` partilha o grupo `scangoal-write`; `observe` só lê metadados. Nenhum usa a sessão. |
| F5 | `scripts/authorize*.py` | Geram `StringSession` nova; não há rotação, validade nem identificador da sessão no repositório. `authorize.py` imprime a sessão no terminal (aceitável porque é local; o aviso está no docstring). |
| F6 | Cloudflare (`ops/continuity`) | `RECOVERY_MODE="observe"`, `RECOVERY_ENABLED="false"`: apenas observa. **[?]** O estado implantado não pôde ser confirmado (o conector Cloudflare falhou com 403 do proxy). Não trata sessões. |
| F7 | Alerta | O passo *Tell the user when a collection fails (at most once an hour)* correu em #2156 (18:28:35). O aviso configurado é genérico e limitado a uma tentativa por hora. A execução do passo, por si só, não confirma a entrega. |
| F8 | Cadeia | Os ciclos usam runners alojados pelo GitHub e abrem ligações novas. Os ciclos falhados voltaram a tentar a recolha em intervalos de cerca de 14 min. A auditoria não mediu os IPs de saída usados nos ciclos. [GitHub-hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#ip-addresses) |

### 1.3 Utilização simultânea da sessão

- **[R] Sobreposição entre runners deste repositório**: nas duas janelas de incidente, 30 jobs, **0 sobreposições**
  (comparação de `started_at`/`completed_at`); em 27/11 18:10–20:00 só correram `Collect ScanGoal` (série) e `pages build`.
- **[R] Outros workflows deste repositório**: só `collect.yml` referencia `TG_SESSION`/`reader`/`watch`
  (`dropping-odds.yml` usa apenas `TG_BOT_TOKEN`/`TG_CHAT_ID`). Execuções noutras refs são inertes (`if … ref == main`).
- **[?] AUTH_KEY_DUPLICATED** não foi observado nos logs consultados. A verificação descrita em F1 apaga o tipo de RPCError,
  pelo que estes logs não permitem excluir essa classe em todas as tentativas.
- **[?] Outros consumidores da mesma sessão** (telemóvel/Termux, computador, `asianodds-h1-lab`): não verificável.
  `asianodds-h1-lab` não está acessível a esta sessão. O utilizador pode confirmar em Telegram → Definições → Dispositivos
  (lista data de início, aplicação, IP e local de cada sessão; é consulta local, sem segredos).

### 1.4 Documentação consultada

- Telegram, *Error handling* (<https://core.telegram.org/api/errors>), via resultado de pesquisa: AUTH_KEY_UNREGISTERED
  (401, chave desconhecida do servidor); SESSION_REVOKED (autorização invalidada, p. ex. pelo utilizador ao terminar sessões);
  SESSION_EXPIRED; AUTH_KEY_DUPLICATED (mesma chave usada em simultâneo em duas ligações; o servidor já invalidou a sessão).
- Código-fonte do Telethon 1.45.0 (instalado a partir do wheel, sem ligação ao Telegram).
- **Limitação:** o acesso de rede desta sessão não permitiu abrir `core.telegram.org` nem `docs.telethon.dev`; a documentação
  oficial não foi lida diretamente, apenas por excertos de pesquisa. Não alterei as definições de rede. Antes de decisões
  finais, confirmar essas páginas.

## 2. Causa-raiz

**Causa direta [D] em #2155.** O Telegram devolveu `AUTH_KEY_UNREGISTERED` para a chave usada numa ligação que tinha lido
mensagens cerca de dois minutos antes. A análise das janelas consultadas não encontrou sobreposição de runners deste
repositório. Os restantes ciclos produziram a indicação genérica descrita em F1.

**Causa estrutural por determinar.**

- **H1 [P] – Padrão de rede ou de ligações.** As novas ligações podem ser relevantes. Desconhecem-se os IPs efetivamente
  usados e não há evidência de que um padrão de rede tenha motivado a rejeição da chave.
- **H2 [P] – Relação com a idade das sessões.** O intervalo entre os dois incidentes é conhecido. Faltam as datas de criação
  das sessões e um conjunto de observações que permita avaliar periodicidade ou expiração.
- **H3 [P] – Intervenção externa** (terminar sessões a partir de outro dispositivo; sessão partilhada com outro consumidor).
  Os registos de Dispositivos e eventuais erros RPC futuros podem ajudar a avaliar esta hipótese.
- **H4 [R] – Concorrência entre runners deste repositório nas janelas analisadas** (§1.3).

Os diagnósticos foram insuficientes (F1, F2). A recolha retomou após intervenção humana, cerca de 58 min e 100 min após
os respetivos primeiros sinais.

## 3. Classificação de riscos

| Nível | Risco | Evidência |
|---|---|---|
| **P0** | Perda de autorização sem recuperação automática configurada para este erro. Nos dois incidentes a retoma seguiu-se a intervenção humana | A/B |
| **P0** | Diagnóstico perdido: o tipo de erro não chega aos logs de forma fiável (F1, F2) | código e logs |
| P1 | Cadeia quebrada por falha do dispatch (19/11, 4 h 12 min) e watchdog Cloudflare configurado em observação no repositório | #1082, F6 |
| P1 | Aviso genérico sujeito a um intervalo mínimo de uma hora entre tentativas | F7 |
| P1 | Ponto único: uma só sessão, um só local de execução | arquitetura |
| P2 | Janela de 400 mensagens: uma interrupção muito longa deixa `pending` (o backfill por páginas mitiga) | `record_window` |
| P2 | Leituras tardias de sinais: o diário prospetivo mantém recibos reais (`stamp_receipt`), mas sinais ao vivo emitidos durante a avaria não geram alerta | observação |

## 4. Soluções comparadas

| Solução | Efeito sobre a causa | Prós | Contras / risco |
|---|---|---|---|
| **S1 Diagnóstico + circuit breaker + alerta confirmado** (`scangoal/session_guard.py`) | Pode reduzir o tempo de deteção e impedir novas tentativas com a chave inválida | Reversível, testado offline | A retoma da recolha continua a depender de uma chave autorizada. Se a confirmação se perder, repetir o aviso pode produzir um duplicado |
| **S2 Sessão exclusiva por projeto + lease** | Limita a reutilização se todos os consumidores participarem | Pode impedir a sobreposição dentro do âmbito do lock | Entre máquinas requer coordenação partilhada ou uma regra operacional verificável |
| **S3 Ligação persistente** (um processo contínuo) | Hipótese em aberto | Pode reduzir a frequência de novas ligações | Requer um anfitrião com processo de longa duração. Ver §4.1 |
| **S4 Recolhedor permanente fora do GitHub** | Hipótese em aberto | Permite avaliar um processo duradouro fora do CI | Disponibilidade, energia, rede, armazenamento e escritor único exigem avaliação. Ver §4.1 |
| **S5 Cloudflare** | Hipótese em aberto | Já previsto no desenho de continuidade | A adequação para um cliente MTProto persistente não foi demonstrada. Ver §4.1 |
| **S6 Sessão B independente (standby)** | Pode permitir a retoma se só a sessão primária perder autorização | Reduz a dependência de uma única chave | Requer uma chave distinta. Uma falha comum pode afetar ambas as sessões |

### 4.1 Avaliação separada de um recolhedor persistente sem custo de alojamento adicional

- Um computador Linux já ligado, com acesso à Internet e disco local, é um candidato sem nova subscrição de alojamento.
  A continuidade depende do dispositivo, da alimentação elétrica e da rede. Um serviço que exija manter equipamento
  ligado adicionalmente pode aumentar o consumo e o custo. Uma ligação residencial pode mudar de endereço IP.
- Um dispositivo Android com Termux tem condicionantes adicionais. As políticas Doze e App Standby podem adiar o trabalho
  em segundo plano e suspender o acesso à rede. [Android Developers](https://developer.android.com/training/monitoring-device-state/doze-standby)
- Uma VM OCI Always Free pode oferecer um processo de longa duração e armazenamento em disco dentro das quotas. A
  capacidade depende da região de origem e a Oracle pode recuperar instâncias consideradas inativas. Estes limites podem
  afetar um recolhedor leve. [OCI Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- Os jobs dos runners alojados pelo GitHub estão limitados a seis horas. O Render gratuito suspende o serviço web após
  15 minutos sem tráfego recebido e não permite disco persistente. [GitHub Actions](https://docs.github.com/en/actions/reference/limits), [Render](https://render.com/docs/free)
- Durable Objects podem abrir TCP de saída. Um socket impede a expulsão do objeto por um máximo de 15 minutos, após os
  quais voltam a aplicar-se as regras normais de expulsão. A continuidade do cliente MTProto e o consumo dentro das
  quotas gratuitas ficaram por validar. [Cloudflare TCP sockets](https://developers.cloudflare.com/workers/runtime-apis/tcp-sockets/), [Cloudflare quotas](https://developers.cloudflare.com/durable-objects/platform/pricing/)

Uma eventual avaliação posterior poderia medir disponibilidade, reconexões, atraso e lacunas de observação num diário
isolado, com uma chave independente e sem outro escritor de dados científicos. A ligação persistente não demonstra a
causa da rejeição da chave nem garante que esta se mantenha válida. A avaliação acima foi documental, sem implementação.

## 5. Arquitetura recomendada

Faseada, mantendo a produção atual até haver prova:

1. **Já (isolado, esta PR)**: `session_guard` como biblioteca testada, sem integração.
2. **Fase 1 – Diagnóstico**: preservar as classes dos erros RPC nas ligações existentes, registar apenas classes e códigos
   seguros e dar ao `watch` um estado de falha quando ocorrer perda definitiva de autorização. Manter a cadeia de workflows
   e deixar o breaker, o failover e a recuperação autónoma desligados.
3. **Fase 2 – Breaker + alerta confirmado**. Avaliar armazenamento durável partilhado e coordenação entre runners.
   O estado usa HMAC-SHA256 completo e conserva o prazo de `FloodWait` mesmo após troca de sessão. Um lembrete de um
   alerta confirmado só fica elegível após 24 h. A biblioteca local ainda depende de um volume com lock POSIX e
   substituição atómica para partilhar estado entre máquinas.
4. **Fase 3 – Eventual piloto de recolhedor persistente** (S4) com **sessão independente** (S6), em modo
   sombra: grava para um diário separado, sem escrever em `data/messages.jsonl`, e compara-se com o GitHub.
5. **Fase 4 – Decisão**, com os dados do piloto: manter GitHub como primário e standby, ou inverter.

## 6. Plano de implementação e rollback

| Fase | Entrega | Rollback |
|---|---|---|
| 0 (esta PR, draft) | `session_guard.py` + testes + este relatório; sem alterar workflows, `reader` nem `watch` | fechar a PR; nada em produção |
| 1 | PR draft própria: diagnóstico no reader e no watch e continuidade dos workflows perante falhas de autorização | fechar a draft. Se vier a ser integrada no futuro, reverter o commit dessa fase |
| 2 | PR própria: breaker + alerta confirmado atrás de variável de repositório (por defeito desligada) | desligar a variável |
| 3 | Piloto separado, sem tocar na cadeia | desligar o dispositivo; revogar a 2.ª sessão em Dispositivos |

A auditoria e a Fase 1 preservam o protocolo C01, os critérios, as estratégias e os dados históricos. As fases seguintes são propostas.

## 7. Critérios de aceitação operacional

1. 100 % dos incidentes de sessão classificados com o nome canónico do erro nos logs (0 `SESSION_NOT_AUTHORIZED` genérico).
2. 1 alerta por incidente (não 7), com causa e instrução, em ≤ 5 min do primeiro erro.
3. 0 tentativas de leitura com breaker aberto e o mesmo segredo; reabertura automática ≤ 1 ciclo após a substituição.
4. 0 sobreposições de uso da sessão; 0 `AUTH_KEY_DUPLICATED`.
5. Recolher as versões disponíveis sem duplicar os registos e preservar os bytes já gravados em `messages.jsonl` e
   `prospective_ledger.jsonl`. Indicar os limites da janela e da evidência sobre versões indisponíveis.
6. 0 segredos em logs/artefactos (verificação por redação e por testes).
7. Para fechar o incidente como **estruturalmente resolvido**, identificar a causa e verificar que a intervenção a elimina.
   Um período sem recorrências documenta estabilidade observada.

## 8. Validação offline

### Commit original

O commit `886ef025da137947b58e3747fc7e311d99e76cf4` incluía 31 testes offline no ficheiro `tests/test_session_guard.py`.
Os cenários simulados cobriam classificação de erros, estado do breaker, redação de credenciais, exclusão mútua local e
comportamento da recolha após uma interrupção. A validação independente confirmou 1213 testes Python e 12 testes
JavaScript aprovados. [CI do commit original](https://github.com/nunovinhas-creator/goal-roi/actions/runs/46062738532)

### Revisão adversarial da PR #114

Os 50 testes focados passaram offline em Python 3.11 com as classes de erro do Telethon 1.45.0 e clientes simulados.

| Cenário | Evidência no commit original | Correcção verificada offline |
|---|---|---|
| Breaker antes de ligar | Dois ciclos contaram duas ligações e dois pedidos, mesmo com o breaker já aberto no segundo ciclo | `run` consulta o estado sob lock antes de invocar a factory. A entrega de alertas pendentes pode ocorrer sem ligar |
| Lease activo | Outro titular adquiriu a lease após o TTL, com o primeiro ainda activo | O lock mantém-se até fechar o descritor. O teste com `SIGKILL` confirma que o sistema o liberta após a morte do titular |
| Entrega de alerta | A decisão gravava uma entrega e suprimia a próxima tentativa sem ter enviado nada | Só uma confirmação positiva grava `alerted_utc`. Falha ou ausência de confirmação mantém a entrega pendente |
| JSON inválido | Listas, nulos e campos inválidos provocaram erros incidentais | Chaves duplicadas, estrutura, versão, tipos e datas inválidos são rejeitados antes de ligar. O ficheiro fica preservado |
| HMAC | Duas sessões sintéticas distintas colidiram nos 32 bits gravados | O HMAC usa os 256 bits de SHA-256 |
| `UnauthorizedError` | `SessionPasswordNeededError` também era tratado como revogação | Só as classes específicas de invalidação definitiva abrem o breaker |
| Falhas desconhecidas e cancelamento | Uma falha desconhecida permitia prosseguir. `diagnose` propagava o cancelamento, mas `classify` marcava-o como repetível | Uma falha desconhecida termina a tentativa. O cancelamento propaga-se e liberta o lock |
| Runners independentes | Uma nova pasta perdia o estado | Processos independentes recuperam o estado na mesma pasta partilhada. O modo de estado obrigatório rejeita a ausência do ficheiro, antes de ligar |
| `FloodWait` | O classificador perdia o número de segundos e permitia uma nova tentativa | O prazo mais longo fica persistido e impede a ligação. Sem prazo utilizável, a biblioteca bloqueia novas tentativas até revisão do estado |

## 9. Limitações e questões em aberto

- A causa estrutural (H1–H3) continua por determinar. Faltam as datas de criação, os dados de rede dos ciclos e o tipo
  de RPCError ocultado em vários casos. A Fase 1 poderá aumentar a evidência disponível nos próximos ciclos.
- Na auditoria inicial, a documentação do Telegram e do Telethon foi consultada através dos excertos indicados em §1.4.
- Não verifiquei o estado implantado do Worker Cloudflare (conector indisponível), nem `asianodds-h1-lab`, nem outros dispositivos.
- A validação independente confirmou as contagens e os estados dos ciclos #2163 e #2164. A recuperação integral de
  todas as versões históricas durante a interrupção continua por verificar.
- O teste entre runners usa subprocessos no mesmo anfitrião. A persistência entre máquinas depende de um volume durável
  comum com locks e operações atómicas validados e relógios sincronizados. Nenhum backend partilhado foi provisionado.
  Um ficheiro legado de 32 bits é rejeitado para revisão explícita, pois não comprova a identidade da sessão atual.
- A auditoria e os testes foram realizados sem ligações reais ao Telegram.

## 10. Veredito

- **Draft PR (`session_guard` isolado + testes + relatório): GO** — não altera produção.
- **Integração em produção: NO-GO por agora** — falta o diagnóstico real (Fase 1) e a identificação da causa antes de
  afirmar qualquer remédio.
- **Incidente com recolha retomada e causa estrutural em aberto.** O intervalo entre os dois incidentes não permite
  prever quando ou se haverá nova rejeição da chave.
