# Auditoria científica adversarial da v3

> **Revisão v3.1.** Desde então foi confirmado e corrigido um defeito: a camada de previsão da v3.0 tratava `+0,5`/`−0,5` como resultados completos ([HALF-SETTLEMENT.md](HALF-SETTLEMENT.md)). Nenhuma decisão avaliada nesta auditoria tinha meia liquidação, por isso todos os números abaixo são idênticos na v3.1. Os ficheiros em `results/` foram regenerados com os hashes da v3.1. A falta de pares no snapshot real é diagnosticada em [COVERAGE-AUDIT.md](COVERAGE-AUDIT.md).

**GO técnico para integração experimental do protocolo v3 em draft. NO-GO para qualquer alegação confirmatória, económica ou de execução. Nenhuma hipótese real tem hoje suporte para registo v3.**

A auditoria parte de `01cef30e27d507b3c9ca52696ed273e6747ccda2` (`audits/temporal-discovery-v2/SCIENTIFIC-AUDIT.md`). Todos os números abaixo foram produzidos pelos scripts deste diretório e estão em `results/`. Os ensaios sintéticos são novos: usam geradores, sementes e datas (março de 2028) disjuntos dos bancos v1/v2. A única exceção é a reavaliação das regras-oráculo, que usa de propósito o banco v1/v2 sem alterar os sorteios.

**Independência.** O autor do protocolo também escreveu a auditoria, por isso esta não é independente quanto à pessoa. Foi desenhada para ser **reproduzível e falsificável por terceiros**: sementes fixas, hashes fixados nos testes, scripts sem rede e resultados versionados. Uma revisão humana externa continua a ser necessária antes de qualquer janela confirmatória.

## Correspondência com os defeitos da v2

| Defeito v2 (auditoria `01cef30`) | Tratamento v3 | Evidência |
| --- | --- | --- |
| Teste de sinal condicionado a dias ≠ 0. Mediana 0 com p = 2⁻²⁰. | O estimando é a média por par. Os empates ficam no numerador e no denominador. O teste é sign-flip por cluster. | `test_ties_stay_in_the_estimand`. Nula com 40% de *pushes*: 0,0% / 0,8% de retenção. |
| Dias assumidos independentes. | Clusters obrigatórios semana × competição, com o máximo dos p. LOO por fonte e por competição. | Nula com efeito variável por semana: 0,8% na v3 contra 5,8% só por dia. Por competição: 0,2% contra 8,8%. |
| Comparador noutro relógio, bloco dia × contrato. | Conjunto de risco no instante do caso, com contrato idêntico, mesmo relógio, mesma oportunidade de entrada e antecedente ausente de forma determinável. | `RiskSetDesign`. Pares sempre com `case.at == control.at`. |
| Alvos igualizados ≠ rendimento dos contratos reservados. | Decomposição entrada / seleção / resultado, com incremento esperado pela lei do gerador **na decisão tomada**. | `reevaluate.py`. |
| Previsão, odd anunciada e execução misturadas. | Três camadas com estados próprios. A execução é sempre `not_demonstrated`. | `layers.py`, `test_execution_is_never_demonstrated`. |
| Composição e confundimento não medidos. | Calibres, preferência pela mesma competição e correção de viés linear pré-registada. | Confundimento por preço: o ingénuo retém, a v3 não (ver abaixo). |
| Protocolo v2 usado como confirmatório. | Quarentena explícita v1/v2, com histórico preservado e hashes fixados. | `Quarantine`, `Immutability`. |

## Calibração do núcleo inferencial

`power_v3.py`: 400 repetições por cenário, 90 dias, 6 pares por dia, 10 competições, odd 2,05 e α = 0,0125 (uma hipótese × duas camadas no primeiro plano). Os pares passam pelo código de produção (`layers` → `inference.assess` → portões). Entre parênteses: intervalo de Wilson a 95% da própria simulação. As colunas de sensibilidade aplicam outras hipóteses estatísticas às mesmas amostras.

| Cenário | Δ previsão | v3 previsão | v3 odd anunciada | Só clusters diários | t por cluster semanal | Teste v2 (descarta empates) | Cobertura IC 95% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Nula independente | 0 | 0,5% (0,1–1,8) | 0,5% (0,1–1,8) | 1,5% | 1,2% | 1,2% | 97,2% |
| Nula, 40% *pushes* | 0 | 0,0% (0,0–1,0) | 0,8% (0,3–2,2) | 1,5% | 2,5% | 1,5% | 97,8% |
| Nula assimétrica (odds justas 3,00 vs 1,50) | 0 | 0,0% (0,0–1,0) | 0,0% | 0,2% | 0,2% | 0,0% | 98,5% |
| Nula, efeito varia por semana (dp 15 pp) | 0 | 0,8% (0,3–2,2) | 0,8% | **5,8%** | 1,0% | 4,0% | 96,0% |
| Nula, efeito varia por competição (dp 15 pp) | 0 | 0,2% (0,0–1,4) | 0,2% | **8,8%** | **7,0%** | **8,2%** | 93,8% |
| Nula, choque semanal comum | 0 | 0,2% | 0,2% | 1,5% | 2,0% | 1,0% | 98,2% |
| Nula, +10% FT perdidos nas derrotas do caso | 0 | 0,5% | 0,5% | 0,5% | 1,0% | 0,8% | 99,0% |
| −8 pp | −0,08 | 0,0% | 0,0% | 0,0% | 0,0% | 0,0% | 97,5% |
| +4 pp | 0,04 | 5,8% (3,9–8,5) | 5,8% | 14,2% | 13,2% | 13,0% | 97,2% |
| +8 pp | 0,08 | 32,8% (28,3–37,5) | 32,8% | 58,0% | 51,0% | 42,5% | 96,5% |
| +12 pp | 0,12 | 77,2% (72,9–81,1) | 77,2% | 94,8% | 89,5% | 86,8% | 98,0% |
| +8 pp, 12 pares/dia | 0,08 | 72,0% (67,4–76,2) | 72,0% | 91,2% | 84,0% | 78,0% | 98,5% |
| +8 pp, 45 dias (7 semanas) | 0,08 | 0,0% (inadmissível) | 0,0% | 32,5% | 20,5% | 23,2% | 98,2% |
| +8 pp, 40% *pushes* | 0,08 | 8,5% | 19,2% | 22,2% | 22,0% | 18,2% | 96,8% |
| +8 pp, efeito varia por semana | 0,08 | 21,8% | 21,8% | 57,0% | 24,0% | 46,8% | 94,0% |
| +8 pp, 5% FT em falta | 0,08 | 1,5% | 1,5% | 7,2% | 6,0% | 5,0% | 100% |
| +8 pp, 20% FT em falta | 0,08 | 0,0% | 0,0% | 0,0% | 0,0% | 0,0% | 100% |
| +12 pp, 5% FT em falta | 0,12 | 14,5% | 14,5% | 43,2% | 38,0% | 33,5% | 100% |

Leitura:

- **Dimensão.** A v3 ficou abaixo de α em todas as nulas, incluindo empates, assimetria, dependência por semana e por competição, e censura seletiva. Agrupar só por dia ou só por semana inflaciona a taxa de falsos positivos quando o efeito do padrão depende da competição (7–9% contra α = 1,25%). Por isso o p oficial é o máximo das estruturas obrigatórias.
- **Potência.** Este conservadorismo custa potência. Para +8 pp são precisos cerca de 12 pares comparáveis por dia durante 90 dias para ~70%. Com 6 por dia fica em ~33%. Com 45 dias o teste é inadmissível (< 8 semanas). Abaixo de +4 pp, a potência é residual.
- **FT em falta.** O limite inferior de pior caso é deliberadamente severo: 5% de FT em falta derruba a potência de 33% para 1,5%. A cobertura do intervalo de identificação sobe para 100% porque o intervalo se alarga. É a troca explícita por validade sob censura seletiva.
- **Intervalos.** A cobertura do IC 95% para a média populacional ficou entre 93,8% e 100% em todos os cenários. Antes do alargamento pela união das estruturas e pelos limites de FT, ficava em 77,5% com efeito por competição e 60,8% com FT em falta. Essa falha foi corrigida durante a auditoria.

## Calibração ponta-a-ponta (eventos → replay → v3)

`calibration_e2e.py`: 40 mundos independentes por cenário, com 90 dias × 24 jogos, duas competições e cotações Stake `Over 2.5` a cada 30 min. A regra testada é `Polymarket ≥ 50k → Stake over, mesma linha`. Cada mundo passa por `Replay` → conjunto de risco → camadas → inferência → portões, a α = 0,0125. O estimador ingénuo, ao estilo v2, compara todos os casos com jogos sem antecedente na sua primeira cotação, sem instante comum (sign-flip semanal). O «incremento verdadeiro» é o efeito causal do antecedente nos casos emparelhados, conhecido pelo gerador. A cobertura refere-se ao IC 95% da v3 para esse incremento.

| Cenário | Pares | Incremento verdadeiro | v3 sem correção | v3 corrigida | Retenção previsão | Retenção odd anunciada | Ingénuo (diferença) | Cobertura |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Nula | 494 | 0 | −0,000 | −0,001 | 1/40 (0–13%) | 0/40 | 0/40 (−0,004) | 38/40 |
| +10 pp | 499 | +0,100 | +0,084 | +0,083 | 22/40 (40–69%) | 20/40 | 37/40 (+0,104) | 36/40 |
| +20 pp | 496 | +0,200 | +0,158 | +0,157 | 40/40 (91–100%) | 40/40 | 40/40 (+0,196) | 31/40 |
| −10 pp | 498 | −0,100 | −0,078 | −0,077 | 0/40 | 0/40 | 0/40 (−0,099) | 37/40 |
| Nula, confundimento por preço (viés favorito-azarão; antecedente concentrado em odds baixas) | 477 | 0 | **+0,021** | **−0,002** | 1/40 (0–13%) | 1/40 | **20/40** (+0,061) | 38/40 |
| Nula, deriva de composição (antecedente cresce na competição que ganha mais) | 477 | 0 | −0,002 | −0,002 | 2/40 (1–17%) | 0/40 | 0/40 (−0,005) | 38/40 |
| Nula, choques semana × competição | 502 | 0 | +0,000 | +0,000 | 0/40 | 0/40 | 1/40 (−0,002) | 38/40 |
| Nula, efeito varia por semana | 494 | −0,002 | −0,006 | −0,005 | 0/40 | 0/40 | 0/40 (−0,005) | 39/40 |
| Nula, 25% FT perdidos nas derrotas expostas | 495 | 0 | +0,061 | +0,061 | 0/40 | 0/40 | **26/40** (+0,074) | 40/40 |
| +10 pp, 5% FT em falta | 494 | +0,100 | +0,073 | +0,073 | 1/40 | 1/40 | 38/40 (+0,099) | 40/40 |

Leitura:

- **Confundimento.** Sob confundimento por preço, o contraste ingénuo reteve 50% das nulas. O comparador no mesmo instante, por si só, deixou um viés de +0,021, porque os casos concentram-se em odds baixas e os calibres admitem um desvio sistemático de −0,037 em log-odd. A correção linear pré-registada reduziu-o a −0,002, e a v3 reteve 1/40.
- **Censura seletiva.** A estimativa de casos completos fica enviesada (+0,061). O teste sobre o limite inferior não reteve nenhuma nula; o ingénuo reteve 65%.
- **Composição e dependência.** Deriva de composição, choques comuns e efeito variável por semana ficaram compatíveis com α. A taxa de 2/40 na deriva tem um intervalo de Wilson largo (1–17%) e merece repetição com mais mundos antes de uma janela real.
- **Atenuação.** Os comparadores que mais tarde exibem o antecedente (~17% neste mundo) diluem o efeito: +0,20 é estimado em +0,16. Daí a cobertura de 31/40 para o incremento causal com +20 pp. O desvio vai sempre em direção a zero, para efeitos positivos e negativos, e torna as alegações mais conservadoras. O estimando oficial é o contraste com comparadores **como reservados no instante**.
- **Potência.** Com ~5,5 pares por dia, +10 pp foi retido em 55% dos mundos, +20 pp em 100% e −10 pp nunca. 5% de FT em falta reduz +10 pp a 1/40, por causa do limite de pior caso.

## Reavaliação das seis regras-oráculo e das regras descobertas

`reevaluate.py` usa sementes 83/84/85 do banco v1/v2 e uma janela de 90 dias após o corte de 30 dias. A tabela separa três coisas:

- **entrada**: casos nos jogos plantados, fecho no terminal plantado e entradas antes de o terminal ser observável;
- **seleção**: o contrato da decisão é o contrato-alvo do gerador;
- **resultado**: incremento de previsão esperado, pela lei de FT do gerador, na decisão realmente tomada contra o comparador realmente escolhido.

**Protocolo** (calibres pré-registados), por semente:

| Regra | Pares | Entrada no terminal | Seleção no alvo | Incremento esperado | Estado |
| --- | --- | --- | --- | --- | --- |
| Oráculo reversão multiorigem | 29 / 25 / 28 | 198/223, 188/215, 210/230 (20–27 prematuras) | 94–95% | +0,04 / +0,12 / +0,08 | insuficiente |
| Oráculo atraso não adjacente | 8 / 10 / 8 | 97–99% | 100% | +0,28 / +0,40 / +0,37 | insuficiente |
| Oráculo contexto × preço LIVE | 0 / 0 / 0 | 100% | 100% | — | insuficiente (sem comparador LIVE) |
| Oráculo oscilação 8 | 3 / 6 / 6 | 92–96% | 98–100% | ≈ 0 | insuficiente |
| Oráculo sobe e desce | 23 / 29 / 28 | 95–99% | 100% | +0,01 / +0,03 / −0,04 | insuficiente |
| Oráculo confirmação tardia | 9 / 8 / 7 | 98–99% | 99–100% | +0,26 / +0,18 / +0,26 | insuficiente |
| Selecionada oscilação | 39 / 36 / 42 | **0**/207, 96/207, **0**/235 | **0**, 146, 218 | **−0,27** / −0,05 / +0,09 | insuficiente |
| Selecionada confirmação tardia | 0 / 1 / 2 | **0**/193 na semente 83, sempre antes da confirmação | 100% | — | insuficiente |
| Selecionada atraso não adjacente | 11 / 0 / 22 | 160/234 e 169/230, com 60–73 prematuras; 206/207 na semente 84 | 76% / 100% / 80% | +0,12 / — / +0,18 | insuficiente |
| Família descoberta (46 / 42 / 42 regras seguidas) | máximo 49 / 54 / 56 | — | — | — | 100% insuficiente |

**Sensibilidade não protocolar** (6 h de idade de cotação, ±180 min, 0,35 em log-odd):

- O oráculo da confirmação tardia teve 112–127 pares, incremento esperado de +0,19 a +0,23 e observado de +0,20 a +0,22. Foi retido em 2/3 sementes.
- O oráculo do atraso não adjacente teve 92–98 pares e incremento de +0,39. Foi retido em 2/3 sementes.
- A regra selecionada para a reversão multiorigem teve 201–220 pares e incremento de +0,12 a +0,16. Foi retida em 2/3 sementes.
- O oráculo do padrão enganador nunca foi retido: o incremento futuro esperado é ≈ 0.
- A regra selecionada da oscilação, com seleção errada, foi rejeitada na semente 83 (p = 0,993).
- **Todas as retenções tiveram incremento esperado positivo.** Nenhuma retenção correspondeu a um efeito nulo ou negativo na decisão tomada.

Conclusões:

1. As regras selecionadas falham sobretudo **na entrada e na seleção**, não no teste. A oscilação selecionada reserva antes do terminal e, na semente 83, aposta na direção oposta ao alvo em 192/192 jogos. O seu incremento esperado é negativo, apesar de o Jaccard estrutural ser 1,00. A confirmação tardia selecionada entra antes da confirmação em 193/193 jogos. Na v2 estas falhas apareciam misturadas com composição.
2. Com os calibres do protocolo, o banco v1/v2 não fornece comparadores no mesmo instante para nenhuma regra. Os jogos têm 1–5 alertas dispersos em ~5 h e os contratos variam. O padrão LIVE não tem comparador possível no gerador, porque só os jogos plantados emitem âncoras LIVE. A v3 declara isto como insuficiência em vez de inventar um contraste.
3. Os calibres relaxados mostram que o desenho distingue padrões verdadeiros de coincidências quando há suporte. **Não são protocolo e não têm valor confirmatório.**

## Snapshot real (exploratório, sem valor confirmatório)

`real_snapshot.py` com corte em 2026-11-24 08:22 UTC: o minerador v2 propôs 54 regras, com 1.851 fechos de caso no total. Deles, 827 (45%) não tinham odd pontual e 1.011 não tinham comparador no conjunto de risco. Houve 13 pares com os calibres do protocolo (máximo de 1 por hipótese) e 128 com os calibres relaxados (máximo < 10 por hipótese). **Nenhuma hipótese atinge o suporte mínimo de registo (20 pares). O plano v3 proposto é vazio.** Para que a v3 tenha potência, a recolha teria de observar o mesmo contrato em muitos jogos ao mesmo tempo, por exemplo séries de cotações regulares. Isso exige alterações ao collector fora do âmbito desta PR, a decidir separadamente.

## Limitações conhecidas

- O sign-flip é exato sob simetria das somas por cluster com a hipótese nula verdadeira. Com assimetria forte de preço entre braços, a simulação manteve a dimensão. A validade continua a ser aproximada fora dos cenários testados.
- A correção de viés é linear em log-odd e relógio. No confundimento por preço sintético anulou o viés (+0,021 → −0,002), mas um viés favorito-azarão não linear, ou dependente de variáveis não observadas, pode sobreviver.
- A reserva única de comparadores pode excluir futuros casos (`controls_later_exposed`, ~17–25% dos comparadores no mundo sintético). O estimando é condicional à reserva no instante e atenua os efeitos em ~17% (ver tabela ponta-a-ponta).
- *Pushes* e meias-linhas entram no retorno, mas não na previsão.
- A execução real (limites, rejeições, odd executável, latência) não é medida. O teste de esforço não é evidência de rentabilidade.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
