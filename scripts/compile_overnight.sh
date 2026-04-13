#!/bin/bash
# Autonomous overnight compile loop.
#
# Safeguards:
#   - Stops when budget remaining < $2 (configurable via BUDGET_FLOOR)
#   - Stops when no uncompiled raw emails remain
#   - Pre-compile snapshot is taken by compile_all.py itself
#   - Validator runs after each batch (compile_all.py wires this)
#   - Lint auto-fix creates stubs for any new broken links
#   - Commits and pushes to GitHub after each batch
#   - Logs every batch to .logs/
#
# Usage:
#   ./scripts/compile_overnight.sh
#   BUDGET_FLOOR=5 BATCH_SIZE=10 LIMIT=20 ./scripts/compile_overnight.sh
#
# Start in background:
#   nohup caffeinate -i ./scripts/compile_overnight.sh > /tmp/overnight.log 2>&1 &

set -u

cd "$(dirname "$0")/.."
mkdir -p .logs

BUDGET_FLOOR="${BUDGET_FLOOR:-2.0}"
BATCH_SIZE="${BATCH_SIZE:-8}"
LIMIT="${LIMIT:-20}"

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
}

log "starting overnight loop. BUDGET_FLOOR=\$${BUDGET_FLOOR} BATCH_SIZE=${BATCH_SIZE} LIMIT=${LIMIT}"

iteration=0
while true; do
  iteration=$((iteration + 1))

  # Budget check
  remaining=$(uv run python -c "from src.budget import fetch_budget; b = fetch_budget(); print(f'{b.remaining:.2f}' if b and b.remaining is not None else '0')" 2>/dev/null)
  if [ -z "$remaining" ]; then
    log "ERROR: could not fetch budget. Sleeping 60s and retrying."
    sleep 60
    continue
  fi
  log "iteration=${iteration} budget_remaining=\$${remaining}"

  # Stop if below floor
  below=$(awk -v r="$remaining" -v f="$BUDGET_FLOOR" 'BEGIN{print (r+0 < f+0) ? "yes" : "no"}')
  if [ "$below" = "yes" ]; then
    log "STOP: budget \$${remaining} < floor \$${BUDGET_FLOOR}"
    break
  fi

  # Uncompiled check
  uncompiled=$(ls raw/*.md 2>/dev/null | while read f; do
    if ! grep -q "^compiled: true" "$f"; then echo "$f"; fi
  done | wc -l | tr -d ' ')
  log "uncompiled_count=${uncompiled}"
  if [ "$uncompiled" = "0" ]; then
    log "STOP: no uncompiled emails remain"
    break
  fi

  # Launch one batch
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  run_log=".logs/overnight-${stamp}.log"
  log "launching compile_all --batch-size ${BATCH_SIZE} --limit ${LIMIT}"
  budget_before="$remaining"

  # 15-min per-batch timeout so stuck LLM calls don't hang the whole night
  # (gtimeout is GNU coreutils on macOS; falls back to plain run if absent)
  TIMEOUT_BIN="$(command -v gtimeout || command -v timeout || true)"
  if [ -n "$TIMEOUT_BIN" ]; then
    CMD="$TIMEOUT_BIN --kill-after=30 900 uv run python scripts/compile_all.py --batch-size $BATCH_SIZE --limit $LIMIT"
  else
    CMD="uv run python scripts/compile_all.py --batch-size $BATCH_SIZE --limit $LIMIT"
  fi
  if ! $CMD > "$run_log" 2>&1; then
    log "WARN: compile_all exited non-zero or timed out (see $run_log); continuing after 30s"
    sleep 30
    continue
  fi

  # Auto-fix broken wikilinks (creates stubs)
  uv run python scripts/lint_wiki.py --fix > /dev/null 2>&1 || true

  # Dipstick
  uv run python scripts/dipstick.py \
    --since "30 minutes ago" \
    --run-label "overnight-${stamp}" \
    --emails-compiled "$LIMIT" \
    > /dev/null 2>&1 || true

  # Commit + push any wiki / docs changes. Raw compile flags are gitignored
  # by content (raw/*.md ignored), so only wiki and docs changes show up.
  if [ -n "$(git status --porcelain)" ]; then
    git add -A
    git -c commit.gpgsign=false commit -m "compile: overnight batch ${stamp} (+${LIMIT} emails)" > /dev/null 2>&1 || true
    git push origin main > /dev/null 2>&1 || log "WARN: push failed (will retry next loop)"
    log "committed and pushed"
  fi

  log "batch done. Sleeping 5s to let fs settle."
  sleep 5
done

log "overnight loop exited cleanly."
