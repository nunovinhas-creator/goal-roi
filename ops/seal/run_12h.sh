#!/bin/sh
# Ciclo de 12 h da cópia selada: atualiza o clone só de leitura, sela origin/main, verifica a cadeia e poda.
# Uso: GOAL_ROI_CLONE=<clone> GOAL_ROI_SEALS=<pasta fora do GitHub> sh ops/seal/run_12h.sh
# Código de saída diferente de 0 = falha a investigar por humano. Não há recuperação automática.
set -eu
: "${GOAL_ROI_CLONE:?defina GOAL_ROI_CLONE}"
: "${GOAL_ROI_SEALS:?defina GOAL_ROI_SEALS}"
HERE=$(cd "$(dirname "$0")" && pwd)
python3 "$HERE/seal.py" seal --repo "$GOAL_ROI_CLONE" --dest "$GOAL_ROI_SEALS" --fetch --ref origin/main
python3 "$HERE/seal.py" verify --dest "$GOAL_ROI_SEALS" --max-age-h 13
python3 "$HERE/seal.py" prune --dest "$GOAL_ROI_SEALS" --keep-days 30
