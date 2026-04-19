#!/usr/bin/env bash
# bench/crb/run-phase3-pipeline.sh
#
# After all 50 Soliton reviews have landed in bench/crb/phase36-reviews/,
# run the CRB offline pipeline (step2 extract → step2_5 dedup → step3 judge)
# against the Soliton entries, using Azure OpenAI gpt-5.2 via managed identity.
#
# Prereqs:
#   - All .md files present in bench/crb/phase36-reviews/ (50 expected)
#   - Sibling checkout at ../code-review-benchmark/ with uv env synced
#   - Azure-AD session active (`az login` or managed-identity available)
#
# Usage:
#   bash bench/crb/run-phase3-pipeline.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
CRB_ROOT="$(dirname "$REPO_ROOT")/code-review-benchmark/offline"

if [ ! -d "$CRB_ROOT" ]; then
  echo "error: $CRB_ROOT not found (sibling checkout expected)" >&2
  exit 1
fi

REVIEW_COUNT=$(find "$REPO_ROOT/bench/crb/phase36-reviews" -name "*.md" | wc -l | tr -d ' ')
if [ "$REVIEW_COUNT" -lt 50 ]; then
  echo "warning: only $REVIEW_COUNT reviews found in phase36-reviews/ (expected 50)" >&2
  echo "         continuing anyway; missing PRs will be flagged" >&2
fi

# Force UTF-8 globally — Soliton review markdown contains emoji (🔴 🟡 ⚪) and
# Unicode arrows; on Windows the default cp1252 codec chokes on them when CRB
# steps `open()` files without explicit encoding.
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

# Azure OpenAI config — overrides the Martian default path in llm_client.py.
export AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-https://aoai-l-eastus2.openai.azure.com/}"
export AZURE_OPENAI_DEPLOYMENT="${AZURE_OPENAI_DEPLOYMENT:-gpt-5.2}"
export AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-2025-01-01-preview}"
# Also set MARTIAN_MODEL so downstream sanitize_model_name emits a clean
# results dir name ("azure_gpt-5.2" instead of the default).
export MARTIAN_MODEL="azure_${AZURE_OPENAI_DEPLOYMENT}"

echo "============================================================="
echo "Soliton × CRB · Phase 3.6 pipeline"
echo "  reviews found : $REVIEW_COUNT"
echo "  judge endpoint: $AZURE_OPENAI_ENDPOINT"
echo "  judge model   : $AZURE_OPENAI_DEPLOYMENT"
echo "============================================================="

# --- 1. Build CRB-compatible benchmark_data.json from Soliton reviews ---
echo ""
echo "[1/4] build benchmark_data.json"
python3 "$REPO_ROOT/bench/crb/build_benchmark_data.py" \
  --reviews-dir "$REPO_ROOT/bench/crb/phase36-reviews" \
  --golden-dir "$CRB_ROOT/golden_comments" \
  --benchmark-prs "$REPO_ROOT/bench/crb/benchmark-prs.json" \
  --output "$CRB_ROOT/results/benchmark_data.json"

# --- 2. step2: extract candidate issues from each Soliton review ---
echo ""
echo "[2/4] step2 extract candidates (soliton, gpt-5.2 judge)"
cd "$CRB_ROOT"
uv run python -m code_review_benchmark.step2_extract_comments --tool soliton --force

# --- 3. step2.5: dedup candidates ---
echo ""
echo "[3/4] step2.5 dedup candidates (soliton)"
uv run python -m code_review_benchmark.step2_5_dedup_candidates --tool soliton --force

# --- 4. step3: judge candidates against goldens ---
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
echo "============================================================="
