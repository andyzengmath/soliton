#!/usr/bin/env bash
# bench/crb/dispatch-phase3_5_1.sh
#
# Generate 50 Soliton reviews for Phase 3.5.1 by looping over
# phase3-dispatch-list.txt and invoking run-poc-review.sh with
# OUTPUT_DIR=bench/crb/phase3_5_1-reviews. Matches the Phase 3.5
# configuration (same 50 PRs, same driver) so F1 deltas are
# attributable to the 4a+4b code changes on main, not config drift.
#
# Supports resumption: if phase3_5_1-reviews/<slug>.md already exists
# and is non-empty, the dispatch is skipped.
#
# Usage:
#   # Sequential (safest — 1 review at a time, 1-2 hours total)
#   bash bench/crb/dispatch-phase3_5_1.sh
#
#   # Parallel (N concurrent claude-p invocations; faster but stresses
#   # the local env)
#   CONCURRENCY=4 bash bench/crb/dispatch-phase3_5_1.sh
#
# Prereqs:
#   - gh CLI authenticated for github.com
#   - Claude Code CLI (`claude`) logged in with a plan that supports
#     programmatic `claude -p` calls
#   - Running from inside the soliton repo clone
#   - main@d7ddfd0 or later (Phase 4a + 4b both merged)
#
# Resumption:
#   Re-running is safe. Only missing/empty reviews are re-generated.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
DISPATCH_LIST="$REPO_ROOT/bench/crb/phase3-dispatch-list.txt"
OUTPUT_DIR="$REPO_ROOT/bench/crb/phase3_5_1-reviews"
CONCURRENCY="${CONCURRENCY:-1}"

# Per-review budget: Phase 3.5 used $2, but 4a (cross-file retrieval)
# and 4b (§2.5 AST pre-check subprocess) add per-review overhead that
# pushes complex PRs past $2. Default $10 is a generous ceiling, not a
# target — the actual per-review spend averages ~$2-4 in practice.
# Operators can override via MAX_BUDGET_USD.
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
echo "Soliton × CRB · Phase 3.5.1 dispatch"
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
  local out="$OUTPUT_DIR/$slug.md"
  local log="$OUTPUT_DIR/.$slug.log"

  echo "  → [$upstream#$pr_num → $slug] starting"
  OUTPUT_DIR="bench/crb/phase3_5_1-reviews" \
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
    # Parallel: throttle to $CONCURRENCY active backgrounded jobs.
    while [ "${#ACTIVE_PIDS[@]}" -ge "$CONCURRENCY" ]; do
      # Prune completed.
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

# Wait for any stragglers.
if [ "${#ACTIVE_PIDS[@]}" -gt 0 ]; then
  echo "waiting for ${#ACTIVE_PIDS[@]} background jobs to finish..."
  wait
fi

FINAL=$(find "$OUTPUT_DIR" -maxdepth 1 -name "*.md" | wc -l | tr -d ' ')
echo "============================================================="
echo "Dispatch complete."
echo "  reviews landed : $FINAL / $TOTAL"
echo "  output dir     : $OUTPUT_DIR"
echo "Next: run \`bash bench/crb/run-phase3_5_1-pipeline.sh\` to score."
echo "============================================================="
