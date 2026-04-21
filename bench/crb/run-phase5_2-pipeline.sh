#!/usr/bin/env bash
# bench/crb/run-phase5_2-pipeline.sh
#
# Phase 5.2: validate the footnote-title strip hypothesis without re-dispatching
# Soliton reviews. Reads bench/crb/phase5_2-reviews/ (produced by
# strip-footnote-titles.py from the existing Phase 5 reviews) and runs the
# CRB judge pipeline (step2 extract → step2_5 dedup → step3 judge). Costs
# only the Azure OpenAI judge portion (~$15) because the Soliton side is
# re-used from Phase 5.
#
# Pre-registered ship criterion (see bench/crb/AUDIT_10PR.md §Appendix A
# update): F1 >= 0.305 AND recall >= 0.52 AND no lang reg > 0.03 vs Phase 5.
#
# Usage:
#   python3 bench/crb/strip-footnote-titles.py   # produce phase5_2-reviews/
#   bash bench/crb/run-phase5_2-pipeline.sh

set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
CRB_ROOT="$(dirname "$REPO_ROOT")/code-review-benchmark/offline"

if [ ! -d "$CRB_ROOT" ]; then
  echo "error: $CRB_ROOT not found" >&2; exit 1
fi

REVIEW_COUNT=$(find "$REPO_ROOT/bench/crb/phase5_2-reviews" -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
if [ "$REVIEW_COUNT" -lt 50 ]; then
  echo "error: only $REVIEW_COUNT reviews in phase5_2-reviews/ (expected 50)" >&2
  echo "       run 'python3 bench/crb/strip-footnote-titles.py' first" >&2
  exit 1
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-https://aoai-l-eastus2.openai.azure.com/}"
export AZURE_OPENAI_DEPLOYMENT="${AZURE_OPENAI_DEPLOYMENT:-gpt-5.2}"
export AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-2025-01-01-preview}"
export MARTIAN_MODEL="azure_${AZURE_OPENAI_DEPLOYMENT}"

echo "============================================================="
echo "Soliton × CRB · Phase 5.2 pipeline (footnote-title strip)"
echo "  reviews found : $REVIEW_COUNT"
echo "  judge model   : $AZURE_OPENAI_DEPLOYMENT"
echo "  baseline      : Phase 5 F1=0.300 (recall 0.522)"
echo "============================================================="

echo ""
echo "[1/4] build benchmark_data.json"
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
echo "[4/4] step3 judge comments"
DEDUP="results/$(printf '%s' "$MARTIAN_MODEL" | tr '/:' '__')/dedup_groups.json"
if [ -f "$DEDUP" ]; then
  uv run python -m code_review_benchmark.step3_judge_comments --tool soliton --force --dedup-groups "$DEDUP"
else
  uv run python -m code_review_benchmark.step3_judge_comments --tool soliton --force
fi

echo ""
echo "============================================================="
echo "Pipeline complete."
echo "  Evaluations: $CRB_ROOT/results/$MARTIAN_MODEL/evaluations.json"
echo "  Next: python3 bench/crb/analyze-phase5.py (compares vs Phase 3.5 — adjust mentally vs Phase 5)"
echo "============================================================="
