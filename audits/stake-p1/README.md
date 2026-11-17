# P1 — correção factual de 1X2 Stake

Base main: `f03bda08878085a27f22735336012a7a4026d445`. Relógio comum: `2026-11-17T18:05:26+00:00`.
PRs #42 e #43 verificadas abertas/Draft, SHAs esperados e CI success. Só houve alterações de dados desde o snapshot anterior.
`initial-snapshot.json` guarda hashes de todos os ficheiros de produção e o estado `not_activated`.

## Correção

`parser.selection_1x2` aceita 1/X/2, Draw e nome completo exato, único, com strip/casefold. Não remove palavras, não usa aliases,
não procura prefixos/substrings. Seleção desconhecida/ambígua devolve None. A salvaguarda no relatório impede que essa seleção
1X2 volte a ser liquidada pela marca do bot. Outros mercados preservam a lógica existente, incluindo live AH dependente de marca.
Nenhum resultado final, mensagem, timestamp, alias, dado histórico ou registo científico é editado.

## Impacto reproduzido

A reprodução carrega o parser E o relatório diretamente do commit base num módulo isolado, compara com o código corrigido e usa
os mesmos ficheiros/relógio. Não replica a lógica antiga por uma fórmula inventada. `impact.json` enumera TODAS as diferenças por caminho JSON.

Sete apostas agregadas mudam de derrota para vitória: mensagens iniciais 28093, 28242, 28322, 28323, 28394, 28580 e 28811.
Correspondem aos 16 avisos/versões identificados na auditoria anterior. Aumento exato do lucro: **14,70 unidades**.

| Grupo | Liquidadas | Pendentes | Unidades antes | Unidades depois | ROI antes | ROI depois |
|---|---:|---:|---:|---:|---:|---:|
| Stake LIVE | 43 | 23 | -2,83 | 0,32 | -6,58% | 0,74% |
| Stake PREMATCH | 61 | 45 | -11,50 | 0,05 | -18,84% | 0,09% |
| Stake conjunto | 104 | 68 | -14,33 | 0,37 | -13,78% | 0,36% |

Total do relatório: 758 apostas elegíveis; 594 liquidadas antes e depois. Mercados com FT mas pendentes: 60, sem alteração.
Os nove grupos factuais restantes (Betfair, Polymarket, Open game, Totals form e Weakened lineup) não mudam.

O cérebro muda como consequência esperada da correção: três hipóteses Stake deixam de estar abandonadas (21→18 no total);
“em observação” passa de 7940 para 7943. Muda a ordenação de hipóteses, quadros, notas e apresentação exploratória.
Walk-forward é recalculado, não congelado: apostas seguidas 318→335, jogos 204→209, liquidadas 268→279,
unidades 12,12→12,35 e ROI 4,52%→4,43%; probabilidade exploratória de seguido superar omitido 59,17%→52,33%.
A curva tem comprimento diferente porque a estratégia reconstruída passou a seguir 17 sinais adicionais. Isto NÃO é evidência prospetiva nova.
Accuracy, audit, daily, insights, operational, questions e timeline também mudam de forma derivada e estão integralmente descritos no JSON.
Certificação antes/depois: `not_activated`, sem alterações ao objeto de validação ou ativação de táticas certificadas.

## Reproduzir

```
python -m unittest discover -s tests -p test_stake_1x2.py -v
python scripts/test_offline.py
python scripts/audit_stake_1x2_impact.py --output-dir /tmp/goal-stake-impact-novo
```

Reprodução exata requer os dados do SHA base e os ficheiros de código desta PR. O destino deve ser novo, sem sobrescrita.
Foram executados sete testes novos e a suite completa (576 testes). 575 passam localmente; um teste Chromium é bloqueado pela restrição
de sockets do ambiente, falha já identificada na base. Nenhum teste foi dispensado/alterado. O CI deve confirmar o SHA final.

## Parecer

APROVAR para integração técnica após CI verde e autorização expressa de merge. P0: nenhum identificado.
P1 conhecido: corrigido e reproduzido nas sete apostas. Risco residual: nomes que não coincidam exatamente permanecem pendentes.
Os conteúdos e classificações exploratórias gerados após eventual integração refletirão a correção; o mecanismo Telegram não é modificado.
Não há merge, envio de teste, edição de secrets, alteração de fontes externas ou certificação científica.

O primeiro CI expôs uma corrida de relógio num teste preexistente: duas construções criavam hipóteses em segundos reais diferentes. O helper de teste passa agora o mesmo `as_of` a `update_registry`, mantendo todas as asserções e sem alterar lógica de produção/Telegram. O CI final deve validar esta correção determinística.
