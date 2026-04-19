# OSS Ecosystem Review: PR-Review Plugins & Skills

> Produced by a parallel research sub-agent during Stage 1 of `/research-pipeline`. Condensed
> from the full agent transcript; canonical analyses below.

---

## 1. Soliton baseline (for comparison)

7 parallel review agents + risk-scorer (6-factor, 0-100) + synthesizer (dedup/conflict/confidence).
Opus for security/hallucination; Sonnet elsewhere. Markdown / JSON / machine-consumable
`AgentInstruction[]` (`--feedback`). GitHub Actions CI via `anthropics/claude-code-action` +
`--plugin-dir`. Large-PR chunking at >1000 lines. Configuration via `.claude/soliton.local.md`.

---

## 2. superpowers (obra/superpowers)

**Architecture**: single `code-reviewer` subagent (`model: inherit`). No parallelism, no risk
scoring, no confidence scoring. Hand-off pattern with placeholders (`{WHAT_WAS_IMPLEMENTED}`,
`{BASE_SHA}`, `{HEAD_SHA}`, `{PLAN_OR_REQUIREMENTS}`).

**Unique features Soliton lacks**:
- `receiving-code-review` skill — protocol for how the **implementer** responds to feedback.
  Explicitly forbids "You're absolutely right!" agreement, mandates verify-before-implement.
- `verification-before-completion` — "Iron Law": no completion claims without fresh verification
  output. Gates merge on `npm test` / `pytest` / `cargo test` actually running with 0 failures.
- Plan alignment as first-class — compares implementation against `{PLAN_OR_REQUIREMENTS}`
  before code quality.
- `finishing-a-development-branch` — 4 exact options (merge / PR / keep / discard), typed
  "discard" confirmation, tests-pass gate.

**Weaknesses vs Soliton**: no risk adaptation, no specialist agents, no confidence scoring,
no cross-file analysis, single-point-of-failure reasoning, no machine output.

**Idea to steal**: plan-alignment Stage 0 + sibling skill `receiving-soliton-findings` for
coding agents.

---

## 3. oh-my-claudecode (OMC)

**Architecture**: 4 standalone agents.
- `code-reviewer` (Opus, disallows Write/Edit) — 2-stage: spec compliance → code quality.
  Uses `lsp_diagnostics` + `ast_grep_search`.
- `critic` (Opus) — adversarial gate-keeper. Pre-commitment predictions, multi-perspective
  (security/new-hire/ops for code; executor/stakeholder/skeptic for plans), pre-mortem,
  **Self-Audit + Realist Check**, escalation to ADVERSARIAL mode on CRITICAL findings.
- `security-reviewer` (Opus) — full OWASP Top 10, severity × exploitability × blast-radius,
  runs dependency audits (`npm audit`, `pip-audit`).
- `verifier` (Sonnet) — evidence-based completion gate; runs tests + `lsp_diagnostics_directory`
  + build; acceptance-criteria matrix.

**Unique features**:
- **Tri-model orchestration (`ccg`)** — `omc ask codex` + `omc ask gemini`, Claude synthesizes
  disagreements. Decomposes into codex-style (architecture/correctness) vs gemini-style
  (UX/alternatives) prompts.
- **`ai-slop-cleaner`** skill — writer/reviewer separation enforced; classifies slop by type
  (duplication / dead code / needless abstraction / boundary violations / missing tests);
  runs one smell-focused pass at a time.
- **LSP + ast-grep integration** — structural (not grep) reference search.
- **Pre-commitment predictions + Realist Check** — reviewer predicts 3-5 likely problem areas
  *before* reading, then compares findings; Realist Check pressure-tests CRITICALs with a
  "Mitigated by:" rationale mandated for any downgrade.
- **ADVERSARIAL escalation mode** on 1 CRITICAL / 3+ MAJOR / systemic-pattern signals.
- **Doctrinally enforced writer/reviewer separation** — "Never approve your own authoring
  output in the same active context."

**Weaknesses vs Soliton**: no risk scorer, no parallel swarm, no synthesizer, no machine output,
no GitHub Actions.

**Ideas to steal (ranked)**:
1. `--crossmodel` mode (Codex/Gemini second opinion on disagreements).
2. Realist Check + Self-Audit post-pass in synthesizer.
3. ADVERSARIAL escalation when critic finds 3+ MAJOR.
4. LSP / ast-grep tool access for cross-file-impact and hallucination agents.
5. Multi-perspective pass as fourth lens in synthesizer.

---

## 4. quantum-loop

**Architecture**: strict two-stage sequential review.
- **Stage 1 `spec-reviewer`** — reads PRD + acceptance criteria + FR-N for the story ID.
  Each criterion → `satisfied` / `not_satisfied` / `partially_satisfied`. Pure JSON output.
  Includes a **wiring-verification mechanical check** — grep for the exact string specified
  in `wiring_verification` field; absent = CRITICAL. **No LLM judgment, pure string match.**
- **Stage 2 `quality-reviewer`** — only runs if Stage 1 passes. Covers error handling, type
  safety, organization, architecture, test quality, security, performance, and coding-standards
  compliance (reads `codebasePatterns` from `quantum.json` + `.claude/rules/*.md` + CLAUDE.md).
  **Violations of documented rules are CRITICAL.**
- **Stage 3 cross-story integration** — after all stories pass. Call-chain tracing (actually
  called, not just imported), type consistency across story boundaries, dead-code scan,
  import resolution. LSP when available.

**How separation is enforced**: Stage 2 agent prompt says "Do NOT comment on spec compliance —
previous reviewer's job." Orchestrator anti-rationalization table: "Run both stages in parallel
to save time" → "Stage 2 is wasted effort if Stage 1 fails."

**Unique features**:
- **Mechanical wiring verification** — non-LLM grep-based check. Exploits that LLMs rationalize
  ("it's equivalent"); wiring check cannot.
- **Cross-story integration review** — catches "function defined but never called" dead code
  across PR boundaries.
- **Coding-standards-as-config** — `quantum.json` + `.claude/rules/` feed review directly;
  documented rules = CRITICAL violations.
- **Retry-once-then-fail** — one fix attempt; second failure marks story failed.

**Weaknesses vs Soliton**: only 2 review dimensions, sequential (no parallelism), no confidence
scoring, no risk adaptation, heavy quantum.json prerequisite.

**Ideas to steal**:
1. Two-stage gate: spec-alignment → quality (Stage 0 spec-reviewer reading `.claude/specs/`
   or REVIEW.md).
2. Mechanical wiring verification in synthesis (for PRs claiming specific call-site edits).
3. Stage-3 integration review as separate `--integration` mode.
4. "Documented rule violations are CRITICAL" policy from CLAUDE.md / `.claude/rules/`.

---

## 5. claude-plugins-official / code-review

**Architecture**: 5 parallel Sonnet agents + Haiku dispatchers + Haiku scorer.
1. Haiku gate (PR closed / draft / trivial / already reviewed)
2. Haiku — list relevant CLAUDE.md files
3. Haiku — summarize the change
4. **5 Sonnet agents in parallel**:
   - Agent 1: CLAUDE.md compliance audit
   - Agent 2: Shallow scan for obvious bugs (only changes, no extra context)
   - Agent 3: Git blame/history analysis
   - Agent 4: **Previous PRs on same files** — check if past comments apply
   - Agent 5: **Code comments in modified files** — check change complies with them
5. Per-issue Haiku confidence scorer — explicit 0/25/50/75/100 rubric. Filters below 80.
6. Post-review Haiku re-check eligibility (race condition: PR may have closed).
7. `gh pr comment` with full-SHA permalinks (`https://github.com/owner/repo/blob/<full-sha>/file#Lstart-Lend`).

**Unique features**:
- **Explicit 0-100 confidence scoring rubric** passed verbatim to scoring agent. Buckets:
  0 (false positive), 25 (maybe), 50 (real but minor), 75 (likely real), 100 (certain).
- **Post-review eligibility re-check** — race-condition guard.
- **Full git-SHA permalinks mandatory** (refuses `$(git rev-parse HEAD)` since Markdown renders literally).
- **Explicit false-positive catalog** enumerating 7 classes to filter (pre-existing, type
  errors, style nits, lint-ignored, intentional, pedantic, unchanged lines).
- **Prior-PR comment mining as its own agent (Agent 4)** — Soliton's historical-context doesn't
  do this.
- **Haiku-for-dispatch pattern** — every non-reasoning step uses Haiku; only the 5 parallel
  reviewers get Sonnet.

**Weaknesses vs Soliton**: no risk adaptation, no dedicated security/hallucination/cross-file,
no synthesizer, 5 agents overlap (CLAUDE.md + comments agents flag same issue).

**Ideas to steal**:
1. **Haiku-for-dispatch tiering** — move eligibility/filter/summarize to Haiku; only reviewers
   on Sonnet.
2. **Prior-PR comment mining** as new agent or extension of historical-context.
3. **Post-review eligibility re-check** — cheap insurance.
4. **Full-SHA permalink format** mandatory.
5. **Reviewing prior code comments as guidance** — check change complies with in-file comments.

---

## 6. everything-claude-code / code-reviewer

**Architecture**: single-pass Opus code-reviewer. Structured checklist: security (CRITICAL),
code quality (HIGH), performance (MEDIUM), best practices (MEDIUM). Outputs severity + bad/good
code examples. Approval: ✅ no CRITICAL/HIGH, ⚠️ MEDIUM-only, ❌ any CRITICAL/HIGH.

**Unique features**:
- **License check for integrated libraries** — Soliton has zero license review.
- **Bad/good code example format enforced** — `// ❌ Bad` vs `// ✓ Good` side by side.
- **Commit-blocking in-scope** — designed to run from pre-commit hook.

**Weaknesses vs Soliton**: same as superpowers (single agent, no specialization).

**Ideas to steal**:
1. **License-checking dimension** — flag new dependencies with non-OSS-compatible licenses.
2. **Enforced bad/good code format** in synthesizer output.

---

## 7. pr-review-toolkit (claude-plugins-official)

**Architecture**: 6 specialist agents (Opus/inherit), file-type-triggered selective dispatch:
- **comment-analyzer** — comment-rot / accuracy. Cross-references every claim in comments
  against code.
- **pr-test-analyzer** — behavioral (not line) coverage; rates gaps 1-10.
- **silent-failure-hunter** — elite error-handling auditor. Catch-block specificity, project
  logging awareness (`logError`, `logForDebugging`, `errorIds.ts`).
- **type-design-analyzer** — 4 dimensions on 1-10 scale (encapsulation / invariant expression /
  usefulness / enforcement). Calls out anti-patterns (anemic domain models, mutable internals).
- **code-reviewer** — general CLAUDE.md compliance + bug detect, 0-100 confidence, ≥80 threshold.
- **code-simplifier** — post-review polish. Runs *after* passing review.

Command dispatch: test files → test-analyzer; error-handling changes → silent-failure-hunter;
new types → type-design-analyzer; always → code-reviewer.

**Unique features**:
- **Sharper dimension specialization** than Soliton's. Cleanly separates comment-rot, silent-
  failure, type-design, test-quality.
- **Silent-failure-hunter** — explicitly tuned for silent failures (empty catches, swallowed
  exceptions, optional chaining that hides errors, fallback-to-mock in prod).
- **Type-design 4-dim rubric** — quantified, comparable across reviews.
- **File-type-triggered dispatch** — simpler than Soliton's weighted model.
- **code-simplifier as post-review polish** — runs after passing review.

**Weaknesses vs Soliton**: no risk scorer, no synthesizer (6 agents could overlap findings),
no cross-file, no hallucination, no historical, no confidence threshold across agents.

**Ideas to steal**:
1. **Silent-failure-hunter as a 9th Soliton agent** (or merge into correctness).
2. **Comment-accuracy agent** triggered when comments changed.
3. **Type-design rubric** — lightweight extension to correctness or new agent.
4. **code-simplifier as optional post-pass** after `/pr-review --apply-fixes`.
5. **File-type-triggered selective dispatch** as cheaper alternative on trivial PRs.

---

## 8. User-mentioned ecosystems

### 8a. Graphite ("gstack") — `withgraphite/graphite-cli` + graphite.com

- `gt` CLI manages stacks of dependent branches; stacked PRs auto-rebase when earlier ones merge.
- **Graphite Diamond + Graphite Agent** — AI review on PR-open, positioned as "fewer but
  better comments."
- **Stack-aware merge queue** — CI runs on final batches, not per-PR-in-stack.
- **Cursor Cloud Agents integration** — review + apply fixes inside PR page.
- Closed source, proprietary. [Internal agent count not disclosed.]

**Unique feature that matters to Soliton**: **stack awareness in review**. When reviewing
PR #3 of a 3-PR stack, the reviewer knows PRs #1-2 are in-flight. Soliton reviews each PR
in isolation — a real gap for teams using stacked workflows (Microsoft, Meta, Shopify,
AI-native teams).

**Idea to steal**: `/pr-review --parent <PR#>` reviews only the delta between this PR and
its parent PR's head. No other OSS AI reviewer does this; Anthropic Code Review does not either.

### 8b. "gsd" — ambiguous

Likely refers generically to git-stacked-diff tooling or Gerrit-style workflows. Candidates:
- **gherrit** (joshlf/gherrit) — Gerrit-style stacked diffs on GitHub.
- **git-gerrit** (fbzhong/git-gerrit) — wrapper for Gerrit workflow.
- **git-gud** — stacked-diff CLI for GitHub/GitLab.

None have built-in AI review. Take-away: same as Graphite — stack-aware review is underserved.

### 8c. oh-my-openagent (code-yeongyu/oh-my-openagent)

Parallel-agent harness around Codex CLI (not Claude Code). Agents: Sisyphus (Opus 4.7 /
Kimi K2.5 / GLM-5 orchestrator), Hephaestus (GPT-5.4 deep worker), Prometheus/Oracle/
Librarian/Explore specialists. 25+ hooks. MCP integrations. No dedicated PR-review agent.

**Unique contribution**: **multi-model-by-default** — models picked by category
("visual-engineering", "deep work", "quick fixes", "ultrabrain logic"), not by name.

**Idea to steal**: **model-by-category pattern**. Soliton's agents pick models by name (Opus
for security/hallucination, Sonnet elsewhere). Category-based routing is more future-proof
— a new model (Opus 4.8, Sonnet 5) is picked up automatically without agent-file edits.

---

## 9. Comparison matrix

| Dimension | Soliton | Superpowers | OMC | Quantum-Loop | claude-plugins | everything-cc | pr-review-toolkit | Graphite | oh-my-openagent |
|---|---|---|---|---|---|---|---|---|---|
| # Review Agents | 7 + risk + synth | 1 | 4 | 2-3 seq | 5 + dispatchers | 1 | 6 (selective) | undisclosed | n/a |
| Parallel/Sequential | Parallel | Single | Independent | **Sequential** | Parallel (fixed 5) | Single | Parallel | [?] | n/a |
| Risk-adaptive | **Yes (6-factor)** | No | No | No | No | No | File-type heuristic | [?] | No |
| Confidence scoring | 0-100 per finding | No | No | No | **0-100 via Haiku** | No | Partial | [?] | No |
| Model mix | Opus+Sonnet by agent | inherit | Opus/Sonnet | single | Sonnet+Haiku | Opus | Opus | [?] | **By category (Opus/GPT/Kimi/GLM)** |
| Spec-alignment | No | **Yes** | Yes (Stage 1) | **Yes (dedicated)** | CLAUDE.md only | CLAUDE.md | No | No | No |
| Security agent | **Yes** | No | **Yes (OWASP+deps)** | Part of quality | Shared | Part of reviewer | silent-failure only | [?] | No |
| Hallucination | **Yes (dedicated)** | No | No | No | No | No | No | [?] | No |
| Historical | **Yes** | No | No | No | **Yes + prior PR comments** | No | No | [?] | No |
| Cross-file | Yes (grep) | No | Implicit (LSP) | Stage 3 | No | No | No | [?] | No |
| LSP awareness | No (grep) | No | **Yes** | **Yes** | No | No | No | [?] | [?] |
| Comment-rot | No | No | No | No | Agent 5 | No | **Yes** | No | No |
| Silent-failure | Partial | No | No | No | No | No | **Yes** | [?] | No |
| Stack awareness | No | No | No | Stage-3 integration | No | No | No | **Yes (native)** | No |
| Tri-model | No | No | **Yes (ccg)** | No | No | No | No | Partial | **Yes** |
| Writer/reviewer separation | Implicit | Strong | **Enforced** | Explicit | Implicit | No | Implicit | [?] | [?] |
| Verification pass / Realist | No | **Yes** | **Yes** | No | Post-review re-check | No | No | [?] | No |
| Synthesis / dedup | **Yes** | No | No | No | No | No | No | No | No |
| Machine output | **Yes (`--feedback`)** | No | No | JSON only | No | No | No | [?] | No |
| CI/CD shipped | **Yes (4 workflows + docs)** | No | No | No | gh-ready | No | No | Hosted | No |
| Pre-existing bug sev | No | No | No | No | No | No | No | [?] | No |
| License check | No | No | No | No | No | **Yes** | No | [?] | No |
| Open source | MIT | Open | Open | Open | Open | Open | Open | Proprietary | Open |

---

## 10. Top 10 ideas to steal (ranked by impact/effort for Soliton)

1. **Spec-alignment Stage 0** (quantum-loop + superpowers) — read REVIEW.md / .claude/specs/ /
   PR description; check coverage of acceptance criteria before dispatching swarm.
2. **Realist Check + Self-Audit in synthesizer** (OMC critic) — pressure-test every CRITICAL
   with "What's the realistic worst case?" and mandate "Mitigated by:" for downgrades.
3. **Tri-model cross-check mode** (OMC ccg + oh-my-openagent) — `/pr-review --crossmodel`
   sends diff to Codex / Gemini via SDK; disagreements become conflict findings.
4. **Stack awareness** (Graphite / gherrit) — `/pr-review --parent <PR#>` reviews only delta
   vs parent PR in a stack.
5. **Silent-failure-hunter agent** (pr-review-toolkit) — empty catches, optional chaining that
   hides errors, fallback-to-mock in prod.
6. **Comment-accuracy agent** (pr-review-toolkit + claude-plugins Agent 5) — stale docstrings,
   comments contradicting code. Triggered when comments changed.
7. **Haiku-tiered dispatch** (claude-plugins-official) — move eligibility / filter / early
   summarization to Haiku. Only 7 review agents + synthesizer on Sonnet/Opus.
8. **Prior-PR comment mining** (claude-plugins-official Agent 4) — `gh pr list --state closed`
   on same files; check if past comments apply.
9. **Mechanical wiring verification** (quantum-loop) — for PRs claiming specific call-site
   edits, generate grep assertions in synthesis; run as deterministic checks.
10. **LSP / ast-grep tool access** (OMC) — replace grep-based call-site search with
    `lsp_find_references`; eliminates missed callers (method overloading, generics, interfaces).

**Status check**: Soliton already leads on risk-adaptive dispatch, synthesis/dedup,
hallucination detection, machine-consumable output, and CI/CD story. No other ecosystem
combines those four. The ideas above extend that lead by closing specific orthogonal gaps
without disturbing the core architecture.

---

**Sources**:

Web: [Graphite](https://graphite.com/) · [Graphite stacked-diffs guide](https://graphite.com/guides/stacked-diffs) · [Top 6 Graphite alternatives 2026 — aikido](https://www.aikido.dev/blog/graphite-alternatives) · [GitHub adds Stacked PRs — InfoWorld](https://www.infoworld.com/article/4158575/github-adds-stacked-prs-to-speed-complex-code-reviews.html) · [oh-my-openagent GitHub](https://github.com/code-yeongyu/oh-my-openagent) · [oh-my-openagent npm](https://www.npmjs.com/package/oh-my-openagent) · [gherrit](https://github.com/joshlf/gherrit) · [git-gerrit](https://github.com/fbzhong/git-gerrit) · [git-gud](https://nlopez.io/introducing-git-gud-a-stacked-diffs-cli-for-github-and-gitlab/) · [Stacked Diffs](https://newsletter.pragmaticengineer.com/p/stacked-diffs)

Local files analyzed (in `C:\Users\andyzeng\.claude\plugins\`):
- `cache\superpowers-marketplace\superpowers\4.0.3\skills\{requesting,receiving}-code-review\SKILL.md`, `verification-before-completion\SKILL.md`, `finishing-a-development-branch\SKILL.md`, `agents\code-reviewer.md`
- `cache\omc\oh-my-claudecode\4.11.3\agents\{code-reviewer,critic,security-reviewer,verifier}.md`, `skills\{ai-slop-cleaner,ccg,verify}\SKILL.md`
- `cache\quantum-loop\quantum-loop\0.3.4\agents\{spec-reviewer,quality-reviewer}.md`, `skills\ql-review\SKILL.md`
- `marketplaces\claude-plugins-official\plugins\code-review\commands\code-review.md`
- `marketplaces\claude-plugins-official\plugins\pr-review-toolkit\agents\*.md`, `commands\review-pr.md`
- `marketplaces\everything-claude-code\agents\code-reviewer.md`, `commands\code-review.md`
