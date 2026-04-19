#!/usr/bin/env bash
# bench/crb/run-poc-review.sh
#
# Run Soliton's /pr-review skill locally against a single upstream GitHub PR and
# capture the review markdown to bench/crb/poc-reviews/<slug>.md.
#
# Used by the CRB Phase 2 POC to gather Soliton's reviews for 5 benchmark PRs
# without GitHub Actions or API-key infrastructure (uses the operator's local
# Claude Code auth).
#
# Usage:
#   bench/crb/run-poc-review.sh <upstream-owner/repo> <pr-number> <output-slug>
#
# Example:
#   bench/crb/run-poc-review.sh getsentry/sentry 93824 python-sentry-93824
#
# Prerequisites:
#   - gh CLI authenticated for github.com
#   - Claude Code CLI available as `claude`, logged in
#   - Running from inside the soliton repo clone

set -euo pipefail

if [ $# -ne 3 ]; then
  echo "usage: $0 <upstream-owner/repo> <pr-number> <output-slug>" >&2
  exit 1
fi

UPSTREAM="$1"
PR_NUM="$2"
SLUG="$3"

REPO_ROOT="$(git rev-parse --show-toplevel)"
SHIM_ROOT="$(dirname "$REPO_ROOT")/soliton-poc-work"
SHIM_DIR="$SHIM_ROOT/$SLUG-shim"
OUTPUT_FILE="$REPO_ROOT/bench/crb/poc-reviews/$SLUG.md"

# Create a minimal git shim so `gh pr view $PR_NUM` (no --repo flag) resolves
# to the upstream repo. The /pr-review skill calls gh without --repo so the
# only way to target an external repo is to fake the `origin` remote locally.
mkdir -p "$SHIM_DIR"
cd "$SHIM_DIR"
if [ ! -d .git ]; then
  git init -q
fi
if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "https://github.com/$UPSTREAM.git"
fi

echo "Starting Soliton review: upstream=$UPSTREAM pr=$PR_NUM → $OUTPUT_FILE" >&2

# --allowedTools mirrors .github/workflows/soliton-review.yml `allowed_tools`
# MINUS `Bash(gh pr comment *)` — we write the review to a local file; we
# explicitly do NOT post to the upstream PR.
exec claude -p \
  --plugin-dir "$REPO_ROOT" \
  --permission-mode acceptEdits \
  --allowedTools Read Write Grep Glob \
    'Bash(git diff *)' 'Bash(git log *)' 'Bash(git show *)' 'Bash(git branch *)' 'Bash(git status)' \
    'Bash(gh pr view *)' 'Bash(gh pr diff *)' 'Bash(gh auth status)' \
    Agent \
  --max-budget-usd 2 \
  "Run /pr-review $PR_NUM. DO NOT post any comments to the PR — we are running a local CRB benchmark evaluation and must not spam upstream maintainers.

When the review is complete, WRITE the full markdown review output to this absolute path:
  $OUTPUT_FILE

The parent directory already exists. On stdout, print only the single word DONE on success (nothing else). Do NOT echo the full review to stdout."
