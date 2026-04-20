# L5 — Deeper cross-file retrieval for correctness & hallucination agents

**Design doc**, not an implementation. Scopes the next realistic F1 lever after Phase 3.6 and 3.7 prompt-tuning experiments both landed net-negative (see `RESULTS.md` §§"Phase 3.6 / 3.7"). Implementation is **3–5 developer-days**, material enough that a design pass is worth the hour.

## Problem statement

Phase 3 FN analysis (see `IMPROVEMENTS.md` §1b) classified 53 missed goldens into five buckets. Two of them — accounting for **~60 %** of all FNs — require cross-file type / signature understanding:

| Category | Share of FNs | Example (from actual Phase 3 data) |
|----------|-------------:|------------------------------------|
| Deep cross-file type understanding | ~35 % | `isinstance(SpawnProcess, multiprocessing.Process)` is always `false` on POSIX (sentry#93824); `ContextualLoggerMiddleware` panics when request is `nil` (grafana#76186) |
| Subtle math / semantics requiring callee inspection | ~25 % | `NewInMemoryDB().RunCommands` returns "not implemented" (grafana#94942); `parseRefreshTokenResponse` returns a Zod `safeParse` wrapper, not the credential object (cal.com#11059) |

These are findings Soliton's current agents can't surface because they only see the **diff**, not the definitions of the symbols the diff references. The correctness agent looking at `isinstance(process, multiprocessing.Process)` has no way to know that `process` came from `multiprocessing.get_context('spawn').Process()` and is a `SpawnProcess` — that fact lives one file away.

## Post-calibration realistic F1 lift estimate

Using the post-Phase-3.7 calibrated model (IMPROVEMENTS.md projections were 3–5× too optimistic on aggressive-precision levers):

- **Original IMPROVEMENTS.md estimate (L5)**: +0.05 F1
- **Calibrated estimate** (recall-first levers tend to realise more of their projected gain than precision-first levers; Phase 3.5 is evidence for this pattern): **+0.02 to +0.04 F1 aggregate**
- **By mechanism**: recall-only improvement targeting ~30 % of the missed-goldens bucket. Plausible to catch half of the "cross-file type understanding" FNs → ~9 additional TPs → recall 0.566 → ~0.63, F1 ~0.30–0.32.

## Scope

**In scope for L5**:
1. One new reusable helper — `skills/pr-review/cross-file-retrieval.md` — that resolves symbols referenced in the diff to their definitions.
2. Agent edits in `agents/correctness.md` and `agents/hallucination.md` to invoke the helper before emitting findings that rely on cross-file facts.
3. A configuration knob to bound retrieval depth (default: 1 hop; max: 2 hops) and token budget.

**Explicitly out of scope**:
- Integration with the `graph-code-indexing` repo's `graph-cli`. That's separately a ROADMAP B item; L5 should work without any graph infrastructure, using only `gh`, `git grep`, and `Read`.
- New agents. We already have eight; adding a ninth is expensive in token budget and orchestration complexity. Instead, make the existing correctness + hallucination agents better.
- Cross-repo symbol resolution (e.g. understanding external npm packages' type definitions).

## Architecture

### Retrieval flow

```
Agent starts (correctness / hallucination)
  │
  │  reads diff + file list from ReviewRequest
  ▼
  Step 1: symbol-extract
    Parse the diff for CALLEE symbols (function calls, method calls,
    type references) that are NOT defined in the diff itself.
    Example: `isinstance(process, multiprocessing.Process)` →
      symbols = ["multiprocessing.Process", "isinstance"]
      (isinstance is builtin, discarded; Process is interesting)
  ▼
  Step 2: resolve-definitions
    For each "interesting" symbol (not a builtin, not trivially
    self-describing):
      a. `git grep -n 'class <sym>\b' -- '*.py' '*.ts' '*.go' '*.java' '*.rb'`
         → finds class / function definitions
      b. If 0 hits, skip (external symbol, out of scope)
      c. If ≥1 hits, `Read` the top match ±15 lines of context
    Cap at 8 resolutions per agent invocation (token budget guard).
  ▼
  Step 3: attach-as-context
    Emit a synthetic `CROSS_FILE_CONTEXT` block into the agent's
    working context before it starts forming findings.
  ▼
  Agent forms findings with cross-file fact awareness.
```

### Budget guard

Each retrieval costs tokens. With 8 resolutions × ~300 tokens of context each = ~2400 tokens added per agent call. Across correctness + hallucination that's ~4800 tokens per PR. At Opus pricing (~$0.015 / 1k output + ~$0.003 / 1k input, agent context is mostly input): ~$0.02 per PR extra. 50 PRs × $0.02 = $1 incremental spend on the full CRB run — negligible.

However some PRs have hundreds of unique callee symbols. Cap the retrieval list at 8 and pick by priority: symbols appearing at diff-changed lines first, then symbols on sensitive paths (auth/security), then alphabetical.

### Configuration

Add to `SKILL.md` Layer 1 defaults:

```
ReviewConfig {
  ...
  crossFileRetrieval: {
    enabled: true
    maxResolutionsPerAgent: 8
    maxHops: 1                    # callee → callee's callers requires hops=2
    tokenBudgetPerAgent: 3000
  }
}
```

And `--cross-file-retrieval off` / `--max-resolutions <N>` CLI flags for experimentation.

## Concrete edits

### 1. New helper: `skills/pr-review/cross-file-retrieval.md`

Document describing:
- How to extract callee symbols from a unified diff in each supported language (Python, JS/TS, Go, Java, Ruby).
- Priority ranking (change-adjacent > sensitive paths > alphabetical).
- The exact `git grep` patterns (per language).
- The `CROSS_FILE_CONTEXT` block format to emit.

Approximately 80–120 lines. No code — this is a prompt-level skill, same as the other existing skills.

### 2. Edit to `agents/correctness.md`

Add a new section 3a "Resolve cross-file symbols" that invokes `cross-file-retrieval` before the existing finding-generation steps. Approximately 25 lines of addition.

### 3. Edit to `agents/hallucination.md`

Same as correctness — add section that calls the helper first. The hallucination agent especially benefits because "does this method / signature exist?" is almost entirely a cross-file question.

### 4. Test update

Update `tests/run-fixtures.md` with a new fixture directory `tests/fixtures/cross-file-type-mismatch/` that reproduces the `isinstance(SpawnProcess, Process)` failure mode, so we have a regression test for L5.

## Rollout plan

1. **Design doc review** (this PR) — scoping, cost estimate, go / no-go.
2. **Implementation PR** (3–5 dev-days): the 4 edits above + fixture + dogfood pass. Opens separately after design approval.
3. **Phase 4 CRB validation**: once implementation merges, re-run the full 50-PR CRB pipeline. Measure F1 delta vs Phase 3.5's 0.277. **Ship criteria**: aggregate F1 ≥ 0.29 AND recall ≥ 0.62 AND no language regresses below its Phase 3.5 number by more than 0.02.
4. If ship criteria fail: close as documented negative-result experiment (same pattern as Phase 3.6 / 3.7). If they pass: this becomes the next published Soliton CRB number.

## Risk register

| Risk | Mitigation |
|------|-----------|
| Retrieval adds latency | Bounded by `maxResolutionsPerAgent = 8`; each `git grep` is <100 ms; Read is fast. Expected added latency: 2–5 s per agent, 10–20 s per PR total. Acceptable vs Phase 3 baseline of 3–6 min per PR. |
| Retrieved content poisons the agent (e.g., pulls in unrelated code that distracts from the diff) | Strict format for the `CROSS_FILE_CONTEXT` block; agents instructed that retrieved content is "definition for reference, not changes to review". |
| Budget blowout on unusual PRs (100+ symbols) | Hard cap of 8 resolutions. Guarantees bounded cost. |
| L5 helps recall but hurts precision (the agent over-interprets retrieved content and invents findings) | Emit findings only when they relate to symbols in the **diff** itself. Retrieved definitions inform judgment but are not themselves review targets. |
| Language extraction is fragile (especially TypeScript with complex syntax) | Start with Python + Go only (simplest grammars); add TS / Java / Ruby in follow-up iterations after Phase 4 validates the approach. Phase 4 ship criteria evaluate aggregate F1 — even a Python-and-Go-only retrieval should show measurable lift. |

## Explicit non-goals

- **Not** pursuing L3 synthesizer dedup further (Phase 3.7 falsified it).
- **Not** pursuing v2.2 description compression further (Phase 3.6 falsified it).
- **Not** pursuing per-language severity gate as the lead lever — it's worth ~+0.01 aggregate F1 and can be added opportunistically during L5 work, not as its own iteration.
- **Not** building graph infrastructure (ROADMAP B) as a prerequisite. L5 works with `gh` + `git grep` + `Read` — the tools the agents already have.

## Exit criteria

After L5 implementation lands and Phase 4 re-runs the full CRB corpus:

- ✅ **Ship as new Soliton best** if aggregate F1 ≥ 0.29 and recall ≥ 0.62.
- ⚠️ **Hold as experiment** if F1 is within ±0.01 of Phase 3.5's 0.277 — inconclusive, not worth shipping the added latency.
- ❌ **Close as negative** if F1 < 0.275 or any language regresses >0.02 below Phase 3.5.

## Dependencies on other work

**None blocking**. Every tool L5 needs (`gh`, `git grep`, `Read`, `Agent`) is already allowed in the dogfood workflow's `allowed_tools`. The design can proceed without ROADMAP B graph work shipping first.

## What this doc is NOT

- An implementation. This is the scope-before-build pass.
- A commitment to specific F1 numbers. Post-Phase-3.6/3.7, projections are discounted 3–5×; the +0.02 to +0.04 realistic estimate incorporates that discount.
- A guarantee the experiment will succeed. Two consecutive prompt-level experiments failed. A third structural experiment might also fail — the design and exit criteria above contain that risk.
