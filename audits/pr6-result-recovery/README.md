# PR 6 — Result Recovery

**Revisão após auditoria independente:** P1-A/P1-B/P1-C corrigidos. Validação atual: **53 testes específicos e 486 testes totais passaram**. Ver [relatório das correções](corrections/README.md), incluindo evidência T1/T2/T3, retenção no dia 31 e filtro BSD. Os logs e números da implementação inicial abaixo são preservados como histórico. A eficácia real continua não demonstrada.

Parecer: **APROVAR COM CONDIÇÕES para revisão técnica em Draft; REJEITAR ativação de produção neste estado.**

Base auditada: `50a94386b0ffb9d881428d89d122a2e8ac3734c1`, main de 16/11/2026.
Relógio reproduzível: 2026-11-16 15:40 UTC (Lisboa). Os dados continuam a evoluir em main.

## Diagnóstico antes do motor

O ponto de partida do pedido (600/467/133) estava desatualizado: a base tem **602 apostas, 467 liquidadas, 135 pendentes em 97 jogos**. Taxa: **77,57%**. O censo foi produzido antes do motor, pelo módulo independente `recovery_audit.py`.

| Categoria | Apostas | Evidência e limite |
|---|---:|---|
| F | 57 | FT já disponível; as regras de liquidação existentes não resolvem o mercado sem informação adicional |
| H | 24 | Quase-correspondências guardadas apenas com nomes; não comprovam identidade nem disponibilidade de resultado |
| J / unknown | 26 | Histórico de tentativas e páginas originais ausente; causa exata indeterminada |
| I | 24 | Prazo conservador de conclusão ainda não decorrido; não prova que o jogo esteja efetivamente em curso |
| C | 3 | Data relevante com pesquisa incompleta comprovada no último estado |
| G | 1 | Estado registado de final após 90 minutos sem marcador regulamentar suficiente |

Não atribuímos A/B/D/E: os dados não permitem comprovar resultado disponível, incompatibilidade de hora, ausência total de consultas históricas ou falta efetiva de cobertura. Em particular, estar fora da janela hoje não prova que nunca foi consultado. Categorias são mutuamente exclusivas; a evidência por fonte permanece na linha mesmo quando a categoria principal é outra.

`pending.json` contém todas as 135 linhas: chave original, ID de auditoria, jogo, mensagem, horas, equipas, competição, tipo, mercado/seleção, FT, fontes, candidatos, janelas, últimas tentativas agregadas, quota/paginação e motivo. Contagens por mercado/fonte e hashes de todos os ficheiros de entrada estão no mesmo relatório. As contagens por fonte são apostas e podem sobrepor-se.

Confirmado no código base:

- Highlightly: `MAX_AGE_DAYS=2`, exclusão dos IDs em `not_covered`, até 8 pedidos por execução. O comentário de quatro horas contradiz o intervalo efetivo de 90 minutos.
- BSD: `MAX_AGE_DAYS=3`, até 12 pedidos por execução.
- Ambos reiniciam a paginação em zero; não conservam páginas nem cursores entre execuções.
- HL 14/11: 610 anunciados, 200 lidos; `quota_remaining=14`, `stopped=daily_quota_low`.
- HL: 56 resultados, 35 quase-correspondências e 67 IDs em `not_covered`; BSD: 19 resultados e 13 quase-correspondências. São entradas por jogo, não apostas.
- A lógica HL já exige dias completos para incrementar ausência. O defeito confirmado não é classificar uma página parcial como ausência: é a exclusão permanente e a falta de retoma/páginas para auditoria.
- 57 pendentes já com FT incluem mercados de partes do jogo, cantos, cartões e seleções que o parser não liquida. Não inventámos regras novas para os resolver.

## Implementação e alcance

`scangoal/recovery.py` é uma ferramenta manual autónoma. Não se liga ao relatório, Telegram ou certificação em produção. Reutiliza os transportes atuais, normalização, validação de marcador e regras de liquidação. Separa planeamento/paginação, identidade, validação, proveniência, persistência e ensaio.

O motor procura todos os pendentes maduros na janela configurável (30 dias por defeito), ignorando a exclusão permanente `not_covered`. Guarda cursor por fonte/dia, eventos observados, hashes das páginas, total, estado, tentativas, orçamento por UTC e instante da observação. Guarda a reserva de pedido **antes** do transporte. Checkpoint atómico com fsync e lock exclusivo para impedir duas execuções sobre o mesmo estado. A ordenação pelo número de tentativas evita esquecer dias antigos; lotes limitados não reiniciam offsets. Segunda passagem completa só após 24 horas; máximo de duas passagens por dia pesquisado. Falhas repetidas param após três tentativas; páginas repetidas, sobrepostas ou total variável exigem revisão manual.

Não há classificação automática de ausência de cobertura. Falha, 429 e página incompleta permanecem distintas. 429 bloqueia a fonte até ao próximo dia UTC (mais conservador do que Retry-After); indisponibilidade tem espera mínima de uma hora. A quota diária configurada é validada contra o plano gratuito; a alocação disponível tem de ser fornecida pelo operador e partilhada com a produção. HL respeita também o cabeçalho de quota restante. Sem alocação explícita, não há pedidos.

Identidade automática exige as duas equipas, todos os horários associados ao jogo, competição exata normalizada quando indicada, candidato único e pesquisa completa dos dias relevantes de pelo menos uma fonte. Preserva Women/U19/U21/II/B; até aliases antigos que apaguem esses marcadores são recusados neste motor. Nomes aproximados só geram propostas com evidência e `review_required`; o ficheiro de aliases de produção não é alterado. IDs estáveis dos fornecedores deduplicam eventos e identificam correções. O ficheiro opcional `--reviewed-mappings` aceita `teams` e `competitions` (nome para lista de nomes), exige `reviewed: true` e `evidence`; a versão completa e o hash ficam no estado. Não altera aliases de produção. Não existe uma tabela revista que ligue os IDs ScanGoal aos fornecedores: esses vínculos não são inventados.

**Limite deliberado:** designações diferentes de competição exigem mapeamento humano explícito via `--reviewed-mappings`; nenhuma correspondência aproximada de competição é aceite automaticamente. O limite ±90 minutos reutiliza o critério existente; a unicidade é verificada no conjunto completo. Correções e conflitos ficam em quarentena persistente; não há substituição silenciosa nem resolução automática.

Os resultados ficam exclusivamente em `state.json/accepted`, acompanhados de fonte, ID, evento, marcador, estado, hash, critério e hora real de observação. Não são importados automaticamente para `results_hl.json`, `results_bsd.json` ou para amostras científicas. Essa integração e a sua revisão cronológica são uma condição de ativação posterior, não uma função já operacional.

## Ensaio em cópia dos dados reais

Reproduzir, sem rede nem escrita em dados de produção:

```bash
python scripts/recovery_rehearsal.py --now 2026-11-16T15:40:00+00:00 --output /tmp/goal-roi-pr6-evidence
```

O script cria uma cópia descartável, executa auditoria/ensaio e verifica hashes de todos os ficheiros originais e da cópia antes/depois. `integrity.json` regista a preservação. `trial.json` conserva os resultados do ensaio.

| Indicador | Antes | Depois | Diferença |
|---|---:|---:|---:|
| Apostas únicas | 602 | 602 | 0 |
| Apostas liquidadas | 467 | 467 | 0 |
| Apostas pendentes | 135 | 135 | 0 |
| Jogos distintos por liquidar | 97 | 97 | 0 |
| Resultados guardados HL | 56 | 56 | 0 |
| Resultados guardados BSD | 19 | 19 | 0 |
| Resultados de terceira fonte | 0 | 0 | 0 |
| Apostas com identidade ambígua H | 24 | 24 | 0 |
| Novos conflitos demonstrados pelo ensaio | 0 | 0 | 0 |
| Pedidos HL/BSD executados | 0 | 0 | 0 |
| Pedidos mínimos previstos, primeira passagem | — | HL ≥6; BSD ≥6 | estimativa, não execução |

O replay usa apenas resultados efetivamente guardados; os candidatos com nomes não são convertidos em resultados. Faltam competição e páginas originais nos caches antigos; não é possível demonstrar novas recuperações offline. **Aumento comprovado: 0 pontos percentuais.** O censo H tem 24 apostas com propostas potenciais, não 24 recuperações confirmadas. Zero conflitos novos no ensaio não prova ausência universal de conflitos nas fontes.

As fixtures dos testes demonstram recuperação, retoma, prevenção de duplicados, correções e conflitos **sintéticos**. Não entram na tabela de recuperações reais nem nos ficheiros de produção.

## Matriz de fontes e custos

Documentação oficial consultada em 16/11/2026:

| Fonte | Gratuito / quota | Histórico / identidade | Cobertura efetiva desta amostra |
|---|---|---|---|
| Highlightly | 100 pedidos/dia; quota partilhada All Sports | filtro por data, paginação e IDs; só `Finished` aceite | 56 resultados já guardados; potencial adicional indeterminado |
| BSD | 7 500 pedidos/dia UTC; limite de página 200 | datas, IDs e nomes; marcador regulamentar separado de ET/penáltis | 19 resultados guardados; potencial adicional indeterminado |
| TheSportsDB V1 | chave pública 123; 30 pedidos/minuto; limites de linhas por endpoint | pesquisa de evento por nome/data; endpoints históricos com limites | teste isolado 0/3 correspondências; não integrada |

Fontes: [HL paginação](https://highlightly.net/football-api/documentation/getting-started/pagination/), [HL plano gratuito](https://highlightly.net/), [BSD preços](https://sports.bzzoiro.com/pricing/), [BSD eventos](https://sports.bzzoiro.com/docs/football/events/), [BSD convenções](https://sports.bzzoiro.com/docs/), [TheSportsDB documentação](https://api.thesportsdb.com/documentation), [TheSportsDB termos](https://api.thesportsdb.com/docs_terms_of_use.php).

TheSportsDB permite uso dos endpoints oficiais em projetos de desenvolvimento e impõe condições próprias de publicação. Na documentação, pesquisa de evento gratuita limitada a 1 linha, listagem diária a 3 e por época a 15; V2 é paga. Não comprovámos que os campos gratuitos distingam sempre o marcador aos 90 minutos. A amostra real isolada usou a chave pública, sem conta nem secrets: Powisle–Jaguar (11/11), Bosnia U19–Georgia U19 (14/11) e Kashima–Yokohama FM (15/11). Três respostas HTTP 200 com `event:null`; URLs, instantes e respostas em `thesportsdb-sample.json`. Falha da pesquisa nominal não prova ausência de cobertura. Não há benefício mensurado para justificar adaptador ou integração. Não houve pedidos às quotas de produção HL/BSD.

O limite inferior de primeira passagem é seis datas por fonte. Para HL, só a data de 610 eventos exige sete páginas em vez de uma: o mínimo passa a **12 pedidos** se a contagem se mantiver. Com uma alocação autorizada de 5 pedidos HL/dia, são **pelo menos 3 dias com quota disponível** para essa primeira passagem, sem garantia de teto. BSD: ≥6 pedidos, pelo menos um dia a 20/dia; paginação real pode aumentar o total. Segunda passagem pode duplicar esses mínimos. Hoje a última quota HL guardada é 14, abaixo da reserva 20, portanto **zero alocação recuperável demonstrada para hoje**. Nenhuma previsão garante terminar em determinado dia; o tamanho de páginas futuras, cobertura e outras tarefas permanecem desconhecidos. Custo monetário previsto: 0 €, condicionado ao plano verificado e à alocação gratuita.

## Testes e integridade

**39 testes específicos passaram; suíte completa: 472 testes passaram.** Ver `recovery-tests.log` e `regression-tests.log`. `synthetic-proof.json` distingue uma recuperação de fixture das zero recuperações reais. A regressão usa `scripts/test_offline.py`, que bloqueia sockets. Mensagens `NOTIFY_OK` no log são de transportes simulados da suíte existente, não envios reais. A nova ferramenta não importa `notify` nem executa chamadas Telegram. Nenhuma dependência, workflow, secret, regra científica ou dado histórico foi alterado. `git diff --name-only BASE HEAD` permite verificar o âmbito; o relatório de hashes cobre todos os dados, incluindo os registos de notificações.

## Runbook e reversão

1. Rever a PR em Draft, o censo por aposta, os limites e as propostas de identidade. A aprovação técnica não autoriza a recolha real.
2. Para só diagnosticar: `python -m scangoal.recovery --audit --output /tmp/pr6-audit`.
3. Para ensaio offline: `python -m scangoal.recovery --backfill --dry-run --output /tmp/pr6-dry`. `--backfill` sem `--allow-live` também fica offline.
4. Antes de recolher: autorização expressa para backfill, confirmar plano/chaves já existentes, reservar quota que a produção não consuma em paralelo e manter **um único diretório de estado** para todas as execuções da recuperação. Não criar estados novos para contornar contadores. Os produtores atuais não partilham lock/orçamento: uma operação coordenada é condição obrigatória.
5. Só após autorização, numa cópia isolada dos dados, disponibilizar as chaves existentes no ambiente sem as imprimir. Exemplo de limite, não executado: `SCANGOAL_DATA_DIR=/copia/isolada python -m scangoal.recovery --backfill --allow-live --history-days 30 --max-requests 4 --available-hl 25 --reserve-hl 20 --daily-hl 5 --available-bsd 120 --reserve-bsd 100 --daily-bsd 20 --output /estado/persistente`. Os valores `available` são exemplos: têm de ser verificados no momento. Não passar `--now` em modo live.
6. Reexecutar sobre o mesmo estado para retomar. Estado e tentativas devem ser arquivados fora dos dados publicados, com backup. Após falha estrutural ou três falhas consecutivas, rever a evidência antes de iniciar nova pesquisa; não apagar tentativas, observações ou consumo reservado.
7. Rever `accepted`, `conflicts` e `alias_proposals`. A ferramenta não tem comando que publique resultados ou aceite aliases. A passagem de resultados para produção requer alteração revista à parte, mantendo a observação real e verificando a cronologia e quarentenas. Não copiar cegamente FT para ficheiros existentes.
8. Reversão: parar invocações manuais; preservar o diretório de estado para auditoria. Como não há workflow nem consumidor em produção, não há dados de produção a reverter. Se no futuro houver importação autorizada, qualquer correção deverá ser explícita e auditada, sem apagar histórico.

## Riscos e decisão

- **P0, bloqueia ativação:** não existe recuperação real adicional demonstrada; não declarar taxas esperadas nem aceitação científica. O ensaio demonstra segurança e identifica os motivos reais, não eficácia live.
- **P1, bloqueia produção:** o novo estado é isolado e não tem integração revista no relatório/cronologia; orçamentos não são compartilhados automaticamente com os produtores existentes; mapeamentos de competição ainda não revistos podem impedir candidatos válidos; faltam páginas reais para validar formatos/limites e resolver os 26 `unknown`.
- **P2:** falhas 429 esperam até ao próximo UTC; pesquisa de dias completos pode gastar mais que busca por ID; estado persistente deve ser arquivado/retido pelo operador, evitando crescimento indefinido e sem o adicionar aos dados publicados.

Resposta às perguntas finais: recuperáveis adicionais com as APIs existentes = **0 comprovados / total potencial desconhecido**; necessidade de terceira fonte = **indeterminada**, 0/3 no teste nominal; apostas com FT mas informação adicional = **57**; legitimamente impossíveis de liquidar = **não quantificável** sem distinguir falta temporária de evidência de indisponibilidade definitiva. A autorização que falta é a recolha real controlada, com reserva de quota e revisão dos resultados; a produção permanece bloqueada até integração e validação próprias.

A PR deve permanecer em Draft, sem merge e sem workflows automáticos.
