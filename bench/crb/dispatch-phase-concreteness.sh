#!/usr/bin/env bash
# bench/crb/dispatch-phase-concreteness.sh
#
# Generate 50 Soliton reviews for the Concreteness prompt-tuning experiment
# per `bench/crb/CONCRETENESS_DESIGN.md`. Mirrors dispatch-phase6.sh's
# skeleton but writes reviews to phase-concreteness-reviews/ and runs against
# the agent prompts on the current branch (feat/concreteness-prompt-tuning),
# which has the suggestion-field tightening per § 3.1 + § 3.2 of the design.
#
# Pre-condition: this script MUST be run from a checkout where
# `agents/correctness.md` and `agents/hallucination.md` have the post-design
# suggestion-field text (verify with `grep -q 'A LITERAL code patch' agents/correctness.md`
# and `grep -q 'A LITERAL replacement' agents/hallucination.md`).
#
# No Phase 6 graph signals or v2.1.0 wirings — those default-OFF flags stay
# OFF (per CONCRETENESS_DESIGN.md § 8). NO SHIM_DIR config-injection needed
# because no per-repo opt-in flag is required for this experiment.
#
# Supports resumption: if phase-concreteness-reviews/<slug>.md already
# exists and is non-empty, the dispatch is skipped.
#
# Usage:
#   # Sequential (safest — 1 review at a time, 1-2 hours total)
#   bash bench/crb/dispatch-phase-concreteness.sh
#
#   # Parallel
#   CONCURRENCY=4 bash bench/crb/dispatch-phase-concreteness.sh
#
# Cost expectation: ~$110-170 single bounded run (50 PRs × Sonnet review).
# Pre-registered SHIP criteria in CONCRETENESS_DESIGN.md § 4:
#   ship  : Δ actionable_TP_rate ≥ +10pp AND F1 ≥ 0.321 AND no language
#           regression > 2σ_lang (0.036) vs PR #133 sphinx-prompt baseline
#   close : everything else (HOLD = CLOSE per σ-aware doctrine)

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
DISPATCH_LIST="$REPO_ROOT/bench/crb/phase3-dispatch-list.txt"
OUTPUT_DIR="$REPO_ROOT/bench/crb/phase-concreteness-reviews"
CONCURRENCY="${CONCURRENCY:-1}"

export MAX_BUDGET_USD="${MAX_BUDGET_USD:-10}"

# Sanity-check: confirm the prompt edits are in place. Without them, this
# would silently measure Phase 5.2 baseline (no concreteness lever applied).
if ! grep -q "A LITERAL code patch" "$REPO_ROOT/agents/correctness.md" 2>/dev/null; then
  echo "error: agents/correctness.md does not contain the concreteness suggestion-field text." >&2
  echo "       expected NEW line 114 to start with 'suggestion: <A LITERAL code patch ready to copy.'" >&2
  echo "       checkout feat/concreteness-prompt-tuning or apply CONCRETENESS_DESIGN.md § 3.1 first." >&2
  exit 1
fi

if ! grep -q "A LITERAL replacement" "$REPO_ROOT/agents/hallucination.md" 2>/dev/null; then
  echo "error: agents/hallucination.md does not contain the concreteness suggestion-field text." >&2
  echo "       expected NEW line 110 to start with 'suggestion: <A LITERAL replacement:'" >&2
  echo "       checkout feat/concreteness-prompt-tuning or apply CONCRETENESS_DESIGN.md § 3.2 first." >&2
  exit 1
fi

if grep -q "Provide concrete fix code in every suggestion" "$REPO_ROOT/agents/correctness.md" 2>/dev/null; then
  echo "warning: agents/correctness.md still contains the old line 131 rule." >&2
  echo "         CONCRETENESS_DESIGN.md § 3.1 says delete it (now redundant with template)." >&2
  echo "         continuing anyway — the rule + template both pointing at concreteness should not regress signal." >&2
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
echo "Soliton × CRB · Concreteness dispatch (suggestion-field tightening)"
echo "  total PRs       : $TOTAL"
echo "  already done    : $EXISTING"
echo "  to generate     : $PENDING"
echo "  concurrency     : $CONCURRENCY"
echo "  output dir      : $OUTPUT_DIR"
echo "  branch          : $(git branch --show-current)"
echo "  baseline        : sphinx-prompt F1=0.330 / actionable=30.6% (PR #133)"
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
  OUTPUT_DIR="bench/crb/phase-concreteness-reviews" \
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
echo "Next: run \`bash bench/crb/run-phase-concreteness-pipeline.sh\` to score."
echo "============================================================="
