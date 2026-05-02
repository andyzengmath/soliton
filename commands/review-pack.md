---
description: Inspect Soliton's risk-adaptive context pack for a PR or local diff (Step 1 + Step 2 + Step 2.5 + Step 2.75; no agent dispatch)
---

# /review-pack

Run Soliton's input-normalization (Step 1) and large-PR chunking (Step 2.75) against a target diff and emit the resulting context pack — the structured input that risk-scorer + review agents would receive — **without** dispatching any review agents. Lets users inspect what Soliton is going to look at before committing the budget for a full `/pr-review` run.

## Arguments

- No argument → review the local branch vs auto-detected base (Step 1 Mode A)
- `<PR-number>` or GitHub PR URL → review the named PR (Step 1 Mode B)

Same input-normalization rules as `/pr-review`. Stack-mode flags (`--parent`, `--parent-sha`, `--stack-auto`) are honored per `rules/stacked-pr-mode.md`.

## Behavior

1. **Run Step 1 (Input Normalization)** per `skills/pr-review/SKILL.md`:
   - Resolve base branch / PR metadata
   - Fetch diff + file list + commit messages
   - Honor stack-mode flags
   - Construct `ReviewRequest`

2. **Run Step 2 (Configuration Resolution)** but only resolve config — do NOT dispatch any agents.

3. **Run Step 2.5 edge-case checks** — but emit warnings, don't STOP. (`/review-pack` should always show the user what they would have gotten.)

4. **Run Step 2.75 (Large PR Chunking)**:
   - If diff <= 1000 lines: report "single-chunk; no chunking needed"
   - Otherwise: emit the chunk plan (groups, line counts, file lists per chunk)

5. **Output the context pack**:

```
Review Pack for <ref>

Source: local-branch | PR #<N> | stack-mode (--parent <P>)
Base branch: main
Head branch: <branch>
Files changed: <count> | Lines: +<add> -<del>
Auto-generated / binary skipped: <N>

Chunk plan (Step 2.75):
- Chunk 1: <directory> (~<lines> lines, files: a.ts, b.ts, ...)
- Chunk 2: ...

Effective config (after Layer 1 → 2 → 3 merge):
- confidenceThreshold: <N>
- skipAgents: [<list>]
- v2 flags: tier0=<bool> spec_alignment=<bool> graph=<bool> realist_check=<bool>
- Phase 6: cross_file_retrieval_java=<bool>

PR description (truncated to first 200 chars):
> <prDescription>

Recommended dispatch (from risk-scorer; not yet run):
[ Skipped — `/review-pack` does not run risk-scorer; pass `--with-risk-score` for the full preview. ]

Cost reference points (no published per-tier aggregate band; see source files):
- IDEA_REPORT target band: $0.10–$0.40 per PR (rules/model-pricing.md line 75)
- MEDIUM-PR projection: ~$0.22 after I5 Haiku-tiering (rules/model-tiers.md line 68)
- Per-MTok rate sheet for Opus/Sonnet/Haiku: rules/model-pricing.md
- Caveat: orchestrator emits `metadata.costUsd` only under a harness that surfaces per-Agent `usage` blocks; Claude Code's Agent tool currently does not, so the value is an estimate-from-markdown-length, not measured.

To proceed with the full review:
  /pr-review <ref>
```

## Use cases

- **Cost preview**: understand which agents will likely fire and the per-tier cost band before authorizing a full review (esp. useful in stacked-PR or large-diff scenarios).
- **Chunking debug**: confirm Soliton's chunking lands sensibly on a multi-thousand-line diff before paying for the dispatch.
- **Config dogfood**: verify that `.claude/soliton.local.md` v2 feature flags resolve as expected.

## Non-goals

- `/review-pack` does NOT dispatch any review agents. It is purely Step 1 + Step 2.5 + Step 2.75 + config preview.
- It does NOT compute the risk score. The risk-scorer agent is part of Step 3, which `/review-pack` deliberately skips. Pass `--with-risk-score` (TODO: future flag) to extend coverage.
- The cost estimate is per-tier projection from `rules/model-pricing.md`, not per-agent token measurement (which is auth-gated on PR #65 / harness instrumentation).

## Strategic context

A2 §1.4 third of three shippable-today slash commands. Surfaces the budget-aware review-context-pack API (§8 of A2) at user-invocable granularity. When the `review_context_pack` skill spec lands as a CRB-measurable experiment (E1 in the 2026-05-02 cross-walk), this command becomes its preview surface.
