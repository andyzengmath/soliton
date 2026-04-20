#!/usr/bin/env bash
# bench/crb/run-phase3_5_1-pipeline.sh
#
# After all 50 Soliton reviews have landed in bench/crb/phase3_5_1-reviews/,
# run the CRB offline pipeline (step2 extract → step2_5 dedup → step3 judge)
# against the Soliton entries, using Azure OpenAI gpt-5.2 via managed identity.
#
# Phase 3.5.1 measures the combined 4a (L5 cross-file retrieval) + 4b
# (hallucination-AST) lift vs the Phase 3.5 F1=0.277 baseline. Ship
# criteria pre-registered in bench/crb/PHASE_4_DESIGN.md:
#   ship     : F1 >= 0.32  AND  recall >= 0.64  AND  no per-lang reg > 0.02
#   hold     : 0.29 <= F1 <= 0.31
#   close    : F1 < 0.29 (documented negative-result)
#
# Prereqs:
#   - All .md files present in bench/crb/phase3_5_1-reviews/ (50 expected);
#     generate via `bash bench/crb/dispatch-phase3_5_1.sh` first.
#   - Sibling checkout at ../code-review-benchmark/ with uv env synced
#   - Azure-AD session active (`az login` or managed-identity available)
#
# Usage:
#   bash bench/crb/run-phase3_5_1-pipeline.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
CRB_ROOT="$(dirname "$REPO_ROOT")/code-review-benchmark/offline"

if [ ! -d "$CRB_ROOT" ]; then
  echo "error: $CRB_ROOT not found (sibling checkout expected)" >&2
  exit 1
fi

REVIEW_COUNT=$(find "$REPO_ROOT/bench/crb/phase3_5_1-reviews" -name "*.md" | wc -l | tr -d ' ')
if [ "$REVIEW_COUNT" -lt 50 ]; then
  echo "warning: only $REVIEW_COUNT reviews found in phase3_5_1-reviews/ (expected 50)" >&2
  echo "         continuing anyway; missing PRs will be flagged" >&2
fi

# Force UTF-8 globally — Soliton review markdown contains emoji (🔴 🟡 ⚪) and
# Unicode arrows; on Windows the default cp1252 codec chokes on them when CRB
# steps `open()` files without explicit encoding.
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

# Azure OpenAI config — identical to Phase 3.5 for apples-to-apples comparison.
export AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-https://aoai-l-eastus2.openai.azure.com/}"
export AZURE_OPENAI_DEPLOYMENT="${AZURE_OPENAI_DEPLOYMENT:-gpt-5.2}"
export AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-2025-01-01-preview}"
export MARTIAN_MODEL="azure_${AZURE_OPENAI_DEPLOYMENT}"

echo "============================================================="
echo "Soliton × CRB · Phase 3.5.1 pipeline (per-language nitpicks gate (v2.1))"
echo "  reviews found : $REVIEW_COUNT"
echo "  judge endpoint: $AZURE_OPENAI_ENDPOINT"
echo "  judge model   : $AZURE_OPENAI_DEPLOYMENT"
echo "  baseline      : Phase 3.5 F1=0.277 (main@d7ddfd0)"
echo "============================================================="

# --- 1. Build CRB-compatible benchmark_data.json from Soliton reviews ---
echo ""
echo "[1/4] build benchmark_data.json"
python3 "$REPO_ROOT/bench/crb/build_benchmark_data.py" \
  --reviews-dir "$REPO_ROOT/bench/crb/phase3_5_1-reviews" \
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
echo ""
echo "Next: compare F1/recall vs Phase 3.5 (0.277 / 0.602) and write up"
echo "      in bench/crb/RESULTS.md § Phase 3.5.1. Apply ship criteria from"
echo "      bench/crb/PHASE_4_DESIGN.md (ship/hold/close bands)."
echo "============================================================="
