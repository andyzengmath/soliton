#!/usr/bin/env bash
# bench/crb/run-sphinx-actionability.sh
#
# Sphinx actionability re-judge against the Phase 5.2 corpus, per
# bench/crb/sphinx-actionability-spec.md. Adds an actionable | non_actionable |
# uncertain rating to every TP, surfacing whether Soliton's true-positive
# findings would actually drive a code change (vs being correct-but-vague).
#
# Re-uses Phase 5.2 review markdown (no Soliton re-dispatch). Costs only the
# Azure OpenAI judge pass (~$15). Output goes to evaluations_sphinx.json so it
# does NOT clobber the standard Phase 5.2 evaluations.json (F1=0.313).
#
# Prereqs:
#   - bench/crb/phase5_2-reviews/ populated (50 .md reviews from PR #56 / #58)
#   - Sibling checkout at ../code-review-benchmark/ on branch
#     `feat/sphinx-actionability-judge` with step3_judge_comments.py extended to
#     support `--sphinx`
#   - Azure-AD session active (`az login` or managed-identity available)
#
# Usage:
#   bash bench/crb/run-sphinx-actionability.sh

set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
CRB_ROOT="$(dirname "$REPO_ROOT")/code-review-benchmark/offline"

if [ ! -d "$CRB_ROOT" ]; then
  echo "error: $CRB_ROOT not found" >&2; exit 1
fi

REVIEW_COUNT=$(find "$REPO_ROOT/bench/crb/phase5_2-reviews" -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
if [ "$REVIEW_COUNT" -lt 50 ]; then
  echo "error: only $REVIEW_COUNT reviews in phase5_2-reviews/ (expected 50)" >&2
  exit 1
fi

# Confirm sibling checkout has the --sphinx-aware step3
if ! grep -q "sphinx_mode" "$CRB_ROOT/code_review_benchmark/step3_judge_comments.py"; then
  echo "error: sibling step3_judge_comments.py is missing --sphinx support" >&2
  echo "       check out feat/sphinx-actionability-judge in ../code-review-benchmark" >&2
  exit 1
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-https://aoai-l-eastus2.openai.azure.com/}"
export AZURE_OPENAI_DEPLOYMENT="${AZURE_OPENAI_DEPLOYMENT:-gpt-5.2}"
export AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-2025-01-01-preview}"
export MARTIAN_MODEL="azure_${AZURE_OPENAI_DEPLOYMENT}"

SAFE_MODEL="$(printf '%s' "$MARTIAN_MODEL" | tr '/:' '__')"
EVALS_OUT="results/$SAFE_MODEL/evaluations_sphinx.json"

echo "============================================================="
echo "Soliton × CRB · Sphinx actionability re-judge (Phase 5.2 corpus)"
echo "  reviews found : $REVIEW_COUNT"
echo "  judge model   : $AZURE_OPENAI_DEPLOYMENT"
echo "  output        : $EVALS_OUT"
echo "============================================================="

echo ""
echo "[1/4] build benchmark_data.json from phase5_2-reviews/"
python3 "$REPO_ROOT/bench/crb/build_benchmark_data.py" \
  --reviews-dir "$REPO_ROOT/bench/crb/phase5_2-reviews" \
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
echo "Sphinx re-judge complete."
echo "  Evaluations : $CRB_ROOT/$EVALS_OUT"
echo "  Standard F1 : $CRB_ROOT/results/$SAFE_MODEL/evaluations.json (preserved)"
echo "  Next        : python3 bench/crb/analyze-sphinx.py"
echo "============================================================="
