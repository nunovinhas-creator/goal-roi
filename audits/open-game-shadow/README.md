# Jogo aberto — infraestrutura prospetiva isolada

Base: `02127fcd311f451148895cafdf065ef53e4d7266` (main observado em 17-11-2026).
O protocolo JSON é a definição executável congelada, incluindo hora UTC, SHA e limiares.
Não há observações prospetivas na criação desta PR. Nenhum resultado histórico foi incorporado no ledger.
A janela é de 30 dias desde o congelamento; resultados têm 72 horas adicionais para observação.
Sem amostra suficiente, ou sem prova externa de publicação anterior ao resultado: INCONCLUSIVO.

## Reproduzir sem rede

```
python -m unittest discover -s tests -p test_shadow.py -v
python scripts/test_offline.py
python -m scangoal.shadow analyse --protocol audits/open-game-shadow/protocol.json --ledger audits/open-game-shadow/ledger.json
```

## Recolha preparada, ainda não instalada

```
python -m scangoal.shadow collect --protocol audits/open-game-shadow/protocol.json --ledger audits/open-game-shadow/ledger.json
```

Lê mensagens originais e caches existentes, usando a hora REAL da execução como decisão. Nunca reconstrói decisões passadas.
O comando não faz pedidos API, não envia Telegram, não toca em ROI/táticas/certificação. Só escreve no ledger explicitamente indicado.
É necessário publicar o ledger antes de o sistema conhecer o resultado, no máximo cinco minutos depois de cada decisão.
Sem instalação autorizada e publicação operacional não existe acompanhamento contínuo. O CLI `capture` é uma interface para fixtures/adaptadores;
entradas fornecidas por essa interface não provam por si só que todos os resultados conhecidos foram declarados.
A análise bloqueia certificação se não houver histórico git íntegro ou decisões publicadas antes dos resultados.
Os timestamps dos commits têm a mesma premissa de confiança do sistema de certificação existente; não são uma assinatura independente.

## Regras e comparação

Uma unidade por jogo e hipótese; primeira mensagem elegível de cada hipótese. H1 pode entrar depois de H2, com outra linha/odd.
Guarda mensagens posteriores sem substituir a decisão. Mercados, seleção, linha (na seleção), minuto, marcador e odd ficam no evento.
O feed não contém uma variável autónoma de período. H1 usa minutos 46..90; minuto 45 e >90 ficam assinalados como período ambíguo,
sem entrada H1. Isto pode perder descontos da segunda parte: limitação de medição explícita, nunca inventar a parte do jogo.
H2 não restringe a parte. Ambas exigem OPEN_GAME, diferença >=2, odd válida e mercado liquidável pelo FT regulamentar.

O contraste principal é a diferença de lucro das duas políticas no MESMO jogo H2, com lucro H1 zero quando não seleciona.
As entradas tardias de H1 e mudanças de mercado/odd ficam expostas na tabela emparelhada. Não há soma de amostras H1+H2.
O contraste confirmatório limita cada lucro a [-1,1] antes de calcular a diferença: estimando robusto pré-especificado.
A diferença sem limite é descritiva. Uma diferença positiva não identifica um efeito causal da segunda parte: linhas, preços e momento também mudam.

Uma cadeia serial comum, selecionada na entrada H2, só admite outro jogo depois do fecho do anterior.
Testes de retorno H1, H2 e contraste emparelhado, em duas direções: alpha 0,05/6, limiar e=120, lambda fixo 0,25.
Bonferroni controla multiplicidade mesmo com sobreposição. O nulo é condicional à informação disponível em cada decisão;
não se afirma independência de jogos simultâneos. Jogos fora da cadeia contam apenas na descrição.
Sem resultado em 72h, usa-se o pior lucro para CADA direção do teste; a liquidação factual continua pendente.
Correções ficam anexadas, preservando o original; bloqueiam inferência até revisão, sem recalcular decisões com hindsight.
A decisão final exige >=100 jogos na cadeia respetiva e fim de janela+72h. Não há prolongamento ou ajuste de limiares automático.
IC de Hoeffding descritivo por jogo, condicional às odds observadas e independência dos lucros; não é um certificado sequencial.
ROI sem haircut e com 2%/5%, resultados parciais, drawdown, pendências e distribuições ficam em `analyse`.

## Auditoria técnica inicial

- P0: nenhum caminho para apostas reais, Telegram, secrets, certificação ou edição dos dados de produção.
- P1 operacional: publicação externa e cadência inferior a 120 segundos ainda não instaladas; monitorização NÃO operacional.
- P1 científico: validade do contraste depende do nulo condicional e prova de cronologia. Sem prova, bloqueio obrigatório.
- P2 medição: minutos >90 não distinguem compensação de prolongamento; H1 exclui-os explicitamente.
- P2 causal: comparação de políticas observadas; não prova isoladamente que a segunda parte causa vantagem.

Decisão: APROVAR a infraestrutura para revisão/uso shadow manual; REJEITAR ativação contínua ou qualquer certificação neste estado.
A aprovação técnica não constitui validação científica de H1/H2.

## Verificação executada

Suite completa offline: 579 testes executados; 578 passaram e 1 teste de navegador ficou bloqueado por restrição de sockets Chromium deste ambiente.
A mesma falha existe na base (569 testes, 568 passaram). Os 10 testes novos passam, incluindo corrupção, backfill, conhecimento futuro,
resultados parciais, sobreposição, cadeia serial, falta de resultados em ambas as direções e publicação anterior ao resultado.
O teste de navegador não foi alterado nem dispensado. Aprovação técnica final exige CI existente verde.
Os ficheiros de produção, workflows, Telegram, ROI e certificação têm diff vazio contra a base.
`historical-description.json` descreve apenas apostas já agregadas pela produção: é diagnóstico retrospetivo, sem valor prospetivo,
e pode omitir mensagens de reforço posteriores com a mesma seleção; não é a amostra congelada.
