#!/bin/sh
# Instala a cópia selada de 12 em 12 horas no Termux (Android). Custo 0. Não toca no Brain, no Obsidian nem no brain-sync.sh.
#
#   sh install.sh            instala ou atualiza (idempotente)
#
# Variáveis opcionais:
#   GOAL_ROI_SEALS_HOME  pasta própria (por omissão ~/goal-roi-selos). Recusada se estiver dentro de uma pasta do Brain/Obsidian.
#   GOAL_ROI_REPO_URL    origem só de leitura (por omissão https://github.com/nunovinhas-creator/goal-roi.git)
#   GOAL_ROI_KEEP_DAYS   retenção completa em dias (30); depois fica 1 selo por semana
#
# O que faz:
#   1. pkg install python git xz-utils cronie termux-services util-linux (gratuitos)
#   2. clone próprio, só de leitura e sem blobs (--filter=tree:0), em $GOAL_ROI_SEALS_HOME/repo
#   3. bin/goal-roi-selo: um ciclo (fetch, selo, verificação SHA-256, poda), com lock próprio, nice, registo e falhas
#   4. crontab: acrescenta UMA linha marcada "# goal-roi-selos", sem alterar as outras; minuto afastado ≥ 20 min do brain-sync
set -eu
TAG='# goal-roi-selos'
BASE="${GOAL_ROI_SEALS_HOME:-$HOME/goal-roi-selos}"
URL="${GOAL_ROI_REPO_URL:-https://github.com/nunovinhas-creator/goal-roi.git}"
KEEP="${GOAL_ROI_KEEP_DAYS:-30}"
say() { printf '%s\n' "goal-roi-selos: $*"; }
die() { printf '%s\n' "goal-roi-selos: ERRO: $*" >&2; exit 1; }

case "${PREFIX:-}" in *com.termux*) ;; *) [ "${TERMUX_TEST:-}" = 1 ] || die "corre só no Termux (ou TERMUX_TEST=1 nos testes)";; esac

# --- Não interferir com o Brain nem com o Obsidian -------------------------------------------------------------
case "$BASE" in /*) ;; *) die "GOAL_ROI_SEALS_HOME tem de ser absoluto";; esac
protected=$(
  { find "$HOME" -maxdepth 5 -name brain-sync.sh -type f 2>/dev/null | while read -r f; do dirname "$f"; done
    find "$HOME" -maxdepth 5 -name .obsidian -type d 2>/dev/null | while read -r d; do dirname "$d"; done; } | sort -u)
for p in $protected; do
  case "$BASE/" in "$p"/*) die "a pasta $BASE está dentro de $p (Brain/Obsidian); escolha outra";; esac
  case "$p/" in "$BASE"/*) die "a pasta do Brain/Obsidian $p ficaria dentro de $BASE; escolha outra";; esac
done

# --- Pacotes -------------------------------------------------------------------------------------------------
if [ "${TERMUX_TEST:-}" != 1 ]; then
  pkg install -y python git xz-utils cronie termux-services util-linux
fi
for c in python3 git xz crontab flock sh; do command -v "$c" >/dev/null 2>&1 || die "falta o comando $c"; done
SH=$(command -v sh)   # no Android não existe /bin/sh: usa-se o caminho real (no Termux, $PREFIX/bin/sh)

# --- Clone próprio, só de leitura ------------------------------------------------------------------------------
mkdir -p "$BASE/bin" "$BASE/logs" "$BASE/selos"
if [ ! -d "$BASE/repo/.git" ]; then
  git clone -q --filter=tree:0 --single-branch --branch main --no-checkout "$URL" "$BASE/repo"
fi
git -C "$BASE/repo" config remote.origin.pushurl "DISABLED-read-only"   # nunca faz push
git -C "$BASE/repo" fetch -q origin main
git -C "$BASE/repo" show origin/main:ops/seal/seal.py > "$BASE/bin/seal.py.new"
mv "$BASE/bin/seal.py.new" "$BASE/bin/seal.py"

# --- Ciclo de 12 h -------------------------------------------------------------------------------------------
cat > "$BASE/bin/goal-roi-selo" <<RUN
#!$SH
# Um ciclo da cópia selada. Registo em $BASE/logs/selo.log; falhas em $BASE/logs/falhas.jsonl. Gerado por install.sh.
BASE='$BASE'; KEEP='$KEEP'
LOG="\$BASE/logs/selo.log"; FAIL="\$BASE/logs/falhas.jsonl"
[ -f "\$LOG" ] && [ "\$(wc -c < "\$LOG")" -gt 1048576 ] && mv "\$LOG" "\$LOG.1"
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
fail() {
  printf '{"utc":"%s","passo":"%s","codigo":%s}\n' "\$(now)" "\$1" "\$2" >> "\$FAIL"
  command -v termux-notification >/dev/null 2>&1 && termux-notification --title "goal-roi: cópia selada falhou" --content "passo \$1, código \$2" >/dev/null 2>&1
  exit "\$2"
}
exec 9>"\$BASE/.lock"
flock -n 9 || { printf '%s ciclo anterior ainda a correr; nada feito\n' "\$(now)" >> "\$LOG"; exit 0; }
cd "\$BASE" || fail inicio 1
{
  printf '%s início\n' "\$(now)"
  nice -n 10 python3 bin/seal.py seal --repo repo --dest selos --fetch --ref origin/main || fail selar \$?
  nice -n 10 python3 bin/seal.py verify --dest selos --max-age-h 13 || fail verificar \$?
  nice -n 10 python3 bin/seal.py prune --dest selos --keep-days "\$KEEP" || fail podar \$?
  printf '%s fim\n' "\$(now)"
} >> "\$LOG" 2>&1
RUN
chmod 700 "$BASE/bin/goal-roi-selo"

# --- Agendamento: uma linha marcada, minuto afastado do brain-sync ----------------------------------------------
current=$(crontab -l 2>/dev/null || true)
others=$(printf '%s\n' "$current" | grep -v "$TAG" || true)
busy=$(printf '%s\n' "$others" | grep 'brain-sync' | awk '{print $1}' | grep -E '^[0-9]+$' || true)
if printf '%s\n' "$others" | grep 'brain-sync' | awk '{print $1}' | grep -qvE '^[0-9]+$'; then
  say "aviso: o brain-sync tem um minuto não fixo; os locks são separados e o ciclo corre com nice"
fi
minute=17
for m in 17 41 5 29 53; do
  ok=1
  for b in $busy; do d=$(( (m - b + 60) % 60 )); [ "$d" -lt 20 ] || [ "$d" -gt 40 ] && ok=0; done
  [ "$ok" = 1 ] && { minute=$m; break; }
done
line="$minute 5,17 * * * $SH $BASE/bin/goal-roi-selo $TAG"
{ [ -n "$others" ] && printf '%s\n' "$others"; printf '%s\n' "$line"; } | crontab -
after=$(crontab -l | grep -v "$TAG" || true)
[ "$after" = "$others" ] || die "o crontab existente mudou; reponha manualmente (crontab -e)"
if [ "${TERMUX_TEST:-}" != 1 ]; then sv-enable crond >/dev/null 2>&1 || say "ative o crond: sv-enable crond"; fi
say "instalado em $BASE; agendado: $line"
say "primeiro ciclo manual: $BASE/bin/goal-roi-selo; verificação: python3 $BASE/bin/seal.py verify --dest $BASE/selos"
