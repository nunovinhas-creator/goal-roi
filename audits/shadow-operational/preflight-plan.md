# Goal ROI — pré-ensaio isolado SHADOW

## Decisão

**PREFLIGHT TESTADO; INSTALAÇÃO INATIVA; CERTIFICAÇÃO INCONCLUSIVA.**

A PR #47 não altera a recolha, o Telegram, as fontes de resultados, os ficheiros `data/`, o estado de certificação ou a `main`. Não cria qualquer workflow novo.

## Prova executada — 17/11/2026

Foi executado um workflow transitório exclusivamente no ramo de desenvolvimento, **SHADOW isolated preflight #1**, com duas jobs separadas, sem credenciais de produção e com permissões de leitura.

- Execução: https://github.com/nunovinhas-creator/goal-roi/actions/runs/44200210820
- Job 1: testes locais, medição do primeiro avistamento e preparação de um outbox sintético; sucesso
- Job 2: descarregamento do artefacto noutro runner, recuperação do registo sintético, confirmação de uma única decisão e um único recibo; sucesso
- Amostra dos últimos 24h: 14 mensagens OPEN_GAME com primeiro avistamento válido, 14 recolhidas até 120s, 0 acima de 120s
- O ensaio não mediu a disponibilização remota dos dados nem a captura pelo runner SHADOW

**A suite completa detectou dois testes de proteção que proíbem acrescentar novos workflows.**
Para respeitar essas salvaguardas, o workflow transitório foi retirado da árvore proposta nesta PR, preservando o histórico de execução e os scripts/testes para reprodução manual. **Não se modificaram nem enfraqueceram as proteções existentes.**

## Ficheiros propostos

- `scripts/shadow_preflight.py`: 
  - `measure` mede a latência entre emissão e primeira recolha, por mensagem OPEN_GAME; não declara aprovação da ativação
  - `stage` cria um outbox de falha de push exclusivamente sintético, com bundles de Git local
  - `restore` verifica hashes e prova preservação de uma única decisão/recibo num checkout novo
- `tests/test_shadow_preflight.py`: oito testes de deduplicação, limites temporais, evidência insuficiente e recuperação sintética
- `audits/shadow-operational/preflight-evidence.json`: liga ao run transitório, com resultados e limites de interpretação

## Reproduzir manualmente, sem rede externa

```bash
python -m unittest discover -s tests -p test_shadow_preflight.py -v
python scripts/shadow_preflight.py measure --path data/messages.jsonl --output /tmp/shadow-delivery.json
python scripts/shadow_preflight.py stage --dir /tmp/shadow-synthetic-new
python scripts/shadow_preflight.py restore --dir /tmp/shadow-synthetic-new --output /tmp/shadow-restored.json
```

Usar destinos novos. São criados apenas remotos Git `file://` locais e sinais sintéticos. Não executar `activate` nem editar instalação/autorização.

## P1 pendentes antes dos 30 dias

1. Medir o percurso completo **sinal → primeira recolha → push para Git remoto → leitura no runner SHADOW**. Os 14/14 dentro de 120s provam apenas a primeira etapa.
2. Demonstrar os atrasos de commit e entre runners e definir um canal isolado de entrega com cobertura suficiente, sem alterar a recolha principal até nova auditoria/autorização.
3. Validar proteção contra falhas, garantia de retenção/restauro do último outbox real e liveness num runner dedicado.
4. Preparar autorização operacional versionada, com ativação e início dos 30 dias como decisão separada.

Nenhuma vantagem estatística é afirmada e o SHADOW continua `prepared_not_installed`, com zero observações reais.
