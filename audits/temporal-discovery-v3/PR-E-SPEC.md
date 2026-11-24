# Especificação técnica · PR E: observabilidade de cotações e cobertura de mercados

**Estado: proposta.** Nada aqui está implementado ou autorizado. A PR E começa com diagnóstico offline. Qualquer recolha adicional, mesmo só de observação, exige **autorização separada** numa fase posterior.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

## Problema

A auditoria de cobertura (`COVERAGE-AUDIT.md`) mostra que a v3.1 não tem suporte porque:

- o bot emite **alertas** e não **séries de preços**: só 162 recotações do mesmo contrato no mesmo jogo, e cotação noutro jogo do mesmo contrato a mediana de 174 min;
- 46% dos casos únicos têm faixa de execução em vez de odd pontual;
- metade dos contratos aparece num único jogo;
- o histórico anterior a 2026-11-23 não tem recibos completos.

O «cérebro» não pode acompanhar a evolução temporal de um preço que só observa quando o bot decide alertar.

## Objetivo

Medir e, numa fase autorizada, aumentar o número de **observações verificáveis e simultâneas do mesmo contrato** em vários jogos, com proveniência e recibos. O alvo é dar à v3.1 (ou a um sucessor pré-registado) comparadores no mesmo instante, sem relaxar critérios.

## Restrições (todas as fases)

- Sem odds manuais da 1xBet, sem *scraping* proibido, sem serviços pagos, sem credenciais novas, sem ordens nem apostas.
- Na fase 0, nenhuma alteração a collector, workflows, Telegram, certificação, `data/` histórico, estratégias congeladas ou variáveis de produção.
- Nenhuma alegação de rentabilidade. A execução continua `not_demonstrated`.

## Fase 0: diagnóstico offline (âmbito da PR E)

Entregáveis, todos em `scangoal/observability/` (novo) e `audits/quote-observability/`, só de leitura sobre `data/`:

1. **Registo canónico de contratos.** Função pura `contract_key(event)` partilhada com a v3: fonte, fase, mercado nativo, período, linha e seleção. Acrescenta uma tabela de equivalências **entre fontes**, para análise e nunca para emparelhar. Produz um inventário de contratos com jogos, cotações e formas de preço.
2. **Série de observações de cotação** (`QuoteObservation`), derivada só de dados guardados:

   ```text
   fixture_id, contract_key, source, phase,
   emitted_utc, available_utc, receipt (completed_page | legacy_upper_bound),
   price_kind (point | interval | indeterminate),
   price_low, price_high, price_point?,
   volume_alert_usd, volume_cumulative_usd?, biggest_ticket_usd?, n_bets?,
   message_id, record_sha256, text_sha256
   ```

   Os intervalos ficam explícitos como limites e nunca se convertem silenciosamente em ponto.
3. **Extratores offline** do texto já guardado, cada um com testes e contagem de cobertura:
   - cronologia de golos `⚽ m' a - b`, como eventos de marcador com a disponibilidade da versão que os trouxe (nunca antes do recibo);
   - maior bilhete e volume acumulado.
4. **Métricas de observabilidade**, reprodutíveis por comando:
   - observações simultâneas do mesmo contrato por hora e por janela de ±45 min de início;
   - distribuição da idade da cotação no instante de cada caso;
   - fração de preço pontual por fonte e por contrato;
   - cascata de falhas da v3 por classe (preço, relógio, identidade, mercado, seleção, fonte, correspondência, disponibilidade, resultados);
   - liquidações não suportadas por mercado e seleção.
5. **Calculadora de potência ligada à cobertura.** Dado o número esperado de pares comparáveis por dia e por família de contratos, devolve a potência da v3.1 para +4/+8/+12 pp. Usa o núcleo de `power_v3.py` e diz quanto tempo de observação seria necessário.
6. **Relatório versionado** com hash do snapshot, sem dados pessoais, regenerável em CI como teste de regressão do diagnóstico (só a lógica, com dados sintéticos).

Critérios de aceitação da fase 0:

- a cascata reproduz exatamente os 13 pares e os 1.851 casos do snapshot de 2026-11-24;
- os extratores têm testes adversariais: versões editadas, recibos tardios, marcadores incoerentes, moedas e abreviaturas (`$3.1K`);
- `git diff` não toca em caminhos protegidos;
- CI verde.

## Fase 1: observação adicional (exige autorização separada)

Só depois de aprovar a fase 0 e de uma autorização explícita, numa PR própria.

- **Fonte.** Apenas endpoints públicos de leitura da Polymarket (catálogo de mercados e livro de ordens ou histórico de preços), sem conta nem chave. É a mesma família do observador existente (P1–P3), que nunca enviou ordens nem usou credenciais. Antes de ativar, rever os termos de uso e as restrições geográficas.
- **Universo.** Jogos com alerta do bot e **jogos pares com início a ±45 min** nos mesmos contratos densos (total 2,5 e handicap ±0,5 no pré-jogo). É isto que cria comparadores no mesmo instante.
- **Cadência.** Uma observação por contrato a cada ≤ 15 min, de T−6 h até ao início, com orçamentos rígidos de pedidos e duração. As execuções e os limites são auditados como no observador existente.
- **Armazenamento.** Diretório próprio, append-only, endereçado por conteúdo, separado de `data/` histórico. Recibos com hora de pedido e de resposta e hash do corpo.
- **Segurança.** Interruptor que desliga tudo, `orders_sent = 0` verificado em testes, ausência de segredos verificada, e nenhuma notificação.
- **Identidade.** Correspondência jogo ↔ mercado por regras determinísticas e auditáveis. O que não for inequívoco fica `identity_not_found` e é contado, nunca adivinhado.

Métricas de saída: pares comparáveis por dia e por contrato, idade das cotações, taxa de identidade resolvida e taxa de preço pontual (meio do livro e melhor oferta, com profundidade).

## Fase 2: uso inferencial (protocolo novo, pré-registado)

- Um sucessor da v3.1 com identificador e hash próprios, que aceite `QuoteObservation` como fonte de comparadores no instante. Mantém contrato exato, relógio, ausência determinável do antecedente e reserva única.
- Plano com potência calculada a partir da cobertura **medida** na fase 1, janela nova e recibo independente antes do início.
- A camada de execução pode passar de «não mensurável» a «aproximada pelo livro», mas continua `not_demonstrated` sem execução real.

## Riscos

| Risco | Mitigação |
| --- | --- |
| Termos de uso ou geobloqueio da fonte pública | Revisão antes da fase 1; sem contorno; parar se não for permitido |
| Diferença entre preço Polymarket e preço do alerta (outra fonte) | Contrato exato inclui a fonte; nunca se emparelham fontes diferentes |
| Viés de seleção do universo de pares | Universo definido antes da recolha, por início e contrato, sem olhar para resultados |
| Aumento de custo ou carga | Orçamentos rígidos e auditoria por execução |
| Tentação de relaxar critérios para criar pares | Proibido: qualquer alteração exige protocolo novo e pré-registo |

## Fora do âmbito

Collector Telegram, notificações, apostas, Betfair (exige credenciais), Stake e 1xBet (sem API pública autorizada) e fornecedores pagos de odds ou resultados.
