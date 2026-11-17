#!/usr/bin/env bash
# Dormant example for a dedicated SHADOW runner with its own concurrency group.
# The source checkout is read-only. Run from the reviewed code checkout.
set -euo pipefail
: "${SHADOW_PUBLISHER_CHECKOUT:?dedicated full-history checkout required}"
: "${SHADOW_SOURCE_CHECKOUT:?read-only collector source required}"
: "${SHADOW_AUTHORIZATION_FILE:?reviewed authorization required}"
: "${SHADOW_OUTBOX:?new ZIP outside both checkouts required}"

# Restore the last unconsumed outbox before each fresh runner starts.
# Retain the new outbox even on nonzero exit, within the SHADOW job's own budget.
# The process keeps 15 of these 480 seconds for local outbox preservation.
# The outer watchdog also bounds interpreter startup and abnormal teardown.
timeout --signal=TERM --kill-after=5s 485s python -m scangoal.shadow_ops supervise \
  --repo "$SHADOW_PUBLISHER_CHECKOUT" --source "$SHADOW_SOURCE_CHECKOUT" \
  --authorization "$SHADOW_AUTHORIZATION_FILE" --outbox "$SHADOW_OUTBOX" \
  --seconds 480 --poll-seconds 10
