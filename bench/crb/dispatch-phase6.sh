#!/usr/bin/env bash
# bench/crb/dispatch-phase6.sh
#
# Generate 50 Soliton reviews for Phase 6 (Java-only L5 cross-file
# retrieval) by looping over phase3-dispatch-list.txt and invoking
# run-poc-review.sh with OUTPUT_DIR=bench/crb/phase6-reviews.
# Matches the Phase 5.2 / Phase 4c configuration (same 50 PRs, same
# driver) so F1 deltas are attributable to the Phase 6a code changes
# on main, not config drift.
#
# Pre-condition: .claude/soliton.local.md MUST set
#   agents:
#     cross_file_retrieval_java:
#       enabled: true
# This is the opt-in flag added in PR #104 (Phase 6a). Without it, the
# correctness agent's §2.5 Cross-File Retrieval section is skipped and
# the run measures Phase 5.2 baseline + zero Phase 6 differential.
#
# Supports resumption: if phase6-reviews/<slug>.md already exists
# and is non-empty, the dispatch is skipped.
#
# Usage:
#   # Sequential (safest — 1 review at a time, 1-2 hours total)
#   bash bench/crb/dispatch-phase6.sh
#
#   # Parallel (N concurrent claude-p invocations; faster but stresses
#   # the local env)
#   CONCURRENCY=4 bash bench/crb/dispatch-phase6.sh
#
# Prereqs:
#   - gh CLI authenticated for github.com
#   - Claude Code CLI (`claude`) logged in with a plan that supports
#     programmatic `claude -p` calls
#   - Running from inside the soliton repo clone
#   - main@<post-PR-#104> (Phase 6a code merged)
#   - .claude/soliton.local.md configured per PHASE_6_DESIGN.md § Pre-condition
#
# Resumption:
#   Re-running is safe. Only missing/empty reviews are re-generated.
#
# Cost expectation: ~$140 single bounded run (50 PRs × Sonnet review +
# GPT-5.2 judge). Pre-registered SHIP criteria in PHASE_6_DESIGN.md:
#   ship  : aggregate F1 ≥ 0.322 AND Java F1 ≥ 0.318 AND no language
#           regression > 2σ_lang (0.036)
#   hold  : aggregate 0.305-0.321; Java 0.290-0.317
#   close : aggregate < 0.305 OR Java < 0.290 OR any language > 2σ_lang

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
DISPATCH_LIST="$REPO_ROOT/bench/crb/phase3-dispatch-list.txt"
OUTPUT_DIR="$REPO_ROOT/bench/crb/phase6-reviews"
CONCURRENCY="${CONCURRENCY:-1}"

# Per-review budget: Phase 5.2 used $2-3 per review. Phase 6a's
# Java-only L5 retrieval adds ~5-10s per Java-touching PR but minimal
# token overhead (cap of 8 git grep + Read pairs per agent invocation).
# Default $10 ceiling matches Phase 4c convention; actual per-review
# spend should remain ~$2-4 in practice.
export MAX_BUDGET_USD="${MAX_BUDGET_USD:-10}"

# Sanity-check: warn if .claude/soliton.local.md doesn't have the opt-in
# flag set. The flag is the gate that enables Phase 6a's §2.5 conditional
# in agents/correctness.md. If the user forgot to set it, this run
# measures Phase 5.2 baseline behavior + nothing else.
LOCAL_CONFIG="$REPO_ROOT/.claude/soliton.local.md"
if [ ! -f "$LOCAL_CONFIG" ]; then
  echo "warning: $LOCAL_CONFIG not found; Phase 6a conditional will not fire" >&2
  echo "         create per templates/soliton.local.md and set" >&2
  echo "         agents.cross_file_retrieval_java.enabled: true before dispatch" >&2
elif ! grep -q "cross_file_retrieval_java:" "$LOCAL_CONFIG" 2>/dev/null; then
  echo "warning: $LOCAL_CONFIG does not enable cross_file_retrieval_java;" >&2
  echo "         Phase 6a §2.5 conditional will not fire — this run will" >&2
  echo "         measure Phase 5.2 baseline behavior only." >&2
elif ! grep -q "cross_file_retrieval_java:" "$LOCAL_CONFIG" -A 2 2>/dev/null | grep -q "enabled: true"; then
  echo "warning: $LOCAL_CONFIG has cross_file_retrieval_java block but" >&2
  echo "         enabled is not set to true — Phase 6a conditional will not fire" >&2
fi

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
echo "Soliton × CRB · Phase 6 dispatch (Java-only L5 retrieval)"
echo "  total PRs       : $TOTAL"
echo "  already done    : $EXISTING"
echo "  to generate     : $PENDING"
echo "  concurrency     : $CONCURRENCY"
echo "  output dir      : $OUTPUT_DIR"
echo "  baseline        : Phase 5.2 F1=0.313 (CRB number of record)"
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
  OUTPUT_DIR="bench/crb/phase6-reviews" \
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
echo "Next: run \`bash bench/crb/run-phase6-pipeline.sh\` to score."
echo "============================================================="
