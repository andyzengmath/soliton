---
description: Compute blast radius (import / reference count + sensitive-path hit) for a file
---

# /blast-radius

Compute the blast radius (number of files that import or reference a target file, plus sensitive-paths hit) using Soliton's risk-scorer heuristic — the same grep-based computation the `risk-scorer` agent uses today (`agents/risk-scorer.md` Factor 1).

This is a degraded-mode standalone surface of Soliton's existing logic. When the graph plugin lands (POST_V2_FOLLOWUPS §B1), this command will switch to graph-cli's `info` + dependency-edges queries; the user-facing contract stays the same.

## Argument

- `<file>` — path to a file in the current repository (relative or absolute).

## Behavior

1. **Validate input**: if `<file>` is missing or doesn't exist, output `Error: file not found: <file>` and STOP.

2. **Compute import-count via grep heuristic** (same as `agents/risk-scorer.md` Factor 1):
   - Extract the file's basename without extension (e.g. `auth-utils.ts` → `auth-utils`)
   - Run `git grep -l "<basename>"` to count files that reference it
   - Strip the input file itself from the count

3. **Compute sensitive-paths hit** (same as `risk-scorer` Factor 3):
   - Apply each pattern from `rules/sensitive-paths.md` (and any `config.sensitivePaths` overrides from `.claude/soliton.local.md`) to the input file path
   - Mark hit if any pattern matches
   - Note: do NOT inline the pattern list in this command's output — read the canonical source so future updates to `rules/sensitive-paths.md` propagate automatically.

4. **Output a compact summary**:

```
Blast Radius for <file>

Importers: <N> files (grep heuristic)
Sensitive: <hit | clean>
Backing source: grep (graph plugin pending §B1; will upgrade when graph-cli ships)

Top 5 importers (alphabetical):
- path/a.ts
- path/b.ts
- ...
```

If the import-count is 0, note explicitly: "0 importers found via grep — file may be entry-point, deleted-only PR target, or grep-heuristic miss. Risk-scorer's full analysis runs the same query against `git diff` context."

## Non-goals

- This is NOT the full risk score. The risk-scorer agent computes 6 factors weighted to a 0-100 score; `/blast-radius` exposes only Factor 1 + Factor 3 in isolation for ad-hoc queries.
- This is NOT graph-aware. When the graph plugin lands, the same surface upgrades to use real call/import edges. Until then, false-positives are possible (any file containing the basename string matches, including comments and unrelated symbols).

## Strategic context

Surfaces a degraded-mode primitive from `Logical_inference/docs/strategy/2026-05-01-A2-agent-integration-architecture.md` § 1.4 ("Seven slash commands"). Three of seven are shippable today; this is one. The other four (`/trace-caller`, `/trace-data-flow`, `/regression-risk`, `/graph-explain`) are gated on graph-cli per POST_V2_FOLLOWUPS §B1.
