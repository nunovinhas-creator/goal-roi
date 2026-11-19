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
