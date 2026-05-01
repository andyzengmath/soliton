# Soliton v2 — Changelog (Stage 2 deliverables)

This changelog documents the v2 changes generated during `/research-pipeline` Stage 2
execution on 2026-04-18. All files are additive markdown/YAML — no runtime/build changes.

---

## v2.1.2 — 2026-05-01

Patch release covering the post-v2.1.1 cluster (PRs #70 through #85, ~16 PRs total). Schema additions + new rule docs + procurement-readiness derivations. No code-runtime changes; backwards-compatible with v2.1.1 integrators.

### New schema (PR #82)

- `skills/pr-review/SKILL.md` Step 6 Format B `metadata` block adds 5 fields:
  - `metadata.totalTokens.input` / `.output` / `.cacheCreation` / `.cacheRead` (sum of per-Agent `usage` blocks)
  - `metadata.costUsd` (computed via per-model rate sheet × token totals; rounded to 4 decimals)
- Heuristic-fallback caveat documented: Claude Code's Agent tool doesn't surface per-Agent `usage` in return values today, so the orchestrator falls back to a length-based heuristic with `*`-suffix annotation in interactive output. Integrators wanting precise costing wrap dispatch upstream of the orchestrator.

### New rule doc (PR #82)

- `rules/model-pricing.md` (NEW, ~85 lines) — per-MTok rate sheet (Opus 4.x \$15/\$75 in/out; Sonnet 4.x \$3/\$15; Haiku 4.x \$1/\$5; cache writes +25%, reads 90% off; verified 2026-04 against anthropic.com/pricing). Per-Agent → per-model → costUsd algorithm. Bedrock/Vertex `costing.rate_overrides` integrator-override pattern. Cost-attribution caveat (review-pass cost only; excludes coding-agent authoring + Tier-0 deterministic-tool runs + graph index build). Rate-update protocol.

### Procurement-readiness numbers published (PRs #71, #74, #76, #82, #83)

- **C1 PetClinic enterprise-Java dogfood** (PR #71): SHIP verdict on 10 merged PRs; 4 oracle-grade catches (gradle sha256 / `--release 17` flag drop / Thymeleaf typo / Collectors.toList immutability). \$2.38 across 10 reviews.
- **§A1 Tier-0 LLM-skip rate** (PR #74 derivation): 60% on PetClinic real-world stream.
- **§A2 Spec-Alignment ≥1 SPEC_ALIGNMENT block** (PR #74): 8 of 10 PRs emitted blocks; 2 correctly downgraded to NONE on empty PR bodies.
- **§A3 Tier-0 LLM-skip rate on CRB corpus** (PR #76): 0% — informational; CRB is selected for non-trivial cases by design.
- **§C2 cost-normalised F1** (PR #83 derivation): CRB \$0.366/PR mean (\$1.17 per F1 unit; F1/\$ = 0.855 — HOLD per pre-reg). Real-world projection (with §A1 60% Tier-0 fast-path): \$0.146/PR (\$0.47 per F1 unit; F1/\$ ≈ 2.14 — SHIP).

Both procurement numbers publishable with explicit CRB-vs-real-world framing. Closes IDEA_REPORT G9 publication gap.

### Doc consistency closures (PRs #72, #73, #77, #79, #80)

- `.claude-plugin/marketplace.json` version bumped from v2.0.1 to v2.1.1 → v2.1.2 (this release); description updated to match the 9-review + 4-infrastructure agent registry.
- `examples/workflows/*.yml` and `docs/ci-cd-integration.md` quickstart snippets bumped from `--branch v0.0.2` (5 stale references) to `--branch v2.1.1` (will follow up to v2.1.2 in a separate PR once this tag is cut and verified).
- README "7 Review Agents" → "Review Agents" (11-row table with default-state column); architecture diagram bumped from "2-7 agents" to "2-9 agents" + adds Step 2.6/2.7/2.8/5.5 boxes.
- `rules/tier0-tools.md` — adds spotbugs to SAST table, java entries to lint/type_check schema, and a NEW Installation cheatsheet § with per-language install commands (winget/brew/pip/npm/cargo/Maven-plugin) + Tier-0 self-test recipe.
- `templates/soliton.local.md` — full rewrite: threshold default 80→85 (Phase 3.5 alignment); commented-out v2 nested feature-flag block; Phase 5.3 evidence section explaining why silent-failure + comment-accuracy default-OFF.
- `CHANGELOG_V2.md:218-219` (this file's v2.1.0 spec section) — silent-failure + comment-accuracy default flags corrected from `true` to `false (was true in v2.1.0; reverted in v2.1.1 per Phase 5.3 evidence)`.
- `idea-stage/POST_V2_FOLLOWUPS.md` §A5 — closure annotation explicitly added.

### Test fixture additions (PR #81)

4 new fixtures under `tests/fixtures/` covering the v2.1.0 wirings:
- `silent-failure-empty-except` — Python try/except swallowing GatewayError
- `comment-accuracy-stale-docstring` — signature change without docstring update
- `realist-check-no-mitigation` — two CRITICALs without Mitigated-by citation
- `cross-file-impact-graph-signals` — TS signature change with 2+ inbound CALLS edges

`python tests/run_fixtures.py --mode structural` now exercises 15 fixtures (was 11). All pass schema validation. End-to-end mode awaits ANTHROPIC_API_KEY in CI (PR #65 OAuth-token equivalent blocking).

### Manifest sync

`marketplace.json` description: "Risk-adaptive parallel PR review — up to 9 review agents (silent-failure + comment-accuracy default-OFF since v2.1.1) + 4 infrastructure agents (Tier-0 / Spec / Graph / Realist Check feature-flagged)".

### Cumulative session spend

~\$3.48 across 16 PRs (mostly the C1 PetClinic dogfood at \$2.38; rest \$0 derivations + docs).

### Deferred to next release

- **§C2 Phase 2 measured re-run (~\$15-25)** — pending harness instrumentation that surfaces per-Agent `usage` in Agent tool return values. The Phase 2 derivation (PR #83) is informationally publishable in the meantime.
- **§C1.B Apache Camel arm with full-swarm dispatch (~\$15-50)** — closes the simulator-caveat from C1 scout.
- **§G3 stack-awareness orchestrator logic (~1 week eng)** — `--parent <PR#>` flag is parsed but not implemented.
- **§B1/§B2/§B3 sibling-repo deps** — full-mode graph-cli + MCP shim + Martian CRB upstream submission.

---

## v2.1.1 — 2026-04-30

Patch release reversing the v2.1.0 default-ON status of two content-triggered agents based on Phase 5.3 CRB evidence (PR #68).

### Default flips (default ON → default OFF)

- `agents.silent_failure.enabled` — was `true` in v2.1.0, now `false`
- `agents.comment_accuracy.enabled` — was `true` in v2.1.0, now `false`

### Why

Phase 5.3's combined-wirings CRB run (PR #68) measured F1 = 0.268 vs Phase 5.2's published 0.313 — a **−0.045 regression at 5.2σ_Δ paired**, well outside the σ_F1=0.0086 noise band measured by PR #48. Per per-agent attribution, the default-ON content-triggered agents emit findings that don't fuzzy-match back to the synthesized review (UNMATCHED FP volume jumped 51 → 180), and CRB's golden set largely doesn't reward error-handling or comment-rot detection. Same per-agent FP-concentration pattern that motivated Phase 5's `skipAgents: [test-quality, consistency]` decision (which gained +0.023 F1 by removing two similar agents from the default dispatch).

The agents themselves are useful for production review (Hora & Robbes 2026 documents real-world value of error-handling and comment-rot detectors); they just don't help leaderboard-style benchmark F1. Integrators who want them on PRs with relevant content should opt in:

```yaml
# .claude/soliton.local.md
agents:
  silent_failure:
    enabled: true
  comment_accuracy:
    enabled: true
```

### What's unchanged

- `agents/silent-failure.md` and `agents/comment-accuracy.md` agent definitions remain shipped (no functionality removed; just the dispatch default flipped).
- README badge stays at "Review_Agents-9" — the max-dispatch count is still 9 when both content-triggered agents are explicitly enabled.
- Phase 5.2's F1=0.313 remains Soliton's CRB number of record.

### What's NOT in this PR
- No changes to realist-check (Step 5.5, default OFF) — wiring stays as shipped.
- No changes to cross-file-impact graphSignals consumption — that lever held neutrally on TS in Phase 5.3 (+0.070 vs P3.5).

---

## v2.1.0 — 2026-04-29

Minor release. Adds v2-promised wirings that were previously dead code (3 agents),
codifies σ-aware pre-registration doctrine for CRB experiments, and adds two CI
workflows (hallucination-ast pytest gate + fixture-runner partial). 18 commits
since v2.0.1, 6 follow-up gaps closed (A4, A5 wiring, I7 wiring, D2, G1, G2-partial).

### New behavior (default ON)

These three agents were registered in `plugin.json` since v2.0.0 but had no dispatch
path, so they never actually ran. v2.1.0 wires them in:

- **`agents/realist-check.md`** (PR #50) — post-synthesis pressure-test pass for
  CRITICAL findings, gated on `synthesis.realist_check` (default OFF for cost
  control). Wired as Step 5.5 in SKILL.md.
- **`agents/silent-failure.md`** (PR #51) — content-triggered dispatch when diff
  contains try/catch/Promise/optional-chaining patterns. Default ON via
  `agents.silent_failure.enabled: true`.
- **`agents/comment-accuracy.md`** (PR #51) — content-triggered dispatch when diff
  modifies comment-marker lines. Default ON via `agents.comment_accuracy.enabled:
  true`.

The two content-triggered agents only fire on diffs that actually touch the
relevant patterns; PRs without try/catch or comment edits dispatch zero additional
agents. The README badge bumped from `Review_Agents-7` → `9` to reflect the new
max-dispatch count.

### New behavior (opt-in via graph signals)

- **`agents/cross-file-impact.md`** (PR #61) — when `graphSignals.dependencyBreaks[]`
  is present from Step 2.8, the agent now consumes graph-derived caller lists
  (confidence 90, deterministic) instead of Grep-walking the codebase. Falls
  through to v1 Grep behavior when graph absent.

### Measurement: judge-noise envelope

- **PR #48** measured GPT-5.2 judge variance: σ_F1 aggregate = 0.0086 across 4
  independent re-runs of `phase5_2-reviews/`. Mean F1 = 0.321; published v2.0.1
  number 0.313 was on the LOW edge. Documented in `bench/crb/judge-noise-envelope.md`.
- **PR #49** codified σ-aware pre-registration doctrine across `POST_V2_FOLLOWUPS.md`
  and `IMPROVEMENTS.md`. Future ship criteria require ≥ 2σ_Δ separation
  (= 0.024 aggregate, 0.036 per-language at n=10).

### CI infrastructure

- **`.github/workflows/hallucination-ast-tests.yml`** (PRs #55, #56, #58) — pytest
  gate on every PR touching `lib/hallucination-ast/`. 130 tests pass, 84% coverage.
- **`.github/workflows/fixture-runner.yml`** (PRs #59, #60) — fixture-runner
  partial automation. Two of three modes wired: structural validation (11 fixtures)
  + phase4b CLI assertion (2 fixtures). Full `/pr-review` mode remains auth-gated.
- **PR #57** — actionable preflight error in `soliton-review.yml` for missing
  ANTHROPIC_API_KEY. Either `claude_code_oauth_token` (PR #65) or Foundry-OIDC
  (closed PR #64 draft) are recommended substitute auth paths for Console-only orgs.

### Documentation

- `idea-stage/POST_V2_FOLLOWUPS.md` — added §A6 (combined Phase 5.3 CRB) and
  §G1/G2/G3 register; refreshed ranked priorities; closed entries for A4 / A5
  wiring / I7 wiring / D2 / G1 / G2-partial.
- `bench/crb/judge-noise-envelope.md` — full writeup of the σ measurement.
- `tests/run-fixtures.md` — § Automated coverage table; full `/pr-review` arm
  marked deferred pending auth.

### Cost

~$45 of measurement spend (Goal B / PR #48 only). All other PRs were $0 API
(docs, wirings, CI infra).


## What v2 adds

### New skill files (orchestration)

| File | Purpose | Model |
|---|---|---|
| `skills/pr-review/tier0.md` | Deterministic gate: lint/SAST/types/secrets/SCA pre-LLM. Emits `TIER_ZERO_START..END` with verdict `clean\|advisory_only\|needs_llm\|blocked`. Fast-path skips LLM on clean trivial PRs. | Haiku (dispatcher) |
| `skills/pr-review/graph-signals.md` | Queries sibling `graph-code-indexing` via `graph-cli` for blast radius, dependency breaks, taint paths, co-change, feature partitions, criticality. Emits `GRAPH_SIGNALS_START..END`. | Deterministic (no LLM) |

### New agent files

| File | Purpose | Model |
|---|---|---|
| `agents/spec-alignment.md` | Stage 0 spec-compliance + mechanical wiring-verification greps. Reads REVIEW.md / .claude/specs/ / PR description. | Haiku |
| `agents/realist-check.md` | Post-synthesis pressure-test pass for CRITICAL findings; mandates "Mitigated by:" for any downgrade (I6). | Sonnet |
| `agents/silent-failure.md` | Detects empty catches, swallowed Promise rejections, optional chaining hiding nullability, fallback-to-mock in prod, assertion-free tests (I7). | Sonnet |
| `agents/comment-accuracy.md` | Detects docstring/comment rot — params, return-type, `@deprecated` markers, example-code drift, stale NOTE/TODO/FIXME (I7). | Haiku |

### New rules files

| File | Purpose |
|---|---|
| `rules/tier0-tools.md` | Canonical tool catalog: ruff, eslint/biome, tsc/mypy, semgrep, gitleaks, osv-scanner, difftastic, jscpd. Invocations + exit-code contracts. |
| `rules/review-md-conventions.md` | REVIEW.md parsing spec — sections, syntax, wiring-verification grammar. Aligns with Anthropic managed Code Review convention. |
| `rules/model-tiers.md` | Haiku / Sonnet / Opus assignments per pipeline step and per agent. ~45 % cost drop on MEDIUM PRs. |
| `rules/graph-query-patterns.md` | `graph-cli` CLI contract: 8 queries (info, blast-radius, dep-diff, taint, co-change, feature-of, centrality, test-files-for). Error codes. Dependencies on graph-code-indexing. |
| `rules/stacked-pr-mode.md` | Stack-awareness design: `--parent <PR#>` / `--parent-sha` / `--stack-auto` flags. Graphite / gherrit / git-gud / feature-rebuild workflows (I8). |

### New workflow

| File | Purpose |
|---|---|
| `examples/workflows/soliton-review-tiered.yml` | 3-stage CI: Tier 0 → fast-path OR block OR LLM swarm. Runs `ruff / eslint / tsc / mypy / semgrep / gitleaks / osv-scanner` in parallel before any API calls. |

### New library specs (runtime code to follow in Phase 2)

| File | Purpose |
|---|---|
| `lib/hallucination-ast.md` | Library spec for the deterministic AST hallucination pre-check (Khati 2026 — 100 % precision, 87.6 % recall). Feeds into `agents/hallucination.md` as a free pre-check, saving ~80 % of Opus calls on Python PRs (I4). |

### Stage-1 research artifacts

All under `idea-stage/`:
- `IDEA_REPORT.md` — primary (5.4 k words, 20 ranked ideas, 4 Gate-1 options)
- `LITERATURE_REVIEW.md` — 36+ arXiv papers 2024-2026
- `OSS_ECOSYSTEM_REVIEW.md` — 7 plugin ecosystems + gstack/gherrit/oh-my-openagent
- `COMPETITOR_AGENTS_REVIEW.md` — 22 tools incl. Qodo #1, CodeRabbit #2, Copilot, BugBot, OpenHands, Devin, Gemini, Codex
- `DESIGN_TRADITIONAL_AND_GRAPH.md` — Tier 0 + Tier 1 architectural spec
- `MANIFEST.md` — index + Gate-1 decision record

## SKILL.md wiring (applied in this commit)

Insert three new steps between existing Step 2.5 (edge case handling) and Step 2.75 (chunking):

- **Step 2.6 — Tier 0 Deterministic Gate** — dispatches `skills/pr-review/tier0.md`.
  Fast-paths `clean` verdict (no LLM run), blocks `blocked` verdict.
- **Step 2.7 — Spec Alignment** — dispatches `agents/spec-alignment.md` (Haiku) with
  REVIEW.md + `.claude/specs/` + PR description as spec sources.
- **Step 2.8 — Graph Signals** — dispatches `skills/pr-review/graph-signals.md` to produce
  `GraphSignals{}`; falls back to grep heuristics if graph unavailable.

Update Step 2.75 chunking to prefer feature-partition grouping over directory grouping when
`GraphSignals.affectedFeatures` is present.

Update Step 3 (risk scorer) to consume `tier0Findings[]` and `graphSignals{}` — replacing the
Grep-based blast-radius heuristic with graph-derived transitive caller count, and adding two
new factors: `taint_path_exists` (20 % weight) and `feature_criticality` (10 % weight).

Update Step 4 (agent dispatch) to pass both `tier0Findings[]` and `graphSignals{}` into each
agent's prompt so agents can skip re-discovery and focus on reasoning.

Update Step 5 (synthesis) to:
- Append Tier-0 findings and spec-compliance findings into the same finding stream.
- Include an "Evidence Chain" section under each critical finding (graph edges + Tier-0
  source citations + prior-PR comment mine hits, where applicable).
- Apply a post-synth **Realist Check** pass (Sonnet): for every CRITICAL, require a
  "Mitigated by:" rationale for any downgrade; pressure-test with "What's the realistic worst
  case if merged?"

## How to validate these files without running them

- **Markdown validity**: all files are plain Markdown with YAML frontmatter where applicable;
  no dependencies.
- **YAML workflow syntax**: run `actionlint` on `examples/workflows/soliton-review-tiered.yml`
  (optional — the workflow uses only stable GH Actions features).
- **Reference consistency**: every file reference in `CHANGELOG_V2.md` points to a real file.
  Confirmed via `ls` after write.

## What v2 does NOT add yet (Tier C in IDEA_REPORT.md)

- Runtime code / libraries (`lib/graph-bridge.ts`, `lib/sarif-normalizer.ts`,
  `lib/hallucination-ast/*.ts`). Specs landed in this commit, implementation deferred to Phase 2.
- Tri-model cross-check (I10 — `--crossmodel`). Phase 2+.
- Execution sandbox (I19 — OpenHands-pattern verify-fix). Phase 2/3.
- Martian CRB publication (I9). Planned week 5-6 after pilot measurements.
- Pre-merge-checks DSL (I11 — CodeRabbit-style NL blockers). Nice-to-have.
- Hunk-grouping + tri-state severity UX (I12 — Devin-style). Nice-to-have.
- Learnings loop in `.omc/state/` (I16). Nice-to-have, needs cross-run state design.
- LSP / ast-grep integration (I17) for cross-file / hallucination agents. Deferred.

### Feature-flag additions for Tier-B items

| Flag | Default | Effect |
|---|---|---|
| `synthesis.realist_check` | `false` | Run `agents/realist-check.md` as post-synthesis pressure-test pass (wired in PR #50) |
| `agents.silent_failure.enabled` | `false` (was `true` in v2.1.0; reverted in v2.1.1 per Phase 5.3 evidence) | Content-triggered: dispatch when diff touches error-handling code (wired in this PR) |
| `agents.comment_accuracy.enabled` | `false` (was `true` in v2.1.0; reverted in v2.1.1 per Phase 5.3 evidence) | Content-triggered: dispatch when diff modifies comment lines (wired in this PR) |
| `stack.auto_detect` | `false` | Auto-detect Graphite/gherrit stack via `gt` CLI on PATH |

CLI flags (new): `--parent <PR#>`, `--parent-sha <SHA>`, `--stack-auto`.

## Rollout — 6-week pilot plan

See `idea-stage/IDEA_REPORT.md` §9. Summary:
- Week 1-2: ship Tier 0 + Haiku tiering + spec-alignment; collect overlap / escape data.
- Week 3-4: ship graph-signals Mode B + deterministic AST hallucination for Python.
- Week 4-5: Realist Check + silent-failure + comment-accuracy.
- Week 5-6: stack awareness + Martian CRB run + cost-normalised F1 blog.

## Compatibility

v2 is opt-in: if `.claude/soliton.local.md` doesn't set `tier0.enabled: true`, Soliton
behaves exactly as v1. Every new step is feature-flagged.

| Flag | Default | Effect |
|---|---|---|
| `tier0.enabled` | `false` | Turns on Tier 0 gate |
| `tier0.skip_llm_on_clean` | `false` | Fast-path skip on clean PRs |
| `spec_alignment.enabled` | `false` | Turns on Stage 0 spec-compliance |
| `graph.enabled` | `false` | Turns on graph-signals (requires `graph-cli` on PATH) |
| `synthesis.realist_check` | `false` | Post-synth Realist Check pass |

Roll out flag by flag with cohort measurement.
