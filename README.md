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
- Estados: em observação (<30), promissora (>=30 e 95 %, pode ser acaso), forte (>=100, fasquia corrigida pelo número de hipóteses testadas), confirmada (>=300), abandonada (>=30 e <=10 %).
- Hipóteses com condições que só se conhecem depois do primeiro sinal (reforço, movimento da odd) são informativas e não entram na lista "a seguir agora".

## Hipóteses do modelo de linguagem
`scangoal/llm.py` pede, uma vez por dia, até 5 hipóteses novas a um modelo no Cloudflare Workers AI (segredos `CLOUDFLARE_ACCOUNT_ID` e `MASTER_BRAIN_LLM_API_KEY`, os mesmos do asianodds-h1-lab). Sem os segredos, o passo é ignorado.
- O modelo só recebe números agregados; nunca mensagens.
- Uma proposta só entra se usar famílias, características e valores que existem. O texto do motivo é limpo e nunca é executado.
- Entram com origem `llm`: só contam os sinais posteriores ao registo.

## Recolha
O agendamento do GitHub não dispara neste repositório. Cada execução espera 8 minutos e lança a seguinte. Para parar, cancela a execução em curso; para retomar, usa "Run workflow". A opção "force_results" consulta já as fontes de resultados (gasta quota).

## Página
GitHub Pages a partir da pasta `docs/` da `main` (Settings → Pages). Mostra `docs/report.json`, atualizado a cada recolha.

## Segredos (Settings → Secrets → Actions)
`TG_API_ID`, `TG_API_HASH`, `TG_SESSION` (gerar com `scripts/authorize_qr.py` num computador, ou `scripts/authorize.py` por código; sessão própria), `TG_SCANGOAL` = `@scangoal_bot`.

## Limitações
- Sinais Betfair não trazem resultado; dependem de outra mensagem do mesmo jogo ou da BSD com nomes exatamente iguais.
- A odd anunciada pode não ser obtível, sobretudo ao vivo e em mercados pequenos.
- Camadas seguintes (perguntas pré-definidas; resumo por LLM a partir dos números) só depois de haver dados.
