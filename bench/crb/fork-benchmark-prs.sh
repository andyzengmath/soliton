#!/usr/bin/env bash
# Fork the CRB offline-benchmark PRs into a target GH org so that Soliton's dogfood
# workflow (installed on that org) auto-reviews each one.
#
# Usage:
#   bench/crb/fork-benchmark-prs.sh --org <org> [--limit N] [--dry-run]
#
# Prerequisites:
#   - gh CLI authenticated (`gh auth status`)
#   - target org exists AND has .github/workflows/soliton-review.yml (or a wrapper calling it) installed
#     on each forked repo so PR opens auto-trigger the review
#   - jq available
#   - bench/crb/benchmark-prs.json present (committed with this PR)

set -euo pipefail

ORG=""
LIMIT=""
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --org) ORG="$2"; shift 2 ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "Unknown flag: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$ORG" ]]; then
  echo "error: --org <org> is required" >&2
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
PRS_JSON="$REPO_ROOT/bench/crb/benchmark-prs.json"

if [[ ! -f "$PRS_JSON" ]]; then
  echo "error: $PRS_JSON not found (run from inside the soliton repo)" >&2
  exit 1
fi

# Extract PR URLs (optionally limit)
if [[ -n "$LIMIT" ]]; then
  URLS=$(jq -r --argjson n "$LIMIT" '.[:$n] | .[].url' "$PRS_JSON")
else
  URLS=$(jq -r '.[].url' "$PRS_JSON")
fi

TOTAL=$(echo "$URLS" | wc -l | tr -d ' ')
echo "Forking $TOTAL benchmark PRs into org '$ORG' (dry-run=$DRY_RUN)"
echo ""

i=0
while IFS= read -r pr_url; do
  i=$((i+1))

  # PR URL format: https://github.com/<owner>/<repo>/pull/<num>
  OWNER_REPO=$(echo "$pr_url" | sed -E 's|https://github.com/([^/]+/[^/]+)/pull/[0-9]+|\1|')
  PR_NUM=$(echo "$pr_url" | sed -E 's|.*/pull/([0-9]+)|\1|')

  # Fork naming convention borrowed from CRB's upstream pattern:
  #   <owner>__<repo>__soliton__PR<num>__<yyyymmdd>
  FORK_REPO_NAME=$(echo "$OWNER_REPO" | tr '/' '_')__soliton__PR${PR_NUM}__$(date +%Y%m%d)

  echo "[$i/$TOTAL] $pr_url"
  echo "         → $ORG/$FORK_REPO_NAME"

  if [[ "$DRY_RUN" == "true" ]]; then
    continue
  fi

  # Check if the fork repo already exists
  if gh repo view "$ORG/$FORK_REPO_NAME" >/dev/null 2>&1; then
    echo "         (already exists — skipping)"
    continue
  fi

  # Fork the upstream repo into the target org, then check out the specific PR as its
  # own branch. The actual PR open into the fork is a downstream step that CRB's
  # step0_fork_prs.py handles — we mirror the fork-name convention here.
  #
  # Note: gh repo fork requires admin on the target org; for orgs you control this
  # flow works out-of-the-box. For orgs you don't control, use gh repo clone + push.

  if ! gh repo fork "$OWNER_REPO" \
       --clone=false \
       --org "$ORG" \
       --fork-name "$FORK_REPO_NAME" >/dev/null 2>&1; then
    echo "         ! fork failed — may already exist under a different name, or target org is not configured for forks. Inspect manually."
    continue
  fi

  echo "         ✓ forked"

  # Add a small delay so we don't hammer the API
  sleep 2
done <<< "$URLS"

echo ""
echo "Done. Next steps:"
echo "  1. Ensure '$ORG/*' has Soliton's dogfood workflow installed."
echo "  2. Open the PR branches on each fork so Soliton auto-reviews them."
echo "     (CRB upstream's step0_fork_prs.py handles this; see its README.)"
echo "  3. Wait 5-15 min for Soliton to post reviews on each fork."
echo "  4. Run CRB's step1_download_prs.py with --tool soliton (after wiring Soliton"
echo "     into the benchmark_data.json tool registry)."
