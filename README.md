# scangoal-roi

Mede o ROI dos sinais do bot Telegram @scangoal_bot com regras fixas. Não faz apostas.

## Como funciona
1. `scangoal/reader.py` lê a conversa com o bot (só leitura) e guarda cada versão de cada mensagem em `data/messages.jsonl` (append-only: a original e as edições com o resultado).
2. `scangoal/parser.py` transforma cada mensagem em campos, por regras (sem LLM). Horas do bot = Moscovo (UTC+3).
3. `scangoal/report.py` calcula o ROI por grupo e escreve `data/report.json`.

## Regra de aposta (fixa)
- 1 unidade na seleção indicada, à odd do **primeiro** sinal de cada (jogo, tipo, mercado, seleção). Os reforços ×2/×4/×8/×16 ficam como atributo.
- ROI à odd anunciada e com 2 % e 5 % de perda de odd.
- Excluídos: odds < 1,10; mensagens vistas pela primeira vez já com resultado.
- Grupos: BETFAIR pré/ao vivo × mercado ≥/< 10 mil dólares; STAKE_LOAD pré/ao vivo; OPEN_GAME; TOTALS_FORM.
- Menos de 300 apostas liquidadas num grupo = sem conclusão.

## Taxa de acerto
Tabela à parte, com todos os sinais que têm resultado (inclui os lidos já com resultado, que ficam fora do ROI).
- Acerto = aposta ganha ou meia ganha pela liquidação própria; devoluções não contam.
- Mercados que o resultado final não decide (ex.: "2nd Team to Score") usam a marca ✅/❌ do bot.
- Sinais sem odd ("Open game" sem linha): acerto = pelo menos mais um golo depois do sinal (regra fixada a 12/11/2026).
- "Taxa necessária" = média de 1/odd, a taxa de acerto que dá lucro zero.

## Análise e interpretação (`scangoal/insights.py`, sem LLM)
- **Evidência por tipo:** estimativa prudente da taxa de acerto e probabilidade de estar acima da taxa necessária (prior centrado em "sem vantagem", com peso de 10 apostas).
- **Movimento da odd:** nos sinais Betfair com reforço, odd do último sinal contra a do primeiro. Antes do jogo é um indicador de concordância do mercado.
- **Calibração:** acerto observado contra o que a odd exige, por faixa de odd.
- **Segmentos:** competição (liga, taça, jovens, feminino), faixa de odd, dia, vários sinais no mesmo jogo, reforço, quota e tamanho do mercado, antecedência, parte do jogo, golos em falta, mercado. Lista fixada a 12/11/2026; só aparecem com 10 ou mais resultados e são sempre exploratórios.
- **Sorte e risco:** saldo contra a oscilação esperada a odds justas, maior queda e pior sequência.
- **Leitura automática:** frases geradas por regras a partir destes números.

## Perguntas pré-definidas (exploratórias)
Escritas a 11/11/2026, antes de haver dados; aparecem no relatório mas nunca contam como conclusão:
Q1 entrar só ao ×4; Q2 sinal sem reforço; Q3 faixas de odd; Q4 quota de mercado ≥ 80 %; Q5 antecedência do sinal pré-jogo; Q6 minuto do sinal Open game; Q7 todas as apostas.
Uma pergunta nova só entra aqui com data, e só vale para dados posteriores a essa data.

## Controlo de qualidade
O relatório conta: mensagens por ler (formatos novos), sinais que o bot alterou depois de publicar, resultados em que a marca ✅/❌ do bot difere da liquidação própria, e exclusões.

## Resultados pela Highlightly (fonte principal para o Betfair)
`scangoal/results_hl.py`: no máximo de 4 em 4 horas, até 8 pedidos por execução, e pára se restarem menos de 15 pedidos do limite diário (100).
Mesmas regras de identidade da BSD. Só conta jogos no estado "Finished"; prolongamento, penáltis e resultados atribuídos ficam em aberto.
Secret: `HIGHLIGHTLY_KEY`. Aliases revistos em `data/team_aliases.json` (`"nome no bot": ["nome no fornecedor"]`).

## Resultados pela BSD
Os sinais Betfair não recebem resultado do bot. `scangoal/results_bsd.py` vai buscá-los à API BSD, no máximo uma vez por hora e 12 pedidos por execução.
- Só liquida com nomes de casa e de fora iguais (depois de normalizar), hora compatível e um único candidato. Nunca por semelhança.
- Nomes diferentes entre o bot e a BSD ficam em `near_misses` (em `data/results_bsd.json`) e só passam a contar depois de acrescentados à mão a `data/team_aliases.json`.
- O resultado do bot tem prioridade; quando ambos existem, o relatório conta as divergências.
- Secret: `BSD_API_KEY`. Sem ele, este passo não faz nada.

## Robustez
- O intervalo do ROI agrupa as apostas por jogo: várias apostas no mesmo jogo não são independentes.
- A fase dos sinais Stake (antes ou durante o jogo) vem da hora da mensagem face à hora do jogo; o bot reescreve as linhas das equipas quando edita.
- `data/state.json` regista cada recolha, mesmo sem mensagens novas; a página usa-o para avisar se a recolha parar.
- Tipos que o bot costuma liquidar também são procurados nas fontes externas quando ficam sem resultado.
- `docs/bets.csv`: todas as apostas e sinais, um por linha.

## Regras de origem e de liquidação (12/11/2026)
- Ficam fora do ROI (continuam na taxa de acerto): sinais lidos já com resultado, sinais já editados quando foram lidos, e sinais Betfair chegados em lote (5 ou mais no mesmo segundo). Se o primeiro sinal de uma aposta fica fora, a aposta inteira fica fora.
- Handicap asiático durante o jogo: só a marca ✅/❌ do bot decide (ao vivo o handicap conta a partir do resultado no momento da aposta).
- Meia vitória conta meio acerto; meia derrota, meia falha.
- Sinais sem odd: a taxa de acerto compara-se com a taxa habitual de "mais um golo" num jogo médio, a partir do minuto do sinal.
- Alinhamento enfraquecido: aposta contra a equipa enfraquecida, ao handicap e odd da mensagem; só quando há exatamente uma.
- Jogos decididos depois dos 90 minutos ficam em aberto e são contados. Sem resultado ao fim de 3 dias: "sem resultado disponível".
- A lista de golos do bot só é usada quando bate certo com o marcador do sinal e com o resultado final.
- Sinais Stake: a odd indicada pode já não existir quando o aviso chega; o controlo de qualidade conta os avisos tardios.

## Minutos dos golos
`scangoal/details_hl.py` pede à Highlightly os golos com minuto de cada jogo já identificado (5 jogos de 2 em 2 horas, dentro da quota gratuita). Um jogo só é usado se os golos somados derem o resultado final.
- "Goal brewing": acerta se o golo seguinte ao sinal cair dentro da janela indicada; compara-se com a probabilidade habitual que o próprio bot indica.
- Jogos decididos depois dos 90 minutos: vale o marcador aos 90, reconstruído pelos minutos.
- Tempo até ao golo seguinte: fonte externa primeiro; a lista do bot só quando bate certo.
- As odds da Highlightly não estão disponíveis no plano gratuito.

## Cérebro (hipóteses)
`scangoal/brain.py` combina as características de cada sinal em hipóteses de 1 ou 2 condições e regista-as em `data/hypotheses.json` (o registo só cresce).
- O gerador só conta quantos sinais têm cada combinação; nunca lê resultados. Por isso estas hipóteses são avaliadas em todos os dados.
- Para acrescentar uma hipótese à mão (ou proposta por um modelo de linguagem), junta uma entrada com `"source": "manual"` ou `"llm"`: só contam os sinais posteriores a `born_utc`.
- Estados (contados em jogos independentes, PR 2): em observação (<30 jogos), promissora (>=30 e 95 %, pode ser acaso), forte (>=100 e certificada, PR 3), confirmada (>=300 e certificada), abandonada (>=30 e <=10 %).
- Validade cronológica (PR 3, regras `chronological_eprocess_v3`; ver `audits/pr3-chronological-validity.md`): um resultado só conta a partir da primeira vez que o sistema provadamente o observou (recolha da mensagem com o resultado, `matched_at_utc`, `fetched_at_utc`; +5 min de margem), nunca por aproximação; sem hora provada só conta "agora". Uma hipótese não decide antes do seu registo (`born_utc`). Evidência prospetiva = jogos cujas apostas foram todas emitidas e lidas depois do registo, decidíveis e com resultado de hora provada (uma só definição, usada pelo relatório e pela máquina de estados).
- Validadas (PR 3, revisões #5911487915 e #5911603577): "forte"/"confirmada" exigem um certificado e a validação está DESLIGADA por defeito. Só depois de uma ativação explícita (`python -m scangoal.certification --activate`, a autorizar no corte) existe o registo append-only `data/certification_ledger.json`: cada tática promissora certificável (condições conhecidas no primeiro sinal, `brain.AS_OF_DIMENSIONS`) é registada uma vez, em tempo real, com rank j e α_j = 0,05/(j(j+1)); as promissoras anteriores à ativação ficam legadas e nunca são certificáveis. Um e-processo de apostas sobre o lucro à odd −2 % tem de ultrapassar 1/α_j em 30 jogos, usando só a primeira aposta de cada jogo (decidida depois do registo e antes de qualquer resultado do jogo) e uma cadeia sem sobreposição: um jogo só entra se o anterior da cadeia já tiver resultado na hora da decisão; sem resultado em 3 dias conta como perda (revisão #5911747123). A cadeia de hashes do registo só prova consistência interna; cada relatório exige a prova de integridade histórica (`certification.history_proof`: histórico git completo, pelo menos uma versão publicada, crescimento append-only até à cópia atual e publicação de cada entrada até 30 min depois do seu carimbo). Sem prova, não há certificação, alertas validados nem novas entradas. Um jogo da cadeia sem resultado em 3 dias fecha como perda pelo relógio do relatório (revisão #5911845265). Com o registo ativo e verificado, P(alguma tática sem vantagem ser certificada) <= 5 % sob os pressupostos do relatório. O Telegram só anuncia validadas com esse certificado.
- Avaliação num instante passado (`as_of`, revisões #5912303516 e #5912461690): só existem os sinais com leitura provada até esse instante (sem carimbo de leitura válido e coerente, o sinal não existe), e os resultados observados depois ficam em aberto.
- Corte da PR 3 (`audits/pr3-cutover-runbook.md`, a executar só com autorização): integra o código com a certificação desligada e migra em silêncio a referência Telegram. A ativação da certificação é um passo separado (`audits/pr3-certification-activation-plan.md`) que exige checkout completo (`fetch-depth: 0`) e outra autorização.
- Estabilidade (PR 2, regras `independent_games_hysteresis_v1`): o estado é reconstituído jogo a jogo pela ordem em que os resultados ficaram conhecidos. Uma promissora só volta a observação abaixo de 90 %; uma abandonada só sai acima de 20 %; uma validada mantém-se até a fasquia com o dobro do erro admitido. Cada mudança tem de se confirmar em 2 jogos seguidos. O relatório mostra `pending_status` (mudança à espera de confirmação), `entry_status` (o que os limiares de entrada dariam sem memória) e o histórico de mudanças.
- Hipóteses com condições que só se conhecem depois do primeiro sinal (reforço, movimento da odd) são informativas e não entram na lista "a seguir agora".
- Um voto por jogo (12/11/2026; unidades revistas a 15/11/2026): sinais repetidos na mesma aposta (jogo, mercado, seleção) contam uma vez; apostas diferentes no mesmo jogo repartem um voto. Dois acertos no mesmo jogo contam um; um acerto e uma falha contam meio de cada. Desde a PR 2 também os limiares (estados, fasquia corrigida, quadro, agenda, walk-forward, balanço) contam jogos, não sinais. O acerto e o ROI mostrados continuam a contar cada sinal; o relatório mostra sinais (`n`), apostas (`bets`) e jogos (`games`).
- Vantagem com intervalo: além da probabilidade de estar acima do necessário, cada hipótese mostra a vantagem estimada em pontos percentuais (acerto menos taxa necessária) com um intervalo de 90 %, e a probabilidade de continuar acima do necessário com 2 % de perda de odd.
- "Rende mais do que o resto do tipo": probabilidade de a hipótese ter mais vantagem do que os restantes sinais do mesmo tipo. Serve para distinguir uma condição que acrescenta algo de uma que só herda o desempenho do tipo. Só informa; não muda o estado.
- "Seguir o cérebro teria compensado?": cada sinal com odd é decidido quando o sistema o leu, só com os resultados que já tinha observado e só com as táticas já registadas (PR 3; antes, 150 minutos depois do início do jogo). Segue-se quando encaixa numa tática apostável com 8 jogos e 80 % de probabilidade nessa altura. Sinais lidos tarde demais para apostar são "não reconstituíveis". Compara seguidos com não seguidos.
- Balanço das propostas (PR 4): só descritivo, por origem, com evidência prospetiva (jogos independentes depois do registo) e lucro por jogo. Nenhuma hipótese é dada como confirmada ou falhada por acertar mais ou menos de metade; o modelo de linguagem recebe só estes números descritivos.

## Avaliação científica da LLM (PR 4)
`scangoal/llm_eval.py` aplica o protocolo pré-registado `audits/pr4-llm-protocol-v5.md` (`llm_incremental_v5`; o código recusa outra versão do ficheiro). O v1 (`audits/pr4-llm-protocol.md`) e os v2 a v4 (`-v2.md` a `-v4.md`) ficam como registo histórico, substituídos depois da revisão e das auditorias independentes da PR #27.
- Pergunta: as hipóteses do modelo escolhem jogos com mais lucro por jogo (odd −2 %), depois do registo, do que hipóteses comparáveis do gerador (20 controlos pré-selecionados sem ler resultados), no mesmo intervalo?
- Nascimento efetivo provado pelo histórico git completo (registo só a crescer); sem prova, a avaliação é "não verificada" e não tem conclusão.
- Só jogos prospetivos (definição da PR 3) posteriores à primeira publicação verificável do protocolo no GitHub (hora do servidor, não do commit) sustentam conclusões; os anteriores são descritivos. Sem prova de publicação, a avaliação é "não verificada".
- Todas as decisões históricas usam só o que o sistema já tinha lido nesse instante: o apoio dos controlos conta sinais lidos até ao nascimento, e os grupos de equivalência são decididos no nascimento de cada hipótese e nunca revistos.
- Grupos de hipóteses equivalentes (Jaccard 0,85 por ligação completa, sensibilidade 0,80 e 0,90) contam uma vez.
- Critério confirmatório único (protocolos v4/v5): um teste por apostas (e-processo) sobre os dias fechados, comparando LLM e controlos dentro de cada dia. A probabilidade de rejeitar falsamente a hipótese nula condicional definida no protocolo (§6-A) é ≤ 5 % por um teorema (desigualdade de Ville), com qualquer dependência entre jogos, mistura de cotações, assimetria e seleção dependente do passado, e mesmo olhando repetidamente. O bootstrap-t é só diagnóstico. Mínimos de grupos, jogos, dias e dias emparelhados.
- Regra do dia seguinte (v5): uma hipótese, e os seus controlos, só contribui a partir do dia UTC seguinte ao seu nascimento. Assim, tudo o que o sistema escolhe para um dia existe antes de o dia começar.
- Alcance: o relatório separa a rejeição da hipótese nula condicional (garantida pelo teorema) da afirmação "as apostas da LLM rendem mais", que exige ainda que o bot não escolha que jogos sinalizar em função de resultados de outros jogos do mesmo dia (A-sel; plausível, não verificável). Nunca é prova de vantagem nem certificação.
- Janela de 365 dias depois da publicação do protocolo. Um dia só entra quando está fechado (3 dias depois do fim); a conclusão, se acontecer, fica com a sua data e nunca é reescrita. Jogos sem resultado comprovado em 3 dias contam contra a hipótese em teste (perda total no lado LLM, ganho máximo no lado dos controlos).
- Revisões retroativas: a avaliação exige que as mensagens e os resultados só tenham crescido no git e que cada observação tenha sido publicada até 30 minutos depois do seu carimbo; a avaliação final usa sempre o primeiro commit de `main` com hora ≥ C.
- O resultado científico tem um `result_sha256` próprio; os metadados de cada execução (commit pedido, versão do Python) ficam à parte, em `run`.
- Calibração e poder: `audits/pr4-evidence/calibration.json` (intervalos exatos de Clopper-Pearson). Com resultados em falta, o poder é baixo (ver `audits/pr4-evidence/missing_results_rate.txt`). Melhorar a cobertura de resultados (por exemplo, aliases de equipas no Betfair) é uma alteração de dados separada, que não muda a metodologia: só ajuda jogos futuros cujo resultado passe a ser observado até 3 dias depois da decisão, e nunca altera dias já fechados.
- Nunca altera estados, certificação, Telegram ou o registo.
- Relatório reprodutível: `python3.11 scripts/audit_pr4_llm.py --ref <commit> --protocol-commit <sha> --protocol-published-utc <hora do servidor GitHub> --protocol-publication-evidence <ligação>` (só leitura, sem rede; exige clone completo). Resultado em `audits/pr4-llm-evaluation.md` e `.json`.
- A página mostra só um resumo sem conclusão (o workflow usa um checkout raso).

## Investigador (modelo de linguagem)
`scangoal/llm.py` chama, de hora a hora, um modelo no Cloudflare Workers AI (segredos `CLOUDFLARE_ACCOUNT_ID` e `MASTER_BRAIN_LLM_API_KEY`). Sem os segredos, o passo é ignorado.
- Quatro modos que rodam: hipóteses novas, refinar as melhores, situações a evitar, e notas sobre uma hipótese concreta.
- Orçamento: o plano gratuito dá 10 000 neurónios por dia; o código estima o custo de cada chamada e pára aos 8 000.
- Limites: 5 propostas por chamada; não há limite diário nem total de hipóteses (só o orçamento gratuito de neurónios limita). Cada proposta precisa de pelo menos 5 sinais já existentes com essas características.
- Duplicados (PR 4): são rejeitadas as propostas iguais a uma hipótese existente e as redundantes no instante da proposta (apanham exatamente os mesmos sinais que uma hipótese existente ou que uma sub-combinação das suas condições). Nunca se rejeita por semelhança. `llm_state.json` conta estas rejeições em `rejected_equivalent`.
- Proveniência (PR 4): cada proposta aceite guarda o `sha256` do pedido e da resposta, a hora do relatório usado e o tamanho do registo nesse momento.
- O modelo só recebe números agregados; nunca mensagens. O texto que devolve é limpo e nunca é executado.
- As propostas entram com origem `llm`: só contam os sinais posteriores ao registo. As notas só entram se referirem uma hipótese existente; os números mostrados vêm dos dados.

## Avisos pelo Telegram
`scangoal/notify.py` envia avisos por um bot teu (segredos `TG_BOT_TOKEN` e `TG_CHAT_ID`; sem eles, o passo é ignorado). Não usa a conta que lê o ScanGoal.
- Avisos de jogos com sinais em aberto que encaixam numa hipótese promissora ou melhor (até 5 jogos por recolha), **só enquanto ainda dá para apostar** (`scangoal/freshness.py`, desde 17/11/2026, para indícios e alertas): pela hora ORIGINAL do sinal, ao vivo até 30 minutos depois; antes do jogo só até ao início conhecido; nunca com o jogo comprovadamente terminado; hora ausente, inválida ou incoerente: não é aviso. A regra aplica-se ao construir `brain.watch` (hora do relatório), ao planear o envio e outra vez com o relógio real imediatamente antes de contactar o Telegram, já com a intenção publicada no git (um sinal que expire durante os envios anteriores ou durante a publicação fica `expired`, nunca é enviado nem contado como entrega); uma promoção de tática nunca reativa sinais antigos, que ficam intactos como dados históricos. Contrato: cada **aposta** (jogo, mercado, seleção) é avisada no máximo uma vez; as apostas novas de um jogo numa recolha saem numa só mensagem. Reforços (×2, ×4…, outra hora de sinal) e a passagem de indício a alerta da mesma aposta nunca geram outro aviso — a certificação da hipótese é anunciada à parte, no resumo diário ("✅ Tática validada"). Uma seleção genuinamente nova num jogo já avisado sai como **atualização** ("A seguir (nova seleção num jogo já avisado)"), só com a aposta nova.
- Tipos de sinal que mudam de comportamento.
- **Um único resumo diário às 22:00 de Lisboa** (`Europe/Lisbon`, hora de verão e de inverno): é o ÚNICO sítio onde saem as mudanças de estado das hipóteses (em observação → promissora, promissora → abandonada, ganho ou perda de certificação…), todas as do período, em blocos curtos, seguidas do resumo do dia (com entregas falhadas, incertas e bloqueadas e relatório degradado, quando os houver). Sem mudanças, o resumo diz "Sem alterações de estado". Uma mudança que surja depois do resumo entra no do dia seguinte; se não couberem todas numa mensagem, as restantes ficam para o seguinte (nunca se perdem nem se repetem).
- Aviso quando uma recolha falha (no máximo um por hora). Se a cadeia de recolha parar por completo, não há quem avise: a página mostra "Recolha parada".

### O que cada mensagem mostra
Aviso de aposta (formato curto, para apostar depressa) — uma aposta simples tem exatamente três linhas:
```
A seguir: UTC Cajamarca — CD Los Chankas
Encaixa em: Jogo aberto | Ritmo indicado pelo bot = menos de 90 (promissora)
Aposta: Over 5 @ 1,91
```
Sem ROI, probabilidades, jogos independentes, evidência ou textos finais. Com várias táticas ou seleções no mesmo jogo, cada aposta aparece uma só vez, por baixo das táticas que a sustentam. Um ALERTA (só com certificado ativo, válido e da própria tática) começa por "🟢 ALERTA", identifica a tática com "🟢 Tática validada (…)" e termina com o rótulo científico exato dessa tática, que a barreira `guard_claims` exige; um indício nunca faz afirmações de certificação.

Resumo diário (22:00 de Lisboa), um bloco por mudança de estado:
```
Resumo das táticas — 16/11

Stake | Faixa de odd 1,50 a 2,00
Estado: Abandonada (antes: em observação)
32 jogos independentes · ROI −25,2%
Certificação: desligada
```
Hipóteses equivalentes (partilham ≥ 85 % dos jogos, compatíveis, ligação completa: nunca cadeias A~B~C) aparecem como representante "e mais N equivalentes". O agrupamento é só de apresentação: não muda estados nem avaliação.

### Entrega no máximo uma vez
- Cada aviso tem uma chave do acontecimento (jogo e seleções, transição de estado, dia do resumo). Antes de enviar, grava-se a intenção em `data/notify_state.json` (atómico) e em `data/notify_log.jsonl` (registo só a crescer: chave, tipo, hora, `sha256` do texto; nunca o token nem o chat). Depois da resposta grava-se o resultado.
- Estados: pendente, entregue (resposta positiva do Telegram), falha confirmada (o Telegram recusou, ou a ligação nunca se fez), incerta (não se sabe se saiu) e bloqueada (barreira de afirmações). Nenhum acontecimento é tentado duas vezes; uma entrega incerta nunca é reenviada nem contada em `sent_total`. Depois de uma falha ou incerteza, os avisos seguintes dessa recolha ficam pendentes e só saem na seguinte se ainda forem válidos.
- Uma queda do processo em qualquer ponto é reparada na execução seguinte: a intenção sem resultado passa a incerta; o que o estado sabe e o registo não (ou o contrário) é reconstituído, sem reenvio e sem perder contadores.
- Entre runners do GitHub Actions, a garantia vem da barreira de publicação (`NOTIFY_DURABLE=git`, ligada no workflow nos passos dos avisos e das leituras rápidas): antes de contactar o Telegram, a intenção é commitada e enviada para o GitHub (`notify: intent … [skip ci]`); se o push falhar, nada é enviado e o aviso fica pendente. Um runner novo, com checkout limpo, nunca reenvia o que outro já pode ter enviado (`audits/pr5-p0-durability-design.md`). O aviso de recolha falhada (`--failure`) fica fora da barreira e **fora da garantia "no máximo uma vez"** (no máximo um por hora, sem registo, como antes).
- `data/notify_state.json` ausente nunca é tratado como instalação nova: o notificador recusa e não envia nada (`NOTIFY_STATE_MISSING`). Um estado vazio (`{}`) ou sem histórico (`sent_total`, `watch_sent`, `statuses` e a marca da PR 1) também é recusado, mesmo sem registo de entregas; ao lado de um registo, o estado tem ainda de ter a marca PR 5 e não pode ter menos entregas do que na migração. Só `python -m scangoal.notify --init-state` autoriza uma instalação nova, com a marca explícita `installation` (recusa se já houver estado ou registo).

### Mudanças de estado sem falsos avisos
- Só se anuncia uma mudança com relatório saudável (mesmas regras, âmbito e verificação da certificação; universo elegível, hipóteses e resultados sem quedas anómalas; fontes de resultados sem perdas), com a hipótese presente no relatório, sem estado pendente e com a mudança fixada pelo cérebro. Uma hipótese ausente é "desconhecida", nunca "voltou a em observação".
- Uma degradação retém as mudanças; se durar 24 h, passa a ser a nova referência (atrasa, nunca descarta).
- O resumo diário compara o último estado anunciado com o atual: uma reversão no mesmo dia (A → B → A) cancela-se sozinha e não é anunciada. A perda de certificação é anunciada mesmo com o relatório degradado. A elegibilidade das apostas (indício ou ALERTA) segue o certificado de cada relatório de imediato, sem esperar pelo resumo.

### Alertas de táticas validadas
- Uma tática só é anunciada como validada com certificado ATIVO, VÁLIDO e da PRÓPRIA hipótese (método `eprocess_online_alpha_v1`, registo ativo e verificado, o id na lista de certificadas e a confirmação com seleção e instante na linha da hipótese). Com a certificação desligada (estado atual), nenhuma mensagem diz "validada", "certificada", "confirmada" ou "forte".
- Cada jogo que encaixe numa tática validada gera um ALERTA curto com o jogo, a tática, as seleções e a odd (e o rótulo do certificado), enquanto dá para apostar (a mesma regra dos indícios: pré-jogo até ao início; ao vivo até 30 minutos depois do sinal).
- Barreira final (`guard_claims`): qualquer texto com essas palavras (qualquer flexão, acentos ou maiúsculas) sem o rótulo verificado da hipótese citada na mesma linha é bloqueado e contado. "Não certificada" e "sem certificado" continuam permitidos. Textos vindos de dados (razões e notas do modelo de linguagem) nunca entram nas mensagens.

### Pré-visualização sem envio
`python -m scangoal.notify --preview [--state ficheiro] [--report ficheiro] [--now ISO] [--out ficheiro]` mostra os avisos que a próxima execução produziria. Não lê credenciais, recusa qualquer ligação de rede e nunca grava estado nem registo.

### Aviso Polymarket para Over 2,5 pré-jogo

O Telegram inclui um indício quando uma mensagem original Polymarket indica **Over 2,5 golos no jogo inteiro**, quota de mercado **abaixo de 80%** e valor sinalizado **abaixo de $10.000**. A odd anunciada segue o mínimo habitual de 1,10. O aviso mostra a quota, o valor, a odd e a hora de início em Lisboa.

A primeira execução do notificador com este filtro guarda a hora de ativação. A partir daí são considerados os sinais emitidos e lidos depois dessa hora, até ao início do jogo. A recolha completa e as leituras rápidas usam o mesmo registo de entrega, por jogo, mercado e seleção.

## Simulador de banca
Na página: aplica uma regra (valor fixo ou percentagem da banca) às apostas liquidadas do saldo estrito, por ordem, com escolha de tipos de sinal e de corte na odd. Corre no navegador; não altera dados.

## Liquidação
- A Highlightly é consultada de 90 em 90 minutos, a partir de 1h50 depois do início do jogo (ou 1h45 depois de um sinal ao vivo). Só o estado "terminado" liquida.
- Um jogo ausente da lista completa do dia, 8 horas depois e em duas leituras, fica marcado como não coberto: deixa de gastar pedidos e a página mostra-o em "Jogos por liquidar".
- `data/manual_results.json` aceita resultados revistos à mão: `{"<id do jogo>": {"ft": [casa, fora], "source": "onde foi visto"}}`. Só entram quando nenhuma outra fonte tem o jogo; divergências são contadas.

## Recolha
O agendamento do GitHub não dispara neste repositório. Cada execução faz a recolha completa, depois passa 8 minutos em leitura rápida e lança a seguinte. Para parar, cancela a execução em curso; para retomar, usa "Run workflow". A opção "force_results" consulta já as fontes de resultados (gasta quota).

### Leitura rápida (`scangoal/watch.py`)
- Nos 8 minutos entre recolhas, relê as últimas 40 mensagens do bot de 2 em 2 minutos, com uma só ligação ao Telegram mantida aberta.
- Quando há mensagens novas, refaz o relatório e envia os avisos; assim um alerta de um sinal ao vivo chega em cerca de 2 a 3 minutos em vez de 10.
- Só leitura. Perante um erro ou um pedido do Telegram para esperar, pára de ler e aguarda o resto do tempo; duas leituras falhadas seguidas param o ciclo. Nunca falha a execução.
- Ajustes por variável de ambiente: `WATCH_SECONDS` (480), `WATCH_INTERVAL` (120), `WATCH_RECENT` (40).

## Página
GitHub Pages a partir da pasta `docs/` da `main` (Settings → Pages). Mostra `docs/report.json`, atualizado a cada recolha.

## Segredos (Settings → Secrets → Actions)
`TG_API_ID`, `TG_API_HASH`, `TG_SESSION` (gerar com `scripts/authorize_qr.py` num computador, ou `scripts/authorize.py` por código; sessão própria), `TG_SCANGOAL` = `@scangoal_bot`.

## Limitações
- Sinais Betfair não trazem resultado; dependem de outra mensagem do mesmo jogo ou da BSD com nomes exatamente iguais.
- A odd anunciada pode não ser obtível, sobretudo ao vivo e em mercados pequenos.
- Camadas seguintes (perguntas pré-definidas; resumo por LLM a partir dos números) só depois de haver dados.

## Elegibilidade da evidência do cérebro (PR 1, 14/11/2026)

O cérebro usa apenas sinais com `in_roi == true`, sem marcas de origem inválida,
com mercado e odd finita >= 1,10. O mesmo filtro aplica-se ao apoio de novas
hipóteses, às classificações, comparações com o resto do tipo, agenda, watch,
pontos de entrada e ao treino e avaliação do walk-forward. A LLM recebe esse
universo filtrado através de `report.LAST['signals']`.

Identidade (PR 2, revisão #5910343843): um sinal só é elegível se o jogo
(`match_id` com texto) e a seleção forem identificáveis (motivos
`no_match_identity` e `no_selection`). Mesmo nas estatísticas exploratórias, um
sinal sem jogo comprovável não vota nem conta como jogo independente (`unidentified`):
nunca se inventa uma identidade por sinal. A 15/11/2026 nenhum sinal elegível estava
nestas condições.

`accuracy` e `insights` continuam exploratórios. `brain.exploratory` conserva as
contagens de estados no universo completo e uma amostra das 150 hipóteses com
mais resultados; não alimenta recomendações. `brain.eligibility` apresenta os
motivos de exclusão (podem sobrepor-se). `in_roi` mantém o significado legado
para compatibilidade; `evidence_eligible` inclui também os critérios de mercado
 e odd. Os dados e o registo de hipóteses existentes não são apagados.

Esta separação não resolve ainda independência dos jogos, estabilidade,
multiplicidade, ativação cronológica ou validação prospectiva. Os nomes antigos
"forte"/"confirmada" não constituem certificação científica.

Ver [auditoria da PR 1](audits/pr1-data-eligibility.md). Testes sem rede:
`python scripts/test_offline.py`. Comparação reproduzível com a base fixada:
`python scripts/audit_data_eligibility.py`.

Após a revisão P1, `operational.accuracy` alimenta o veredito principal e
`operational.regime` alimenta os avisos de comportamento. Ambos exigem
`scope=eligible_bets_only`; relatórios antigos não usam estatísticas exploratórias
como alternativa. A narrativa e as tabelas legadas do dashboard estão identificadas
como exploratórias. A primeira leitura do regime elegível estabelece uma referência
silenciosa em `operational_regime`, preservando o campo histórico `regime`.
Os testes offline incluem agora a execução de `answer()` em Node (necessário no
ambiente de testes), sem chamadas externas.

### Migração silenciosa dos estados Telegram

A primeira referência elegível é registada uma só vez em
`migrations.eligible_statuses_v1`, preservando os estados antigos e o histórico de
entrega. Transições futuras continuam a gerar avisos. O comando offline
`python -m scangoal.notify --migrate-statuses` prepara exclusivamente essa referência,
sem credenciais nem envios, e pode ser repetido sem efeitos adicionais.
A PR 5 acrescenta a marca `telegram_format_v2` (`python -m scangoal.notify --migrate-format`, offline e idempotente): semeia
a referência anunciada com a referência atual e marca como avisados os sinais já avisados no formato antigo, sem enviar
nada e sem mexer em `sent_total`, `watch_sent`, nas três marcas anteriores nem em nenhum outro campo.
Consultar o [procedimento de integração e reversão](audits/pr1-safe-integration.md)
antes de qualquer integração autorizada. Não restaurar um estado de notificações
antigo sobre um estado que já recebeu novas entregas.
