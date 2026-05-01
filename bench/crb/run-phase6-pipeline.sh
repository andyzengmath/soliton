#!/usr/bin/env bash
# bench/crb/run-phase6-pipeline.sh
#
# After all 50 Soliton reviews have landed in bench/crb/phase6-reviews/,
# run the CRB offline pipeline (step2 extract → step2_5 dedup → step3 judge)
# against the Soliton entries, using Azure OpenAI gpt-5.2 via managed identity.
#
# Phase 6 measures the Java-only L5 cross-file retrieval lift vs the
# Phase 5.2 F1=0.313 baseline. Ship criteria pre-registered in
# bench/crb/PHASE_6_DESIGN.md:
#   ship  : aggregate F1 ≥ 0.322 AND Java F1 ≥ 0.318 AND no language
#           regression > 2σ_lang (0.036)
#   hold  : aggregate 0.305-0.321; Java 0.290-0.317; up to 1 language regression
#   close : aggregate < 0.305 OR Java < 0.290 OR any language > 2σ_lang
#
# σ_F1 = 0.0086 (PR #48 envelope), σ_Δ paired ≈ 0.0122, σ_lang ≈ 0.018 at n=10.
#
# Prereqs:
#   - All .md files present in bench/crb/phase6-reviews/ (50 expected);
#     generate via `bash bench/crb/dispatch-phase6.sh` first.
#   - Sibling checkout at ../code-review-benchmark/ with uv env synced
#   - Azure-AD session active (`az login` or managed-identity available)
#
# Usage:
#   bash bench/crb/run-phase6-pipeline.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
CRB_ROOT="$(dirname "$REPO_ROOT")/code-review-benchmark/offline"

if [ ! -d "$CRB_ROOT" ]; then
  echo "error: $CRB_ROOT not found (sibling checkout expected)" >&2
  exit 1
fi

REVIEW_COUNT=$(find "$REPO_ROOT/bench/crb/phase6-reviews" -name "*.md" | wc -l | tr -d ' ')
if [ "$REVIEW_COUNT" -lt 50 ]; then
  echo "warning: only $REVIEW_COUNT reviews found in phase6-reviews/ (expected 50)" >&2
  echo "         continuing anyway; missing PRs will be flagged" >&2
fi

# Force UTF-8 globally — Soliton review markdown contains emoji (🔴 🟡 ⚪) and
# Unicode arrows; on Windows the default cp1252 codec chokes on them when CRB
# steps `open()` files without explicit encoding.
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

# Azure OpenAI config — identical to Phase 5.2 / Phase 4c for apples-to-apples comparison.
export AZURE_OPENAI_ENDPOINT="${AZURE_OPENAI_ENDPOINT:-https://aoai-l-eastus2.openai.azure.com/}"
export AZURE_OPENAI_DEPLOYMENT="${AZURE_OPENAI_DEPLOYMENT:-gpt-5.2}"
export AZURE_OPENAI_API_VERSION="${AZURE_OPENAI_API_VERSION:-2025-01-01-preview}"
export MARTIAN_MODEL="azure_${AZURE_OPENAI_DEPLOYMENT}"

echo "============================================================="
echo "Soliton × CRB · Phase 6 pipeline (Java-only L5 retrieval)"
echo "  reviews found : $REVIEW_COUNT"
echo "  judge endpoint: $AZURE_OPENAI_ENDPOINT"
echo "  judge model   : $AZURE_OPENAI_DEPLOYMENT"
echo "  baseline      : Phase 5.2 F1=0.313 (CRB number of record)"
echo "  ship criteria : F1 ≥ 0.322 AND Java F1 ≥ 0.318"
echo "============================================================="

# --- 1. Build CRB-compatible benchmark_data.json from Soliton reviews ---
echo ""
echo "[1/4] build benchmark_data.json"
python3 "$REPO_ROOT/bench/crb/build_benchmark_data.py" \
  --reviews-dir "$REPO_ROOT/bench/crb/phase6-reviews" \
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
echo "Next: compare F1/recall vs Phase 5.2 (0.313 / 0.522) and write up"
echo "      in bench/crb/RESULTS.md § Phase 6. Apply ship criteria from"
echo "      bench/crb/PHASE_6_DESIGN.md (ship/hold/close bands). Report"
echo "      per-language slice (Java +0.046 was the Phase 4c.1 signal we"
echo "      expect to recover; Go regression hypothesis is that removing"
echo "      NOT_FOUND_IN_TREE suppression returns Go to ~Phase 5.2 baseline)."
echo "============================================================="
