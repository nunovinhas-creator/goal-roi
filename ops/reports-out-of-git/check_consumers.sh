#!/bin/sh
# Procura consumidores dos relatórios gerados numa pasta (por exemplo o repositório do Brain ou a pasta do Termux).
# Só leitura: não altera nada. Uso: sh ops/reports-out-of-git/check_consumers.sh <pasta> [<pasta> …]
# Saída: uma linha por referência a report.json/bets.csv (excluindo prospective_report.json) ou ao site/raw do goal-roi.
# Código 0 = nenhuma referência; 1 = há referências a rever antes da fase 2; 2 = uso incorreto.
[ $# -ge 1 ] || { echo "uso: $0 <pasta> [...]" >&2; exit 2; }
found=0
for dir in "$@"; do
  [ -d "$dir" ] || { echo "não é uma pasta: $dir" >&2; exit 2; }
  hits=$(grep -rnI --exclude-dir=.git --exclude-dir=node_modules -E \
    '(^|[^_a-z])report\.json|bets\.csv|raw\.githubusercontent\.com/nunovinhas-creator/goal-roi|nunovinhas-creator\.github\.io/goal-roi' \
    "$dir" 2>/dev/null | grep -v 'prospective_report\.json' || true)
  if [ -n "$hits" ]; then printf '%s\n' "$hits"; found=1; fi
done
[ "$found" -eq 0 ] && echo "nenhuma referência aos relatórios gerados"
exit "$found"
