#!/bin/sh
# Remove só a linha "# goal-roi-selos" do crontab. Os selos ficam, salvo --purge (apaga a pasta própria).
set -eu
TAG='# goal-roi-selos'
BASE="${GOAL_ROI_SEALS_HOME:-$HOME/goal-roi-selos}"
others=$( (crontab -l 2>/dev/null || true) | grep -v "$TAG" || true)
printf '%s\n' "$others" | crontab -
if [ "${1:-}" = "--purge" ]; then
  [ -f "$BASE/bin/goal-roi-selo" ] || { echo "recusado: $BASE não parece uma instalação goal-roi-selos" >&2; exit 1; }
  chmod -R u+w "$BASE" && rm -rf "$BASE"
fi
echo "goal-roi-selos: desinstalado${1:+ ($1)}"
