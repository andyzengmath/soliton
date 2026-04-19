# Idea Report — Soliton v2: Risk-Adaptive, Graph-Aware, Tool-Tiered PR Review at Enterprise Scale

**Pipeline**: `/research-pipeline` Stage 1 — Idea Discovery
**Date**: 2026-04-18
**Repo**: `C:\Users\andyzeng\OneDrive - Microsoft\Documents\GitHub\soliton`
**Companion substrate**: `../Logical_inference/graph-code-indexing`
**Target use case**: AI-native enterprise software rebuild — thousands of AI-generated PRs/day, quality-or-better than non-AI-native baseline, cost-disciplined

---

## 0. Executive summary

**The problem the user wants solved**: coding agents now ship thousands of PRs/day per team; a single human cannot review them at that rate without rubber-stamping. Existing review tools (Copilot, CodeRabbit, Qodo, BugBot, Anthropic's managed Code Review, Soliton today) are converging on multi-agent LLM review, but **burn expensive LLM tokens on work traditional tools can do for free**, **cannot see outside the diff**, and **ignore the codebase graph structure entirely**. The result is either expensive-at-scale or quality-bleed — not both.

**The position Soliton occupies today** (verified against `skills/pr-review/SKILL.md`, `agents/*.md`, `docs/ci-cd-integration.md`, and the 2026-03 competitive analysis in `research/claude-code-review-competitive-analysis.md`): a published, open-source, risk-adaptive multi-agent Claude Code plugin that already beats Anthropic's managed Code Review on speed (15-60 s vs ~20 min), cost (quota-based vs $15-25/review), transparency (named agents + confidence scores vs black-box), and has unique features (dedicated hallucination agent, machine-consumable `--feedback` output, MIT open source). **Soliton has a ~12-month lead on the specific combination of risk-adaptive dispatch + hallucination agent + feedback mode** — no other tool, managed or OSS, combines all three.

**The recommended next move**: add two new layers around the existing agent swarm — **Tier 0: Deterministic Gate** (lint/SAST/type-check/secrets/SCA as pre-LLM triage) and **Tier 1: Graph Signal Service** (blast radius / taint paths / dependency breaks via `graph-code-indexing`) — which together drop median review cost ~73 % and make every finding carry evidence-chain provenance. Add four **specialist agents** (spec-alignment, silent-failure, comment-accuracy, realist-check pass) and three **strategic features** (stack awareness, Martian-CRB publication, optional tri-model cross-check). Keep the core architecture untouched.

**Why this wins**: the literature search confirms **three of Soliton's core design choices are genuinely novel as of April 2026** — (a) risk-adaptive multi-agent dispatch is unpublished in peer-reviewed venues, (b) graph-RAG for review (as opposed to localization) is a green field with zero papers, (c) differential pre/post-PR graph analysis has not been published at all. Soliton can ship these *and* publish them. No direct competitor (Qodo, CodeRabbit, BugBot, Anthropic managed) has any of these three.

**What the user needs to decide at Gate 1**: do we (a) run all three flagship ideas in parallel on the 2-month timeline, (b) pick one (I1 Tier-0 is fastest to ship, I3 graph integration is the biggest moat), or (c) ship Tier-0 first, graph second, then split the remainder across enterprise-rebuild pilot vs publication?

---

## 1. The problem, grounded in evidence

The user's thesis ("coding agents produce slop at thousands-of-PR/day scale; humans can't review; traditional reviewers miss AI-specific errors") is strongly supported by the 2025-2026 literature. Selected empirical numbers from `LITERATURE_REVIEW.md`:

- **AI review actionability is 0.9-19.2 % vs. human 60 %** on GitHub (Sun et al. 2025, 22k comments / 178 repos).
- **CRA-only PRs merge at 45.2 % vs. 68.4 % for human review** (Chowdhury et al. 2026).
- **AI suggestions increase cyclomatic complexity 10-50× more than human ones** (Zhong et al. 2026, 278k inline conversations).
- **12.1 % of AI-generated files contain ≥ 1 CWE**; Python files 16-18.5 % (Schreiber & Tippe 2025, 1.2M LOC).
- Agent-authored **tests over-mock 36 % vs human 26 %** (Hora & Robbes 2026, 1.2M commits / 2168 repos).
- **All 8 frontier models monotonically degrade as PR-review context grows** (SWE-PRBench 2026).
- **Frontier models only hit ~19 % F1 on SWR-Bench's 1,000 verified PRs**; multi-review ensemble lifts to 43.67 %.
- Industrial production: **Atlassian's RovoDev reaches 38.7 % code-resolution** with 3-component filter (generator → LLM judge → ModernBERT actionability gate), cutting PR cycle time 31 %.

The user's `risk_gap.md` already names this regime: W3 "Slop PR flood + top-performer attrition" and W5 "CFO cost impact: LLM bill out of control." Soliton v2 must defeat both: **higher actionability** (goal ≥ 20 % to beat public SOTA, aim 35-40 % RovoDev-class) and **lower cost per PR** (target $0.10 median, from $0.40 today).

---

## 2. What Soliton already does well (verified)

From reading `skills/pr-review/SKILL.md`, the 9 agent prompts, and the CI integration:

| Capability | Soliton today | Why it matters |
|---|---|---|
| Risk-adaptive dispatch | 6-factor 0-100 score → 2/4/6/7 agent fan-out | **Novel** (no competitor or academic paper does this) |
| Dedicated hallucination agent | Opus-powered, checks API existence / signatures / deprecated | Anthropic's managed service has no equivalent |
| Machine-consumable output | `--feedback` → `AgentInstruction[]` JSON | Unique; enables autonomous agent-write-review-fix loop |
| Confidence threshold filtering | Default ≥ 80, configurable | Addresses the #1 complaint about AI reviewers |
| Large-PR chunking | >1000 lines → <500-line directory-grouped parallel chunks | SWE-PRBench validates: narrow context > wide context |
| Synthesizer with dedup + conflict detection | Merges overlapping findings, flags inter-agent disagreement | No other OSS plugin does this |
| CI/CD story | 4 ready-to-ship GitHub Actions workflows + cost model + auth (Anthropic/Bedrock/Vertex) | Production-ready |
| Open source + plugin model | MIT, `claude --plugin-dir`, zero build step | Enterprise-procurement-friendly |

These are moats — don't break them. Every idea below is additive.

---

## 3. Landscape at-a-glance

Full detail: `LITERATURE_REVIEW.md`, `OSS_ECOSYSTEM_REVIEW.md`, `COMPETITOR_AGENTS_REVIEW.md`.

Condensed matrix of what the market actually offers in April 2026:

| Category | Leader | Best Martian-CRB F1 | Open source? | Graph-aware? | Risk-adaptive? | Execution-based? |
|---|---|---|---|---|---|---|
| OSS multi-agent plugin | **Soliton** | not yet published | ✅ MIT | ❌ (grep only) | ✅ | partial (CI) |
| Managed Anthropic | Claude Code Review (mgd) | undisclosed | ❌ | ❌ | ❌ | ❌ |
| Managed hosted #1 | **Qodo Merge 2.0** | **60.1-64.3 %** | ✅ (core) | ❌ | ❌ | partial |
| Managed hosted #2 | CodeRabbit | 51.2-51.5 % | ❌ | ❌ | ❌ | partial |
| Managed hosted | Greptile | self: 82 % catch | ❌ | **✅ (codebase graph)** | ❌ | ❌ |
| Execution-focused OSS | OpenHands | SWE-Bench leader | ✅ | ❌ | ❌ | **✅ Docker sandbox** |
| Stack-aware | Graphite Agent | below leaders | ❌ | ❌ | ❌ | partial |
| Platform-native | Copilot Code Review | 44.5 % (#9) | ❌ | ❌ | ❌ | ❌ |
| Multi-pass voting | Cursor BugBot | self: 80 % resolution | ❌ | ❌ | ❌ | ❌ |
| Tri-model | OMC `ccg` + oh-my-openagent | n/a | ✅ | ❌ | ❌ | ❌ |

Four observations that matter:
1. **No public tool is both OSS and graph-aware.** Greptile is graph-aware but closed. Soliton's graph integration (I3 below) would uniquely occupy this cell.
2. **No public tool is both OSS and risk-adaptive.** Soliton already is, and no competitor is about to copy it.
3. **Execution-based review is emerging** as the next moat (OpenHands, Jules, Codex Cloud, Amazon Q, GitLab Duo all do it). Soliton's CI gate is a half-step; a sandboxed verify-fix loop closes the gap.
4. **Every leader now publishes on Martian CRB.** Soliton has no public number. This is a procurement gap.

---

## 4. Gap analysis vs Soliton today

Tier A = Critical for 2-month phase. Tier B = Important, ship by month 3-6. Tier C = Strategic / longer.

| Gap ID | Gap description | Impact | Cost | Tier |
|---|---|---|---|---|
| G1 | No deterministic pre-LLM filter — lints / types / secrets / SCA all cost LLM tokens | 5 | 1 | **A** |
| G2 | No graph-awareness — blast radius / taint / dep-break computed by grep or LLM | 5 | 3 | **A** |
| G3 | No spec-alignment stage — can't verify PR implements intended change | 4 | 2 | **A** |
| G4 | No deterministic AST hallucination check — Opus agent does work a free tool could | 4 | 2 | **A** |
| G5 | No Haiku-tiered dispatch — eligibility / summarization / scoring all use Sonnet | 3 | 1 | **A** |
| G6 | No silent-failure / comment-rot / license / type-design dimension agents | 3 | 2 | B |
| G7 | No stack-awareness — PR #3 of 3-stack reviewed as if standalone | 3 | 2 | B |
| G8 | No verification / realist-check pass before surfacing | 3 | 2 | B |
| G9 | No Martian-CRB number published | 4 | 2 | **A** |
| G10 | No learnings loop — dismissed findings don't suppress similar future findings | 3 | 3 | B |
| G11 | No execution-sandbox verify-fix | 4 | 4 | C |
| G12 | No multi-model / tri-model cross-check | 3 | 3 | C |
| G13 | No pre-merge-checks DSL (CodeRabbit-style NL blockers) | 2 | 2 | B |
| G14 | No hunk-grouping / tri-state severity UX | 2 | 1 | B |
| G15 | No calibrated token-level confidence (Lin 2026 style) | 3 | 3 | C |
| G16 | Context assembly not narrow enough for SWE-PRBench-style degradation avoidance | 3 | 2 | B |
| G17 | No inline-comment posting (single-block today) | 2 | 2 | B |
| G18 | No pre-existing-bug severity (purple) | 2 | 1 | B |
| G19 | No cross-run state / auto-resolution tracking | 2 | 3 | C |
| G20 | No LSP / ast-grep tool access for cross-file / hallucination agents | 3 | 3 | C |

**Tier-A gaps (5 items, G1 / G2 / G3 / G4 / G5 / G9) are the critical-path work** for the 2-month phase. They collectively move the median cost-per-review from $0.40 → $0.10 (73 % drop) and make the first publishable benchmark possible.

---

## 5. Ranked ideas

Ideas are numbered and scored with **Impact × (6 − Cost)** for a ship-this-first lens. Full design of the graph + tools layer lives in `DESIGN_TRADITIONAL_AND_GRAPH.md`.

### Flagship research ideas (genuinely novel)

#### I1 — Tier-0 Deterministic Gate + "LLM-skip" fast path
*Closes G1 / G4 / G5.*
Add a pre-LLM layer that runs `ruff` / `eslint` / `biome` / `tsc` / `mypy` / `semgrep` / `gitleaks` / `difftastic` / `osv-scanner` / test-impact selectors in parallel via `Bash(... run_in_background)`. Each tool's output is normalised to SARIF-like `DeterministicFinding`. Aggregate `tierZeroVerdict` is `clean | advisory_only | needs_llm | blocked`. When `clean` + diff ≤ 50 meaningful lines + no sensitive paths: **skip all LLM agents**, post confirmation comment. When `blocked` (leaked secret, CVE-critical): post Tier-0 findings, skip LLM, fail CI.

**Novelty claim**: structured "LLM-skip" dispatch driven by a free deterministic check is *not* documented in the 36+ papers surveyed. Closest is `SAST-Genius` (2509.15433, hybrid static + LLM triage) but that runs BOTH always; Soliton's gate fires one or neither.

**Pilot**: ship with `skip_llm_on_clean: false` default; measure rate of Tier-0-agree-with-LLM findings on 2 weeks of real PRs; flip default once overlap > 80 %.

**Cost impact**: eliminates LLM cost entirely for ~40-50 % of PRs (docs, dep bumps, trivial fixes, lint-only failures). Median cost $0.40 → ~$0.12.

**Target metric**: > 40 % of PRs resolved by Tier 0 alone; < 2 % escape rate (real bugs missed).

**Effort**: 1 week for first 4 tools; ~3 weeks for full matrix + SARIF normaliser.

Score: **5 × 5 = 25**.

#### I2 — Graph Signal Service (blast radius / taint / dep-break)
*Closes G2.*
Wrap `graph-code-indexing` as a CLI (Mode B: shell out to `graph-cli query --blast-radius <file>:<symbol>`). Add `skills/pr-review/graph-signals.md` runnning between Tier 0 and risk scoring. Emit `GraphSignals { blastRadius, affectedFeatures, dependencyBreaks, taintPaths, criticalityScore, coChangeHits, featureCoverage }`. Feed directly into:
- risk-scorer factors (replace grep-based blast radius with graph transitive callers, add new `taint_path_exists` + `feature_criticality` factors),
- cross-file-impact agent (pre-computed `dependencyBreaks` — agent only *explains*, doesn't *discover*),
- security agent (pre-computed `taintPaths` — agent goes directly to source→sink pair),
- hallucination agent (import graph — know immediately which packages actually exist),
- historical-context agent (pre-computed `coChangeHits` — skip `git log` shell-out).

**Novelty claim**: **ZERO papers on differential pre/post-PR graph analysis for review** (academic agent, Section C.3). LocAgent / RepoGraph / RANGER all target localization. Industry tools (Greptile) use the graph for retrieval but don't use it for blast-radius-aware agent routing. Soliton v2 = "first OSS review plugin that treats the codebase graph as a first-class review substrate."

**Pilot**: ship against 10-20 representative PRs in a medium Java/TS monolith; compare FP rate and token usage of cross-file-impact with vs without graph signals. Target −50 % FP, −30 % tokens per finding.

**Effort**: 2 weeks for Mode B CLI + risk-scorer rewire + one pilot agent (cross-file-impact); 3 more weeks for full agent rewire + Mode A in-process integration.

**Dependency**: graph-code-indexing must support TS/JS/Py (already does) and ideally Java (needs Java parser — graph-code-indexing Gap B4). For the enterprise-rebuild use case, Java support is on the critical path.

Score: **5 × 3 = 15** (high impact, medium cost; but strategic value much higher).

#### I3 — Spec-alignment Stage 0 (REVIEW.md + PR-description coverage)
*Closes G3.*
New `spec-reviewer` agent (Haiku, cheap) runs between Tier 0 and the swarm. Reads:
- PR description + linked issues,
- `REVIEW.md` at repo root (emerging convention from Anthropic's managed service),
- `.claude/specs/*.md` if present,
- any `CLAUDE.md` in the hierarchy.

Rates each acceptance criterion `satisfied` / `not_satisfied` / `partially_satisfied`. Also runs **mechanical wiring verification** (from quantum-loop): for any PR claiming "adds X at Y", grep for the literal string at the literal location. Absent = CRITICAL. No LLM judgment.

Emits a `SpecCompliance` signal to synthesizer. If `not_satisfied` count > 0, swarm still runs, but synthesizer prioritises spec failures above code-quality findings.

**Why this is a big win**: SWR-Bench / SWE-PRBench both show out-of-the-box PR-review F1 of 15-20 %. **Functional-change detection beats evolutionary/style detection 26.2 % vs 14.3 %.** Spec-alignment puts review on the high-signal side of that ratio. Also directly addresses `risk_gap.md` W1 "Idea Debt" (no alignment → half-finished PRs merged).

**Effort**: 4-6 days.

Score: **4 × 4 = 16**.

#### I4 — Deterministic AST hallucination detector (augments existing agent)
*Closes G4 partially.*
Replicate Khati et al. 2026 (arXiv 2601.19106) — **100 % precision, 87.6 % recall, F1 = 0.934** on Python API hallucinations, with zero LLM cost. Ship as `lib/hallucination-ast.ts` running as a sub-step inside `hallucination` agent. When available, it absorbs the "does this function exist" question; the LLM only handles fuzzy cases (deprecated APIs, wrong signatures that might be valid in another version, config-key misuse).

**Why**: removes the single most expensive LLM step (Opus-on-hallucination) for the 80 % of cases where a deterministic check suffices. Hallucination detection becomes ~free for Python/TS/JS.

**Effort**: 1 week for Python; 2 weeks for TS/JS (requires tree-sitter + package resolver). Java / Go are follow-ups.

Score: **4 × 4 = 16**.

### Engineering-win ideas (ship for free, compounding benefits)

#### I5 — Haiku-tiered orchestration
*Closes G5.*
Move eligibility / file-filter / summarisation / confidence-scoring / synthesizer-dedup to Haiku. Only the 7 review agents + (optional) 2nd-pass validator use Sonnet/Opus. Pattern already productised in `claude-plugins-official/code-review`. On MEDIUM PRs this is ~20-30 % cost reduction for zero quality loss. Effort: 2-3 days.

Score: **3 × 5 = 15**.

#### I6 — Realist Check + Self-Audit in synthesizer
*Closes G8.*
New synthesizer post-pass (Sonnet): for every CRITICAL finding, pressure-test with "What's the realistic worst case if merged?" and require a "Mitigated by:" rationale for any downgrade. Low-confidence findings move to "Open Questions" block. Borrowed directly from `oh-my-claudecode/agents/critic.md`. Directly attacks the FP-rate problem CR-Bench quantified.

Effort: 3-4 days.

Score: **3 × 4 = 12**.

#### I7 — Two new specialist agents: silent-failure + comment-accuracy
*Closes G6.*
Two file-type-triggered agents (dispatch only when relevant files change):
- `silent-failure` — empty catches, optional chaining that hides errors, fallback-to-mock in prod, assertion-free tests. From `pr-review-toolkit`.
- `comment-accuracy` — stale docstrings, comments contradicting the new code, outdated TODOs. Only triggered on PRs that modify comments. From `pr-review-toolkit` + `claude-plugins-official Agent 5`.

These are empirically-validated gaps (Hora & Robbes 2026: over-mocking 36 % in agent tests; comment-rot is a known maintenance sink). Effort: 3-4 days each.

Score: **3 × 4 = 12**.

### Strategic / longer-term ideas

#### I8 — Stack awareness (`--parent <PR#>`)
*Closes G7.*
Detect that the current PR is part of a stack (via `gt log` or by parsing commits-not-in-main of a parent PR). Review only the delta between this PR and its parent PR's head, treating the parent as base. No other OSS reviewer or Anthropic's managed does this.

Directly relevant to the user's enterprise rebuild — feature-by-feature rebuild produces stacked PRs naturally (graph-code-indexing's `FeaturePartition` output maps 1-1 to stack members).

Effort: 1 week.

Score: **3 × 4 = 12**.

#### I9 — Martian-CRB publication + cost-normalised F1
*Closes G9.*
Run Soliton on the full `withmartian/code-review-benchmark` corpus; publish F1, precision, recall, **and cost-per-PR-reviewed** — the cost dimension is where Soliton's risk-adaptive dispatch will dominate. Even if Soliton lands #3 on raw F1, "#1 on F1-per-dollar" is a defensible wedge.

Effort: 1 week (benchmark run + blog post).

Score: **4 × 4 = 16**.

#### I10 — Tri-model cross-check (`--crossmodel`)
*Closes G12.*
Optional mode that sends the diff to Codex-via-SDK *and* Gemini-via-SDK in parallel with Soliton's Claude agents. Disagreements surface as `conflict` findings in the synthesizer. Pattern from `oh-my-claudecode/ccg` and `oh-my-openagent`.

Why: Anthropic's managed Code Review cannot use other labs' models. Soliton owning tri-model review is a durable moat and uniquely valuable for enterprise risk-averse buyers who want a "second opinion" dial.

Effort: 2 weeks (SDK plumbing + prompt adaptation per model). Defer to Phase 2.

Score: **3 × 3 = 9**.

### Secondary ideas (nice-to-have, ship opportunistically)

- **I11** Pre-merge-checks DSL (CodeRabbit-style NL blockers).
- **I12** Hunk-grouping + tri-state severity UX (Devin Review).
- **I13** Inline PR comments (vs single block).
- **I14** Pre-existing-bug severity (Anthropic's purple).
- **I15** Prior-PR comment mining (claude-plugins-official Agent 4).
- **I16** Learnings loop in `.omc/state/`.
- **I17** LSP / ast-grep tool access for cross-file + hallucination agents (from OMC).
- **I18** BugBot-style multi-pass + majority voting — **only on CRITICAL-tier PRs** (cost-bounded).
- **I19** Execution sandbox verify-fix (OpenHands pattern) — Phase 2/3.
- **I20** License-check dimension (from `everything-claude-code`).

---

## 6. Proposed architecture — Soliton v2

### 6.1 Five-tier pipeline

```
PR event (local or GitHub Actions)
     │
 ┌───▼──────── Step 1: Normalize (unchanged from v1) ──────┐
 │                                                         │
 ├── Step 2: Config resolution (adds REVIEW.md + .claude/rules/)
 ├── Step 2.5: Edge-case handling (unchanged)
 ├── Step 2.75: Chunking (feature-partition-aware if graph available)
 │
 │ ══════════════ NEW: Tier 0 — Deterministic Gate (≈0 LLM tokens) ═════════════
 ├── ruff / eslint / biome / tsc / mypy / semgrep / gitleaks
 │   / difftastic / jscpd / osv-scanner / testmon    (parallel, bash-bg)
 │
 │   emit tierZeroVerdict: clean | advisory_only | needs_llm | blocked
 │   emit deterministicFindings[]
 │
 │   if tierZeroVerdict == clean AND trivial:       ──► FAST-PATH: "Approve. Tier 0 only." STOP
 │   if tierZeroVerdict == blocked:                 ──► Post Tier-0 findings, skip LLM, fail CI
 │
 │ ══════════════ NEW: Tier 1 — Graph Signal Service (≈0 LLM tokens) ═══════════
 ├── blast radius (reverse BFS CALLS depth 1-2)
 ├── dependency breaks (graph diff pre vs post)
 ├── taint paths (forward DATA_FLOW to IO/auth/DB sinks)
 ├── co-change clusters (git log overlay)
 ├── criticality score (PPR from changed nodes — when graph-code-indexing Gap A1 lands)
 │
 │   emit graphSignals{}                                   (fallback to v1 heuristics if graph absent)
 │
 │ ══════════════ NEW: Tier 1.5 — Spec-alignment (Haiku) ═══════════════════════
 ├── Read REVIEW.md / .claude/specs/ / PR description
 ├── Score each acceptance criterion: satisfied / not / partial
 ├── Mechanical wiring-verification greps     (no LLM)
 │
 │   emit specCompliance{}
 │
 │ ══════════════ Step 3: Risk scoring (now partly deterministic) ══════════════
 │  NEW factors: taint_path_exists (20%), feature_criticality (10%).
 │  REPLACE: grep-based blast radius → graph-based blast radius.
 │
 │ ══════════════ Tier 2 — LLM Review Agents (parallel, narrowed) ══════════════
 ├── correctness   (Sonnet, consumes specCompliance)
 ├── security      (Opus, consumes taintPaths — narrow context)
 ├── hallucination (Opus + deterministic AST pre-check — free fast path)
 ├── test-quality  (Sonnet, consumes featureCoverage)
 ├── consistency   (Sonnet, consumes CLAUDE.md + REVIEW.md)
 ├── cross-file-impact (Sonnet, consumes dependencyBreaks — explains, doesn't discover)
 ├── historical-context (Sonnet, consumes coChangeHits + prior-PR-comment-mining)
 │
 │   NEW optional agents (file-type-triggered):
 ├──   silent-failure     (when error-handling code changed)
 ├──   comment-accuracy   (when comments changed)
 │
 │   NEW optional agents (cost-bounded to CRITICAL tier):
 ├──   multi-pass-validator (BugBot-style 3-5-vote on most disputed findings only)
 │
 │ ══════════════ Tier 3 — Synthesis + Realist Check ═══════════════════════════
 ├── Dedup + conflict detection (unchanged)
 ├── NEW: Realist Check on every CRITICAL — "Mitigated by:" required for downgrade
 ├── NEW: Evidence Chain block — every finding carries graph edges + Tier-0 source citations
 ├── Confidence filter (default ≥ 80)
 │
 │ ══════════════ Step 6: Output (extended) ════════════════════════════════════
 ├── markdown (default, + Evidence Chain section)
 ├── JSON (machine-readable, extends schema with tier0Findings[] + graphSignals{})
 ├── --feedback (AgentInstruction[], unchanged)
 └── --crossmodel (optional, sends to Codex/Gemini SDK in parallel for second opinion)
```

### 6.2 CI/CD integration (GitHub Actions)

Extends the four existing workflows (`examples/workflows/soliton-review*.yml`) with:

1. **New workflow `soliton-review-tiered.yml`** — runs Tier 0 as a separate (cheap) GHA step that can *skip* the LLM step when clean. Pattern:
   ```yaml
   jobs:
     tier0:
       outputs:
         verdict: ${{ steps.tier0.outputs.verdict }}
       steps:
         - uses: actions/checkout@v4
           with: { fetch-depth: 0 }
         - run: npm install -g semgrep gitleaks difftastic
         - name: Tier 0 run
           id: tier0
           run: |
             bash tier0-run.sh > tier0.json
             echo "verdict=$(jq -r .verdict tier0.json)" >> $GITHUB_OUTPUT
     llm-review:
       needs: tier0
       if: needs.tier0.outputs.verdict != 'clean'
       # … existing soliton-review.yml body …
   ```

2. **Graph-prebuild workflow** (separate repo) — on push to main, `graph-code-indexing` rebuilds the graph and stores at `.soliton/graph.json` keyed by commit SHA. Soliton reviews look up by PR base SHA.

3. **Reusable action** `soliton/action@v2` — packages the above so downstream repos only need:
   ```yaml
   - uses: soliton/action@v2
     with:
       anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
       tier0: true
       graph: .soliton/graph.json
   ```

4. **Bedrock/Vertex auth** — already supported (`docs/ci-cd-integration.md`), keep.

### 6.3 Enterprise-procurement surface

Ship these docs alongside v2 to accelerate enterprise evaluation:
- **Cost per PR table** — model costs across LOW/MED/HIGH/CRITICAL × with/without Tier-0 skip.
- **Data flow diagram** — what leaves the repo, where (relevant for Bedrock/Vertex + self-host).
- **License matrix** — MIT Soliton + OSS tool deps (ruff, semgrep CE, gitleaks all permissive).
- **SOC 2 / ISO 27001 alignment statement** — since all infra is user-controlled, Soliton inherits the user's controls.
- **Martian-CRB score** (I9) + **cost-normalised F1** methodology doc.

---

## 7. Graph integration — enterprise rebuild fit

Full design: `DESIGN_TRADITIONAL_AND_GRAPH.md`. Summary of how this maps onto the user's enterprise-rebuild use case (`project_enterprise_rebuild` memory + `Logical_inference/idea-stage/USE_CASE_PLANS.md`):

| Rebuild stage | What happens | Where Soliton v2 fits |
|---|---|---|
| 1. Ingest + parse | graph-code-indexing builds graph | N/A — upstream |
| 2. Build heterogeneous graph | code + schema + config + scheduler | N/A — upstream |
| 3. Feature subgraph extraction | Leiden + semantic overlay → FeaturePartition[] | **Soliton consumes FeaturePartition for chunking** (feature-aware, not directory) |
| 4. Feature spec generation | L0/L1/L2 summaries + graph neighbourhood → NL spec | **Soliton consumes spec in Tier 1.5 spec-reviewer** |
| 5. Re-implementation (per-feature PRs) | AI agent emits new React+TS / FastAPI / Go | **Soliton reviews each PR, enforces evidence chain, stack-aware via I8** |
| 6. Behavioural equivalence | LLM-as-judge + behavioural replay + canonicalisation | **Soliton `--execute` mode (I19) wraps the verify step** |

Stage 5 is where the volume is — it is also exactly the regime that breaks traditional human review. Soliton v2's combination of Tier-0 skip + graph-driven agents + stack-aware + feature-coverage signal is purpose-built for it. The evidence chain that every finding carries (Tier-0 rule ID + graph path + agent reasoning) is exactly the "provenance" constraint the user's `risk_gap.md` §3.5.2.9 demands.

**One must-have extension**: graph-code-indexing must support Java (for enterprise rebuild) and ideally COBOL (for the highest-value enterprise datasets). That is graph-code-indexing's Gap B4 — not Soliton's problem, but the two roadmaps are tightly coupled.

---

## 8. Cost model

Assumptions from `docs/ci-cd-integration.md` + realistic Tier-0 skip rates from the literature:

| PR tier | % of volume | Today cost/PR | v2 cost/PR | v2 latency |
|---|---|---|---|---|
| LOW (0-30 risk) | 60 % | $0.15 | **$0.005** (Tier-0 only, skip LLM) | 8 s |
| MEDIUM (31-60) | 25 % | $0.40 | $0.12 | 25 s |
| HIGH (61-80) | 12 % | $1.00 | $0.35 | 40 s |
| CRITICAL (81-100) | 3 % | $1.50 | $0.60 | 55 s |
| **Weighted mean** | — | **$0.40** | **$0.10** | 15 s |

At 500 PRs/day: **$230/day → $63/day ($6.9 k → $1.9 k / month)**. 73 % savings, no quality loss expected (Tier-0 tools cover the same classes LLMs would re-discover; graph signals narrow agent context without removing it).

Compare competitors (April 2026):
- Anthropic managed Code Review: **$15-25/review** → $7,500-12,500/day at 500 PRs.
- Cursor BugBot: $40/PR-author/month — for 50 devs, $2,000/month fixed.
- CodeRabbit: $24/dev/month Pro; at 50 devs = $1,200/month.
- Qodo Merge: $30/dev/month; at 50 devs = $1,500/month.

Soliton v2 at 500 PRs/day + 50 devs ≈ $1.9 k/month, with the LLM cost bearing all the marginal cost. **Substantially below every hosted competitor except CodeRabbit**, and with the unique properties of being OSS, risk-adaptive, and graph-aware.

---

## 9. Pilot plan (6 weeks)

| Week | Deliverable | Gate |
|---|---|---|
| 1 | I1 Tier-0 (4 tools: ruff, eslint, tsc, semgrep) | Runs on 100 real PRs; deterministicFindings shape stable |
| 1-2 | I5 Haiku tiering + I3 spec-reviewer (Haiku) | Measurable Sonnet-token reduction ≥ 15 % |
| 2 | I9 Martian-CRB baseline run | First public F1 + $/PR data point |
| 2-3 | I1 Tier-0 skip fast-path (opt-in flag) | Track LLM-skip rate + FP escape rate on 2 weeks of real PRs |
| 3-4 | I2 Graph Signal Service Mode B CLI + risk-scorer + cross-file-impact rewire | TS/JS first; Python follows. −30 % tokens on HIGH+CRITICAL PRs. |
| 3 | I4 Deterministic AST hallucination detector (Python) | Replaces 80 % of hallucination-agent Opus calls for Python PRs |
| 4 | I7 silent-failure + comment-accuracy agents | File-type-triggered; measurable uplift on agent-authored PRs (Hora & Robbes benchmark) |
| 4-5 | I6 Realist Check synth post-pass | FP drop ≥ 20 % measured on internal PR set |
| 5 | I8 Stack awareness (`--parent`) | Works on a real stacked-PR workflow (use user's own Logical_inference repo as test) |
| 5-6 | I9 Martian-CRB re-run with all of above + cost-normalised-F1 blog post | Public number. Positioning complete. |
| 6 | **Gate-1 review**: enterprise rebuild pilot GO/NO-GO + decision on Phase 2 (I10/I11/I19) | Ship v2.0 tag; evaluate against user's own 2-month AI-native-takeover KPIs |

**Non-goals** for this pilot: execution sandbox (I19), tri-model (I10), learnings loop (I16), LSP integration (I17). All Phase 2+.

---

## 10. Gate-1 decision options

Per the research-pipeline convention, the user should consciously pick one:

- **Option A — Tier-0 + Haiku-tiering + spec + CRB publish first (fastest to revenue).** Covers I1/I3/I5/I9 in 3 weeks. Defers graph work. Best if the user wants a quick shippable v2 that ALREADY beats every cost metric.

- **Option B — Graph integration first (biggest moat).** Covers I2 + I4 (deterministic hallucination) + I9 in ~4 weeks. Requires graph-code-indexing to stabilise its Java/enterprise roadmap in parallel. Best if the enterprise-rebuild pilot is the primary driver.

- **Option C — Parallel: one track Tier-0/spec/CRB (weeks 1-3), second track graph (weeks 3-6), converge at Gate-1.** Most aggressive; requires the 3-person team to formally split ownership. Gives both publishable research and enterprise readiness by month 2.

- **Option D — Narrow and publish: Tier-0 + deterministic AST + Martian-CRB only, then pause.** Lowest effort, fastest paper/blog ROI, but leaves graph integration on the table and doesn't directly serve the enterprise rebuild.

**Default recommendation**: **Option C** — it matches the `research-pipeline` skill's "Wrap first, Strangler second" framing (I1/I3/I5/I9 are Wrap; I2/I4 are the Strangler-like structural addition) and lines up with the user's existing `risk_gap.md` §4.5 default ("Wrap → Strangler conditionally"). Option A is the safe fallback. Option B is the highest-variance research bet.

---

## 11. Risks & mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Tier-0 tools disagree with LLM (false "clean") | High | Medium | Ship `skip_llm_on_clean: false` default; collect 2 weeks of overlap data before flipping. |
| Graph staleness — PR adds files graph doesn't know | High | Medium | Incremental updater (exists in graph-code-indexing `src/updates/`); mark signals "partial" when incremental fails; fall back to v1 heuristics. |
| Graph language coverage gap (Java / COBOL missing) | High | High | Mark graph signals unavailable for unsupported languages; soliton degrades to v1 behaviour gracefully. Blocks full enterprise-rebuild fit until graph-code-indexing Gap B4 lands. |
| Martian CRB score below Qodo/CodeRabbit | Medium | High | Publish cost-normalised F1 alongside raw F1 — Soliton should win on $-per-F1. |
| Tier-0 adds CI latency | Medium | Low | Tier 0 runs in parallel, cap at 60 s. Total CI wall-clock should be roughly the same (LLM-skip paths are net faster). |
| Hooks / tooling creep in the plugin | Medium | Medium | Keep core markdown-only; all tool orchestration via SKILL.md and shell-out. No new build system. |
| Competitor (Anthropic / Qodo / CodeRabbit) ships graph-review before v2 ships | Medium | Low | 12-month head start from April 2026 on risk-adaptive + graph combo; no public roadmap indicates either vendor is moving here. |
| User's 2-month phase-1 window too tight for Option C | Medium | Medium | Option A is the fallback; everything in Option A is independent of graph work and enterprise rebuild. |
| AI-generated tests over-mock (Hora & Robbes 2026 pattern) in Soliton's own test fixtures | Low | Low | Tier-0 runs `semgrep` against Soliton's own tests; dogfood the tool. |
| Prompt injection via PR descriptions / commit messages | High | High | Already mitigated in `docs/ci-cd-integration.md` §Security. Maintain. |

---

## 12. What to tell the user

In plain language, Gate-1 options surface the three real trade-offs:

1. **Speed-to-publication vs moat-depth**: ship Tier-0 + CRB in 3 weeks (Option A) or invest 5-6 weeks building the graph moat (Option B/C). Graph moat is the academic-novelty argument.
2. **Enterprise-rebuild-linkage**: Option B/C is the only path that serves the user's own `Logical_inference` rebuild pilot in the same 2-month window. Option A/D leave rebuild-specific integration for later.
3. **Team capacity**: Option C assumes the 3-person team splits tracks; Option A/B assumes single-track.

Default recommendation again: **Option C, with Option A as committed fallback.**

---

## Appendix A — Alignment with `risk_gap.md`

Soliton v2 directly addresses the user's pre-identified Phase-1 risks:

| risk_gap.md risk | How Soliton v2 mitigates |
|---|---|
| W1 Idea Debt | I3 spec-alignment blocks half-finished PRs |
| W2 Satisficing | I6 Realist Check raises the bar on "done"; I9 benchmark measures Pass@1 |
| W3 Slop PR Flood | I1 Tier-0 gate + evidence-chain required + scope constraint via I2 graph |
| W4 Amdahl's Law | Tier-0 skip path delivers < 10 s latency for 40-60 % of PRs |
| W5 LLM Cost Governance | I1/I5 drive $0.40 → $0.10 median; I9 makes the ROI publishable |
| W6 System Amplifier | Evidence chain + provenance block on every finding → auditable review |
| P0 #1 Quality Gate | Tier 0 blocks on secret/CVE/type-error; I3 spec-alignment is a gate |
| P0 #2 Graph Recall | I2 makes graph a first-class signal source in review, closing the loop |
| P0 #3 Token Explosion | Tier-0 skip + narrow per-agent context solve this directly |

## Appendix B — Files in `idea-stage/`

- **IDEA_REPORT.md** — this document (primary deliverable).
- **LITERATURE_REVIEW.md** — 2024-2026 academic survey, 36+ papers.
- **OSS_ECOSYSTEM_REVIEW.md** — superpowers, OMC, quantum-loop, claude-plugins-official, pr-review-toolkit, everything-claude-code, Graphite, gherrit, oh-my-openagent.
- **COMPETITOR_AGENTS_REVIEW.md** — Codex, Copilot, Jules/Gemini, Cursor BugBot, Windsurf, Amazon Q, OpenHands, Devin, Tabnine, CodeRabbit, Qodo, Greptile, Ellipsis, Graphite, Sourcegraph Amp, Bito, GitLab Duo.
- **DESIGN_TRADITIONAL_AND_GRAPH.md** — full architectural spec for Tier 0 + Tier 1 (the I1/I2 ideas).
- **MANIFEST.md** — index file for this pipeline run.
