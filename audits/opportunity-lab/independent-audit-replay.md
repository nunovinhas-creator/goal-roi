# Replay dos quatro bloqueadores das PR #62–#65

O replay de interpretação usa `b3ab1b92c6689e622aacfcc8544003b906ec774b:data/messages.jsonl`. Compara o parser de `b3ab1b92`, o parser do rascunho anterior `2061a55b` e o parser corrigido. A unidade é uma versão de mensagem. A primeira versão é a primeira linha do respetivo ID no ficheiro. Os identificadores abaixo são os primeiros 12 caracteres de SHA-256 sobre o texto UTF-8. Nenhuma mensagem ou nome de equipa é reproduzido.

## As doze diferenças do replay anterior

Foram lidas 1.008 versões Polymarket, incluindo 510 primeiras versões. As doze diferenças anteriores correspondem a seis primeiras versões e seis edições. Âmbito e elegibilidade são avaliados separadamente.

| SHA-256 (prefixo) | Versão | Justificação individual |
|---|---|---|
| `67f6cfca8df6` | primeira | A cotação explicita `Corners Over 8.5`. O mercado é cantos, fora da regra #55. |
| `17c077384f4d` | primeira | `1H Under 0.5` durante o jogo é total de golos da primeira parte e LIVE. |
| `7aa8d10bfea8` | edição | Conserva `1H Under 0.5` e o marcador LIVE. A edição não dá origem a outro sinal. |
| `1aaf7b341070` | edição | Conserva `Corners Over 8.5`. A edição não altera o mercado da cotação original. |
| `34fe5fb8b414` | primeira | `1H Over 1.5` pré-jogo resolve a primeira parte, fora da regra de jogo inteiro. |
| `b890e018c3cd` | edição | Conserva `1H Over 1.5` e o período da primeira versão. |
| `ac5b9182f9c4` | primeira | Outra cotação pré-jogo `1H Over 1.5`, explicitamente da primeira parte. |
| `f56266a12589` | primeira | Cabeçalho `×4 · LIVE` sem minuto nem marcador. As versões corrigidas mantêm a mensagem inelegível. |
| `5cd50e2543fe` | primeira | Cabeçalho `LIVE` sem minuto nem marcador. A ausência de data de início também impede tratar como PRE. |
| `cf76f3c25483` | edição | Conserva a cotação da primeira parte de `ac5b9182f9c4` e continua a edição dessa mensagem. |
| `a0fd23378dbe` | edição | Edição do cabeçalho incompleto `×4 · LIVE`. O estado da cotação continua sem prova. |
| `99be97280866` | edição | Edição do cabeçalho incompleto `LIVE`. Continua inelegível. |

Face ao rascunho anterior, o novo parser produziu 29 diferenças adicionais nos campos comparados. Em 25 versões, incluindo 13 primeiras, uma seleção anteriormente agregada no mercado genérico deixou de ser uma aposta porque o âmbito não estava demonstrado. As outras quatro são as versões `LIVE` incompletas já listadas, que continuam inelegíveis e são apresentadas como mensagens Polymarket de diagnóstico. As oito versões documentadas de cantos ou primeira parte mantêm a classificação explícita. Nenhuma das doze passa o estudo ou o aviso #55.

## Regressões para revisão independente

- `tests/test_opportunity_forward.py`: grava um FT 2–1 às 20:00, regista uma correção às 22:00 e compara resultados no corte das 21:00. Confirma também que o snapshot legado do fornecedor permanece intacto e que um resultado atual isolado não preenche retroativamente o arquivo.
- `tests/test_polymarket_alerts.py`: cobre a mensagem completa até ao parser, ao aviso em memória e à admissão prospetiva. Inclui Over 2,5 legítimo e âmbitos de equipa, cartões, prolongamento e desconhecidos.
- `tests/test_match_identity.py`: cruza stake, ROI, ranking, benchmark e admissão LLM com aliases exatos, colisões, informação tardia e identidades parciais. A prova de alias usa hora com fuso, equipas ordenadas e competição exatas após normalização de apresentação.
- `tests/test_llm_budget.py`: usa dois runners independentes e um remoto Git bare local. Interrompe a execução imediatamente depois de publicar a reserva, perde uma confirmação de escrita e disputa a última capacidade com escritas concorrentes. O teste do ponto de entrada também comprova que uma configuração sem prova remota não chama Workers AI.

A disponibilidade do novo arquivo de FT é o instante observado da recolha, e a auditoria Git verifica o acréscimo das versões e a coerência de publicação. As revisões são arquivadas quando o fornecedor as devolve numa consulta já programada. Ficheiros legados com apenas o valor corrente continuam acessíveis aos relatórios operacionais. O percurso científico usa versões comprovadas. O replay da auditoria ou do ranking aceita `--as-of` com fuso explícito.

A reserva remota é um registo separado do checkout da recolha. Um ACK perdido conserva o débito que possa ter sido publicado. A capacidade reservada nunca é libertada nesse dia. Sem referência anterior, o primeiro dia UTC é fechado por precaução. O passo só admite chamadas em `main` quando o operador confirmou o plano Free por `GOAL_ROI_LLM_FREE_CONFIRMED=true`. A variável e a referência de quota não foram configuradas por esta correção. O limite Git é local ao repositório e o limite global depende do plano Free da conta. A publicação no GitHub e as permissões em produção exigem verificação humana após aprovação, com as estratégias e a certificação inativas.

## Comparação operacional

Replay de 1.711 primeiras mensagens com o relógio do relatório de `b3ab1b92`. A coluna antes usa o código de `main` do mesmo commit. A coluna depois usa a pilha corrigida.

| Medida | Antes | Depois |
|---|---:|---:|
| Stake PRE, apostas / liquidadas / vitórias | 155 / 99 / 50 | 155 / 99 / 50 |
| Stake LIVE, apostas / liquidadas / vitórias | 98 / 58 / 36 | 98 / 58 / 36 |
| Polymarket PRE, apostas / liquidadas / vitórias | 195 / 175 / 84 | 188 / 175 / 84 |
| Polymarket LIVE, apostas / liquidadas / vitórias | 46 / 45 / 23 | 45 / 44 / 22 |
| Total do relatório, apostas únicas / liquidadas | 932 / 752 | 924 / 751 |
| Candidatos Stake, candidatos #55, avisos planeados | 0 / 0 / 0 | 0 / 0 / 0 |

O rascunho anterior tinha os mesmos totais agregados da coluna antes. A redução no histórico Polymarket resulta da regra positiva de interpretação. O teste adversarial em memória comprova também a passagem de um aviso legítimo. A ativação original de #55 é `2026-11-19T15:19:15.977169+00:00`. O replay prospetivo devolve zero jogos admitidos, ROI indisponível e certificação inativa.

## Reauditoria de 20/11/2026

Os scripts `provas.py`, `quota2.py` e `replay2.py` do ZIP independente foram executados offline. O ZIP continha também `LEIA-ME.txt`. Os casos foram convertidos em regressões que funcionam com o checkout normal, incluindo o replay das 37 versões distintas de mensagens com equipas e competições substituídas.

| PR | Falha reproduzida no commit auditado | Resultado com a correção | Teste permanente e risco residual |
|---|---|---|---|
| #62 | O FT disponível às 03h00 UTC de 20/11 passou a constar como conhecido às 04h00 UTC de 24/11 após uma confirmação concordante. | A primeira disponibilidade continuou às 03h00 e a vitória foi preservada após reinício. Uma divergência antes do prazo causou quarentena. A divergência posterior ficou na reconciliação. | `test_result_settlement_reaudit.py`. A liquidação exige que o arquivo conserve as observações com horas provadas. |
| #63 | `72%` junto de `93%` passava o aviso. A grafia `Over 2.50` ficava de fora. | O percurso do parser ao aviso #55 e ao estudo rejeitou a quota contraditória e aceitou a linha equivalente. Yellow Cards, Team Total, Overtime, cabeçalhos contraditórios e primeiras leituras já editadas continuaram excluídos. O replay reproduziu as 12 diferenças originais, as 25 exclusões adicionais e os quatro casos LIVE de diagnóstico, com sobreposição de quatro casos. | `test_polymarket_alerts.py` e `test_polymarket_reaudit_replay.py`. Um âmbito de mercado sem prova continua a ser excluído. |
| #64 | Dois sinais com identidade parcial duplicavam a exposição. Reutilizar um ID em 26/11 fazia a vitória desaparecer em 27/11. | Cérebro e avaliação LLM mantiveram uma unidade com prova parcial compatível. O relatório, ranking, benchmark e controlos conservaram a exposição original. A colisão teve um motivo de exclusão próprio. | `test_match_identity.py`. Uma associação sem prova suficiente permanece em quarentena e os resultados associados ao ID após a colisão ficam isolados. |
| #65 | Com o modo Git ativado e sem confirmação explícita, o transporte simulado era chamado e a reserva subia de 55.000 para 57.367 décimos de neurónio. | Zero chamadas e saldo remoto de 55.000. Nos testes de concorrência apenas um runner obteve a última capacidade. A interrupção e a perda de ACK mantiveram os débitos. A recusa de escrita bloqueou a chamada. | `test_llm_budget.py`. A conta e o plano Cloudflare aguardam confirmação documental. O indicador de configuração é uma condição de execução. |

### Snapshot fixo da pilha A → B → C → D

As nove execuções usaram uma cópia dos ficheiros `data/` do commit `b3ab1b92c6689e622aacfcc8544003b906ec774b` e o relógio `2026-11-19T19:30:24+00:00`. A ativação de #55 permaneceu em `2026-11-19T15:19:15.977169+00:00`. O aviso foi planeado só em memória. A certificação científica permaneceu inativa.

| Etapa | Mensagens | Apostas / liquidadas | Mercado por liquidar | Sinais de investigação elegíveis / excluídos | Candidatos #55 / avisos planeados |
|---|---:|---:|---:|---:|---:|
| Base | 1711 | 932 / 752 | 97 | 932 / 197 | 0 / 0 |
| A | 1711 | 932 / 752 | 97 | 932 / 197 | 0 / 0 |
| A → B | 1711 | 924 / 751 | 91 | 924 / 197 | 0 / 0 |
| A → B → C | 1711 | 924 / 751 | 91 | 919 / 202 | 0 / 0 |
| A → B → C → D | 1711 | 924 / 751 | 91 | 919 / 202 | 0 / 0 |

O relatório operacional de cada etapa corrigida coincidiu com o do respetivo commit auditado, incluindo odds, resultados, lucro, ROI, intervalos descritivos e exclusões. A comparação com a base mostra a diferença já introduzida pelo âmbito de mercado em B. Em todas as etapas, as exclusões operacionais foram 37 primeiras leituras com resultado, 1 primeira leitura já editada, 12 sinais em lote e 2 com odd abaixo do mínimo. O estudo #55 e o benchmark tiveram zero jogos admitidos, zero stake liquidada, lucro de zero unidades e ROI indisponível.

A tabela seguinte usa a ordem jogos / apostas / liquidadas / vitórias / devoluções / lucro em unidades / ROI às odds anunciadas, reduzidas em 2% e reduzidas em 5%. Os ROI são proporções.

| Grupo | Base e A | B, C e D |
|---|---|---|
| `BETFAIR/live/mkt<10K` | 37 / 78 / 37 / 16 / 0 / 7.07 / 0.1911 / 0.1673 / 0.1315 | 37 / 78 / 37 / 16 / 0 / 7.07 / 0.1911 / 0.1673 / 0.1315 |
| `BETFAIR/live/mkt>=10K` | 59 / 74 / 61 / 20 / 0 / 13.58 / 0.2226 / 0.1982 / 0.1615 | 59 / 74 / 61 / 20 / 0 / 13.58 / 0.2226 / 0.1982 / 0.1615 |
| `BETFAIR/pre/mkt<10K` | 14 / 19 / 14 / 3 / 0 / -8.42 / -0.6014 / -0.6094 / -0.6214 | 14 / 19 / 14 / 3 / 0 / -8.42 / -0.6014 / -0.6094 / -0.6214 |
| `BETFAIR/pre/mkt>=10K` | 4 / 6 / 4 / 2 / 0 / 1.52 / 0.38 / 0.3524 / 0.311 | 4 / 6 / 4 / 2 / 0 / 1.52 / 0.38 / 0.3524 / 0.311 |
| `OPEN_GAME` | 181 / 182 / 181 / 85 / 42 / 16.69 / 0.0922 / 0.0754 / 0.0501 | 181 / 182 / 181 / 85 / 42 / 16.69 / 0.0922 / 0.0754 / 0.0501 |
| `POLYMARKET/live` | 32 / 46 / 45 / 23 / 0 / 4.04 / 0.0898 / 0.068 / 0.0353 | 32 / 45 / 44 / 22 / 0 / 3.08 / 0.07 / 0.0486 / 0.0165 |
| `POLYMARKET/pre` | 66 / 195 / 175 / 84 / 0 / -15.33 / -0.0876 / -0.1058 / -0.1332 | 66 / 188 / 175 / 84 / 0 / -15.33 / -0.0876 / -0.1058 / -0.1332 |
| `STAKE_LOAD/live` | 43 / 98 / 58 / 36 / 1 / 5.6 / 0.0966 / 0.075 / 0.0426 | 43 / 98 / 58 / 36 / 1 / 5.6 / 0.0966 / 0.075 / 0.0426 |
| `STAKE_LOAD/pre` | 48 / 155 / 99 / 50 / 5 / 2.25 / 0.0228 / 0.0035 / -0.0253 | 48 / 155 / 99 / 50 / 5 / 2.25 / 0.0228 / 0.0035 / -0.0253 |
| `TOTALS_FORM` | 63 / 63 / 63 / 40 / 0 / -1.7 / -0.0269 / -0.0438 / -0.0692 | 63 / 63 / 63 / 40 / 0 / -1.7 / -0.0269 / -0.0438 / -0.0692 |
| `WEAKENED_LINEUP` | 15 / 16 / 15 / 8 / 0 / 0.07 / 0.005 / -0.0131 / -0.0403 | 15 / 16 / 15 / 8 / 0 / 0.07 / 0.005 / -0.0131 / -0.0403 |

A comparação económica do cérebro aplicou a mesma data de nascimento de diagnóstico (`2026-11-01T00:00:00+00:00`) e uma unidade por encontro. A reconciliação por ordem de leitura voltou a contabilizar exposições válidas. Criou um motivo de exclusão para cinco sinais com ID reutilizado, sem alterar as contagens dos alertas ou da regra #55.

| Medida da população económica | C auditado | C corrigido | D auditado | D corrigido |
|---|---:|---:|---:|---:|
| Admitidos | 448 | 453 | 448 | 453 |
| Stake liquidada | 406 | 409 | 406 | 409 |
| Resultados confirmados | 327 | 328 | 327 | 328 |
| Vitórias | 170 | 171 | 170 | 171 |
| Derrotas confirmadas | 126 | 126 | 126 | 126 |
| Devoluções | 31 | 31 | 31 | 31 |
| Derrotas imputadas | 79 | 81 | 79 | 81 |
| Pendentes | 42 | 44 | 42 | 44 |
| Quarentena de FT | — | 0 | — | 0 |
| Lucro às odds anunciadas | -64.065 | -65.065 | -64.065 | -65.065 |
| Lucro às odds reduzidas em 2% | -70.0637 | -71.1037 | -70.0637 | -71.1037 |
| ROI às odds anunciadas | -0.1578 | -0.1591 | -0.1578 | -0.1591 |
| ROI às odds reduzidas em 2% | -0.1726 | -0.1738 | -0.1726 | -0.1738 |

O avaliador LLM foi executado offline com a prova de publicação do protocolo já registada no projeto. Avaliou 64 hipóteses em 64 grupos, com zero jogos LLM contabilizados e sem conclusão científica.

A pilha acumulada passou 751 testes Python e 12 testes JavaScript. A suite Python incluiu a regressão de atualização do dashboard em Playwright com Chromium, com os pedidos de rede intercetados.
