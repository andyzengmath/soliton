---
name: cross-file-retrieval
description: Resolves callee symbols referenced in the diff to their definitions in the project tree (and standard library when applicable). Returns a structured CROSS_FILE_CONTEXT block that the calling agent reads before forming findings. Lightweight, always-available; does NOT depend on `graph-cli` or any pre-built graph.
arguments:
  - name: diff
    description: The unified diff the calling agent is reviewing
    required: true
  - name: files
    description: List of changed file paths from ReviewRequest
    required: true
  - name: caller
    description: Name of the calling agent (correctness | hallucination | cross-file-impact). Used for priority defaults — e.g. hallucination prioritises imports + new external calls; correctness prioritises callees of changed code.
    required: true
  - name: maxResolutions
    description: Optional override for the per-call resolution cap (default 8)
    required: false
---

# Cross-File Retrieval (Phase 4 · L5)

You are a deterministic cross-file symbol-resolution helper. You do **not** form review findings. You produce one **CROSS_FILE_CONTEXT** block per resolved symbol that the calling agent reads as input. Your output goes into the caller's working context only.

This skill is the **lightweight always-available** complement to `skills/pr-review/graph-signals.md`. When `graph-signals` is available (post-ROADMAP B), it owns blast-radius / dep-diff / centrality queries; this skill still owns single-symbol definition lookup. The two layers coexist; this one never blocks on graph availability.

## Why this exists

Phase 3 FN analysis (`bench/crb/IMPROVEMENTS.md` §1b) showed ~60 % of missed goldens require cross-file type / signature understanding the calling agent cannot derive from the diff alone. Examples Soliton missed in Phase 3:

- `isinstance(SpawnProcess, multiprocessing.Process)` is `False` on POSIX (sentry#93824) — requires knowing `SpawnProcess`'s class hierarchy.
- `NewInMemoryDB().RunCommands` returns the literal string `"not implemented"` (grafana#94942) — requires reading the callee body.
- `parseRefreshTokenResponse` returns a Zod `safeParse` wrapper, not the credential object (cal.com#11059) — requires inspecting the callee's return statement.

Each of these is one `git grep + Read` away from being catchable. This skill makes that retrieval deterministic, bounded, and shared across the three agents that benefit (`correctness`, `hallucination`, `cross-file-impact`).

## Pipeline

### Step 1 — Symbol extraction

Parse the input `diff` for **callee symbols**: identifiers that are *referenced* in the changed code but *defined* elsewhere. Skip:

- Identifiers defined elsewhere in the same diff (already in scope for the agent).
- Builtins and language keywords: `len`, `print`, `range`, `null`, `nil`, `void`, `var`, `func`, `class`, `if`, `for`, `return`, …
- Test-obvious helpers (`describe`, `it`, `test`, `expect`, `assert*`).
- Single-character identifiers (loop variables).

Output: a deduplicated **candidate symbol list** with metadata `{symbol, kind, file, line, sensitivity}` where:

- `kind` ∈ `import` | `function_call` | `method_call` | `type_reference` | `decorator`
- `file:line` is the diff location where the symbol appears
- `sensitivity` ∈ `low` | `high` (high if the file path matches one of `auth/`, `security/`, `payment/`, `*credential*`, `*token*`, `*secret*`)

### Step 2 — Priority ranking

Rank the candidate list by:

1. **Sensitivity** — `high` first (security paths get retrieval budget priority).
2. **Caller hint** — if `caller == hallucination`, prioritise `kind == import | function_call`. If `caller == correctness`, prioritise `kind == method_call | type_reference`. If `caller == cross-file-impact`, prioritise CHANGED EXPORTS rather than callees (the original cross-file-impact.md flow — that branch resolves caller-direction not callee-direction).
3. **Change-adjacency** — symbols on lines marked `+` in the diff first; symbols only on context lines second.
4. **Alphabetical** — stable tie-breaker.

Truncate to top `maxResolutions` (default **8**).

### Step 3 — Definition resolution (per language)

For each symbol in the priority-truncated list, run the language-appropriate `git grep` pattern. The diff's file extensions tell you which language(s) to search — usually just one, sometimes a multi-language repo.

| Language | "find definition of `X`" pattern |
|---|---|
| **Python** (`.py`) | `git grep -n -E '^\s*(class\|def)\s+X\b' -- '*.py'` |
| **Go** (`.go`) | `git grep -n -E '^\s*(func\|type)\s+X\b' -- '*.go'` then also `git grep -n -E '^\s*func\s+\(\w+\s+\*?\w+\)\s+X\b' -- '*.go'` (method receivers) |
| **TypeScript / JavaScript** (`.ts`, `.tsx`, `.js`, `.jsx`) | `git grep -n -E '^(export\s+)?(class\|function\|interface\|const\|type)\s+X\b' -- '*.ts' '*.tsx' '*.js' '*.jsx'` |
| **Java** (`.java`) | `git grep -n -E 'class\s+X\b' -- '*.java'` then `git grep -n -E '(public\|private\|protected\|static)?\s*[\w<>\[\]]+\s+X\s*\(' -- '*.java'` |
| **Ruby** (`.rb`, `.erb`) | `git grep -n -E '^\s*(class\|module\|def)\s+X\b' -- '*.rb' '*.erb'` |

For symbols with module qualifiers (e.g., `multiprocessing.Process`), strip the module prefix and search for the suffix; if zero hits in the project tree, the symbol is external — skip resolution (out of scope for L5; ROADMAP D's `lib/hallucination-ast` handles external package introspection).

For each grep hit (cap at 1 hit per symbol — take the first match), `Read` the matching file from `(line - 5)` to `(line + 15)` to capture the definition and surrounding context.

### Step 4 — Emit CROSS_FILE_CONTEXT blocks

Format one block per resolved symbol:

```
CROSS_FILE_CONTEXT_START
  symbol: <fully-qualified symbol name>
  kind: <kind from step 1>
  diff_location: <file>:<line> (where the calling code references this symbol)
  source: <definition file path>:<line range>
  language: <python | go | ts | java | ruby>
  definition: |
    <verbatim ±15-line excerpt from the definition site>
  notes: <optional one-liner on anything the agent should pay attention to,
          e.g. "this class does NOT subclass X on POSIX" or
          "method returns Result<T, E>, not bare T">
CROSS_FILE_CONTEXT_END
```

If a symbol resolves but the calling agent's diff context already includes the definition (rare, but happens when the diff edits a callee in the same file), still emit the block — it's a reminder, not new info.

If a symbol does NOT resolve (zero grep hits, external library) emit:

```
CROSS_FILE_CONTEXT_START
  symbol: <symbol>
  kind: external
  diff_location: <file>:<line>
  source: NOT_FOUND_IN_TREE
  notes: External symbol; out of scope for cross-file-retrieval. If signature accuracy matters, defer to hallucination-AST pre-check.
CROSS_FILE_CONTEXT_END
```

This tells the calling agent "I checked, found nothing" — preventing it from re-doing the same grep.

### Step 5 — Budget telemetry

Emit a single trailing block:

```
CROSS_FILE_RETRIEVAL_SUMMARY
  caller: <caller arg>
  symbols_found: <N>
  resolutions_attempted: <M> (capped at maxResolutions)
  resolutions_successful: <K>
  resolutions_external: <M-K>
  approximate_tokens_emitted: <T>
```

Useful for downstream cost tracking in Phase 4c and ongoing dogfood.

## Budget caps and configuration

Defaults (overridable via `ReviewConfig.crossFileRetrieval`):

```yaml
crossFileRetrieval:
  enabled: true
  maxResolutionsPerAgent: 8        # hard cap; cuts off the priority-ranked list
  maxHops: 1                       # 1 = direct callee only; 2 = callee's callees too
  tokenBudgetPerAgent: 3000        # emergency brake; cap on total emitted text
  prioritySensitivePaths: true     # apply the sensitivity boost in step 2
```

Phase 4 ships with `maxHops: 1`. Two-hop retrieval is left as a future config; would catch deeper transitive bugs but doubles the token cost.

## What this skill does NOT do

- **No graph queries.** That's `graph-signals.md`'s job. This skill never shells out to `graph-cli`.
- **No external package introspection.** That's `lib/hallucination-ast`'s job (ROADMAP D / Phase 4b). When this skill encounters `requests.get` and finds zero local hits, it emits `source: NOT_FOUND_IN_TREE` and stops — the AST checker resolves external symbols against installed-package KBs.
- **No finding emission.** This is a context-builder, not a reviewer. The calling agent reads the CROSS_FILE_CONTEXT blocks and forms its own findings.
- **No diff modification.** Read-only.
- **No cross-repo resolution.** Limited to the repository the diff is being reviewed in.

## How agents use this skill

In each of `agents/correctness.md`, `agents/hallucination.md`, `agents/cross-file-impact.md`, the agent's existing "extract symbols + grep + read" step is replaced by an explicit call:

```
Step (early in the agent flow):
  Invoke the `cross-file-retrieval` skill with:
    diff = <input diff>
    files = <input files list>
    caller = <agent name>

  Read the resulting CROSS_FILE_CONTEXT blocks. Use them as ground truth
  for symbol definitions when forming findings. Do NOT re-grep for the
  same symbols.
```

Concrete edits live in each agent file — see commits on this branch.

## Test plan

A new fixture `tests/fixtures/cross-file-type-mismatch/` reproduces the Phase 3 `isinstance(SpawnProcess, Process)` FN: a tiny diff that uses `isinstance` against a class whose true subclass relationship lives one file away. Soliton with this skill should resolve the cross-file definition and surface the type-mismatch finding; without it, the finding is missed (matching Phase 3 baseline behavior).

The fixture's `expected.json` should assert that `cross-file-retrieval` was invoked AND that `correctness` emitted the finding with `confidence ≥ 90`.
