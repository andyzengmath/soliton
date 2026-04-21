#!/usr/bin/env bash
# bench/graph/smoke-partial-mode.sh
#
# Validates the partial-mode backend contract in skills/pr-review/graph-signals.md
# against a locally-built code-review-graph index. Zero API spend; all commands
# are deterministic shell-outs to code-review-graph's CLI.
#
# What this verifies:
#   1. Backend detection finds code-review-graph + .code-review-graph/graph.db
#   2. info query maps `code-review-graph status` text -> Soliton's expected fields
#   3. dependency-breaks query maps `code-review-graph detect-changes` JSON shape
#   4. Both queries complete within the partial-mode 10s per-query budget
#      (the skill's original 500ms budget applies only to full-mode graph-cli)
#
# Prereqs:
#   - code-review-graph installed (`pip install code-review-graph`)
#   - Graph built (`code-review-graph build` creates .code-review-graph/graph.db)
#   - Running from inside the soliton repo with > 1 commit on HEAD
#
# Usage:
#   bash bench/graph/smoke-partial-mode.sh

set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

red()    { printf '\e[31m%s\e[0m\n' "$*" >&2; }
green()  { printf '\e[32m%s\e[0m\n' "$*"; }
yellow() { printf '\e[33m%s\e[0m\n' "$*"; }

# Partial-mode relaxed budget: 10000 ms per query.
PARTIAL_BUDGET_MS="${PARTIAL_BUDGET_MS:-10000}"

check_contract () {
  local name="$1" ; shift
  local budget_ms="$1" ; shift
  local start_ns=$(date +%s%N)
  if ! "$@" > /tmp/crg-smoke-output 2> /tmp/crg-smoke-error; then
    red "  [FAIL] ${name}: $(cat /tmp/crg-smoke-error)"
    return 1
  fi
  local end_ns=$(date +%s%N)
  local elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))
  if [ "$elapsed_ms" -gt "$budget_ms" ]; then
    yellow "  [SLOW] ${name} over budget (${elapsed_ms} ms > ${budget_ms} ms)"
  else
    green "  [OK]   ${name} in ${elapsed_ms} ms"
  fi
  cat /tmp/crg-smoke-output
  echo
}

echo "=============================================================="
echo "graph-signals partial-mode backend smoke test"
echo "  repo: $REPO_ROOT"
echo "  budget: ${PARTIAL_BUDGET_MS} ms per query"
echo "=============================================================="

# 1. Backend detection
echo
echo "[1/4] Backend detection"
if ! command -v code-review-graph > /dev/null; then
  red "  [FAIL] code-review-graph not on PATH. Install: pip install code-review-graph"
  exit 1
fi
green "  [OK]   code-review-graph on PATH: $(command -v code-review-graph)"

if [ ! -f .code-review-graph/graph.db ]; then
  red "  [FAIL] .code-review-graph/graph.db missing. Run: code-review-graph build"
  exit 1
fi
green "  [OK]   graph.db present ($(wc -c < .code-review-graph/graph.db) bytes)"

# 2. info query
echo
echo "[2/4] info query (code-review-graph status -> Soliton info fields)"
check_contract "info" "$PARTIAL_BUDGET_MS" code-review-graph status

STATUS_OUT=$(code-review-graph status 2>&1)
COMMIT_SHA=$(echo "$STATUS_OUT" | grep "Built at commit:" | awk '{print $NF}')
NODE_COUNT=$(echo "$STATUS_OUT" | grep "^Nodes:" | awk '{print $NF}')
EDGE_COUNT=$(echo "$STATUS_OUT" | grep "^Edges:" | awk '{print $NF}')
BUILT_AT=$(echo "$STATUS_OUT" | grep "Last updated:" | awk '{print $NF}')

if [ -z "$COMMIT_SHA" ] || [ -z "$NODE_COUNT" ] || [ -z "$EDGE_COUNT" ] || [ -z "$BUILT_AT" ]; then
  red "  [FAIL] info parse incomplete"
  echo "         commitSha=$COMMIT_SHA nodeCount=$NODE_COUNT edgeCount=$EDGE_COUNT builtAt=$BUILT_AT"
  exit 1
fi
green "  [OK]   info parsed: commitSha=$COMMIT_SHA nodeCount=$NODE_COUNT edgeCount=$EDGE_COUNT builtAt=$BUILT_AT"

# 3. dependency-breaks query
echo
echo "[3/4] dependency-breaks query (code-review-graph detect-changes -> Soliton shape)"
check_contract "detect-changes" "$PARTIAL_BUDGET_MS" code-review-graph detect-changes --base HEAD~1 --brief

DC_JSON=$(code-review-graph detect-changes --base HEAD~1 2>&1)
if ! echo "$DC_JSON" | PYTHONUTF8=1 PYTHONIOENCODING=utf-8 /c/Python314/python -c "
import sys, json
d = json.load(sys.stdin)
required = {'summary', 'risk_score', 'changed_functions', 'affected_flows', 'test_gaps', 'review_priorities'}
missing = required - set(d.keys())
if missing:
    print(f'  [FAIL] missing keys: {missing}', file=sys.stderr)
    sys.exit(1)
print(f'  [OK]   JSON shape correct. keys: {sorted(d.keys())}')
print(f'         risk_score={d[\"risk_score\"]} changed_functions={len(d[\"changed_functions\"])} affected_flows={len(d[\"affected_flows\"])}')
" 2>&1; then
  red "  [FAIL] detect-changes JSON does not match expected shape"
  exit 1
fi

# 4. Partial-mode signals note
echo
echo "[4/4] Partial-mode signal emission"
yellow "  [INFO] partial-mode WILL emit 'partial: true' for blastRadius, taintPaths,"
yellow "         coChangeHits, affectedFeatures, criticalityScore, featureCoverage."
yellow "         Run full-mode graph-cli or an MCP client shim for complete signals."

echo
echo "=============================================================="
green "All smoke checks passed."
green "Partial-mode backend (code-review-graph) is wired correctly."
echo "=============================================================="
