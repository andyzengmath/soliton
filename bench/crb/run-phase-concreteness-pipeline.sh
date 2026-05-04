#!/usr/bin/env bash
# bench/crb/run-phase-concreteness-pipeline.sh
#
# Score the Concreteness experiment reviews (50 .md files in
# bench/crb/phase-concreteness-reviews/) with the Sphinx-extended judge.
# Writes evaluations_concreteness_sphinx.json (separate from PR #133's
# evaluations_sphinx.json so both can co-exist for delta computation).
#
# Pre-registered SHIP/HOLD/CLOSE bands per CONCRETENESS_DESIGN.md § 4.
#
# Prereqs:
#   - bench/crb/phase-concreteness-reviews/ populated (50 .md files; run
#     dispatch-phase-concreteness.sh first)
#   - Sibling checkout at ../code-review-benchmark/ on a branch with the
#     --sphinx-aware step3_judge_comments.py (Sphinx Phase 2 patch from
#     PR #133's bench/crb/sphinx-step3-judge-patch.diff applied)
#   - Azure-AD session active (`az login` or managed-identity available)
#
# Cost expectation: ~$11 (re-uses PR #133's Sphinx pipeline; same judge
# pass against new reviews).
#
# Usage:
#   bash bench/crb/run-phase-concreteness-pipeline.sh

set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
CRB_ROOT="$(dirname "$REPO_ROOT")/code-review-benchmark/offline"

if [ ! -d "$CRB_ROOT" ]; then
  echo "error: $CRB_ROOT not found" >&2; exit 1
fi

REVIEW_COUNT=$(find "$REPO_ROOT/bench/crb/phase-concreteness-reviews" -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
if [ "$REVIEW_COUNT" -lt 50 ]; then
  echo "error: only $REVIEW_COUNT reviews in phase-concreteness-reviews/ (expected 50)" >&2
  echo "       run \`bash bench/crb/dispatch-phase-concreteness.sh\` first" >&2
  exit 1
fi

# Confirm sibling checkout has the --sphinx-aware step3
if ! grep -q "sphinx_mode" "$CRB_ROOT/code_review_benchmark/step3_judge_comments.py"; then
  echo "error: sibling step3_judge_comments.py is missing --sphinx support" >&2
  echo "       apply bench/crb/sphinx-step3-judge-patch.diff or check out" >&2
  echo "       feat/sphinx-actionability-judge in ../code-review-benchmark" >&2
  exit 1
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-https://aoai-l-eastus2.openai.azure.com/}"
export AZURE_OPENAI_DEPLOYMENT="${AZURE_OPENAI_DEPLOYMENT:-gpt-5.2}"
export AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-2025-01-01-preview}"
export MARTIAN_MODEL="azure_${AZURE_OPENAI_DEPLOYMENT}"

SAFE_MODEL="$(printf '%s' "$MARTIAN_MODEL" | tr '/:' '__')"
EVALS_OUT="results/$SAFE_MODEL/evaluations_concreteness_sphinx.json"

echo "============================================================="
echo "Soliton × CRB · Concreteness scoring (Sphinx-extended judge)"
echo "  reviews found : $REVIEW_COUNT"
echo "  judge model   : $AZURE_OPENAI_DEPLOYMENT"
echo "  output        : $EVALS_OUT"
echo "  baseline      : sphinx-prompt F1=0.330, actionable=30.6% (PR #133)"
echo "  SHIP gate     : Δ actionable ≥ +10pp AND F1 ≥ 0.321 AND no per-lang > 0.036"
echo "============================================================="

echo ""
echo "[1/4] build benchmark_data.json from phase-concreteness-reviews/"
python3 "$REPO_ROOT/bench/crb/build_benchmark_data.py" \
  --reviews-dir "$REPO_ROOT/bench/crb/phase-concreteness-reviews" \
  --golden-dir "$CRB_ROOT/golden_comments" \
  --benchmark-prs "$REPO_ROOT/bench/crb/benchmark-prs.json" \
  --output "$CRB_ROOT/results/benchmark_data.json"

echo ""
echo "[2/4] step2 extract candidates (soliton, gpt-5.2)"
cd "$CRB_ROOT"
uv run python -m code_review_benchmark.step2_extract_comments --tool soliton --force

echo ""
echo "[3/4] step2.5 dedup candidates"
uv run python -m code_review_benchmark.step2_5_dedup_candidates --tool soliton --force

echo ""
echo "[4/4] step3 judge with Sphinx actionability addendum"
DEDUP="results/$SAFE_MODEL/dedup_groups.json"
if [ -f "$DEDUP" ]; then
  uv run python -m code_review_benchmark.step3_judge_comments \
    --tool soliton --force --structured --sphinx \
    --evaluations-file "$EVALS_OUT" \
    --dedup-groups "$DEDUP"
else
  uv run python -m code_review_benchmark.step3_judge_comments \
    --tool soliton --force --structured --sphinx \
    --evaluations-file "$EVALS_OUT"
fi

echo ""
echo "============================================================="
echo "Concreteness scoring complete."
echo "  Evaluations : $CRB_ROOT/$EVALS_OUT"
echo "  Baseline    : $CRB_ROOT/results/$SAFE_MODEL/evaluations_sphinx.json (PR #133)"
echo "  Next        : python3 bench/crb/analyze-sphinx.py --evals \\"
echo "                $CRB_ROOT/$EVALS_OUT"
echo "                Apply CONCRETENESS_DESIGN.md § 4 decision rule."
echo "============================================================="
