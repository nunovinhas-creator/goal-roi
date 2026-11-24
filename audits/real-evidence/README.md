# Evidência real · cobertura, observabilidade e relações

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.** Nada aqui é evidência validada.

Os dados são o arquivo do `main` em `e587c3c` (`messages.jsonl` sha256 `5f0666b5…`). O relógio da análise é 2026-11-24 18:35 UTC. A análise usa o *parser*, a identidade dos jogos e a reconciliação de resultados do próprio repositório, sem nenhuma odd nem resultado introduzidos à mão. Corre sobre uma cópia de `data/`, por isso não escreve nada em produção. [Números completos](evidence.json) · [Script](analyze.py)

Cada secção separa **observação** (contagem direta), **hipótese exploratória** (padrão sem confirmação) e **evidência validada**. Desta última não existe nenhuma.

## 1–3. Cobertura e observabilidade (observação)

| Pergunta | Número |
| --- | ---: |
| Mensagens do canal interpretadas (versões incluídas: 7 339) | 4 550 |
| Registos de sinal | 2 467 |
| **Jogos distintos observados** | **1 206** |
| Jogos com odd explícita no sinal | 976 |
| Jogos com marcador final | 914 |
| **…dos quais confirmados por fornecedor independente** | **0** |
| Sinais elegíveis (com mercado, seleção e odd válidos, proveniência verificada, sem rajada nem edição) | 2 121 |
| **Avaliáveis sem informação futura**: elegíveis, liquidados, vistos antes de o resultado ser conhecido e, se pré-jogo, antes do início | **1 471 sinais · 724 jogos** |
| Entradas no registo prospetivo de validação | **0** (nunca ativado) |

De onde vêm os resultados: mensagem do bot (1 343), marca do bot (436), nota de fim de jogo do bot (11), sem resultado (313). **O mesmo emissor que dá o sinal dá também o resultado.**

## 4–5. Relações (observação e hipótese exploratória)

O ROI é por unidade, à odd anunciada, sem custos. Os IC95 são aproximados e assumem independência entre sinais.

| Relação | O que se observa | Avaliável prospetivamente? |
| --- | --- | --- |
| Tipos de alerta no mesmo jogo | 237 de 942 jogos têm mais de um tipo; o par mais frequente é Polymarket + Stake (143). Em 741 pares de sinais de totais de fontes diferentes, **331 (45%) apontam em sentidos opostos.** | Sim, usando só os sinais anteriores |
| Marcador depois de alerta ao vivo | Em 633 sinais ao vivo, houve mais golos depois do sinal em 72,4%. A taxa base pelo minuto é 65,9%. | Sim: minuto e marcador são conhecidos no sinal. Falta a odd do mercado "mais golos" para saber se isto já está no preço. |
| Volume Betfair | Mercado ≥10 mil $: ROI +19,9% (n=38, IC [-40%, +80%]). Abaixo: −8,3% (n=35). | Sim, mas n é pequeno demais para concluir |
| Movimento da odd depois do sinal | Encurtou: ROI +5,1% (n=151). Alargou: −8,9% (n=132). | **Não**: `odds_last` é uma edição posterior sem hora fiável, ou seja, informação futura |
| Sinal repetido | Repetido −2,6% (n=369), único −0,4% (n=1 212) | Não: as repetições chegam depois |

Por grupo, todos os IC95 do ROI incluem zero. O mais negativo é o Polymarket pré-jogo (−6,7%, IC [−17,6%, +4,3%], n=447). No relatório do repositório, com 5% de custos, o IC desse grupo fica inteiramente negativo.

## 6. Hipóteses novas (exploratórias)

Usei períodos disjuntos. A **descoberta** usou sinais vistos antes de 20/11 (689) e a **verificação**, feita uma única vez, os vistos a partir de 20/11 (892). Dos 25 segmentos (grupo × fase × faixa de odd), dois passaram a regra de seleção na descoberta. Nenhum se manteve:

| Segmento | Descoberta | Verificação |
| --- | --- | --- |
| Open Game, ao vivo, odd 1,50–2,50 | +11,2% (n=155) | +1,3% (n=155, IC [−9,8%, +12,4%]) |
| Stake, ao vivo, odd 1,50–2,50 | +33,9% (n=42) | −6,2% (n=75) |

O cérebro do repositório já tinha visto todo este histórico. Testou 14 820 hipóteses e marcou 30 como "promissoras", quando o acaso daria em média 148,6. É o padrão de ruído com testes múltiplos.

Merecem **acompanhamento prospetivo**, não aposta:
- **H-a.** Golos depois de um alerta ao vivo, comparados com a probabilidade implícita na odd do mercado de golos. Exige recolher essa odd.
- **H-b.** Divergência entre fontes no mesmo jogo (sentidos opostos) como sinal de incerteza: evitar, não apostar.
- **H-c.** Open Game ao vivo 1,50–2,50, já sem ganho na verificação. Só vale a pena com registo prévio, porque o histórico já o "usou".

## 7. O que impede resultados úteis

| Bloqueio | Prova | Solução |
| --- | --- | --- |
| **B1. Resultados independentes nunca entram.** | Há 113 resultados Highlightly e 48 BSD guardados, mas 0 ficam associados. `report.external_matches` exige `league` no registo do fornecedor e nenhum registo o tem. Só por equipas e hora, ligar-se-iam 87 e 22, e nos 47 comparáveis o marcador coincide sempre com o do bot. | Guardar `league` (ou `target_league`) quando o coletor associa o jogo, ou associar por identidade de equipas e hora com verificação. É uma alteração de produção e exige a tua autorização. |
| **B2. Cobertura de fornecedores baixa.** | Highlightly parou por `daily_quota_low` (quota restante 12). No máximo 109 dos 914 jogos terminados (12%) têm registo externo associável. | Aumentar a quota ou dar prioridade aos jogos com sinal elegível. |
| **B3. Não há fita de odds independente.** | O observador Polymarket fez 1 tentativa, sem livro de ofertas válido. Todas as odds vêm do texto do bot. | Ativar uma recolha de cotações com hora de receção, só leitura e sem ordens. |
| **B4. A validação prospetiva nunca foi ativada.** | `validation.state = not_activated`, 0 entradas no registo. | Ativar o registo com hipóteses fixadas **antes** dos jogos. É a única via para evidência válida. |
| **B5. O histórico é curto e já foi explorado.** | 13 dias, com cerca de 1 471 sinais avaliáveis, e todos já vistos pelo cérebro. | Nada do passado confirma. Só os jogos futuros, depois do registo. |

## Próxima ação executável

**Ativar o registo prospetivo** (B4) com 2 ou 3 hipóteses fixadas hoje, entre elas H-a e H-c, e **corrigir a associação de resultados independentes** (B1). Cada jogo futuro passa então a contar como evidência válida, com resultado confirmado por uma fonte que não é o próprio emissor do sinal. Ambas mexem em produção e por isso aguardam a tua autorização.
