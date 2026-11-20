# Goal ROI — fontes e infraestruturas gratuitas para o SHADOW
**Investigação documental: 18 de novembro de 2026. Estado: apenas descoberta; nenhuma fonte ligada nem autorização para ativação.**

## Decisão executiva

A prioridade é preservar **o mesmo evento de origem ScanGoal/Telegram** que determinou as hipóteses SHADOW. Uma alternativa de estatísticas em direto não é a mesma população de sinais e **não pode ser substituída silenciosamente** no protocolo congelado H1/H2.

Dois problemas distintos:
- **Fonte**: como receber uma versão verificável do alerta original, imediatamente após o bot ScanGoal o enviar, sem usar a sessão MTProto existente do coletor.
- **Runtime e durabilidade**: como persistir cada sinal antes dos 120 segundos e garantir recuperação sem depender do disco temporário do GitHub Actions.

## Fontes possíveis — ordenadas por interesse

| Opção | Sinal original ScanGoal? | Requisitos de acesso | Estado |
| --- | --- | --- | --- |
| A. Integração autorizada pelo ScanGoal — webhook, canal dedicado ou mensagem para bot cooperante | Potencialmente sim, na origem | Consentimento e suporte do fornecedor; verificar contrato, formato, timestamps, eventos editados e SLA | **Primeira opção para perguntar; não confirmada** |
| B. Telegram Secretary Mode (Chat Automation) ligado apenas à conversa ScanGoal | Potencialmente sim | Criar bot próprio e estabelecer ligação à conta do utilizador; validar se recebe **mensagens de outro bot** em business_message; confirmação experimental indispensável | **Hipótese nova; NÃO VALIDADA** |
| C. Segunda autorização MTProto/Telethon (sessão nova e independente, mesma conta ou conta autorizada) | Sim, se a conversa contiver os alertas | Autorização explícita, credenciais isoladas, runtime 24/7, respeitar rate limits/ToS | **Tecnicamente plausível; NÃO AUTORIZADA** |
| D. Página pública ScanGoal Live (https://scangoal.com/live.aspx) | Não, não é exatamente o evento do bot | Acesso público à tabela e filtros; automatização/licença/latência/estabilidade por verificar com fornecedor | **Fonte contextual, NÃO equivalente ao SHADOW original** |
| E. Outra API gratuita de futebol/odds | Não | Quota, latência e direitos a verificar individualmente; regra e amostra seriam diferentes | **Só serve para protocolo científico NOVO** |

### Nota sobre o Telegram (alterações de 2026)

O Telegram documenta **Secretary Mode** para bots ligados a contas pessoais, com seleção das conversas autorizadas e updates business_message/edited_business_message/deleted_business_messages. Desde maio de 2026 é possível ligar um bot sem Telegram Premium. Contudo, o Telegram restringe normalmente a leitura de mensagens de outros bots; a documentação sobre *Bot-to-Bot Communication Mode* apresenta exceções específicas. **Não foi demonstrado** que mensagens existentes enviadas pelo @scangoal_bot à conta do utilizador sejam entregues ao Secretary Bot. Nem é possível ativar Bot-to-Bot Mode no bot de terceiro sem a cooperação do fornecedor. Não afirmar que resulta sem teste real com autorização da conta.

Fonte primária:
- https://core.telegram.org/bots/features#business-bots
- https://core.telegram.org/api/bots/connected-business-bots
- https://core.telegram.org/api/bots/bot-to-bot
- https://core.telegram.org/bots/api-changelog
- https://core.telegram.org/bots/faq

**Riscos**: a ligação Secretary dá acesso a conversas selecionadas e pode conceder ações em nome da pessoa. Para qualquer piloto, limitar a uma única conversa com allowlist de chat/sender; zero permissões de resposta, eliminação, modificação ou acesso a outras conversas, se a UI o permitir. Não reutilizar bot/sessão produtiva.

### Acesso ScanGoal

O próprio ScanGoal confirma que a tabela Live, estatísticas, tendências e páginas de jogo são gratuitas, mas distingue os alertas e estratégias Pro enviados pelo @scangoal_bot. Não foi localizada oferta oficial de webhook ou endpoint público para reproduzir o **mesmo** alerta OPEN_GAME, com versão, hora e odds originais. Sem confirmação expressa, nem scraping agressivo nem engenharia reversa de endpoints privados; consultar termos e disponibilidade da integração com o fornecedor. Uma leitura de 'pace' não equivale ao instante do alerta.

Fontes:
- https://scangoal.com/faq.aspx
- https://scangoal.com/live.aspx
- https://scangoal.com/about.aspx

## Runtime — opções a custo potencialmente zero

| Tecnologia | Utilidade | Limitações importantes | Veredito |
| --- | --- | --- | --- |
| **Cloudflare Workers Free + webhook + D1 Free** | Receção HTTP de eventos, armazenamento privado, deduplicação com base de dados, sem VM contínua | 100 000 requests/dia, 10 ms CPU/request (Free); D1 falha ao atingir quotas; **não é cliente MTProto permanente**. Exige fonte capaz de enviar webhook | **Preferencial SE A ou B funcionar** |
| **Oracle OCI Always Free VM** | Python/Telethon com ligação MTProto 24/7, volumes persistentes, uma sessão independente | Registo exige cartão; capacidade insuficiente por região e recuperação de VMs inativas; uptime não garantido, obrigações de administração | **Preferencial SE C for necessária e houver capacidade** |
| **Google Cloud Compute e2-micro Free** | VM 24/7 e dados em disco | Benefício de VM limitado a regiões US selecionadas; exige conta de faturação; componentes como IPv4 e consumo acima das quotas podem gerar custos | **Plano alternativo condicionado a faturação estritamente controlada** |
| **Dispositivo local dedicado (PC sempre ligado/Android Termux)** | Processo Telethon independente sem cloud paga | Energia, bateria, suspensão Android, rede e reinício; nem sempre 24/7 | **Fallback experimental, não SLA** |
| **GitHub Actions / Cloudflare Cron** | Ensaios offline, relatórios, reconciliação e rotinas periódicas | GitHub schedule atrasa ou perde runs; Cron não fornece consumo contínuo de updates MTProto | **Não serve como ingestão 120 s garantida** |

Fontes oficiais:
- https://developers.cloudflare.com/workers/platform/limits/
- https://developers.cloudflare.com/d1/platform/pricing/
- https://developers.cloudflare.com/changelog/post/2026-09-01-d1-free-tier-limit-enforcement/
- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- https://www.oracle.com/cloud/free/
- https://docs.cloud.google.com/free/docs/free-cloud-features
- https://docs.github.com/en/actions/how-tos/troubleshoot-workflows
- https://developers.cloudflare.com/workers/configuration/cron-triggers/
- https://developer.android.com/topic/performance/background-optimization

## Arquitetura preferencial SE existir webhook autorizado

```
ScanGoal original / Telegram Secretary Mode (*se funcionar e for autorizado*)
  -> Bot API webhook HTTPS autenticado (header secreto + ID allowlist)
  -> Cloudflare Workers Free
  -> D1 privado, event_id+version+hash, UTC e primeira observação imutável
  -> consumidor somente leitura + validação de resultados passados
  -> eventual recibo Git e ledger SHADOW (protocolo novo aprovado)
```

A entrega no webhook **não garante** que o consumidor científico observou o sinal em 120 s. Medir timestamps da mensagem, receção no webhook, persistência durável, disponibilização, leitura pelo consumidor e confirmação remota. Qualquer queda, atraso, UTC anómalo, mensagem editada ou evento ausente deve falhar fechado.

Não armazenar texto bruto nem URL numa branch Git pública. Para diagnosticar, preferir dados mínimos em D1 com retenção definida, proveniência/autenticação separada de hashes autoafirmados e acesso privado. O protótipo atual da PR #47 só admite eventos **sintéticos**, e assim deve permanecer até nova autorização expressa.

## Experiências de baixo risco, por ordem

**P0 — sem ligação a serviços:**
1. Pedir ao fornecedor ScanGoal se aceita enviar gratuitamente eventos OPEN_GAME a um canal/webhook/bot autorizado, ou disponibilizar endpoint documentado. Não pedir dados pagos.
2. Reproduzir offline, com fixture de tipo business_message, filtros sender/chat, deduplicação/edições e webhook assinado. Não conectar conta ou bot de produção.
3. Preparar matriz de perigos/limites Cloudflare Free e duração da retenção sem deploy.

**Após autorização EXPRESSA adicional:**
4. Testar Secretary Mode exclusivamente na conversa ScanGoal, num bot novo com permissões mínimas e sem respostas; verificar se há business_message de **outro bot**, preservando os IDs/UTC. Se não houver, arquivar B como NO-GO.
5. Se B falhar, estudar autorização independente MTProto Telethon 1.45.0 e capacidade OCI Always Free, sem tocar na sessão atual.
6. Medir numa experiência piloto SEM certificação, em condições reais, durante um período autorizado: cobertura, 120s exatos, percentis, falhas, reinícios e recuperação de armazenamento; garantir separação do coletor e dos avisos.

**Nunca** ligar automaticamente a investigação ao workflow produtivo, nem ativar 30 dias, nem declarar certeza científica baseada em simulador.

## Situação factual de partida

Auditoria independente PR #47: 14/14 sinais com primeira leitura do coletor em <=120 s, mas apenas 6/14 com primeiro commit <=120 s. Não basta melhorar Git; é necessário outro percurso de ingestão. O protótipo atual tem GO só para Draft sintético e NO-GO operacional.

**Decisão:** primeira tentativa de integração oficial com ScanGoal; simultaneamente priorizar teste documentado de Secretary Mode (sem ligar antes de autorização). Plano alternativo: sessão Telethon autónoma numa VM Always Free sujeita a confirmação das restrições de custos e privacidade.
