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
| Recuperação | #1161, primeira leitura 18:59:34; `SCANGOAL_INTERRUPTION … outside_window False`, 5 novas versões | #2163, início 19:54:51 |
| Duração sem leitura | ≈ 58 min | ≈ 100 min (histórico Git: 18:14 → 19:58) |
| Mudança de sessão | substituição do Secret (intervenção humana) | idem |

Outros factos verificados:
- **[D]** As leituras de 19/11 e 27/11 terminaram sem lacuna de dados: a janela de 400 mensagens cobriu a interrupção
  (`outside_window False` em 19/11).
- **[D]** Lacunas de recolha desde 12/11 (histórico de `data/state.json`): 15/11 95 min; **19/11 254 min (03:00→07:15)**;
  19/11 60 min (18:01→19:01); **27/11 104 min**. A de 19/11 03:00 **não** foi de sessão: a leitura Telegram esteve OK até
  ao fim (#1082, 03:00:57) e falhou o passo *Start the next collection* às 03:00:58, partindo a cadeia até às 07:13.
  Este é um modo de falha distinto (P1, ver §3).
- **[D]** Das 23 execuções falhadas desde 11/11, 15 têm `SESSION_NOT_AUTHORIZED`: #1 (11/11, arranque), #1154–1160, #2156–2162.
  As restantes (publicação, `pip install`, dispatch) não envolvem a sessão.
- **[D]** Período entre os dois incidentes: 8 d 0 h 12 min 49 s (18:01:25 → 18:14:14). A sessão instalada em 19/11 estava
  a funcionar às 18:59:34, logo a sua vida foi **entre 7 d 23 h 14 min e 8 d 0 h 12 min**. A primeira sessão viveu
  ≤ 7 d 22 h 38 min (a execução #1, 11/11 19:23, ainda falhou). Duas vidas ≈ 8 dias, uma delas com margem de ~1 h.

### 1.2 Código auditado — achados

| # | Ficheiro | Achado |
|---|---|---|
| F1 | `scangoal/reader.py` (`collect`) | `client.is_user_authorized()` do Telethon 1.45.0 faz `except errors.RPCError: self._authorized = False` (verificado no código-fonte). Todos os RPCError (AUTH_KEY_UNREGISTERED, SESSION_REVOKED, SESSION_EXPIRED, AUTH_KEY_DUPLICATED, USER_DEACTIVATED…) ficam como `SESSION_NOT_AUTHORIZED`. **A causa exata de 19/11 ficou irrecuperavelmente perdida; a de 27/11 só se conhece pela leitura rápida.** |
| F2 | `scangoal/watch.py` (`rounds`) | Um único `read()` falhado só incrementa `fails`; com 4 leituras por execução, o erro em 18:14:14 terminou o passo com `WATCH_DONE reads 3` e código 0. O primeiro sinal da morte da sessão foi **mascarado como sucesso**. Quando falha por `RuntimeError('session not authorized')` o tipo real também se perde. |
| F3 | `collect.yml` | `concurrency: scangoal-write` com `cancel-in-progress: false` garante um job a correr e no máximo um pendente. Os passos Telegram são sequenciais (reader → … → watch), cada um com `disconnect()` em `finally`. |
| F4 | `continuity.yml` | O job `restart` partilha o grupo `scangoal-write`; `observe` só lê metadados. Nenhum usa a sessão. |
| F5 | `scripts/authorize*.py` | Geram `StringSession` nova; não há rotação, validade nem identificador da sessão no repositório. `authorize.py` imprime a sessão no terminal (aceitável porque é local; o aviso está no docstring). |
| F6 | Cloudflare (`ops/continuity`) | `RECOVERY_MODE="observe"`, `RECOVERY_ENABLED="false"`: apenas observa. **[?]** O estado implantado não pôde ser confirmado (o conector Cloudflare falhou com 403 do proxy). Não trata sessões. |
| F7 | Alerta | O passo *Tell the user when a collection fails (at most once an hour)* correu em #2156 (18:28:35). Existe alerta, mas genérico, sem causa nem instrução, e repetido por hora durante o incidente. |
| F8 | Cadeia | Cada ciclo estabelece ligações novas a partir de um runner novo (IP diferente do pool do GitHub, ≈100 vezes por dia). Em ciclo falhado repete-se uma tentativa inútil a cada 14 min. |

### 1.3 Utilização simultânea da sessão

- **[R] Sobreposição entre runners deste repositório**: nas duas janelas de incidente, 30 jobs, **0 sobreposições**
  (comparação de `started_at`/`completed_at`); em 27/11 18:10–20:00 só correram `Collect ScanGoal` (série) e `pages build`.
- **[R] Outros workflows deste repositório**: só `collect.yml` referencia `TG_SESSION`/`reader`/`watch`
  (`dropping-odds.yml` usa apenas `TG_BOT_TOKEN`/`TG_CHAT_ID`). Execuções noutras refs são inertes (`if … ref == main`).
- **[R] AUTH_KEY_DUPLICATED**: não observado em nenhum log. Pela documentação, é o erro esperado quando a mesma chave
  envia pedidos em paralelo por duas ligações TCP; o erro observado foi outro (AUTH_KEY_UNREGISTERED).
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

**Causa direta [D]:** o servidor do Telegram deixou de reconhecer a chave de autorização (AUTH_KEY_UNREGISTERED observado
em 27/11), enquanto a ligação estava ativa e sem qualquer sobreposição de uso a partir deste repositório. A sessão
foi invalidada **do lado do Telegram**; não foi esgotada nem corrompida do lado do projeto.

**Causa estrutural [não demonstrada]:** não é possível, com a evidência disponível, afirmar *porque* o Telegram invalidou a
chave. As hipóteses, por ordem de plausibilidade:

- **H1 [P] – Terminação por proteção anti-abuso/sessão com padrão de IP anómalo.** Uma chave de utilizador usada de ≈100 IPs
  de datacenter diferentes por dia, com ligações curtas e recorrentes, é um padrão que sistemas anti-abuso podem tratar como
  suspeito. Consistente com F8. Sem prova: exige a política interna do Telegram, que não é pública.
- **H2 [P] – Prazo de ≈ 8 dias.** Duas vidas de sessão ≈ 8 dias (§1.1) e hora do dia semelhante. Se as horas de criação das
  sessões (visíveis em Dispositivos) coincidirem com ~8 d antes da morte, passa a quase demonstrada. Com 2 pontos pode ser coincidência.
- **H3 [P] – Intervenção externa** (terminar sessões a partir de outro dispositivo; sessão partilhada com outro consumidor).
  Distinguível pelo registo de segurança do Telegram/«Dispositivos» e por SESSION_REVOKED em vez de AUTH_KEY_UNREGISTERED.
- **H4 [R] – Concorrência entre runners deste repositório** (§1.3).
- **H5 [R] – Reconexões frequentes por si só**: o padrão manteve-se estável durante 7 dias (centenas de ciclos sem erro) antes de cada incidente.

**A causa estrutural fica por determinar.** O que é demonstrável é a *falta de diagnóstico* (F1, F2) e a *falta de
recuperação automática*: ambos os incidentes exigiram 58 e 100 min de intervenção humana.

## 3. Classificação de riscos

| Nível | Risco | Evidência |
|---|---|---|
| **P0** | Sessão invalidada sem recuperação automática possível; ≈ 8 d entre incidentes; requer ação humana (QR) | A/B |
| **P0** | Diagnóstico perdido: o tipo de erro não chega aos logs de forma fiável (F1, F2) | código e logs |
| P1 | Cadeia quebrada por falha do dispatch (19/11, 4 h 12 min) e watchdog Cloudflare só em observação | #1082 |
| P1 | Alerta genérico, repetido de hora a hora, sem causa nem instrução | F7 |
| P1 | Ponto único: uma só sessão, um só local de execução | arquitetura |
| P2 | Janela de 400 mensagens: uma interrupção muito longa deixa `pending` (o backfill por páginas mitiga) | `record_window` |
| P2 | Leituras tardias de sinais: o diário prospetivo mantém recibos reais (`stamp_receipt`), mas sinais ao vivo emitidos durante a avaria não geram alerta | observação |

## 4. Soluções comparadas (custo adicional: zero em todas, salvo indicação)

| Solução | Efeito sobre a causa | Prós | Contras / risco |
|---|---|---|---|
| **S1 Diagnóstico + circuit breaker + alerta único** (`scangoal/session_guard.py`) | Não evita a invalidação; reduz tempo de deteção/resposta e elimina tentativas inúteis | Zero custo, reversível, testado offline | Não recupera sozinha |
| **S2 Sessão exclusiva por projeto + lease** | Elimina H3 se o consumidor externo existir | Simples; regra operacional | Só local; entre máquinas depende de disciplina e da concorrência do GitHub |
| **S3 Ligação persistente** (um processo contínuo) | Remove os ~100 IPs/dia (ataca H1) | Reduz o padrão suspeito; menor latência | GitHub Actions não oferece processo permanente (job ≤ 6 h; cadeia de re-despacho) |
| **S4 Recolhedor permanente fora do GitHub** (Termux no telemóvel, ou PC doméstico) | Idem S3, com IP residencial estável | Zero custo em hardware já existente | Disponibilidade depende do dispositivo; escrita única no Git exige desenho para não violar o escritor único |
| **S5 Cloudflare** | Não recomendado como recolhedor | Já usado para continuidade | Workers não mantêm MTProto fiável; Durable Objects exigiriam cliente JS não verificado. Fica como watchdog |
| **S6 Sessão B independente (standby)** | Recuperação sem esperar pelo humano | Mitiga qualquer causa | Só segura com chaves **distintas** por local (nunca a mesma `StringSession` em dois sítios); a 2.ª sessão sofre os mesmos riscos H1/H2 |

## 5. Arquitetura recomendada

Faseada, mantendo a produção atual até haver prova:

1. **Já (isolado, esta PR)**: `session_guard` como biblioteca testada, sem integração.
2. **Fase 1 – Diagnóstico** (requer aprovação): usar `diagnose()` em vez de `is_user_authorized()` no `reader`; registar apenas o
   nome canónico do erro; `watch` não mascarar erro de autorização como sucesso.
3. **Fase 2 – Breaker + alerta único**: breaker persistido fora do Git (ou em `data/` só com fingerprint HMAC, sem segredo);
   alerta imediato com causa e instrução, repetido no máximo a cada 24 h; fecho automático quando o Secret muda.
4. **Fase 3 – Piloto de recolhedor persistente em IP estável** (S4) como **segunda sessão independente** (S6), em modo
   sombra: grava para um diário separado, sem escrever em `data/messages.jsonl`, e compara-se com o GitHub.
5. **Fase 4 – Decisão**, com os dados do piloto: manter GitHub como primário e standby, ou inverter.

Pré-requisito antes da Fase 3: o utilizador recolhe em «Dispositivos» as datas de criação/IP das sessões (resolve H2/H3).

## 6. Plano de implementação e rollback

| Fase | Entrega | Rollback |
|---|---|---|
| 0 (esta PR, draft) | `session_guard.py` + testes + este relatório; sem alterar workflows, `reader` nem `watch` | fechar a PR; nada em produção |
| 1 | PR própria: `diagnose()` no reader, erro de autorização visível no watch | reverter o commit; o reader volta a `is_user_authorized` |
| 2 | PR própria: breaker + alerta único atrás de variável de repositório (por defeito desligada) | desligar a variável |
| 3 | Piloto separado, sem tocar na cadeia | desligar o dispositivo; revogar a 2.ª sessão em Dispositivos |

Cada fase requer autorização expressa; nenhuma mexe no protocolo C01, nos critérios, estratégias ou dados históricos.

## 7. Critérios de aceitação operacional

1. 100 % dos incidentes de sessão classificados com o nome canónico do erro nos logs (0 `SESSION_NOT_AUTHORIZED` genérico).
2. 1 alerta por incidente (não 7), com causa e instrução, em ≤ 5 min do primeiro erro.
3. 0 tentativas de leitura com breaker aberto e o mesmo segredo; reabertura automática ≤ 1 ciclo após a substituição.
4. 0 sobreposições de uso da sessão; 0 `AUTH_KEY_DUPLICATED`.
5. Recuperação sem perda: mensagens da interrupção recolhidas exatamente uma vez; `messages.jsonl` e `prospective_ledger.jsonl`
   anteriores byte a byte intactos.
6. 0 segredos em logs/artefactos (verificação por redação e por testes).
7. Para fechar o incidente como **estruturalmente resolvido**: ≥ 3 janelas de 8 dias sem invalidação, ou causa
   identificada e eliminada com evidência (não basta a substituição manual).

## 8. Testes preparados (`tests/test_session_guard.py`, 31, offline)

Classificação de todos os erros de autorização e transitórios; diagnóstico sem repetições; breaker (sete falhas → 1 alerta,
reabertura por novo segredo, transitórias nunca abrem, estado corrompido, sem segredo no estado); redação de StringSession,
api_hash e token de bot; o `reader` imprime só o tipo da exceção; lease com 16 concorrentes (exatamente 1 vencedor —
o teste revelou e corrigiu uma corrida na minha primeira versão); recuperação de mensagens após interrupção sem duplicados,
edição como nova versão, lacuna superior à janela assinalada e diário prospetivo intacto; o módulo não está ligado à produção
e não abre ligações.

## 9. Limitações e questões em aberto

- Causa estrutural não demonstrada (H1–H3). Resolve-se com: datas de criação em «Dispositivos»; se a nova sessão cai também
  ≈ 8 dias depois; tipo de erro registado (Fase 1).
- Não li diretamente a documentação oficial (rede restrita); só excertos.
- Não verifiquei o estado implantado do Worker Cloudflare (conector indisponível), nem `asianodds-h1-lab`, nem outros dispositivos.
- Os 121 novos registos de #2163 vêm do enunciado; o log de #2163 ainda não estava concluído quando consultei.
- Não foram feitas ligações reais ao Telegram (restrição). Qualquer teste de H1 exige ligação real e aprovação separada.
- A suite completa (`unittest discover -s tests`) é lenta; os 31 testes novos e `test_collection_continuity` passam.

## 10. Veredito

- **Draft PR (`session_guard` isolado + testes + relatório): GO** — não altera produção.
- **Integração em produção: NO-GO por agora** — falta o diagnóstico real (Fase 1) e a identificação da causa antes de
  afirmar qualquer remédio.
- **Incidente: NÃO estruturalmente resolvido.** A substituição manual recuperou a recolha, mas a causa ficou por demonstrar
  e a recorrência (≈ 8 dias) é esperada até haver diagnóstico e recuperação automática.
