#!/usr/bin/env bash
# hooks/blast-radius-warning.sh
#
# Soliton Hook C — PostToolUse blast-radius warning.
#
# Per Logical_inference/docs/strategy/2026-05-01-A2-agent-integration-architecture.md
# § 6.1 ("Three Claude Code hooks"), this is the third of the proposed
# hooks shipped in degraded-mode form (no graph dependency). When Claude
# Code edits a file that is referenced by many other files in the repo,
# this hook emits an advisory warning so the developer can decide whether
# the change warrants extra review.
#
# Mechanism mirrors agents/risk-scorer.md Factor 1: count files that
# grep-match the edited file's basename. When the graph plugin lands
# (POST_V2_FOLLOWUPS §B1), this script can be upgraded to use real
# call/import edges; user-facing integration (PostToolUse hook config)
# unchanged.
#
# WIRING: this script does NOT install itself. Users wire it into their
# Claude Code settings.json per docs/hooks-integration.md. Defaults are
# advisory-only (exit 0 always); never blocks tool execution.
#
# Trigger: PostToolUse hook matched on Edit and Write tools.
#
# Input contract (stdin): Claude Code's hook system pipes a JSON payload.
# The script expects at minimum:
#   {"tool": "Edit"|"Write", "tool_input": {"file_path": "<path>", ...}}
# If the payload is malformed, missing the file_path, or references a
# non-existent file, the script exits silently with status 0.
#
# Output contract (stderr): on threshold breach, emits a multi-line
# advisory block. On non-breach or any error, emits nothing.
#
# Configuration env vars:
#   SOLITON_BLAST_THRESHOLD — minimum importer count to trigger warning
#                              (default: 10)
#   SOLITON_BLAST_QUIET    — if set to any non-empty value, suppress all
#                              output (script still runs and exits 0)

set -e

THRESHOLD="${SOLITON_BLAST_THRESHOLD:-10}"

# Skip immediately if quiet mode is requested
[ -n "${SOLITON_BLAST_QUIET:-}" ] && exit 0

# Read payload from stdin (non-blocking; exit silently if no input)
PAYLOAD=""
if [ -t 0 ]; then
    # No stdin; nothing to do
    exit 0
fi
PAYLOAD="$(cat)"

if [ -z "$PAYLOAD" ]; then
    exit 0
fi

# Extract file_path. Use python3 if available (most robust); fall back to a
# bare-bones grep parse if not. Either way, exit silently on any failure.
FILE_PATH=""
if command -v python3 >/dev/null 2>&1; then
    FILE_PATH=$(printf '%s' "$PAYLOAD" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    tool = d.get('tool', '')
    if tool not in ('Edit', 'Write'):
        sys.exit(0)
    print(d.get('tool_input', {}).get('file_path', ''))
except Exception:
    sys.exit(0)
" 2>/dev/null) || exit 0
else
    # Fallback parse. Grep for "file_path":"..." pattern. Less robust but
    # works on systems without python3.
    case "$PAYLOAD" in
        *'"tool":"Edit"'*|*'"tool":"Write"'*) ;;
        *) exit 0 ;;
    esac
    FILE_PATH=$(printf '%s' "$PAYLOAD" | grep -oE '"file_path":"[^"]+"' | head -1 | sed 's/"file_path":"\([^"]*\)"/\1/')
fi

# Bail if extraction failed or file doesn't exist
[ -z "$FILE_PATH" ] && exit 0
[ ! -f "$FILE_PATH" ] && exit 0

# Bail if not in a git repo (we use git grep for the importer count)
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

# Compute basename without extension (mirror agents/risk-scorer.md Factor 1)
FILE_BASENAME=$(basename "$FILE_PATH")
SYMBOL="${FILE_BASENAME%.*}"

# Skip when symbol is too short (would match too broadly: "a", "x", etc.)
if [ "${#SYMBOL}" -lt 4 ]; then
    exit 0
fi

# Count importers via git grep. Strip the file itself from the result set.
# The `--fixed-strings` flag prevents regex meta-characters in the basename
# from causing false positives.
IMPORTER_COUNT=$(git grep -l --fixed-strings "$SYMBOL" 2>/dev/null | grep -v -F "$FILE_PATH" | wc -l | tr -d ' ')

# Bail under threshold
if [ "$IMPORTER_COUNT" -lt "$THRESHOLD" ]; then
    exit 0
fi

# Sensitive-paths check (mirrors agents/risk-scorer.md Factor 3 + rules/sensitive-paths.md
# defaults; inlined here for hook self-containment, intentionally a forked copy
# since hooks should be portable single-file scripts)
SENSITIVE="clean"
case "$FILE_PATH" in
    *auth/*|*security/*|*payment/*|*.env|*migration*|*secret*|*credential*|*token*|*.pem|*.key)
        SENSITIVE="hit (sensitive path)" ;;
esac

# Emit advisory
{
    echo ""
    echo "⚠️  Soliton blast-radius warning (Hook C)"
    echo "   File: $FILE_PATH"
    echo "   Importers: $IMPORTER_COUNT files (grep heuristic; threshold=$THRESHOLD)"
    echo "   Sensitive: $SENSITIVE"
    echo "   Suggestion: run /blast-radius $FILE_PATH for top-5 importers + full context"
    echo "   Hook source: hooks/blast-radius-warning.sh (advisory; never blocks)"
    echo ""
} >&2

exit 0
