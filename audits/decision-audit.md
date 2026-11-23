# Auditoria temporal de decisões

A vista offline compara a decisão atual do motor com um diagnóstico conservador. O JSON detalhado identifica o encontro e a mensagem, o contrato, a fase, a odd anunciada, a emissão, o carimbo da primeira leitura, o marco do registo e os limites temporais. Inclui a linha e os SHA-256 da versão original. O hash do registo cobre o objeto JSON completo em UTF-8 com chaves ordenadas e sem espaços de separação. O hash de texto é comparado com o que o diário guardou. O JSON não contém texto das mensagens ou botões.

```sh
python -B -m scripts.decision_audit --as-of 2026-11-23T15:53:29.959234Z
```

Acrescentar `--summary` para omitir as decisões por encontro ou `--hypothesis 'FAMÍLIA | CONDIÇÕES'` para comparar um registo geral. A opção `--registration-ref` fixa a referência Git do registo. A vista oculta a estratégia congelada da PR #76 em cortes anteriores ao marco atestado. A PR #55 usa a primeira cotação elegível e o prazo original do protocolo.

## Como ler a comparação

- `current.reported_economics` calcula a economia atual com as regras existentes. `current.reconciled` aplica as novas categorias e a simulação adversa aos mesmos compromissos. `proposed` usa a primeira mensagem admissível em cada encontro e mostra as mesmas categorias. Cada decisão mantém as duas escolhas e os motivos de divergência. `fixtures_reviewed` inclui sinais rejeitados e tentativas sem identidade comprovada. As contagens por motivo podem sobrepor-se.
- O diagnóstico exige uma versão com hash verificável, emissão e leitura estritamente posteriores ao marco e uma janela ainda compatível com o contrato. Para carimbos antigos, acrescenta a margem de observação do projeto. Um pré-jogo precisa de kickoff na mensagem. Um sinal LIVE sem minuto verificável ou cuja janela possa chegar ao fim do período é assinalado. Os limites de 45 e 90 minutos são conservadores e ignoram o desconto. O diagnóstico também mostra quando um sinal anterior ao marco encobriu um sinal elegível posterior no mesmo encontro.
- Para hipóteses gerais, a data efetiva é o máximo da data declarada e da data do primeiro commit no prefixo Git que contém a definição atualmente visível. Uma definição editada ganha um novo marco. A diferença entre a declaração e a data Git é assinalada e pode resultar de publicação normal. O relógio Git é local ao repositório. A cronologia com prova externa das regras congeladas conserva os seus próprios marcos.
- Os seis estados são exclusivos. Uma liquidação confirmada inclui devoluções e meias liquidações. Um FT verificável sem um pagamento calculável tem categoria própria mesmo depois do prazo. Uma ausência de resultado passa de pendente a perda imputada no prazo. Conflitos são apresentados como quarentena, e evidência inválida ou sem tempo verificável fica como desconhecida. Reconciliações tardias aparecem em separado.
- O cenário adverso usa uma unidade por jogo admitido, aplica o fator 0,98 às odds anunciadas de liquidações confirmadas e debita uma unidade a cada jogo por resolver. Inclui pendências, quarentenas, FT sem liquidação, desconhecidos e perdas imputadas. O denominador é todo o conjunto admitido em cada coluna. O retorno é teórico e os custos e a execução continuam por demonstrar.

## Reprodução com dados fixos

A referência seguinte fixa o histórico do registo, o diário, a ativação da PR #55 e as versões de resultados fora do diretório de trabalho. O comando de auditoria lê a cópia e escreve apenas para stdout.

```sh
snapshot=$(mktemp -d)
git archive 58f66949a96a0e76ac15aeb7216218f7d2201495 data | tar -x -C "$snapshot"
SCANGOAL_DATA_DIR="$snapshot/data" python -B -m scripts.decision_audit \
  --as-of 2026-11-23T15:53:29.959234Z \
  --registration-ref 58f66949a96a0e76ac15aeb7216218f7d2201495 \
  --hypothesis 'Jogo aberto | Parte do jogo = 2.ª parte' \
  --hypothesis 'Stake | Mercado = outros mercados' --summary
```

| Coorte | Admitidos atual / diagnóstico | Confirmados atual / diagnóstico | ROI teórico atual a odds ×0,98 | ROI adverso atual / diagnóstico |
|---|---:|---:|---:|---:|
| PR #55 congelada | 6 / 6 | 5 / 5 | +20,54% sobre 5 | +0,45% / +0,45% |
| PR #76 congelada | 0 / 0 | 0 / 0 | — | — |
| Jogo aberto, segunda parte, registo geral | 277 / 277 | 268 / 268 | +0,51% sobre 271 | −1,66% / −1,66% |
| Stake, outros mercados, registo geral | 148 / 91 | 72 / 47 | −38,41% sobre 106 | −55,89% / −59,36% |

Na PR #55, o cenário adverso imputa uma perda ao jogo pendente. Em Jogo aberto, o cálculo atual já inclui três imputações e o cenário adverso acrescenta seis pendentes no denominador e nas perdas. Na hipótese Stake, 57 encontros deixam de ser admitidos pelo diagnóstico por falta de minuto LIVE comprovado. Dos 148 admitidos pelo cálculo atual, 67 tinham FT mas continuavam sem uma liquidação calculável do contrato. Os testes sintéticos reproduzem diferenças de admissão, incluindo o minuto 89, o pré-jogo sem kickoff e o reaproveitamento de uma mensagem posterior ao marco.

O replay usa os carimbos observados no diário e as versões de resultados visíveis no corte. Testes acrescentam um sinal e uma edição futuros, um conflito de identidade e correções de resultados. No mesmo corte, as decisões e a evidência ficam idênticas. Num corte posterior, a liquidação pode evoluir com a evidência disponível. Para reproduzir uma auditoria mesmo que alguém reescreva um ficheiro ou fabrique carimbos anteriores, conservar o SHA da revisão dos dados além do corte. Git local e uma hora anunciada pelo bot têm garantias limitadas sobre o tempo real de publicação ou sobre a possibilidade de execução.

## Testes

```sh
python -B -m unittest discover -s tests -v
node --test ops/continuity/watchdog.test.mjs
```

O teste offline do dashboard usa Playwright e Chromium, como na CI. `DASHBOARD_CHROMIUM` pode indicar um Chromium instalado no sistema.
