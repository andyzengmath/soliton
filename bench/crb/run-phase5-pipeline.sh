#!/usr/bin/env bash
# bench/crb/run-phase5-pipeline.sh
#
# After all 50 Soliton reviews have landed in bench/crb/phase5-reviews/,
# run the CRB offline pipeline (step2 extract → step2_5 dedup → step3 judge)
# against the Soliton entries using Azure OpenAI gpt-5.2 via managed identity.
#
# Phase 5 measures the effect of the agent-dispatch change (testing +
# consistency disabled by default via skipAgents) vs the Phase 3.5
# F1=0.277 baseline. Ship criteria pre-registered in
# bench/crb/AUDIT_10PR.md §Appendix A:
#   ship  : F1 >= 0.30  AND  recall >= 0.52  AND  no per-lang reg > 0.03
#   hold  : 0.28 <= F1 <= 0.30
#   close : F1 < 0.28  OR  any lang reg > 0.05
#
# Prereqs:
#   - All .md files present in bench/crb/phase5-reviews/ (50 expected)
#   - Sibling checkout at ../code-review-benchmark/ with uv env synced
#   - Azure-AD session active (`az login` or managed-identity available)
#
# Usage:
#   bash bench/crb/run-phase5-pipeline.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
CRB_ROOT="$(dirname "$REPO_ROOT")/code-review-benchmark/offline"

if [ ! -d "$CRB_ROOT" ]; then
  echo "error: $CRB_ROOT not found (sibling checkout expected)" >&2
  exit 1
fi

REVIEW_COUNT=$(find "$REPO_ROOT/bench/crb/phase5-reviews" -name "*.md" | wc -l | tr -d ' ')
if [ "$REVIEW_COUNT" -lt 50 ]; then
  echo "warning: only $REVIEW_COUNT reviews found in phase5-reviews/ (expected 50)" >&2
  echo "         continuing anyway; missing PRs will be flagged" >&2
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

export AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-https://aoai-l-eastus2.openai.azure.com/}"
export AZURE_OPENAI_DEPLOYMENT="${AZURE_OPENAI_DEPLOYMENT:-gpt-5.2}"
export AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-2025-01-01-preview}"
export MARTIAN_MODEL="azure_${AZURE_OPENAI_DEPLOYMENT}"

echo "============================================================="
echo "Soliton × CRB · Phase 5 pipeline (agent-dispatch defaults)"
echo "  lever         : skipAgents = [test-quality, consistency]"
echo "  reviews found : $REVIEW_COUNT"
echo "  judge endpoint: $AZURE_OPENAI_ENDPOINT"
echo "  judge model   : $AZURE_OPENAI_DEPLOYMENT"
echo "  baseline      : Phase 3.5 F1=0.277 (recall 0.566)"
echo "============================================================="

echo ""
echo "[1/4] build benchmark_data.json"
python3 "$REPO_ROOT/bench/crb/build_benchmark_data.py" \
  --reviews-dir "$REPO_ROOT/bench/crb/phase5-reviews" \
  --golden-dir "$CRB_ROOT/golden_comments" \
  --benchmark-prs "$REPO_ROOT/bench/crb/benchmark-prs.json" \
  --output "$CRB_ROOT/results/benchmark_data.json"

echo ""
echo "[2/4] step2 extract candidates (soliton, gpt-5.2 judge)"
cd "$CRB_ROOT"
uv run python -m code_review_benchmark.step2_extract_comments --tool soliton --force

echo ""
echo "[3/4] step2.5 dedup candidates (soliton)"
uv run python -m code_review_benchmark.step2_5_dedup_candidates --tool soliton --force

echo ""
echo "[4/4] step3 judge comments (soliton)"
DEDUP_GROUPS="results/$(printf '%s' "$MARTIAN_MODEL" | tr '/:' '__')/dedup_groups.json"
if [ -f "$DEDUP_GROUPS" ]; then
  uv run python -m code_review_benchmark.step3_judge_comments --tool soliton --force --dedup-groups "$DEDUP_GROUPS"
else
  echo "  (no dedup_groups.json at $DEDUP_GROUPS — running without dedup)"
  uv run python -m code_review_benchmark.step3_judge_comments --tool soliton --force
fi

echo ""
echo "============================================================="
echo "Pipeline complete."
echo "  Results dir : $CRB_ROOT/results/$MARTIAN_MODEL/"
echo "  Evaluations : $CRB_ROOT/results/$MARTIAN_MODEL/evaluations.json"
echo ""
echo "Next: compare F1/recall vs Phase 3.5 (0.277 / 0.566) and write up"
echo "      in bench/crb/RESULTS.md § Phase 5. Apply ship criteria from"
echo "      bench/crb/AUDIT_10PR.md §Appendix A (ship/hold/close bands)."
echo "============================================================="
