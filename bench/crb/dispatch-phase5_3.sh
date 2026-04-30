#!/usr/bin/env bash
# bench/crb/dispatch-phase5.sh
#
# Phase 5.3: realist-check + silent-failure + comment-accuracy + cross-file-impact graph-signal change (disable `test-quality` and
# `consistency` by default via skipAgents). Re-runs the Phase 3.5 50-PR
# corpus against the current SKILL.md to measure the aggregate F1 effect
# of the per-agent FP concentration found in bench/crb/AUDIT_10PR.md
# §Appendix A.
#
# Same PRs, same driver, same budget ceiling as Phase 4c.1 — only the
# hardcoded skipAgents default changed.
#
# Usage:
#   bash bench/crb/dispatch-phase5.sh                 # sequential
#   CONCURRENCY=3 bash bench/crb/dispatch-phase5.sh   # parallel
#
# Resumption is safe; only missing/empty reviews regenerate.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
DISPATCH_LIST="$REPO_ROOT/bench/crb/phase3-dispatch-list.txt"
OUTPUT_DIR="$REPO_ROOT/bench/crb/phase5_3-reviews"
CONCURRENCY="${CONCURRENCY:-1}"

export MAX_BUDGET_USD="${MAX_BUDGET_USD:-10}"

if [ ! -f "$DISPATCH_LIST" ]; then
  echo "error: dispatch list not found at $DISPATCH_LIST" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

TOTAL=$(wc -l < "$DISPATCH_LIST" | tr -d ' ')
EXISTING=0
PENDING_LIST=()

while IFS=' ' read -r UPSTREAM PR_NUM SLUG; do
  [ -z "${UPSTREAM:-}" ] && continue
  OUT="$OUTPUT_DIR/$SLUG.md"
  if [ -s "$OUT" ]; then
    EXISTING=$((EXISTING + 1))
  else
    PENDING_LIST+=("$UPSTREAM $PR_NUM $SLUG")
  fi
done < "$DISPATCH_LIST"

PENDING=${#PENDING_LIST[@]}

echo "============================================================="
echo "Soliton × CRB · Phase 5.3 dispatch"
echo "  lever           : all v2 wirings active (realist-check + silent-failure + comment-accuracy + graphSignals.dependencyBreaks)"
echo "  total PRs       : $TOTAL"
echo "  already done    : $EXISTING"
echo "  to generate     : $PENDING"
echo "  concurrency     : $CONCURRENCY"
echo "  output dir      : $OUTPUT_DIR"
echo "============================================================="

if [ "$PENDING" -eq 0 ]; then
  echo "all 50 reviews already present — nothing to do."
  exit 0
fi

DISPATCHED=0
ACTIVE_PIDS=()

run_one () {
  local spec="$1"
  local upstream pr_num slug
  read -r upstream pr_num slug <<< "$spec"
  local log="$OUTPUT_DIR/.$slug.log"

  echo "  → [$upstream#$pr_num → $slug] starting"
  OUTPUT_DIR="bench/crb/phase5_3-reviews" \
    bash "$REPO_ROOT/bench/crb/run-poc-review.sh" \
      "$upstream" "$pr_num" "$slug" \
      > "$log" 2>&1 \
    && echo "  ✓ [$slug] done" \
    || echo "  ✗ [$slug] FAILED (see $log)"
}

for spec in "${PENDING_LIST[@]}"; do
  DISPATCHED=$((DISPATCHED + 1))
  if [ "$CONCURRENCY" -le 1 ]; then
    echo "[$DISPATCHED/$PENDING] $spec"
    run_one "$spec"
  else
    while [ "${#ACTIVE_PIDS[@]}" -ge "$CONCURRENCY" ]; do
      NEW_ACTIVE=()
      for pid in "${ACTIVE_PIDS[@]}"; do
        kill -0 "$pid" 2>/dev/null && NEW_ACTIVE+=("$pid")
      done
      ACTIVE_PIDS=("${NEW_ACTIVE[@]}")
      [ "${#ACTIVE_PIDS[@]}" -lt "$CONCURRENCY" ] && break
      sleep 5
    done
    echo "[$DISPATCHED/$PENDING] $spec (background)"
    run_one "$spec" &
    ACTIVE_PIDS+=("$!")
  fi
done

if [ "${#ACTIVE_PIDS[@]}" -gt 0 ]; then
  echo "waiting for ${#ACTIVE_PIDS[@]} background jobs to finish..."
  wait
fi

FINAL=$(find "$OUTPUT_DIR" -maxdepth 1 -name "*.md" | wc -l | tr -d ' ')
echo "============================================================="
echo "Dispatch complete."
echo "  reviews landed : $FINAL / $TOTAL"
echo "  output dir     : $OUTPUT_DIR"
echo "Next: run \`bash bench/crb/run-phase5_3-pipeline.sh\` to score."
echo "============================================================="
