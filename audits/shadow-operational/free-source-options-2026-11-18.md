# SHADOW — pesquisa de fontes e execução gratuita (18/11/2026)

**Parecer: existe uma via gratuita plausível, mas ainda NÃO há fonte real nem SLA de 120 s demonstrados.** Esta pesquisa é apenas arquitetural. Não ativa Telegram, SHADOW, workflows ou serviços. PR #47 mantém-se Draft.

## Fonte de dados confirmada no Goal ROI

- `scangoal/reader.py` lê a conversa com `TG_SCANGOAL` através de Telethon/MTProto, usando uma **conta de utilizador** e `TG_SESSION`. `receive_updates=False`; efetua leituras periódicas.
- `.github/workflows/collect.yml` lança o leitor até 220 s, faz o primeiro push de dados, corre `scangoal.watch` com leituras de 120 em 120 segundos e só depois faz o push seguinte.
- Os dados históricos recolhidos em 24 h mostram 14/14 sinais com primeira recolha <=120 s, mas apenas 6/14 com primeiro commit local <=120 s; o Git remoto e o consumidor independente não foram medidos. Não extrapolar esta amostra para um SLA.
- `scangoal/shadow_delivery.py` auditado recusa `origin != synthetic` e destinos que não sejam um Git bare local; não deve ser contornado sem revisão formal.

## Alternativas de obtenção dos sinais

| Fonte | Gratuita | Capta mensagens do ScanGoal? | Latência potencial | Bloqueio |
|---|---|---|---|---|
| **Segunda sessão Telethon/MTProto, conta autorizada** | Sim, API Telegram | Sim, se a conta tiver acesso legítimo à conversa | Segundos com atualizações push; polling pode ser mais lento | Sessão nova, segurança e processo sempre ligado |
| Bot API próprio, adicionado a grupo/canal | Sim | **Não garantido** na conversa privada do utilizador com o bot ScanGoal; requer participação e permissões do bot | Webhook quase imediato quando elegível | Acesso a mensagens de terceiros e permissões; Bot-to-Bot Mode não substitui acesso à conversa privada |
| Canal/grupo de encaminhamento autorizado | Sim | Apenas se o proprietário autorizar o encaminhamento e o conteúdo for legitimamente acessível | Segundos, mas depende de quem encaminha | Nova integração; risco de perda, alterações de IDs e proveniência |
| Website/API pública ScanGoal | Desconhecido | **Não demonstrado** feed oficial equivalente e completo | Desconhecida | Não inventar endpoint, autorização ou cobertura |
| Reutilizar `main/data/messages.jsonl` | Sim | Sim, com atraso | 8/14 commits históricos já tardios | Não cumpre a meta científica em parte da amostra |

Telegram suporta sessões MTProto distintas; Telethon recomenda sessões diferentes para processos concorrentes. Não reutilizar `TG_SESSION` do coletor, nem executar dois processos com a mesma sessão.

Fontes:
- https://core.telegram.org/api/datacenter
- https://docs.telethon.dev/en/stable/quick-references/faq.html
- https://core.telegram.org/bots/faq
- https://core.telegram.org/api/bots/bot-to-bot

## Onde manter o leitor ligado sem pagar

| Plataforma | Plano gratuito | Adequação ao leitor Telethon sempre ligado | Risco |
|---|---|---|---|
| **Oracle Cloud Always Free VM** | Até 2 micro AMD ou quota Arm A1 | **Melhor candidato**, Linux com processo Python persistente, reinício automático, armazenamento de disco e saída MTProto | Capacidade de criação não garantida, eventual recolha de instâncias ociosas, configuração/conta necessária |
| **Google Cloud Compute Engine e2-micro Free Tier** | Uma e2-micro equivalente em regiões elegíveis, com limites de disco/tráfego | **Candidato alternativo**, VM Linux contínua e sessão separada | Regiões elegíveis, faturação associada e risco de exceder limites; configurar orçamento e restrições |
| Cloudflare Workers Free | 100 000 pedidos/dia, cron até ao minuto | **Bom para webhook/receção e armazenamento**, não é alojamento nativo de Telethon Python com socket MTProto persistente | Precisaria de fonte HTTP push elegível; cron de 1 minuto não prova disponibilidade do leitor |
| Render Web Service Free | 750 h/mês | **Inadequado para SLA**, suspende após 15 min sem tráfego inbound; filesystem efémero | Reinício ~1 min e perda de dados locais |
| GitHub Actions | Min cron 5 min, arranque sem SLA | **Inadequado como leitor de eventos em 120 s** | Fila e latência, limite de execução |
| GitHub Codespaces | Quota gratuita sujeita a limites | Apenas desenvolvimento, não runtime 24/7 | Suspensão por inatividade |

Fontes oficiais:
- https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm
- https://docs.cloud.google.com/free/docs/free-cloud-features
- https://developers.cloudflare.com/workers/platform/limits/
- https://developers.cloudflare.com/workers/configuration/cron-triggers/
- https://render.com/docs/free
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax

## Arquitetura preferida para futuro piloto **mediante autorização separada**

1. VM Always Free independente com sessão MTProto própria e permissões mínimas, só leitura, `receive_updates=True` e reconciliação periódica. Nunca reutilizar sessão ou secrets do coletor.
2. Registo local durável de eventos e checkpoints, com cifragem, fsync, deduplicação de versões, monitorização de lacunas e relógios UTC. Não publicar textos/URLs/IDs em ramo público.
3. Adaptador de dados **novo e auditado**, distinto do protótipo sintético atual; decidir se o consumidor SHADOW fica na mesma VM ou noutro serviço. Se estiver noutro runner, medir a entrega completa e guardar outbox persistente com autenticação.
4. Contabilizar o instante de emissão, atualização recebida, primeira persistência, commit, confirmação de entrega e instante de observação do consumidor; guardar métricas agregadas, erros e heartbeat.
5. Validar em piloto curto autorizado, com falhas de rede/reinício e prova de persistência. Só depois discutir uma janela prospetiva de 30 dias; não incluir resultados futuros na decisão.

## Portas de decisão

- **GO para desenho e testes offline:** sim.
- **GO para ativação de fonte real:** NÃO, falta autorização e sessão independente.
- **GO para SLA 120 s:** NÃO, falta medição em operação real e disponibilidade.
- **GO para SHADOW 30 dias:** NÃO.
- **Condição externa a confirmar:** disponibilidade efetiva de VM Always Free e elegibilidade de conta, sem converter para serviço pago.

**Próximo passo seguro:** preparar um manifesto de configuração para VM com modo simulado e um contrato de adaptador de fonte autenticada, mantendo tudo desligado. Nenhuma instalação ou ligação a Telegram deve ocorrer sem aprovação expressa.

## Controlo financeiro obrigatório — Google Cloud (atualização de 18/11/2026)

O proprietário confirmou ter conta Google Cloud, mas definiu uma restrição **absoluta de 0 € de faturação**. Isto altera a decisão: **não provisionar a VM nem qualquer recurso faturável** apenas por constar do Free Tier.

### Risco confirmado em documentação oficial

- Compute Engine Free Tier cobre **uma e2-micro** nas regiões `us-west1`, `us-central1` ou `us-east1`, **30 GB-mês de Persistent Disk standard** e **1 GB/mês de saída para destinos elegíveis**. Créditos/quotas não equivalem a bloqueio de cobrança.
- O **IPv4 externo utilizado por uma VM normal** custa **US$ 0,005/hora**, com apenas **uma hora por mês por conta** gratuita segundo a tabela atual (cerca de US$ 3,60 por 30 dias). IP estático não atribuído também pode gerar encargos. **É proibida** a configuração habitual de VM com IPv4 externo sob esta restrição.
- Sem IPv4 público, um NAT IPv4 Cloud NAT também tem custos de instância, IP e tráfego; **não o criar**.
- Google Cloud Budgets com *alerts only* não são teto de despesa. Os novos *spend caps* são atualmente elegíveis apenas para serviços específicos (Gemini API, Agent Platform, Cloud Run e Cloud Run functions); não protegem utilização contínua de **Compute Engine**, armazenamento ou tráfego de rede. Mesmo os spend caps não suspendem recursos persistentes que continuem a acumular custos.
- O **IPv6 externo atribuído à VM não tem custo de endereço**. Telegram/MTProto fornece endpoints IPv6 e Telethon suporta `use_ipv6=True`. Uma VM `e2-micro` com **IPv6 externo sem IPv4 público e sem NAT** é uma **hipótese de estudo**, ainda não certificada: confirmar suporte da rede, arranque, atualização de dependências e todas as ligações exclusivamente IPv6, e que o tráfego mensal permanece dentro da quota. Uma falha IPv6 não autoriza criar IPv4/NAT.
- Um alerta de faturação ou um script que desative o projeto **não é garantia de despesa zero**, devido a atrasos de medição e possíveis encargos já incorridos.

### Decisão vinculativa do projeto

**NO-GO para criar recursos Google Cloud enquanto não houver um mecanismo efetivo e verificado que impeça faturação**, incluindo IP, rede e armazenamento. A investigação offline, manifestos sem segredos e testes sintéticos no repo são autorizáveis; **nenhuma operação de criação/ativação** é abrangida.

Não presumir que a conta existente é de teste, que tem créditos, que a faturação está desligada, nem que já existem VMs elegíveis. O estado da conta deve ser confirmado de forma de leitura apenas pelo proprietário. Não introduzir scripts que abram serviços ou façam upgrade de faturação.

### Referências oficiais verificadas em 18/11/2026

- https://docs.cloud.google.com/free/docs/free-cloud-features
- https://cloud.google.com/vpc/network-pricing
- https://docs.cloud.google.com/billing/docs/how-to/budgets
- https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps
- https://docs.cloud.google.com/billing/docs/how-to/budget-api-spend-cap
- https://docs.cloud.google.com/compute/docs/instances/create-ipv6-instance
- https://docs.telethon.dev/en/stable/modules/client.html
- https://core.telegram.org/api/datacenter
