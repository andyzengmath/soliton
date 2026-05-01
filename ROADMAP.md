# Soliton Roadmap

Forward-looking work tracked outside any single PR. Each section below maps to an idea
from `idea-stage/IDEA_REPORT.md` §5 or to an explicit deferral surfaced during review.

Status legend: 🟢 in progress · 🟡 next up · ⚪ backlog · ✅ shipped.

---

## In progress

### A · Martian Code Review Bench publication (I9)

🟢 **Goal**: run Soliton against `withmartian/code-review-benchmark` and publish F1 +
**cost-normalised F1** (F1 per dollar) — closes the biggest procurement-readiness gap
(every leader in the 2026 landscape has a CRB number; Soliton has none).

**Status**: POC branch `feat/crb-benchmark-poc`. Infrastructure setup in progress.

**Phase 1 (this session)**:
- [ ] Clone / review the CRB harness
- [ ] Adapt Soliton invocation to CRB's expected reviewer-API shape
- [ ] Run a 5-10 PR proof-of-concept sample
- [ ] Compute initial F1 + per-PR cost numbers
- [ ] Commit a `bench/crb/` directory with the adapter + POC results

**Phase 2 (future session)**:
- [ ] Full-corpus run
- [ ] Publish blog post / repo README benchmark section
- [ ] Submit to `withmartian/code-review-benchmark` leaderboard if they accept external entries

---

## Next up

### B · Graph Signal Service runtime (I2)

🟡 **Goal**: wire `skills/pr-review/graph-signals.md` to an actual `graph-cli` binary
from the sibling [`graph-code-indexing`](../Logical_inference/graph-code-indexing)
repo, so the skill's 8 documented queries (blast-radius, dep-diff, taint, co-change,
feature-of, centrality, test-files-for, info) return real results rather than always
emitting `GRAPH_SIGNALS_UNAVAILABLE`.

**Depends on**: graph-code-indexing shipping:
- `graph-cli` CLI binary (packaged from existing `src/retrieval/*.ts`)
- Java parser (Gap B4 in that repo's `MANIFEST.md`)
- PPR centrality (Gap A1)
- Co-change edges (Gap A6)

Soliton side is ready; work is cross-repo and non-trivial.

### D · Deterministic AST hallucination pre-check runtime (I4)

🟡 **Goal**: implement the Khati et al. 2026 pattern (arXiv 2601.19106) as a standalone
`@soliton/hallucination-ast` package — spec already lives in `lib/hallucination-ast.md`.
Plugs into `agents/hallucination.md` as a free deterministic pre-check for Python today
(100 % precision, 87.6 % recall on Khati's test set), TS/JS in a follow-up.

**Why**: removes the single most expensive Opus call in the pipeline for the ~80 % of
hallucination cases that reduce to "does this function exist in this package version".

**Effort**: ~1 week for Python implementation + corpus validation; self-contained in
soliton.

### C · Close the fixture-runner gap

🟡 **Goal**: the `## Deferred` section of `tests/run-fixtures.md` currently tracks:

- [ ] Automated shell / CI runner that feeds each fixture through the orchestrator and asserts against `expected.json`
- [ ] `tests/fixtures/tier0-blocked-cve/` (OSV-scanner critical CVE → `blockReason: cve_critical`)
- [ ] `tests/fixtures/tier0-blocked-type-error/` (tsc fatal type error → `blockReason: type_error_fatal`)
- [ ] `tests/fixtures/tier0-needs-llm/` (non-trivial clean diff → default Tier-0 path)
- [ ] `.gitleaksignore` or inline `# gitleaks:allow` on the `tier0-blocked-secret` fixture so consumers' full-tree scans don't false-positive

~2 hours total. Converts our semantic fixtures from docs-only to regression-guarding. Lower-priority than A/B/D but close-the-loop useful.

### Also close the TODO in `examples/workflows/soliton-review-tiered.yml`

- [x] **Done 2026-04-30** (PRs #72 + #73 + #77 + #79). All `examples/workflows/*.yml` files now clone `--branch v2.1.1`; `docs/ci-cd-integration.md` quickstart snippets bumped via PR #79. Original TODO retained as a checked-off item for traceability.

---

## Backlog (from `idea-stage/IDEA_REPORT.md` §5)

⚪ **I10** Tri-model cross-check (`--crossmodel`) — send the diff to Codex/Gemini SDKs in parallel with Claude, surface disagreements as conflict findings (uniquely unavailable to Anthropic managed Code Review).

⚪ **I11** Pre-merge-checks DSL — CodeRabbit-style NL rules as blockers in `.claude/soliton.local.md`.

⚪ **I12** Hunk-grouping + tri-state severity UX (Devin-style red/yellow/gray tagging + inline chat).

⚪ **I16** Learnings loop in `.omc/state/` — per-repo memory of accepted/rejected findings that biases future reviews (CodeRabbit pattern).

⚪ **I17** LSP / ast-grep tool access for cross-file + hallucination agents (pattern from oh-my-claudecode).

⚪ **I18** BugBot-style multi-pass + majority voting on CRITICAL-tier PRs only (cost-bounded).

⚪ **I19** Execution sandbox verify-fix loop (OpenHands pattern) — run the PR branch in Docker, reproduce asserted bugs, verify that suggested fixes compile + pass tests.

⚪ **I20** License-check dimension — flag new dependencies with non-OSS-compatible licenses.

---

## Already shipped (PR history)

- ✅ **PR #6** — v2 scaffold (I1 tier0, I3 spec-alignment, I5 model-tier Haiku rollout, I6 realist-check agent, I7 silent-failure + comment-accuracy agents, I8 stack-awareness flags, I2 graph-signals skill spec, I4 lib/hallucination-ast spec, SKILL.md Steps 2.6/2.7/2.8 wired) + 4 fix rounds
- ✅ **PR #7** (+ cleanup **PR #8**) — SHA-pinned Actions across all 6 workflows + Dependabot config + workflow-level `permissions: {}` default + verb-opening agent descriptions
- ✅ **PR #11** — v2 test fixtures (`tier0-clean`, `tier0-blocked-secret`, `tier0-advisory-only`, `spec-alignment-unmet-checklist`) + dogfood `Assert review produced output` step + 5 self-review fixes
