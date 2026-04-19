# Idea-Stage Manifest — Soliton v2

**Pipeline run**: `/research-pipeline` (Stage 1 — Idea Discovery)
**Date**: 2026-04-18
**Prompt (from user)**: Research how to resolve AI-native PR review at scale (slop-PR flood, thousands-of-PRs/day regime, enterprise rebuild target). Cover (1) current literature, (2) Claude Code native (code-review, ultra-review), (3) other OSS plugin/skills (superpowers, oh-my-claudecode, oh-my-openagent, gstack, gsd), (4) other agents (Codex, Copilot CLI, Gemini CLI, Cursor, …). Product must remain a plugin/skill + ship CI/CD integration. Exploit traditional tools aggressively + integrate the sibling `graph-code-indexing` system for dependency / data-flow signals.

**Branch**: `feat/ci-cd-integration`

## Outputs

| File | Purpose | Size |
|---|---|---|
| [IDEA_REPORT.md](IDEA_REPORT.md) | **Primary deliverable.** 10 ranked ideas (I1-I10) + 10 secondary (I11-I20), five-tier architecture, CI/CD integration plan, cost model, 6-week pilot plan, Gate-1 options A/B/C/D, risk register. | ~5.2k words |
| [LITERATURE_REVIEW.md](LITERATURE_REVIEW.md) | 2024-2026 academic survey. 10 primary papers + 6 honourable mentions covering: actionability (Sun 2025, RovoDev 2026, Zhong 2026); benchmarks (SWR-Bench, CR-Bench, SWE-PRBench, Sphinx); AI-code error profile (Schreiber & Tippe 2025, Hora & Robbes 2026); hallucination (Khati 2026 deterministic AST); calibration (Lin 2026). 5 tables, 3 key research gaps directly relevant to Soliton. | ~2.6k words |
| [OSS_ECOSYSTEM_REVIEW.md](OSS_ECOSYSTEM_REVIEW.md) | Deep-dive on 7 OSS plugin ecosystems: superpowers, oh-my-claudecode, quantum-loop, claude-plugins-official/code-review, everything-claude-code, pr-review-toolkit, oh-my-openagent. Plus user-mentioned "gstack" (Graphite) and "gsd" (gherrit/git-gerrit). 20-column comparison matrix + top-10 ideas to steal. | ~3.5k words |
| [COMPETITOR_AGENTS_REVIEW.md](COMPETITOR_AGENTS_REVIEW.md) | 22-tool market landscape: Codex, Copilot Code Review, Jules, Gemini Code Assist, Cursor BugBot, Windsurf, Amazon Q, OpenHands, Devin, Tabnine, Continue.dev, Aider, CodeRabbit, Qodo Merge, Greptile, Ellipsis, Graphite Agent, Sourcegraph Amp, Bito, GitLab Duo. Architecture, pricing, benchmarks. Martian-CRB leaderboard. 10 emerging cross-ecosystem patterns. | ~3.2k words |
| [DESIGN_TRADITIONAL_AND_GRAPH.md](DESIGN_TRADITIONAL_AND_GRAPH.md) | Architectural spec for Tier 0 (deterministic gate: lint/SAST/types/secrets/SCA/AST-diff/clone/test-impact) and Tier 1 (graph signals: blast radius, taint paths, dependency breaks, co-change, feature criticality). Concrete integration points with `graph-code-indexing`'s existing 8 edge types. Cost model. Risks. | ~2.8k words |
| MANIFEST.md | This file. | — |

## Summary of Stage-1 output

### Top-ranked ideas (recommendation priority)

| Idea | Score | Tier | Ship by | Summary |
|---|---|---|---|---|
| **I1** Tier-0 Deterministic Gate | 25 | A | Week 1-3 | Lint/SAST/types/secrets/SCA pre-LLM gate + `LLM-skip` fast path for clean trivial PRs; ≈73 % median cost reduction. |
| **I3** Spec-alignment Stage 0 | 16 | A | Week 1-2 | Haiku agent reads REVIEW.md + PR desc; mechanical wiring-verification greps; blocks half-finished PRs. |
| **I4** Deterministic AST hallucination detector | 16 | A | Week 3 | Khati 2026 pattern (100 % precision, 87.6 % recall) replaces ~80 % of hallucination-agent Opus calls. |
| **I9** Martian-CRB publication + cost-normalised F1 | 16 | A | Week 5-6 | First public benchmark number; positioning moat. |
| **I2** Graph Signal Service | 15 | A | Week 3-5 | Blast radius / taint / dep-breaks via `graph-code-indexing` CLI; feeds risk scorer + 4 agents. Novel. |
| **I5** Haiku-tiered orchestration | 15 | A | Week 1-2 | Eligibility / filter / summarise / score on Haiku; Sonnet only for reviewers. |
| **I6** Realist Check + Self-Audit synth post-pass | 12 | B | Week 4-5 | Pressure-test every CRITICAL; mandate "Mitigated by:" for downgrade. |
| **I7** silent-failure + comment-accuracy agents | 12 | B | Week 4 | File-type-triggered; empirically-validated gaps (Hora & Robbes 2026). |
| **I8** Stack awareness (`--parent <PR#>`) | 12 | B | Week 5 | No other OSS reviewer does this; directly serves enterprise-rebuild feature-chain PRs. |
| **I10** Tri-model cross-check (`--crossmodel`) | 9 | C | Phase 2 | Codex/Gemini SDK second opinion on disagreements; uniquely unavailable to Anthropic managed. |

Plus 10 secondary ideas (I11-I20) including pre-merge-checks DSL, hunk grouping, inline comments, pre-existing-bug severity, prior-PR comment mining, learnings loop, LSP tool access, multi-pass majority voting, execution sandbox, license check.

### Recommended Gate-1 decision

**Default**: Option C (parallel tracks — Track 1: I1/I3/I5/I9 in weeks 1-3, Track 2: I2/I4 in weeks 3-6, converge at month 2 Gate). Matches the user's existing "Wrap first, Strangler second" routing from `risk_gap.md` §4.5.

**Fallback**: Option A (I1/I3/I5/I9 only, defer graph work).

**Not recommended**: Option B (graph-first) unless enterprise-rebuild pilot is immediate; Option D (publication-only) unless team capacity severely constrained.

### Prerequisites (regardless of option)

- Test Tier-0 tooling (ruff, eslint, tsc, semgrep, gitleaks, difftastic) actually runs on GitHub Actions ubuntu-latest in <60 s on typical diffs.
- Graph-code-indexing CLI story (Mode B shell-out) must be stable before I2 can start.
- Graph-code-indexing Java parser is **not** a Soliton prerequisite for Tier-A ideas but **is** a blocker for full enterprise-rebuild fit — track as external dependency.

## Gate-1 checkpoint

🚦 **Human checkpoint**: before proceeding to Stage 2 (implementation), please confirm:

1. **Which option?** A / B / C / D (default recommendation: **C**).
2. **Any ideas to drop** from the selected option?
3. **Any ideas to add** from the secondary list (I11-I20)?
4. **Enterprise-rebuild pilot linkage** — should Soliton v2 be validated specifically on a graph-code-indexing-indexed codebase (Eclipse BIRT, Spring PetClinic, or the user's internal Java monolith) as part of pilot weeks 5-6?
5. **Any scope changes** from the risk register (e.g., graph-code-indexing Java parser slippage forces B→A)?

## Next actions if proceeding

After user decision at Gate 1:
- **Stage 2 (implementation)** — follow `IDEA_REPORT.md` §9 pilot plan week-by-week.
- **Stage 3** (`/run-experiment` or `/experiment-queue`) — only if graph ablations are run; otherwise skipped (no GPU work required for this pipeline).
- **Stage 4** (`/auto-review-loop`) — apply to the `IDEA_REPORT.md` itself (cross-model review of this plan).
- **Stage 5** — write narrative report summarising Soliton v2 shipping deltas for internal comms.
- **Stage 6** — optional `/paper-writing` when Martian-CRB numbers are in; target Venue = ICSE 2027 or MSR 2027 short paper on "risk-adaptive multi-agent PR review with graph signals."

## Verification status

- **Source quality**: all 36 arXiv papers in `LITERATURE_REVIEW.md` referenced with IDs + links. Five items marked `[unverified]` (see §6 of that file) should be re-checked before any public claim.
- **Soliton-internal claims** verified directly against source: `skills/pr-review/SKILL.md` (512 lines), `agents/risk-scorer.md`, `agents/hallucination.md`, `agents/cross-file-impact.md`, `agents/historical-context.md`, `docs/ci-cd-integration.md`, `docs/prd-soliton.md`, `docs/prd-ai-native-takeover.md`, `research/claude-code-review-competitive-analysis.md`, `research/research-pr-review-agents.md`, `research/AI-Native Development Quality Assurance.md`, `research/deep-research-report (1).md`, `risk_gap.md`.
- **Graph-code-indexing claims** verified via `src/types/EdgeType.ts` (8 edge types confirmed: PARENT_CHILD, CALLS, DATA_FLOW, IMPORTS, INHERITS, IMPLEMENTS, REFERENCES, CONFIGURES), `src/analyzers/*` listing, `src/retrieval/*` listing, `package.json` (tree-sitter-python + @babel/parser + better-sqlite3), `demo/` entry-point list.
- **Enterprise-rebuild claims** verified against `../Logical_inference/idea-stage/USE_CASE_PLANS.md` and `MANIFEST.md`.
