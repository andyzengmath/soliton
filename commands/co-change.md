---
description: List files that historically co-change with a target (git log heuristic; CO_CHANGE-style)
---

# /co-change

List the files that have historically been changed in the same commits as a target file. Uses `git log` as a degraded-mode CO_CHANGE proxy — a standalone heuristic inspired by CO_CHANGE graph semantics (note: the `historical-context` agent uses git history for bug-fix frequency / churn / blame patterns, NOT for co-occurrence; this command introduces the per-file co-change query separately). Will upgrade to true CO_CHANGE graph edges when available per POST_V2_FOLLOWUPS §B1.

## Argument

- `<file>` — path to a file in the current repository.

## Behavior

1. **Validate input**: if `<file>` is missing or doesn't exist, output `Error: file not found: <file>` and STOP.

2. **Find co-change candidates via git log**:
   ```bash
   git log --name-only --pretty=format: --since="6 months ago" -- "<file>"
   ```
   This returns every file ever changed in a commit that also touched `<file>`.

3. **Count co-occurrence frequency**:
   - Strip blank lines and the input file itself
   - Aggregate counts: how many times each candidate file was in a same-commit grouping with `<file>`
   - Sort by frequency descending

4. **Output the top-10 co-changers**:

```
Co-change for <file> (last 6 months, git log heuristic)

| Rank | Co-changer | Count |
|------|------------|-------|
| 1    | path/x.ts  | 12    |
| 2    | path/y.ts  | 9     |
| ...  | ...        | ...   |

Backing source: git log (graph plugin pending §B1; will upgrade to CO_CHANGE edges from graph-code-indexing when available).
```

If 0 candidates: "No co-changers found — file may be new (< 6 months of history), rarely modified, or always changed in isolation."

## Use cases

- **Pre-PR sanity check**: before opening a PR that modifies `auth/session.py`, see which files historically move together — likely candidates for downstream regression checks.
- **Onboarding**: when joining a project, `/co-change <core-file>` reveals which files form natural cohort sets.
- **Refactor scope**: planning a rename of `<file>`? Co-change list approximates the touch surface.

## Non-goals

- This is NOT the full `historical-context` agent run. The agent integrates CO_CHANGE with author-velocity, churn, and recent-bug-fix patterns weighted into the risk score. `/co-change` exposes only the raw co-occurrence frequency.
- The git log heuristic over-counts mechanical / formatter-only commits. False positives are common when commits touch many files simultaneously (e.g., dependency bumps, mass renames).

## Strategic context

A2 §1.4 slash-command surface. Companion to `/blast-radius` and `/review-pack`. The full graph-edge upgrade lands when CO_CHANGE edges from the sibling graph-code-indexing repo become consumable (§B1 / §B2 MCP shim).
