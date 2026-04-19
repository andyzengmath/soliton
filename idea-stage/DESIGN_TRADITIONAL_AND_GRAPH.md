# Design Note: Traditional Tools + Codebase Graph Integration for Soliton

**Context**: Soliton today uses an LLM-only multi-agent review pipeline. To hit enterprise-scale
quality/cost targets — thousands of AI-generated PRs/day, sub-$0.10/review median — we must
(1) delegate every deterministic check to cheap traditional tools before any LLM runs, and
(2) feed graph-derived impact signals into agent dispatch and synthesis. This note specifies
the new "pre-LLM triage" layer and the graph integration surface.

**Scope**: concrete architectural design. Companion to `IDEA_REPORT.md`.

---

## 1. Why this matters

Soliton today burns Sonnet/Opus tokens on work that `ruff --fix`, `eslint`, `semgrep`, or
`tsc --noEmit` could answer in <1s for free. A typical LOW-risk PR today fires 2 Sonnet agents
(~$0.15). A typical HIGH-risk PR fires 6 agents including Opus (~$1). On a 500-PR/day team that
is $75-500/day today — and each PR pays for correctness that linters already guarantee.

Parallel problem: the LLM agents *cannot see outside the diff*. `cross-file-impact.md:38` greps
for caller names textually; `correctness.md` and `security.md` have no notion of blast radius
or data-flow paths. Every agent re-discovers context from scratch, in tokens.

The graph-code-indexing sibling repo already computes CALLS, DATA_FLOW, IMPORTS, INHERITS,
IMPLEMENTS, REFERENCES, CONFIGURES edges — exactly the signals review needs. Soliton doesn't
use any of this yet.

**Design goal**: two new layers — Tier-0 Deterministic Gate (pre-LLM) and Tier-1 Graph Signal
Service — that filter the obviously-clean 60-80% of PRs down to seconds/cents, route the
risky 20-40% to a narrower, better-informed LLM swarm, and keep provenance throughout.

---

## 2. Pipeline — before vs after

### Before (current soliton)

```
PR → normalize → config → edge cases → chunking → risk-scorer (Sonnet, agent)
     → 2-7 LLM review agents (parallel, Sonnet/Opus)
     → synthesizer (Sonnet) → markdown/JSON
```

Total cost for a HIGH-risk PR: ~200k tokens, ~45s, ~$1.00.

### After (tiered)

```
PR → normalize → config → edge cases → chunking
     │
     ▼
 Tier 0: Deterministic Gate (0 LLM tokens, <5s)
     ├── Formatter / Linter / Style checker
     ├── Type checker
     ├── Static analyzer (SAST, secret scan, SCA)
     ├── AST structural diff
     ├── Clone / duplication detector
     └── Test impact selector
     │
     ▼ emit: deterministicFindings[], tierZeroVerdict
     │
 Tier 1: Graph Signal Service (0 LLM tokens, <3s)
     ├── Blast radius query (CALLS/IMPORTS reverse BFS depth 1-2)
     ├── Dependency break check (diff against pre-PR graph slice)
     ├── Data-flow to sink (taint path to IO/auth/DB sinks)
     ├── Co-change overlay (git log mining)
     └── Centrality score (PPR from changed nodes)
     │
     ▼ emit: graphSignals{blastRadius, affectedFeatures, taintPaths, criticality}
     │
 Risk-scorer (now deterministic formula; optional LLM only when graph unavailable)
     │
     ▼ 
 Tier 2: LLM Review Agents (2-7, parallel) — NOW WITH:
     ├── Tier-0 findings injected as already-known issues (don't re-find)
     ├── Graph signals injected (focus on blast-radius files, tainted flows)
     └── Smaller context — graph pre-selected relevant non-diff files
     │
     ▼
 Tier 3: Synthesizer (cross-tier dedup + verification pass)
     │
     ▼ markdown/JSON output
```

Target cost for a HIGH-risk PR: ~80k tokens, ~20s, ~$0.35 (65% cheaper). For LOW-risk PRs that
Tier 0/1 clear: **0 LLM tokens, <8s, ~$0.001** (just the deterministic tool runs).

---

## 3. Tier 0 — Deterministic Gate

### 3.1 Goals

- Exclude obvious fails (lint errors, type errors, known CVEs, secrets) at ~$0 cost.
- Produce structured findings with exact file:line locations and SARIF-compatible metadata so
  the LLM layer can **skip re-finding** them.
- Mark trivially-clean PRs as "no LLM review needed" (confirm-only comment, <1¢).

### 3.2 Check matrix

| Check class | Canonical tools (pluggable) | Languages | Runs on | Stop-early? |
|---|---|---|---|---|
| Formatting | `prettier`, `black`, `ruff format`, `gofmt` | All | Diff only | No — auto-fix or note |
| Linting | `eslint`, `ruff`, `golangci-lint`, `clippy`, `checkstyle` | Per-lang | Diff + imports | No |
| Type check | `tsc --noEmit`, `mypy`, `pyright`, `go build` | Typed langs | Diff + imports | **Yes if fatal** |
| SAST | `semgrep`, `bandit`, `gosec`, `brakeman`, CodeQL | Per-lang | Diff only | **Yes on HIGH severity** |
| Secret scan | `gitleaks`, `trufflehog` | All | Full diff | **Yes on match** |
| SCA (deps) | `snyk`, `osv-scanner`, `dependabot`, `pip-audit` | All manifest | `package.json`/`go.mod`/etc | **Yes on critical CVE** |
| AST structural diff | `difftastic`, `gumtree`, `ast-grep` | TS/JS/Py/Go/Java | Diff | No — emits structural change type |
| Clone detection | `jscpd`, `pmd-cpd`, `similarity-py` | Per-lang | Full repo | No |
| Test impact | `pytest-testmon`, `jest --findRelatedTests`, `bazel query` | Per-lang | File list | No |
| Style/convention | `biome`, `rubocop`, `pylint` conventions | Per-lang | Diff | No |

**Default shortlist** (to ship first): `ruff`, `eslint`/`biome`, `tsc`, `mypy`, `semgrep ci`,
`gitleaks`, `difftastic`, `jscpd`. All open-source, all run in ~5-15s on typical diffs.

### 3.3 Orchestration

- New `skills/pr-review/tier0.md` runs between **Step 2.5** and **Step 3** in the current `SKILL.md`.
- Each tool runs in parallel via `Bash(... run_in_background)`.
- Output normalised to a common `DeterministicFinding` shape (SARIF-compatible):
  ```
  { tool, rule, severity, file, lineStart, lineEnd, message, suggestedFix?, category }
  ```
- Emit aggregate `tierZeroVerdict`: `clean | advisory_only | needs_llm | blocked`.

### 3.4 Fast-paths (cost kill)

- `tierZeroVerdict == clean` **and** diff ≤ 50 meaningful lines **and** no sensitive paths →
  **skip all LLM agents**, post "Soliton: no issues found (Tier 0 only)." Saves 100% of LLM cost
  for the ~40% of PRs that are trivial updates, doc tweaks, dependency bumps.
- `tierZeroVerdict == blocked` (secret leaked, CVE-critical dep) →
  post Tier-0 findings as CRITICAL, **skip LLM agents**, fail CI. No point running Sonnet to
  confirm a string-matched AWS key.

### 3.5 Configuration

Add to `.claude/soliton.local.md`:
```yaml
tier0:
  enabled: true
  tools:
    lint: ["eslint", "ruff"]
    type_check: ["tsc", "mypy"]
    sast: ["semgrep"]
    secrets: ["gitleaks"]
  skip_llm_on_clean: true          # free mode
  block_on: ["secret_leak", "cve_critical", "type_error_fatal"]
  # null = auto-detect language
  languages: null
```

### 3.6 Why these specific tools

- `ruff` replaces 9 Python tools (flake8, pylint, isort, pydocstyle, bandit partial, …) in a
  single Rust binary, <200ms on typical diff. Free.
- `semgrep` has a curated OWASP Top 10 ruleset + a thriving community rule repo; runs offline.
- `difftastic` gives structural change types ("function signature changed", "control-flow added")
  that feed directly into risk scoring.
- All tools are commodities — no vendor lock-in, no LLM dependency.

---

## 4. Tier 1 — Graph Signal Service

### 4.1 What graph-code-indexing provides (verified by reading the repo)

From `Logical_inference/graph-code-indexing/src/types/EdgeType.ts`:
```ts
enum EdgeType {
  PARENT_CHILD, CALLS, DATA_FLOW, IMPORTS,
  INHERITS, IMPLEMENTS, REFERENCES, CONFIGURES
}
```

From `src/retrieval/`: `BM25Engine`, `CentralityScorer`, `GraphExpander`, `EdgeImportanceScorer`,
`DirectSearch`, `DataFlowService`. Storage is `InMemoryGraphDatabase` + SQLite.

Known-missing today (`MANIFEST.md` Gap A1, A6, B3): PPR / RWR centrality (degree only),
co-change edges from git, cross-encoder reranking.

### 4.2 What soliton needs from the graph

For each changed file `f`, changed symbol `s`, and sensitive sink (DB/IO/auth):

| Review question | Graph query |
|---|---|
| How many callers does `s` have? (blast radius) | reverse BFS over `CALLS` depth 1-2 |
| Which features does `f` belong to? | `PARENT_CHILD` ascent to package + co-change cluster |
| Does `s`'s change break a caller signature contract? | compare `CALLS` targets pre vs post (graph diff) |
| Does user input flow into a dangerous sink in the diff? | forward `DATA_FLOW` BFS from diff-touched input variables |
| Which config keys drive `s`? | reverse `CONFIGURES` BFS |
| Which tests exercise `s`? | reverse `REFERENCES` BFS filtered to test-file nodes |
| Is `s` a central hub (many fan-in/fan-out)? | `CentralityScorer` on pre-PR graph |
| Has `s` co-changed recently with other files in this PR? | git-log overlay (needs Gap A6 to be first-class) |

### 4.3 Integration shape — `GraphSignalService`

New module `skills/pr-review/graph-signals.md`, runs between Tier 0 and risk scoring.

```
GraphSignalService.compute(request: ReviewRequest): GraphSignals {
  blastRadius:    { file: string; directCallers: number; transitiveCallers: number }[]
  affectedFeatures: { partitionId: string; nodes: string[]; confidence: number }[]
  dependencyBreaks: { caller: string; callee: string; reason: "sig_change"|"removed" }[]
  taintPaths:     { source: string; sink: string; kind: "sql"|"xss"|"ssrf"|"auth"; edges: string[] }[]
  criticalityScore: { symbol: string; pprScore: number }[]
  coChangeHits:   { file: string; historicalPartners: string[] }[]
  featureCoverage: { partitionId: string; testFiles: string[]; coverage: "full"|"partial"|"none" }[]
}
```

### 4.4 Two integration modes

**Mode A — soliton calls graph-code-indexing as a library (preferred)**
- Require graph-code-indexing as a Node dependency in soliton's plugin manifest.
- Ship a thin `graph-bridge.ts` that loads the pre-built graph JSON from a known path
  (`.soliton/graph.json` or env var).
- Graph must be pre-built by a separate CI step (cached by commit SHA).

**Mode B — soliton shells out to a `graph-cli` (MVP, language-agnostic)**
- graph-code-indexing exposes a CLI like `graph-cli query --blast-radius <file>:<symbol>`.
- soliton's Tier-1 uses `Bash(graph-cli *)` calls.
- No compile-time dependency, works even if graph is Python or Java in the future.

Mode B first (ships in days). Mode A when graph-code-indexing's API stabilizes.

### 4.5 How graph signals shape the LLM tier

1. **Risk scorer factors** (new):
   - `blast_radius` replaces the current Grep-based heuristic (`risk-scorer.md:24-31`). Use
     graph-verified transitive caller count, not text-pattern matches — this alone kills a big
     FP class where a function name coincidentally matches a substring.
   - Add two new factors: `taint_path_exists` (weight 20%, overrides sensitive-paths heuristic
     when available) and `feature_criticality` (weight 10%, PPR centrality).

2. **Agent focus areas** (existing but now graph-driven):
   - `cross-file-impact` gets `dependencyBreaks` pre-computed — it only **explains** the breaks,
     doesn't **discover** them. Cuts its work ~10× and eliminates Grep FPs.
   - `security` gets `taintPaths` — goes directly to the source→sink pair instead of scanning
     the whole diff.
   - `hallucination` gets the import graph — knows immediately which external packages are
     actually installed, skips the `npm ls` / `pip show` shell-outs.
   - `historical-context` gets `coChangeHits` — instead of `git log` per file (slow), use
     pre-computed co-change clusters.

3. **Chunking strategy**:
   - Currently groups by first-level directory (`SKILL.md` Step 2.75). Replace with feature-
     partition-aware chunking: files in the same graph community become one chunk even if in
     different directories. Keeps cross-file reasoning intact.

4. **Evidence chain output**:
   - Every finding now carries graph-derived evidence (`edges: [...]`, `partition: "..."`).
     This is exactly the "evidence chain" the user's `risk_gap.md` §3.5.1 demands.

### 4.6 What happens when the graph is unavailable

If graph-code-indexing isn't installed, if the target language isn't supported, or if the
graph is stale: soliton must degrade gracefully.

```
if !graphSignals.available:
    risk_scorer uses Grep-based heuristics (today's behavior)
    cross-file-impact uses Grep (today's behavior)
    log warning: "Graph signals unavailable — running in fallback mode"
    continue with LLM tier
```

No hard dependency. The graph *improves* cost/quality; its absence doesn't block review.

---

## 5. Escalation hierarchy

The three tiers implement an explicit cost/accuracy trade-off:

| Tier | Per-PR cost | Latency | What it catches | What it misses |
|---|---|---|---|---|
| Tier 0 (deterministic) | ~$0 | 3-10s | Known CWEs, lint, type errors, secrets, deps | Logic bugs, semantic issues, AI hallucinations |
| Tier 1 (graph) | ~$0 | 1-3s | Blast-radius, taint, dep-breaks, feature criticality | Subtle logic, style, spec compliance |
| Tier 2 (LLM agents) | $0.10-1.00 | 15-60s | Semantic bugs, AI hallucinations, logic errors, spec drift, test-quality, consistency | — |
| Tier 3 (human) | $$$ | minutes-hours | Business intent, Chesterton's Fence, UX judgment | — |

**Gating rules** (enforced in `SKILL.md`):
- Tier 0 blocks can short-circuit (secret leak, CVE-critical, type error). LLM still runs for
  *other* findings by default — unless `--fast-fail` flag is set.
- Tier 1 "low criticality + clean Tier 0" → skip expensive agents (security/hallucination
  stays Opus, but correctness/test-quality drop to Haiku instead of Sonnet).
- Tier 2 confidence < 80 + Tier 0/1 didn't independently confirm → downgrade to nitpick or
  suppress. (Graph can confirm e.g. "cross-file-impact found a call-site break" only if the
  graph agrees `dependencyBreaks` contains it.)
- Tier 3 (human) only invoked when Tier 1 criticality score ≥ 85 OR when the PR touches a
  graph-identified "Chesterton's Fence" node tagged `customer-specific`, `legacy-compat`,
  `workaround`, `integration` (per `risk_gap.md` §3.5.2.1 mitigation 1).

---

## 6. New components added to the repo

```
soliton/
├── skills/
│   └── pr-review/
│       ├── SKILL.md                 (modified: add Step 2.6 Tier-0, Step 2.7 Tier-1)
│       ├── tier0.md                 (NEW: orchestrate deterministic tools)
│       └── graph-signals.md         (NEW: query graph-bridge, emit GraphSignals)
├── agents/
│   ├── (existing 9 agents — prompts updated to consume graphSignals + tier0Findings)
│   └── tier-zero-tool-adapters/     (NEW: one adapter per tool family)
│       ├── linter-adapter.md
│       ├── type-check-adapter.md
│       ├── sast-adapter.md
│       ├── secret-scan-adapter.md
│       └── ast-diff-adapter.md
├── rules/
│   ├── (existing)
│   ├── tier0-tools.md               (NEW: canonical tool choices + exit-code conventions)
│   └── graph-query-patterns.md      (NEW: how each review agent queries the graph)
├── lib/                             (NEW: a tiny TS/JS helper lib)
│   ├── graph-bridge.ts              (NEW: wraps graph-code-indexing API / CLI)
│   ├── sarif-normalizer.ts          (NEW: converts tool output to DeterministicFinding)
│   └── escalation-policy.ts         (NEW: tier gating decisions)
└── examples/workflows/
    └── soliton-review-tiered.yml    (NEW: full 3-tier CI gate)
```

---

## 7. Cost model — 500-PR/day enterprise team

Assumptions (from `docs/ci-cd-integration.md`):
- LOW PRs: 60% of volume, $0.15 today → $0.001 with Tier 0 skip
- MEDIUM PRs: 25% of volume, $0.40 today → $0.12 (smaller LLM context, Tier 0 hits absorbed)
- HIGH PRs: 12% of volume, $1.00 today → $0.35
- CRITICAL PRs: 3% of volume, $1.50 today → $0.60

| Tier | Daily LLM cost today | With Tier 0/1 | Savings |
|---|---|---|---|
| 500 × mix | ~$230/day ($6.9k/month) | ~$63/day ($1.9k/month) | ~73% |

Assumes ~85% of Tier-0-clean PRs get the LLM skip (conservative — can be higher with good
config).

---

## 8. Risks & open questions

1. **Tier 0 tool false positives** — enterprise lint configs are already noisy. Mitigation:
   Tier 0 only surfaces findings that would block CI *and* only on changed lines (`reviewdog`
   pattern). Existing repo debt doesn't enter review output.

2. **Graph staleness** — pre-built graph keyed by commit SHA. If PR adds a new file, the graph
   doesn't know it yet. Mitigation: incremental updater exists (`graph-code-indexing`
   `src/updates/`). If incremental fails, mark signals "partial" and fall back.

3. **Language coverage** — graph-code-indexing supports TS/JS/Py/C/C++. Java, Go, Rust,
   COBOL are missing. Enterprise rebuild explicitly needs COBOL + Java. This is a graph-
   code-indexing roadmap item (Gap B4 in `MANIFEST.md`), not soliton's problem, but we must
   **not** surface graph claims when the graph is empty for that language.

4. **Graph + Tier 0 can disagree** — e.g. Semgrep flags a SQLi sink, but graph's DATA_FLOW
   finds no actual path from user input. Policy: Tier-0 severity capped at "improvement" when
   graph disconfirms. Explicit `conflict` emitted to synthesizer.

5. **CI runner budget** — all Tier 0 tools run on ubuntu-latest. Typical 1-minute overhead,
   fine. If monorepo, may need self-hosted runners — out of scope for this design.

6. **Vendor lock on graph** — if graph-code-indexing stalls, soliton falls back to Mode B (CLI
   shell-out) and can swap in any alternative that exposes the same CLI contract. We specify
   the CLI, not the implementation.

---

## 9. Rollout plan

**Phase 1 (week 1)** — Tier 0 only:
- Ship `tier0.md` with the default 4 tools (ruff/eslint, tsc/mypy, semgrep, gitleaks).
- Gate: Tier 0 runs for free; LLM tier still runs unchanged.
- Measure: how often Tier 0 already-found the issues LLM re-discovers (dedup signal).

**Phase 2 (week 2-3)** — fast-path:
- Add `skip_llm_on_clean` config flag (default off).
- Measure cost delta, FP escape rate on the team's internal PRs.
- Flip default once escape rate < 2%.

**Phase 3 (week 3-5)** — Graph signals:
- Ship `graph-signals.md` in Mode B (CLI shell-out).
- Rewire `risk-scorer` to use graph-blast-radius; rewire `cross-file-impact` to consume
  `dependencyBreaks`.
- Measure: review cost drop for HIGH+CRITICAL PRs, FP drop for cross-file findings.

**Phase 4 (week 5-6)** — Synth & polish:
- Conflict resolution rules between tiers.
- New output section `## Evidence Chain` listing graph-derived provenance.
- Benchmark against LocBench / MuLocBench (per `risk_gap.md` §5) to get publishable A/B.

---

## 10. Success metrics

Track before rollout vs after:

| Metric | Baseline (today) | Phase-2 target | Phase-3 target |
|---|---|---|---|
| Median cost/PR | ~$0.40 | $0.20 | $0.10 |
| p95 latency | ~60s | 40s | 25s |
| FP rate (developer dismissals) | 10-15% | 8% | 5% |
| Critical catch rate | ~80% (internal) | 85% | 90% |
| % PRs with zero LLM calls | 0% | 30% | 50% |
| Evidence-chain coverage | 0% | 20% | 80% |
