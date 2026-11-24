# PR D · Inferência científica v3 (`temporal-risk-set-v3`)

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**

A branch parte de `01cef30e27d507b3c9ca52696ed273e6747ccda2`, o HEAD da PR #84, que por sua vez assenta na PR #83. Nenhuma das duas está em `main` e nenhuma foi fundida aqui: a PR D aponta para a branch da PR #84 e preserva a dependência. O código novo vive em `scangoal/discovery/v3/` e `scangoal/discovery/quarantine.py`. A única alteração a código existente é a CLI (`scangoal/discovery/__main__.py`), que passa a aceitar `--version 3` e a anotar as saídas v1/v2 com o bloqueio confirmatório.

Não foram tocados collector, Telegram, workflows, `data/`, certificação, estratégias congeladas nem variáveis de produção. Não há descoberta agendada, notificações nem apostas. Tudo corre offline, sem odds inseridas à mão e sem qualquer odd da 1xBet.

## Pergunta científica

A v2 comparava casos e controlos em blocos dia × contrato, com relógios diferentes e um teste de sinal que descartava os dias empatados. Assim não separava um padrão que **coincide** com resultados de um padrão que **acrescenta informação** ao preço disponível no mesmo instante. A v3 responde a três perguntas distintas sobre os mesmos pares:

| Camada | Pergunta | Estatística por par |
| --- | --- | --- |
| Previsão | O antecedente acrescenta informação além da odd anunciada? | (ganhou − 1/odd)caso − (ganhou − 1/odd)comparador, só em desfechos decisivos |
| Odd anunciada | O retorno à odd anunciada é melhor do que o do comparador **e** positivo em absoluto? | retorno líquido (2% sobre ganhos, 0,02 por unidade) do caso − do comparador; IC do retorno absoluto do caso > 0 |
| Execução | Seria rentável executar? | Não mensurável offline. Só um teste de esforço com degradação de odd 0/2/5/10% e o ponto de equilíbrio. Estado fixo: `not_demonstrated`. |

## Comparador no mesmo instante de risco

Um caso é o primeiro fecho observado da sequência num jogo, no instante `t` em que o último recibo fica disponível. Nesse instante, o comparador é escolhido entre outros jogos que, usando só recibos disponíveis até `t`:

1. têm exatamente o mesmo contrato: fonte, fase, mercado nativo, período, linha e seleção;
2. têm identidade canónica de jogo com um único início conhecido em `t`;
3. têm odd pontual cotada há no máximo 30 min (5 min em LIVE), a menos de 0,15 em log-odd do caso;
4. estão no mesmo relógio relativo: ±45 min até ao início, ou ±5 min de jogo com marcador idêntico;
5. satisfazem o último passo da regra, ou seja, têm a mesma oportunidade de entrada;
6. mostram ausência **determinável** do antecedente em `t`.

A ordem de escolha é: mesma competição, menor distância escalada, cotação mais recente e desempate SHA-256 com sal fixo. Cada jogo é reservado uma vez. Um comparador que mais tarde exibe o padrão conserva a reserva e é contado (`controls_later_exposed`). Uma correção de viés linear pré-registada, ajustada **só nos comparadores liquidados**, remove a diferença residual de log-odd e relógio dentro dos calibres (Abadie–Imbens). O relatório mostra a estimativa com e sem correção.

## Inferência

- **Estimando**: diferença média por par. Os pares empatados (diferença 0) ficam no numerador e no denominador. Os *pushes* não contam como evidência de previsão, mas contam no retorno.
- **Teste**: sign-flip unilateral por cluster sobre as somas do limite inferior. É exato até 16 clusters; acima disso usa 20.000 sorteios com semente fixa e correção (1+k)/(1+B).
- **Dependência**: os clusters obrigatórios são a semana do início e a competição. O p oficial é o **máximo** entre as estruturas estimáveis (≥ 8 clusters). Dia e fonte são reportados. Também se exige robustez *leave-one-out* por fonte e por competição: retirar qualquer grupo deixa a média positiva.
- **FT em falta**: o teste usa o limite inferior (caso perde, comparador ganha). O IC vai do limite inferior ao superior e é a união sobre as estruturas obrigatórias. É preciso FT em ≥ 90% dos pares.
- **Suporte**: ≥ 60 pares e ≥ 8 semanas.
- **Multiplicidade**: Holm sobre todas as hipóteses do plano × {previsão, odd anunciada}, com α = 0,05/[n(n+1)] para o plano n. Os q-valores BH são descritivos. A família tem no máximo 12 hipóteses.
- **Estados**: `experimental` (o máximo possível), `rejected` ou `insufficient`. Cada camada tem o seu estado. `executable_profitability` é sempre `not_demonstrated`.

## Registo e janela confirmatória

O registo v3 (`scangoal/discovery/v3/registry.py`) é separado do ledger v1/v2. Usa domínio de recibo próprio, `temporal-risk-set-plan-v3`, e diretório externo. Um plano fixa a família, o hash do protocolo, o hash do intérprete e o corte de descoberta, e começa com `confirmatory_results = 0`. A seleção para registo é **cega ao desfecho**: ordena por pares emparelhados no histórico (≥ 20), nunca por FT, taxa de acerto ou ROI. A janela de 90 dias começa à meia-noite UTC após o recibo RFC 3161 independente e após o corte. O resultado só é decidido após o fecho e a liquidação. Um protocolo v1/v2 não pode ser registado em v3, e o identificador `tr3:` liga a regra ao protocolo.

## Quarentena v1/v2

`scangoal/discovery/quarantine.py` bloqueia explicitamente qualquer alegação confirmatória dos protocolos `temporal-day-contract-v2` (`659d7de3…`) e `temporal-v1` (`1c49b4d9…`). A v1 usa o mesmo teste de sinal que descarta empates (`study.sign_p`). As saídas `discover` e `evaluate` v1/v2 da CLI mantêm o `status` calculado como histórico e acrescentam `confirmatory_status = blocked_defective_protocol`, `status_as_computed` e o bloco `quarantine`. Os protocolos, intérpretes, ledgers e auditorias anteriores não mudam. Os testes fixam os seus hashes.

## Hashes

| Objeto | SHA-256 |
| --- | --- |
| Protocolo v3 | `1726e67ee8ff345175468471b76cccae4124a087c8f45ca0dcbf5b5b877f8939` |
| Intérprete v3 (bytes dos módulos interpretativos) | `987a4d68468ab93ee7969258b0d36eab709fcd47c64a9a19d2a01a65e87ca161` |
| Protocolo v2 (inalterado) | `659d7de3788657abfed2b6a07170f16381e3b7acca202084f67a6af18ed98f3a` |
| Intérprete v2 (inalterado) | `a22d681b4d66767ee5b3cf06bbe05e446beeddfd5ddc39406ac870d0f178b6c9` |
| Protocolo v1 (inalterado) | `1c49b4d9a4d5bba54a1d7b05c2a4eec821dbb05bd834fcc1f6e2c87fde081041` |

## CLI

```sh
# Exploratório: minera com o minerador v2, reidentifica em v3, avalia no histórico.
python -m scangoal.discovery discover --version 3 --as-of 2026-11-24T08:22:00Z --format summary

# Pré-registo e avaliação confirmatória só em diretórios externos.
python -m scangoal.discovery discover --version 3 --as-of 2026-11-24T08:22:00Z \
  --registry /tmp/goal-roi-d-plans --commit
python -m scangoal.discovery registry --version 3 --as-of "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --registry /tmp/goal-roi-d-plans
python -m scangoal.discovery evaluate --version 3 --as-of "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --registry /tmp/goal-roi-d-plans --receipts /tmp/goal-roi-d-receipts
```

## Reprodução

```sh
python -m unittest discover -s tests -p test_discovery_v3.py -v
PYTHONPATH=. python audits/temporal-discovery-v3/power_v3.py --repetitions 400 > audits/temporal-discovery-v3/results/power_v3.json
PYTHONPATH=. python audits/temporal-discovery-v3/calibration_e2e.py --repetitions 40 > audits/temporal-discovery-v3/results/calibration_e2e.json
PYTHONPATH=. python audits/temporal-discovery-v3/reevaluate.py > audits/temporal-discovery-v3/results/reevaluate.json
PYTHONPATH=. python audits/temporal-discovery-v3/real_snapshot.py --as-of 2026-11-24T08:22:00Z > audits/temporal-discovery-v3/results/real_snapshot.json
python -m unittest discover -s tests -v
node --test ops/continuity/watchdog.test.mjs
git diff --name-status 01cef30e27d507b3c9ca52696ed273e6747ccda2..HEAD
```

Os resultados versionados em `results/` foram gerados com Python 3.13 nesta branch. Os geradores usam sementes fixas. A suite de testes corre em Python 3.11, tal como o CI. A [auditoria científica](SCIENTIFIC-AUDIT.md) interpreta-os.
