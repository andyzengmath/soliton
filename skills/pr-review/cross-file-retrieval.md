# Cross-File Retrieval Skill (Phase 6 — Java-only experimental)

**Default: OFF** as of Phase 6 ship. Enable per-repo via `agents.cross_file_retrieval_java.enabled: true` in `.claude/soliton.local.md`. Phase 6 is gated on a single bounded ~$140 CRB measurement (see `bench/crb/PHASE_6_DESIGN.md` § Exit criteria). Until that run completes and clears SHIP, this skill is opt-in only.

**Scope: Java only.** Phase 6 deliberately narrows to one language to avoid the cross-language regression that closed Phase 4c (Go regressed −0.112 due to `NOT_FOUND_IN_TREE` suppression baked into the Phase 4a skill). This skill has **NO suppression rule** — it is purely additive (only adds CROSS_FILE_CONTEXT, never removes agent findings).

## When to call

Caller agents (currently `correctness` only) invoke this skill when BOTH of the following hold (read from the orchestrator-resolved "Feature flags" block injected into the agent's prompt by SKILL.md Step 4.1 step 6 + Step 4.2 — **NOT** read from `config.*` directly):

1. `cross_file_retrieval_java_enabled` (Feature flags block) is `true`; AND
2. `java_files` (Feature flags block) is non-empty.

The orchestrator pre-resolves these by checking `config.agents.cross_file_retrieval_java.enabled` from `.claude/soliton.local.md` AND scanning the files list for `*.java` paths; the agent never reads `config.*` itself. This mirrors the silent_failure / comment_accuracy gating pattern. Otherwise: skip (Phase 5.2 baseline behavior preserved).

## Input

- `diff` — unified diff of all changes (already passed to caller agent)
- `files` — list of changed files
- `budgetCap` — max symbol resolutions per agent (default: 8)

## Pipeline

### 1. Java symbol extraction

Parse the `diff` for Java CALLEE symbols that are NOT defined in the diff itself. Heuristic patterns:

- `<Type>\.<method>\(` → method call on a type
- `implements <Interface>\b` → interface contract reference
- `@Override` followed by a method declaration → override signature
- `extends <Class>\b` → superclass reference

Filter out:
- Java built-ins: `java.util.*`, `java.lang.*`, `java.io.*`
- Self-defined symbols (defined in the diff itself)
- Test scope: anything under `**/test/**`, `**/Test*.java`, `**/*Test.java`

Priority ordering (top-N by `budgetCap`):
1. Symbols on changed lines (highest)
2. Symbols on sensitive paths (auth/, security/, payment/)
3. Alphabetical (lowest)

### 2. Symbol resolution (no suppression)

For each priority symbol, run a `git grep` resolution:

```bash
git grep -E "(class|interface) <symbol>\b"
git grep -E "(public|private|protected)?\s*\w+\s+<symbol>\s*\("
```

**Behavior on resolve outcome**:
- **0 hits**: SKIP this symbol. Do NOT emit a "NOT_FOUND_IN_TREE" block. The caller agent reasons normally without the missing context — the skill is purely additive, never subtractive.
- **≥1 hits**: Read the top match's surrounding ±15 lines via the Read tool.

### 3. Emit context block

For each successfully-resolved symbol, emit a `CROSS_FILE_CONTEXT` block to the caller agent's working context:

```
CROSS_FILE_CONTEXT_START
  symbol: <SymbolName>
  source: <file>:<line>
  definition: |
    <±15 lines of code around the definition>
  notes: reference only — not a review target
CROSS_FILE_CONTEXT_END
```

The caller agent is instructed (via the calling agent's prompt) to treat resolved definitions as **REFERENCE ONLY**, not as review targets — findings should only be emitted for symbols in the diff itself.

## Budget caps

- `budgetCap` resolutions per agent invocation (default 8). Hard cap on `git grep` + `Read` count.
- Bypassed entirely on diffs with zero `*.java` files modified.
- Latency budget: ~5-10 s per Java-touching PR added to baseline.

## Output

Pure additive:
- 0 to N `CROSS_FILE_CONTEXT_START..END` blocks attached to the caller agent's working context before it forms findings.
- No agent dispatch decisions changed.
- No findings suppressed.

## Phase 6 success criteria (pre-registered in `bench/crb/PHASE_6_DESIGN.md`)

| Outcome | Aggregate F1 | Java F1 | Per-language regression > 2σ_lang |
|---|---:|---:|---|
| ✅ SHIP | ≥ 0.322 | ≥ 0.318 | None |
| ⚠️ HOLD | 0.305-0.321 | 0.290-0.317 | Up to 1 lang |
| ❌ CLOSE | < 0.305 | < 0.290 | Any |

## Non-goals

- **No suppression rule.** No `NOT_FOUND_IN_TREE` block. Phase 4c.1 isolation showed this rule (not L5 retrieval itself) drove the Go regression.
- **No other languages.** Java-only scope. Future phases may extend per-language one at a time after Phase 6 ships.
- **No agent §2.5 hallucination-AST integration.** Phase 5.3 evidence (PR #68) showed §2.5 + cross-file-impact graphSignals together regressed F1 by 0.045. The lib stays standalone.
- **No new agents.** Existing `correctness` agent gains a conditional skill call; no new dispatcher entries.
