# Phase 4 · Structural F1 push — L5 cross-file retrieval + hallucination-AST pre-check

**Design doc**, not an implementation. Expanded from the original L5-only scope after design-doc review feedback: combining L5 with the already-specced `lib/hallucination-ast.md` (ROADMAP D) produces a compounding effect, day-one 5-language support is trivial, and ship criteria should be raised to match the larger bet.

Two prompt-level experiments (Phase 3.6, 3.7) landed net-negative, establishing that the prompt-tuning ceiling is around Phase 3.5's F1 = 0.277. Phase 4 is **agent-level structural work**, not more prompt tweaks.

## Alignment with existing specs (no duplication)

| Existing artifact | Role | What Phase 4 takes / leaves |
|---|---|---|
| `skills/pr-review/graph-signals.md` | Heavyweight Tier-1 cross-file queries (blast-radius, dep-diff, taint, co-change, feature-of, centrality, test-files-for) via `graph-cli`. Emits `GRAPH_SIGNALS_UNAVAILABLE` until ROADMAP B ships. | **Phase 4 does NOT re-implement any of these 8 queries.** L5 retrieval is the *lightweight always-available* complement: single-symbol definition lookup only, via `git grep` + `Read`. When `graph-cli` ships and graph-signals is available, Phase 4 retrieval still runs for symbol-definition lookup; graph-signals owns blast-radius + dep-diff. The two layers coexist. |
| `lib/hallucination-ast.md` (ROADMAP D) | Zero-LLM tree-sitter check that resolves imports / calls / method-access against installed-package introspection. Khati 2026 reports **100 % precision, 87.6 % recall, F1 = 0.934** on Python. Runs inside `agents/hallucination.md` as a pre-check. | **Phase 4 implements this.** The spec is already written; Phase 4 produces the actual `lib/hallucination-ast` Python package. |
| `agents/cross-file-impact.md` | Already does Grep + Read for callers of changed exports. Language-agnostic LLM reasoning on top. | **Phase 4 refactors its retrieval machinery into a shared skill** (`skills/pr-review/cross-file-retrieval.md`) so `correctness`, `hallucination`, and `cross-file-impact` all use it. Reduces duplication, opens the door for day-one 5-language support without re-coding extraction three times. |
| `agents/hallucination.md` | Current Opus-backed hallucination agent. Single most expensive agent per Phase 3 cost analysis. | **Phase 4 inserts hallucination-AST as a pre-check**, bypassing Opus on the 80 % of cases the AST check handles deterministically. Falls through to Opus only on genuinely ambiguous cases. |

## Two-component architecture

### Component A · L5 cross-file retrieval skill (shared)

New file: `skills/pr-review/cross-file-retrieval.md` (~100 lines). Shared by `correctness`, `hallucination`, and `cross-file-impact`.

**Inputs**: diff, files list, ReviewConfig (for budget caps).

**Pipeline**:

```
1. symbol-extract
     Parse the diff for CALLEE symbols (function calls, method calls,
     type references) that are NOT defined in the diff itself.
     Language-agnostic: pattern-match the diff text for identifier-like
     tokens, filter out builtins + self-defined + test-obvious.
     Budget: top-N by priority (change-adjacent > sensitive paths > alpha).

2. resolve-definitions (per language, day-one support for all 5)
     For each priority-N symbol, run the appropriate `git grep` pattern:
       Python     : (class|def) <sym>\b
       Go         : (func|type) <sym>\b  OR  func \(\w+ \*?\w+\) <sym>\b
       TypeScript : (class|function|interface|const|type) <sym>\b
       Java       : class <sym>\b  OR  (public|private)?\s*\w+\s+<sym>\s*\(
       Ruby       : (class|def) <sym>\b
     If 0 hits, skip (external symbol, out of scope).
     If ≥1 hits, `Read` the top match ±15 lines of context.

3. attach-as-context
     Emit a CROSS_FILE_CONTEXT block into the calling agent's working
     context before it forms findings. Format:
       CROSS_FILE_CONTEXT_START
         symbol: multiprocessing.Process
         source: /usr/lib/python3.12/multiprocessing/process.py:24
         definition: |
           class BaseProcess: ...
           class Process(BaseProcess): ...
         notes: SpawnProcess in multiprocessing.context does NOT subclass Process on POSIX
       CROSS_FILE_CONTEXT_END
```

**Budget caps** (revised per Q1 review — these are cost guards, scalable):

```yaml
crossFileRetrieval:
  enabled: true
  maxResolutionsPerAgent: 8     # hard cap; ~3k tokens context per agent max
  maxHops: 1                    # callee → caller needs hops=2; 1 is default
  tokenBudgetPerAgent: 3000     # emergency brake
```

Incremental cost on a 50-PR run: ~$1 extra judge-side, ~$0.05 extra per PR Soliton-side. Latency: +10–20 s per PR. Cap can be raised via `--max-resolutions` CLI flag or config layer.

### Component B · hallucination-AST Python implementation (ROADMAP D)

Spec already exists at `lib/hallucination-ast.md`. Phase 4 produces:

- `lib/hallucination-ast/` (Python package)
- Tree-sitter bindings for Python (day-one); TS/Go/Java/Ruby as follow-ups
- Introspection KB builders for `site-packages` + `node_modules` + `go.sum` + pom dependencies
- Four finding rules per the spec: `identifier_not_found`, `signature_mismatch_arity`, `signature_mismatch_keyword`, `deprecated_identifier`
- Each finding emitted at confidence 100 (zero-LLM), bypasses `agents/hallucination.md`'s Opus step

**Shipping gate**: reproduce Khati 2026's 100 % precision / 87.6 % recall numbers on their published test set BEFORE integrating into Soliton. Validates the implementation against an external benchmark before we rely on it.

## Realistic F1 estimates (post-Phase-3.7 calibration)

| Lever | Mechanism | Original IMPROVEMENTS.md | Post-3.7 discount | Phase 4 estimate |
|---|---|---:|---|---:|
| L5 cross-file retrieval | Recall: catch cross-file semantic goldens | +0.05 | 3–5× haircut | +0.02 to +0.04 |
| Hallucination-AST | Precision + recall on hallucination-class FN/FP | +0.01 (ROADMAP D's own estimate) | less discount (deterministic, Khati-validated) | +0.03 to +0.05 |
| **Stacked combined** | | | ~20 % overlap penalty | **+0.04 to +0.07** |

**Phase 4 target F1**: 0.32 to 0.35. Matches the "aim higher" direction. Still honest about the discount — we don't claim 0.40 when two experiments taught us projections over-state by 3–5×.

## Ship criteria (raised)

| Outcome | Aggregate F1 | Recall | Per-language regression | Action |
|---|---:|---:|---|---|
| ✅ **Ship** | ≥ 0.32 | ≥ 0.64 | No language > −0.02 vs Phase 3.5 | Replace Phase 3.5 as published best |
| ⚠️ **Hold** | 0.29–0.31 | 0.60–0.63 | — | Evaluate per-component: ship whichever (L5 alone, or hallucination-AST alone) passes |
| ❌ **Close negative** | < 0.29 | < 0.60 | Any language > −0.03 | Document as experiment; don't merge |

**Raised bar**: 0.32 is more ambitious than the original L5-only 0.29 target. Matches the combined realistic range.

## Effort

| Component | Effort |
|---|---|
| L5 `cross-file-retrieval.md` skill + agent edits + 5-language pattern set + shared-retrieval refactor of `cross-file-impact.md` | 3–5 dev-days |
| Hallucination-AST Python implementation per existing `lib/hallucination-ast.md` spec | ~1 dev-week |
| Integration + dogfood + Phase 4 full 50-PR CRB re-run | 2–3 dev-days |
| **Total** | **~1.5–2 dev-weeks** |

## Rollout plan

1. **This PR (scope-before-build)** — approve the combined L5 + D design. Ships only `bench/crb/PHASE_4_DESIGN.md`, no code.
2. **Phase 4a — L5 skill only** (3–5 dev-days): ship the retrieval helper + agent edits + 5-language patterns. Dogfood on Soliton's own PRs first. **Do not run full CRB yet.**
3. **Phase 4b — Hallucination-AST Python** (~1 week): implement per `lib/hallucination-ast.md` spec. Validate against Khati 2026's public test set first (100 % precision, 87.6 % recall target). Then integrate into `agents/hallucination.md` as pre-check.
4. **Phase 4c — Full CRB validation** (one final ~$100 run): 50 PRs under the same GPT-5.2 judge as Phase 3.5. Measure against ship criteria above. Bounded spend — one run, not exploratory iterations.

## Risk register

| Risk | Mitigation |
|---|---|
| Latency bloat from retrieval | Hard caps on resolutions + token budget per agent. Expected +10–20 s / PR; Phase 3 baseline is 3–6 min/PR. |
| Retrieved content distracts the agent ("oh, this external function has issues too") | L5 skill's output format explicitly labels retrieved definitions as "reference only, not review target". Agents instructed: emit findings only for symbols in the **diff**. |
| Language pattern drift (TS syntax complex, Ruby has unusual class semantics) | Ship Python + Go first as internal dogfood; TS/Java/Ruby patterns validated on Soliton's own past PRs before the CRB run. Don't burn $100 on unvalidated TS extraction. |
| Hallucination-AST introspection KB is stale | Per-PR snapshot of `site-packages` / `node_modules` / etc. at diff head SHA. Khati 2026 covers this; no new invention needed. |
| Combined L5 + D compounding breaks (interactions between precision and recall mechanisms) | Phase 4a ships standalone and gets internal dogfood validation; Phase 4c runs only when both components are individually-validated. |
| The 3–5× projection discount is itself uncertain | Phase 4c uses pre-registered ship criteria; if F1 < 0.29 the PR closes as negative-result regardless of hope. Same discipline as Phase 3.6/3.7. |
| Budget blowout on a PR with 100+ symbols | Hard cap at 8 resolutions/agent. Bounded. |

## Explicit non-goals

- **No retries** of v2.2 description compression or v2.3 synthesizer dedup (both falsified in Phase 3.6 / 3.7).
- **No new agents.** Already have 8; Phase 4 enhances correctness + hallucination, doesn't add a ninth.
- **No graph infrastructure** (ROADMAP B) as a prerequisite. When `graph-cli` ships independently, it supersedes L5's would-be blast-radius queries; Phase 4 makes no commitment there.
- **Per-language severity gate (v2.1)** stays as an opportunistic add-in — if the L5 cross-file work restores TypeScript recall naturally, v2.1 is moot. If not, revisit in a Phase 4.5 mini-patch.
- **Cross-repo symbol resolution** (external packages' types) is out of scope for L5 — covered by hallucination-AST's introspection KB path, not L5.

## Exit criteria (pre-registered)

After Phase 4c completes the full CRB re-run under the same GPT-5.2 judge as Phase 3.5:

| Aggregate F1 observed | Recall observed | Any language regression > 0.02 | Decision |
|---:|---:|---:|---|
| ≥ 0.32 | ≥ 0.64 | No | **Ship as new published Soliton CRB number** |
| 0.29–0.31 | 0.60–0.63 | Possibly | **Hold** — evaluate per-component: ship L5 alone? hallucination-AST alone? |
| < 0.29 | < 0.60 | Likely | **Close** as documented negative-result experiment |

## What this doc is NOT

- An implementation. This is scope-before-build.
- A commitment to specific F1 numbers. Post-calibration realistic +0.04 to +0.07 is the estimate range, not a guarantee.
- A bet that both components individually succeed. Phase 4a and 4b can ship independently; 4c only combines them for the published number.
- An alternative to the existing `graph-signals.md` / `lib/hallucination-ast.md` specs. Phase 4 **implements** parts of those specs and fills a retrieval gap between them.
