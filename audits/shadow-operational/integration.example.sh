#!/usr/bin/env bash
# PREPARED EXAMPLE ONLY. Not called by any workflow; never run without approved installation.
# The existing collector keeps writing data/messages.jsonl and existing result caches.
# This sidecar consumes those local observations; no Telegram client/result API is added.
set -euo pipefail
: "${SHADOW_PUBLISHER_CHECKOUT:?dedicated full-history checkout on shadow/h1-h2-operational required}"
: "${SHADOW_SOURCE_CHECKOUT:?existing collector checkout required}"
: "${SHADOW_AUTHORIZATION_FILE:?reviewed explicit installed/authorized record required}"
: "${SHADOW_FAILURE_OUTBOX:?new output ZIP outside production directories required}"

# Before launching this command, restore the latest unconsumed failure artifact with:
# python -m scangoal.shadow_ops restore-outbox --repo "$SHADOW_PUBLISHER_CHECKOUT" \
#   --source "$SHADOW_SOURCE_CHECKOUT" --authorization "$SHADOW_AUTHORIZATION_FILE" --outbox "$archive"
# On first authorized installation only, create/publish a NEW operational protocol with:
# python -m scangoal.shadow_ops activate --repo "$SHADOW_PUBLISHER_CHECKOUT" \
#   --source "$SHADOW_SOURCE_CHECKOUT" --authorization "$SHADOW_AUTHORIZATION_FILE"

on_exit() {
  code=$?
  if [ "$code" -ne 0 ]; then
    python -m scangoal.shadow_ops export-outbox --repo "$SHADOW_PUBLISHER_CHECKOUT" \
      --source "$SHADOW_SOURCE_CHECKOUT" --authorization "$SHADOW_AUTHORIZATION_FILE" \
      --outbox "$SHADOW_FAILURE_OUTBOX" || exit 3
    # The authorized existing job must upload this capped ZIP as a failure artifact (retention 1 day).
    # Missing/failed artifact preservation must fail the job, not be marked continue-on-error.
  fi
  exit "$code"
}
trap on_exit EXIT
python -m scangoal.shadow_ops supervise --repo "$SHADOW_PUBLISHER_CHECKOUT" \
  --source "$SHADOW_SOURCE_CHECKOUT" --authorization "$SHADOW_AUTHORIZATION_FILE" \
  --seconds 1080 --poll-seconds 10
