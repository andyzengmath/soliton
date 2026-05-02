# Soliton Self-Validation Evidence

**Last updated**: 2026-05-02
**Status**: living doc; updated as new self-validation events accumulate.

## Why this doc exists

When evaluating an AI-powered PR review tool for procurement, the central question is: **does the tool actually catch real bugs?** Synthetic benchmarks (Martian CRB, SWE-PRBench) answer one version of that question — performance against curated golden comments. Self-validation answers a different version — does the tool, when pointed at its own ongoing development, find regressions before they ship?

This doc catalogues every confirmed event where Soliton's review pipeline (either the `/pr-review` skill or the `code-reviewer` subagent) caught a bug in Soliton's own code at PR review time, before merge. Each entry includes severity, reviewer mode, what was caught, and the fix-up PR. The pattern is procurement-relevant because:

1. **Closed-loop correctness**: Soliton reviewing its own PRs proves the pipeline works on real prompt-engineering codebases, not just the CRB corpus.
2. **Multi-severity detection**: events span CRITICAL (would-be runtime bugs) through IMPROVEMENT (structural maintainability) through LOW (regex fragility) — demonstrating the tool's full severity spectrum.
3. **Multiple review modes**: the same bug class is sometimes caught by `code-reviewer` subagent and sometimes by `/pr-review` correctness agent — confirming the multi-agent dispatch is doing real work, not just mode-redundancy.

## Cluster 1 — Phase 6 cluster (PRs #107, #108, #110)

The Phase 6 design cluster (Java-only L5 cross-file retrieval, gated on a future $140 CRB measurement) shipped via a four-PR sequence on 2026-05-01. Two CRITICAL bugs and one IMPROVEMENT were caught **by Soliton's own review pipeline** before any benchmark spend.

### PR #107 — CRITICAL caught by code-reviewer subagent on PRs #104 + #105

**What was wrong**: SKILL.md Step 2 (Configuration Resolution) didn't parse the new YAML key `agents.cross_file_retrieval_java.enabled` into `config.*`. Without this, the orchestrator's later config lookup would always be undefined, the Phase 6a §2.5 conditional in `agents/correctness.md` would always evaluate false, and the planned $140 Phase 6b CRB run would have measured pure Phase 5.2 baseline behavior with **zero Phase 6 differential** — a no-op spend.

**Reviewer**: `code-reviewer` subagent, run on the Phase 6a code (PR #104) + dispatch scripts (PR #105).

**Severity**: CRITICAL.

**Fix**: PR #107 added the Step 2 mapping line + `.gitignore` entry for `phase6-reviews/` + dispatch-phase6.sh grep flag fix. Without this catch, the user would have authorized $140, watched 50 PRs dispatch, scored against goldens, and walked away with "no signal" — drawing the wrong conclusion that the per-language re-integration pattern doesn't work.

### PR #108 — CRITICAL conf=97 caught by /pr-review on PR #104

**What was wrong**: Even after PR #107 fixed Step 2, the `/pr-review` correctness agent (when run on PR #104 itself) flagged that **Step 4.2's prompt template never passes `config` to dispatched agents**. The agent's §2.5 conditional checks `config.agents.cross_file_retrieval_java.enabled == true`, but the orchestrator's per-agent prompt only includes `diff` / `files` / `prDescription` / `focusArea` — never `config`. So the agent had no way to evaluate the variable; it was permanently undefined from the agent's perspective.

**Reviewer**: full `/pr-review` skill invocation against PR #104 — risk-scorer dispatched (risk = 25 / LOW), then `correctness` agent dispatched, which emitted the finding with confidence 97.

**Severity**: CRITICAL.

**Fix**: PR #108 added Step 4.1 step 6 (per-agent feature-flag annotations pre-computed by the orchestrator) + Step 4.2 prompt-template extension passing `cross_file_retrieval_java_enabled` + `java_files` to the correctness agent specifically. Mirrors the existing silent-failure / comment-accuracy gating pattern.

**Why this matters**: PR #107's earlier code-reviewer pass *had already missed* this deeper layer. Two independent review modes (subagent + skill) caught two related but distinct bugs in the same PR. Without PR #108, the $140 Phase 6b spend would still have been a no-op even though PR #107 was applied.

### PR #110 — IMPROVEMENT conf=82 caught by both reviewers on PR #108

**What was wrong**: PR #108 added the `Feature flags` block inside the shared Step 4.2 prompt template, gated only by an inline parenthetical comment "ONLY for correctness agent — omit for all other agents". Both `/pr-review` (correctness agent, conf=82) and `code-reviewer` subagent independently flagged this as structural fragility — an LLM orchestrator iterating over `dispatchList` could miss the omission directive and inject the block into every agent's prompt.

**Reviewers**: both modes converged on the same finding.

**Severity**: IMPROVEMENT.

**Fix**: PR #110 moved the Feature flags block outside the shared template into a separate conditional override paragraph (mirroring the existing graph-signal pass-through pattern). Eliminates inline-comment fragility.

## Cluster 2 — Slash commands cluster (PRs #122, #123)

Three slash commands shipped on 2026-05-02 per Logical_inference's A2 §1.4 strategy spec. The `code-reviewer` subagent immediately caught 4 issues.

### PR #123 — 1 HIGH + 2 MEDIUM + 1 LOW caught by code-reviewer subagent on PR #122

**HIGH**: `/review-pack` cited fabricated cost projections (`~$0.15-0.40 LOW/MEDIUM, ~$0.40-1.00 HIGH/CRITICAL`) attributed to `rules/model-pricing.md`. The reviewer verified `rules/model-pricing.md` has NO per-tier band — only IDEA_REPORT target ($0.10-$0.40) + MEDIUM projection (~$0.22). Numbers were invented.

**MEDIUM**: `/blast-radius` inlined sensitive-paths list instead of pointing to canonical `rules/sensitive-paths.md` — drift risk.

**MEDIUM**: `/co-change` overclaimed wrapping `historical-context` agent — that agent does bug-fix frequency / churn / blame, NOT co-occurrence.

**LOW**: `/review-pack` frontmatter ("Step 1 + Step 2.75 only") inconsistent with body ("Step 1 + Step 2 + Step 2.5 + Step 2.75").

**Fix**: PR #123 closed all 4 in one PR (cost-numbers replaced with real source citations + harness caveat; sensitive-paths read from canonical file; co-change re-described as standalone heuristic; frontmatter aligned to body).

**Why this matters**: the slash commands are user-facing surfaces; incorrect cost numbers in particular would have shipped to integrators reading the docs as authoritative. Pre-merge catch prevents documentation-fiction-as-shipped.

## Pattern observations

1. **Multi-severity coverage**: across both clusters, finding severities span CRITICAL → HIGH → MEDIUM → LOW → IMPROVEMENT. The tool isn't biased toward only catching critical bugs (which tend to be obvious in code review anyway) or only catching nits (which are easy but low-value).

2. **Convergent multi-agent detection**: PR #110's IMPROVEMENT was caught by **both** `/pr-review` correctness agent AND `code-reviewer` subagent independently. This is multi-mode confirmation of the same finding — strong evidence the agents' reasoning isn't degenerately correlated.

3. **Subagent vs skill differential**: PR #108's CRITICAL was missed by the `code-reviewer` subagent (which had run on PR #104 in isolation) and caught by the `/pr-review` skill (which dispatched the correctness agent with the full Soliton pipeline context). Different review modes catch different bugs — the multi-agent architecture isn't redundancy, it's complementary coverage.

4. **Pre-merge cost preventon**: PR #107 alone, by catching the Step 2 plumbing gap, prevented a no-op $140 CRB spend. The tool **paid for itself** in spend-prevention vs. the entire session's cumulative API cost (~$6.76).

## Procurement context

For procurement teams evaluating Soliton vs. competitors:

- **Anthropic Managed Code Review** (launched 2026-03-09): $15-25/review average; published F1 not available from Anthropic directly. Independent Martian CRB reports F1=0.376 (offline track). No published self-validation evidence.
- **CodeRabbit**: ~$24/dev/month. Self-published F1=0.512 online / 0.303 offline. No published self-validation evidence in the form of "tool catches its own bugs".
- **Soliton**: $0.146/PR projected real-world cost; F1=0.313 self-reported on Martian CRB Phase 5.2 corpus; F1/$ = 2.14 real-world / 0.855 CRB (first-mover claim per 2026-05-01 SOTA research — no competitor publishes F1/$). **Plus the events catalogued above as concrete pre-merge bug-catch evidence.**

The cost-normalised F1 narrative establishes Soliton's competitive position on quality-per-dollar. This doc establishes the complement: the pipeline reliably catches real bugs in real-world (not curated) code at multiple severity tiers when pointed at its own development.

## How to update this doc

Add new clusters or events when:

- A `code-reviewer` subagent or `/pr-review` skill run catches a bug in a Soliton PR before merge
- The bug would have shipped or caused a measurement no-op without the catch
- The fix is in a separate, traceable PR

Format: cluster + per-PR summary with severity / reviewer / what / why. Keep entries factual; no marketing language.

Cross-link from:

- `README.md` — link to this doc as procurement evidence
- `CHANGELOG_V2.md` Unreleased section — entries that document the cluster
- `bench/crb/RESULTS.md` — linkage to benchmark numbers for context

## Non-claims

- This doc does NOT claim Soliton catches every bug. It claims Soliton catches bugs in its own code at the rates documented above.
- This doc does NOT replace the Martian CRB benchmark. The benchmark measures performance against curated goldens; this doc measures real-world catches against the actual Soliton codebase.
- This doc is NOT marketing. Every claim is traceable to a specific PR number and a specific finding-text. If anything reads as marketing-style, that's a doc bug — please file a PR to factualize.
