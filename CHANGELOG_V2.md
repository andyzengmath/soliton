# Soliton v2 — Changelog (Stage 2 deliverables)

This changelog documents the v2 changes generated during `/research-pipeline` Stage 2
execution on 2026-04-18. All files are additive markdown/YAML — no runtime/build changes.

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
| `agents.silent_failure.enabled` | `true` | Content-triggered: dispatch when diff touches error-handling code (wired in this PR) |
| `agents.comment_accuracy.enabled` | `true` | Content-triggered: dispatch when diff modifies comment lines (wired in this PR) |
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
