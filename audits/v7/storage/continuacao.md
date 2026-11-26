# Continuação da auditoria V7

## Âmbito e cortes

O diagnóstico do parser está na [PR #101](https://github.com/nunovinhas-creator/goal-roi/pull/101). O arquivo, snapshots e continuidade estão na [PR #102](https://github.com/nunovinhas-creator/goal-roi/pull/102). São drafts. O código de produção analisado é e3a0760813f8b2ba4f4953a84dde0a697a117a10 e o corte histórico de 1 363 é 12e1de7d5dfe5abb5a4c867d9c17f7c5206eb12f. O código dos snapshots testado é o HEAD 33b469e47f20cd0a6ece2ef7ad369dce18ad54c7 da [PR #98](https://github.com/nunovinhas-creator/goal-roi/pull/98).

A aplicação de produção, Pages, as automações, a V6.1, o Brain V5, o protocolo e o registo prospetivo ficam fora da alteração. Os ponteiros e dados de referência devem continuar fixados durante as comparações. Não usar credenciais nem chamar providers durante os ensaios offline. Os únicos acessos externos necessários para preparar o ambiente são GitHub para ler os objetos e as dependências públicas manifestadas.

## Reproduzir

Usar Python com os requisitos do repositório, Node e Chromium/Playwright instalado localmente. Usar um clone limpo em e3a07608 e uma worktree separada em 33b469e4. A raiz scratch desta auditoria tem os checkouts em repo e pr98, o código executável em deliverables e os logs integrais em evidence. Os drafts versionam os scripts principais e as saídas agregadas.

A partir da raiz do clone de referência, com DELIVERABLES a apontar para a pasta extraída do pacote:

    export PYTHONDONTWRITEBYTECODE=1
    unset SCANGOAL_DATA_DIR
    PYTHONPATH=".:$DELIVERABLES" python3 "$DELIVERABLES/parser_audit.py" --inventory /tmp/v7-inventario-msg.csv
    PYTHONPATH=".:$DELIVERABLES" python3 -m unittest test_parser_audit test_segment_store -v
    python3 "$DELIVERABLES/git_growth.py"
    python3 "$DELIVERABLES/storage_sim.py" --repo . --days 1825
    PYTHONPATH=. python3 "$DELIVERABLES/continuity_drill.py" --archive data/messages.jsonl
    node "$DELIVERABLES/continuity_watchdog.mjs" .
    node --test ops/continuity/watchdog.test.mjs

Usar PR98_DIR para o checkout da PR #98 e MAIN_DIR para o corte principal. Em ambos os tipos de ensaio a escrita usa apenas a cópia temporária:

    cd "$PR98_DIR"
    PYTHONPATH=. python3 "$DELIVERABLES/pr98_adversarial.py" --pr . --report-root "$MAIN_DIR"
    node "$DELIVERABLES/pr98_cache_probe.cjs" .
    python3 "$DELIVERABLES/snapshot_pack_comparison.py" --main "$MAIN_DIR" --pr .
    python3 "$DELIVERABLES/pages_preview.py" --pr . --report-root "$MAIN_DIR" --output /tmp/v7-pages-preview.tar.gz
    V6_CHROMIUM=/usr/bin/chromium RUNS=3 node audits/v6-intelligence/measure.cjs

O benchmark de relatório atual usa cópia descartável da worktree da PR, substituindo somente docs/report.json e data/prospective_report.json pelos bytes de MAIN_DIR antes de chamar o benchmark. O servidor de teste precisa de permissão para socket de loopback. Definir V6_CHROMIUM e DASHBOARD_CHROMIUM para usar um Chromium existente.

## Trabalho pendente e ordens de precedência

1. **PR #98:** manter inativa. Corrigir em ramo isolado a reutilização do manifesto idêntico sem verificar corpos, a mistura após interrupção, a revisão conjunta da entrada prospetiva e o caminho de verificação por comprimento. Amostrar tempos no corte atual com menor ruído, incluindo p95 com pelo menos 20 repetições e prova em HTTPS/CDN de staging após autorização. Meta de 2 s para primeiro conteúdo e conclusão, com versão anterior íntegra disponível.
2. **Parser:** dar a cada classe uma referência semântica, incluindo os 105 nomes Kalshi não literais, variantes de contratos negados e os rótulos Stake que o liquidante rejeita. Confirmar jurisdição do marcador, relógio e regras de cada mercado antes de qualquer normalização. A PR #101 é só diagnóstico. A linha de base é 1 379 e as 144 rejeições Polymarket representam outro denominador.
3. **Arquivo:** estender a prova de bytes aos resultados, hipóteses e restantes estados, desenhar o índice de consulta e medir disco, retenção e espelho independente. O protótipo testa o diário e publicações locais. Validar os consumidores do report.json, do Android e do Brain antes de propor mudança de rotas.
4. **Continuidade:** ensaiar restauração fora do mesmo provedor com cópia verificada e instrução humana para erro de credenciais. Os drills cobrem 24/72 h visíveis, não recuperação de mensagens já apagadas ou versões substituídas. Não ativar recuperação automática nesta fase.

Prioridade alta é garantir integridade e preservar o protocolo e o registo, seguida da avaliação de desempenho e custo. Um novo PR operacional deve ter autorização própria. Não alterar PR #98 nem o Brain V5 durante o período experimental.

## Estado após a continuação de 26/11/2026

Ramo `claude/goal-roi-v7-continuacao-ozagkj` (inclui as PRs #101 e #102 por merge, sem as alterar).

1. **PR #98:** os quatro defeitos estão corrigidos num protótipo isolado (`audits/v7/snapshots/`). Falta: portar para um ramo próprio da V6.1 com autorização, prova em HTTPS/CDN de staging e medição periódica do p95 (margem atual ~0,21 s).
2. **Parser:** classes semânticas fechadas para os 105 Kalshi, 52 negados e 144 Polymarket (`audits/v7/parser/SEMANTICA.md`). Falta: regras escritas de cada contrato, revisão humana da lista `aliases_para_revisao_humana`, liquidante para negação/spread e corpus etiquetado. Spreads contraditórios, `12`, nomes sem lado e cabeçalhos sem relógio ficam em quarentena.
3. **Arquivo:** prova byte a byte de todas as fontes e vistas, índice, retenção e exportação feitas (`audits/v7/storage/ARQUITETURA.md`). Falta: piloto da cópia selada a 12 h num disco fora do GitHub e decisão (com autorização) sobre retirar as vistas do Git.
4. **Continuidade:** restauração a partir de espelho verificada em 7 cenários (`restore_drill.py`). Falta: ensaio num fornecedor realmente distinto e instrução humana para erro de credenciais (sem automação).

Comandos de reprodução: ver os README de cada pasta e `ARQUITETURA.md`.

## Protocolo para incidentes futuros

- Identificar primeiro a última revisão válida, usando commit, manifestos e hashes, e verificar se a falha vem de recolha, autenticação, publicação ou CDN. Manter datas de corte e de disponibilidade separadas.
- Isolar mais de um escritor e manter o último relatório íntegro. Em perda de rede com resposta incerta, verificar ancestry ou marca de publicação antes do retry. Em notificação incerta, conservar o intent e não reenviar automaticamente.
- Recuperar páginas de Telegram para arquivo separado. Comparar IDs, páginas, ordem e hashes antes de concluir. A verificação da história disponível não certifica mensagens apagadas. Nenhuma página de recuperação é aplicada ao registo prospetivo.
- Recuperar de Git local, bundle verificado e espelho autorizado quando a nuvem estiver indisponível. Reproduzir a vista inteira e comparar o manifesto. Reter a revisão anterior. A reposição do publicador requer aprovação e não elimina história.
- Fechar o incidente com último SHA íntegro, hora de retoma, lacunas ainda desconhecidas, evidência de deduplicação e registo do que mudou. Retomar a investigação com as regras congeladas.

**RENTABILIDADE NÃO DEMONSTRADA — NENHUMA ESTRATÉGIA VALIDADA.**
