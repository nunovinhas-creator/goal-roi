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

## Perguntas pré-definidas (exploratórias)
Escritas a 11/11/2026, antes de haver dados; aparecem no relatório mas nunca contam como conclusão:
Q1 entrar só ao ×4; Q2 sinal sem reforço; Q3 faixas de odd; Q4 quota de mercado ≥ 80 %; Q5 antecedência do sinal pré-jogo; Q6 minuto do sinal Open game; Q7 todas as apostas.
Uma pergunta nova só entra aqui com data, e só vale para dados posteriores a essa data.

## Controlo de qualidade
O relatório conta: mensagens por ler (formatos novos), sinais que o bot alterou depois de publicar, resultados em que a marca ✅/❌ do bot difere da liquidação própria, e exclusões.

## Página
GitHub Pages a partir da pasta `docs/` da `main` (Settings → Pages). Mostra `docs/report.json`, atualizado a cada recolha.

## Segredos (Settings → Secrets → Actions)
`TG_API_ID`, `TG_API_HASH`, `TG_SESSION` (gerar com `scripts/authorize_qr.py` num computador, ou `scripts/authorize.py` por código; sessão própria), `TG_SCANGOAL` = `@scangoal_bot`.

## Limitações
- Sinais Betfair não trazem resultado; só são liquidados quando outra mensagem do mesmo jogo o traz.
- A odd anunciada pode não ser obtível, sobretudo ao vivo e em mercados pequenos.
- Camadas seguintes (perguntas pré-definidas; resumo por LLM a partir dos números) só depois de haver dados.
