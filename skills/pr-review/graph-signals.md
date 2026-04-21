---
name: graph-signals
description: Queries the sibling graph-code-indexing system for blast radius, dependency breaks, taint paths, co-change clusters, and feature criticality. Emits structured signals that downstream agents consume as pre-computed context. No LLM reasoning — this is a deterministic graph-query orchestrator.
arguments:
  - name: graph_path
    description: "Optional path to a pre-built graph JSON; when absent, uses `.soliton/graph.json` or the value of the SOLITON_GRAPH_PATH env var"
    required: false
---

# Graph Signals — Tier 1

You are the Graph Signal Service orchestrator. You run AFTER Tier 0 (deterministic gate) and
BEFORE risk scoring. Your job is to query a pre-built code graph from the sibling
`graph-code-indexing` repo and emit structured signals that every downstream agent can consume
as pre-computed context, instead of rediscovering cross-file structure with grep or LLM calls.

**Non-negotiables**:
- You do NOT use an LLM. All queries are `graph-cli` shell-outs.
- You complete within 3 seconds on a typical PR.
- If the graph is unavailable or stale, you emit `GRAPH_SIGNALS_UNAVAILABLE` and downstream
  tiers fall back to v1 grep-based behaviour. Never block the review on graph absence.

## Input

You receive from `SKILL.md` Step 2.75:
- `diff` — unified diff of all changes
- `files` — list of changed files (paths + statuses)
- `config.graph` — graph configuration (path, mode, timeout)

## Backend detection

Run these checks in order and pick the first that succeeds. Every subsequent query obeys the selected backend's contract; do not mix backends within a single review.

1. **Full mode (`graph-cli`)** — `command -v graph-cli` succeeds AND `$GRAPH_PATH` (resolved per "Step 1 — locate the graph" below) points to a valid `.json` file.
2. **Partial mode (`code-review-graph`)** — `command -v code-review-graph` succeeds AND `.code-review-graph/graph.db` exists under the repo root (or `$CRG_ROOT/graph.db` if `CRG_ROOT` env var set). Degrades gracefully: only `info` and `dependencyBreaks` signals populate; others emit with `partial: true`.
3. **Unavailable** — neither backend available. Emit `GRAPH_SIGNALS_UNAVAILABLE` and STOP; downstream pipeline falls back to v1 grep-based heuristics.

In the emitted signal block, set `mode` to `full | partial | unavailable` so downstream agents know how much to trust the absence of a signal.

## Graph interface (Mode B — CLI shell-out)

Soliton v2 ships as `Mode B`: it shells out to a `graph-cli` binary exposed by
`graph-code-indexing`. This keeps the plugin markdown-only (no Node dependencies inside Soliton).

Canonical commands (see `rules/graph-query-patterns.md` for the full list):

```bash
# 1. Sanity check: graph is valid and fresh
graph-cli info --graph $GRAPH_PATH
# → { "commitSha": "abc...", "nodeCount": N, "edgeCount": E, "builtAt": "ISO-8601" }

# 2. Blast radius
graph-cli query --blast-radius "$FILE:$SYMBOL" --depth 2 --graph $GRAPH_PATH
# → { "directCallers": [...], "transitiveCallers": [...], "totalFiles": N }

# 3. Dependency break diff (pre vs post)
graph-cli query --dep-diff "$FILE" --base-graph $BASE_GRAPH --head-graph $HEAD_GRAPH
# → { "removedExports": [...], "signatureChanges": [...], "brokenCallers": [...] }

# 4. Taint path search
graph-cli query --taint "$FILE:$LINE_RANGE" --sinks io,auth,db,shell --graph $GRAPH_PATH
# → { "paths": [{source, sink, kind, edges: [...]}] }

# 5. Co-change history
graph-cli query --co-change "$FILE" --window-days 180 --graph $GRAPH_PATH
# → { "cluster": [...], "strength": 0-1 }

# 6. Feature partition membership
graph-cli query --feature-of "$FILE" --graph $GRAPH_PATH
# → { "partitionId": "...", "members": [...], "apiSurface": [...] }

# 7. Centrality (PPR when available; fallback to degree)
graph-cli query --centrality "$FILE:$SYMBOL" --graph $GRAPH_PATH
# → { "pprScore": 0-1, "degree": N, "method": "ppr"|"degree" }

# 8. Test coverage nodes
graph-cli query --test-files-for "$FILE" --graph $GRAPH_PATH
# → { "testFiles": [...], "coverage": "full"|"partial"|"none" }
```

Every command must:
- Exit 0 on success, non-zero on error.
- Emit JSON to stdout.
- Complete within 500 ms (individual query); 3 s total budget for this skill.

## Graph interface (partial mode — `code-review-graph`)

When full mode is unavailable but `code-review-graph` is installed, use this contract. Only `info` and `dependency-breaks` map to CLI subcommands; everything else emits with `partial: true` and empty payload.

```bash
# 1. info  →  code-review-graph status
code-review-graph status
# Plain-text output; parse with:
#   Nodes: N              -> nodeCount
#   Edges: E              -> edgeCount
#   Files: F              -> fileCount
#   Built at commit: <sha>-> commitSha
#   Last updated: ISO     -> builtAt
# Wrap into: { "commitSha": "...", "nodeCount": N, "edgeCount": E, "builtAt": "..." }

# 2. dependency-breaks  →  code-review-graph detect-changes --base $BASE_BRANCH
code-review-graph detect-changes --base "$baseBranch" 2>/dev/null
# Structured JSON:
#   {
#     "summary": "...",
#     "risk_score": 0-1,
#     "changed_functions": [{"name", "file", "reason"}...],
#     "affected_flows": [...],
#     "test_gaps": [...],
#     "review_priorities": [{"file", "reason", "severity"}...]
#   }
# Map `changed_functions` + `review_priorities` to Soliton's `dependencyBreaks.brokenCallers`.
# If result.risk_score > 0.5 OR any review_priorities[] has severity=="critical",
# set dependencyBreaks[].severity = "critical"; else "improvement".
```

All other queries (`blast-radius`, `taint-paths`, `co-change`, `feature-partition`, `centrality`, `test-files-for`) are not available in partial mode. Emit these signals as:

```yaml
# In partial mode only — example for blastRadius
blastRadius:
  partial: true
  reason: "code-review-graph backend — blast-radius MCP-only; run full mode for per-symbol signals"
```

**Latency budget for partial mode.** The `exit-0-JSON-500ms` contract above applies to full-mode `graph-cli`. The `code-review-graph` Python CLI has meaningfully higher per-call latency. Measured on this repo 2026-04-21:

| Component | Steady-state | Notes |
|---|---:|---|
| Python interpreter startup | ~1.0 s | `python -c "pass"` |
| Python + `code_review_graph` imports | ~1.1 s | Cold import |
| `code-review-graph --help` (no graph access) | ~0.6 s | Baseline binary launch |
| `code-review-graph status` (full query) | **~8–11 s** | Windows + OneDrive-synced `.code-review-graph/graph.db`; includes git freshness checks |

Transient OneDrive sync bursts have been observed pushing `status` to ~70 s during heavy file-sync activity; this is an environment artifact, not a steady-state cost. For partial mode, relax the contract to:

- Per-query timeout: **10 s** (instead of 500 ms).
- Total skill budget: **20 s** (instead of 3 s).
- If a query exceeds its timeout, mark the corresponding signal `partial: true` with `reason: "backend timeout"` and continue.

This is an acknowledged cost of using a Python process-invocation backend vs the native `graph-cli` Node binary. Operators targeting Soliton's original 3 s total budget should prefer full mode or host `.code-review-graph/graph.db` outside a cloud-synced path. Partial mode is the pragmatic dogfood path while `graph-cli` matures in the sibling repo.

## Graph availability check

### Step 1 — locate the graph

In priority order, matching the selected backend from "Backend detection":

**Full mode (`graph-cli`):**
1. `config.graph.path` from `.claude/soliton.local.md`
2. `SOLITON_GRAPH_PATH` env var
3. `.soliton/graph.json` at repo root

**Partial mode (`code-review-graph`):**
1. `$CRG_ROOT/graph.db` if `CRG_ROOT` env var set
2. `.code-review-graph/graph.db` at repo root (default `code-review-graph build` output)

If neither backend has a graph at any of its candidate paths, emit `GRAPH_SIGNALS_UNAVAILABLE` and STOP.

### Step 2 — freshness check

Run `graph-cli info --graph $PATH`. Compare `commitSha` to `git merge-base $baseBranch HEAD`:

- Match → graph is fresh for the base; usable.
- Graph is ahead of base but contains it as ancestor → usable.
- Graph is stale by ≤ 10 commits → usable with `stale` flag set.
- Graph is stale by > 10 commits OR on a divergent branch → try incremental update via
  `graph-cli update --since $COMMIT`. If that fails, emit `GRAPH_SIGNALS_UNAVAILABLE`.

## Signal computation

### Step 3 — blast radius

For each changed file and each exported symbol in the diff:

```
for symbol in exported_symbols_in_diff(file):
    result = graph_cli("query --blast-radius ${file}:${symbol} --depth 2")
    signals.blastRadius.push({
        file, symbol,
        directCallers: result.directCallers.length,
        transitiveCallers: result.transitiveCallers.length,
        affectedFiles: unique(result.transitiveCallers.map(x => x.file)),
    })
```

### Step 4 — dependency breaks

For each file with a signature change or removed export (detected via `difftastic` output
from Tier 0, OR by diffing the AST-level exports):

```
for file in files_with_signature_changes:
    result = graph_cli("query --dep-diff ${file} --base-graph ${base_graph} --head-graph ${head_graph}")
    if result.brokenCallers:
        signals.dependencyBreaks.push({
            changedFile: file,
            brokenCallers: result.brokenCallers,  // [{file, line, reason}]
            severity: "critical" if broken signature at runtime else "improvement"
        })
```

### Step 5 — taint paths

For each file that touches user-input boundaries (`request`, `req`, `args`, `params`, URL
params, form body parsers) AND the diff is non-trivial:

```
for source_file in files_with_input_touches:
    ranges = diff_hunks_containing_input_sources(source_file)
    for range in ranges:
        result = graph_cli("query --taint ${file}:${range} --sinks io,auth,db,shell,exec,xss")
        for path in result.paths:
            signals.taintPaths.push({
                source: path.source,
                sink: path.sink,
                kind: path.kind,         # sql|xss|ssrf|auth|shell|path-traversal
                edges: path.edges,        # the DATA_FLOW path
                confidence: path.confidence,
            })
```

### Step 6 — co-change clusters

```
for file in changed_files:
    result = graph_cli("query --co-change ${file} --window-days 180")
    if result.cluster.length > 0:
        signals.coChangeHits.push({
            file,
            historicalPartners: result.cluster,      # files that usually change together
            strength: result.strength,                # 0-1
        })
```

### Step 7 — feature membership

```
for file in changed_files:
    result = graph_cli("query --feature-of ${file}")
    signals.affectedFeatures.push({
        file,
        partitionId: result.partitionId,
        members: result.members,         # all files in the partition
        apiSurface: result.apiSurface,   # public API points
    })

# Dedup by partitionId
signals.affectedFeatures = dedup_by(signals.affectedFeatures, "partitionId")
```

### Step 8 — centrality / criticality

Only computed for functions with non-trivial callers (≥ 3). For everything else, skip.

```
for symbol in changed_exported_symbols_with_callers:
    result = graph_cli("query --centrality ${file}:${symbol}")
    if result.pprScore > 0.01 or result.degree > 10:
        signals.criticalityScore.push({
            symbol,
            pprScore: result.pprScore,
            degree: result.degree,
            method: result.method,
        })
```

### Step 9 — feature coverage

For each affected feature partition:

```
for partition in signals.affectedFeatures:
    coverage = "none"
    for file in partition.members:
        if file matches test-file patterns:
            coverage = "full"  # simplification; refine later
            break
    else:
        # Any test files modified in this diff?
        if any(f.status in [M, A] and matches_test_pattern for f in partition.members):
            coverage = "partial"
    signals.featureCoverage.push({
        partitionId: partition.partitionId,
        testFiles: [...],
        coverage,
    })
```

## Output

Emit in this exact block format:

```
GRAPH_SIGNALS_START
graph:
  mode: full|partial
  backend: graph-cli|code-review-graph
  path: <path>
  commitSha: <sha>
  freshness: fresh|stale
  nodeCount: N
  edgeCount: E
blastRadius:
  - file: <f>
    symbol: <s>
    directCallers: N
    transitiveCallers: N
    affectedFiles: [...]
dependencyBreaks:
  - changedFile: <f>
    brokenCallers: [{file, line, reason}, ...]
    severity: critical|improvement
taintPaths:
  - source: <f:line>
    sink: <f:line>
    kind: sql|xss|ssrf|auth|shell|path-traversal
    edges: [...]
    confidence: 0-100
coChangeHits:
  - file: <f>
    historicalPartners: [...]
    strength: 0-1
affectedFeatures:
  - partitionId: <id>
    members: [...]
    apiSurface: [...]
criticalityScore:
  - symbol: <f:sym>
    pprScore: 0-1
    degree: N
    method: ppr|degree
featureCoverage:
  - partitionId: <id>
    testFiles: [...]
    coverage: full|partial|none
GRAPH_SIGNALS_END
```

If graph was unavailable:

```
GRAPH_SIGNALS_UNAVAILABLE
reason: <path_not_found | stale | tool_error>
fallback: grep-based heuristics
```

## Consumers

| Signal | Consumed by | How |
|---|---|---|
| `blastRadius` | `risk-scorer` Factor 1 | Replaces grep-based count |
| `dependencyBreaks` | `cross-file-impact` agent | Pre-computed; agent only *explains* breaks |
| `taintPaths` | `security` agent | Narrow context — go directly to source → sink |
| `taintPaths` | `risk-scorer` new Factor 7 | Weight 20% if any path exists |
| `coChangeHits` | `historical-context` agent | Replaces `git log` shell-out |
| `affectedFeatures` | `SKILL.md` Step 2.75 chunking | Feature-aware chunking for large PRs |
| `affectedFeatures` | Synthesizer evidence chain | Every finding gets partition context |
| `criticalityScore` | `risk-scorer` new Factor 8 | Weight 10% feature_criticality |
| `featureCoverage` | `test-quality` agent | Pre-flagged partitions with `coverage: none` |

## Graceful degradation

- **Graph unavailable**: skill exits with `GRAPH_SIGNALS_UNAVAILABLE`. Downstream tiers fall
  back to their v1 grep behaviour. Soliton still produces a review, just less precise on
  cross-file claims.
- **Partial graph** (e.g., language not supported): emit signals for supported files, mark
  others `partial` in output. Downstream agents still use what's available.
- **Graph error on one query**: skip that query, emit others, continue.
- **Graph timeout**: kill query at 500 ms, skip, continue.

## Rules

- NEVER use an LLM in this skill. If you find yourself reasoning about the graph, you're
  doing the wrong job — downstream agents reason, you just deliver signals.
- Every signal must have a provenance pointer (graph commit SHA + query used) so the
  synthesizer can cite graph edges as evidence chain entries.
- Total wall-clock ≤ 3 s. If over, emit what you have and mark `partial: true`.
- When graph signals contradict Tier-0 findings (e.g., Semgrep flags SQLi but graph shows no
  data-flow path): emit both; mark as `conflict` in the signal, synthesizer resolves.
