# Graph Query Patterns

Canonical contract between Soliton and the sibling `graph-code-indexing` system. This file
specifies the `graph-cli` interface Soliton depends on, decoupled from the implementation.

## Why a CLI contract

Soliton ships as a markdown-only Claude Code plugin. It cannot bundle Node / TypeScript
dependencies without a build step. The CLI contract:

- Keeps Soliton implementation-agnostic (could be backed by any graph system).
- Lets the graph repo stay a separate release cycle.
- Gives a natural place for caching, quotas, and rate limits.
- Is easy to stub / mock for tests.

## Binary

Expected on PATH (user installs `graph-code-indexing` separately, or uses a Docker wrapper in
CI). If missing, `graph-signals.md` emits `GRAPH_SIGNALS_UNAVAILABLE` and the pipeline falls
back to v1 heuristics.

Name: `graph-cli`. Current implementation: Node.js wrapper shipped with
`graph-code-indexing@>=0.2.0`. Soliton specifies the contract; any compatible binary works.

## Global flags

| Flag | Purpose |
|---|---|
| `--graph <path>` | Path to built graph JSON (or `.soliton/graph.json` default) |
| `--base-graph <path>` | For diff queries: pre-PR graph |
| `--head-graph <path>` | For diff queries: post-PR graph |
| `--format json` | Output format (only option for now) |
| `--timeout-ms <n>` | Per-query timeout (default 500 ms) |
| `--quiet` | Suppress progress output; only emit result JSON |

## Alternative provider: MCP-server backends

The primary contract above targets `graph-cli` from sibling `graph-code-indexing`. Until that CLI is packaged (`graph-code-indexing@>=0.2.0` release pending), Soliton's `skills/pr-review/graph-signals.md` can alternatively consume an MCP-server provider that exposes equivalent queries. Two OSS providers implement a compatible surface today:

- **`code-review-graph`** by tirth8205 — Tree-sitter → SQLite, 23 languages, 28 MCP tools, `crg-daemon` for incremental updates, git-hook auto-refresh. MIT. `pip install code-review-graph && code-review-graph install` configures `~/.claude.json` automatically. Reports 8.2× avg token reduction on 6 OSS repos (reproducible via `code-review-graph eval --all`).
- **`better-code-review-graph`** by n24q02m — fork of the above with (a) qualified-call resolution + bare-name fallback, (b) pagination (`max_results`, truncation flags) for unbounded outputs, (c) dual-mode embedding (local ONNX `qwen3-embed` ~200 MB + multi-provider cloud fallback). Closer to production-readiness; same MCP surface.

### Adapter mapping (Soliton query → MCP tool or backend CLI)

Validation status against `code-review-graph` 2.3.2 (tested 2026-04-21 on this repo; 278-node graph built in 15.9 s). **CLI** column is the subcommand exposed by `code-review-graph`'s top-level binary; **MCP** column is the tool name exposed via `code-review-graph serve` (stdio).

| Soliton query | `graph-cli` command | crg CLI | crg MCP tool | Validation |
|---|---|---|---|---|
| `info` | `graph-cli info --graph <path>` | `code-review-graph status` | `list_graph_stats_tool` + `list_repos_tool` | ✅ CLI tested: emits `Nodes/Edges/Files/Languages/Last updated/Built on branch/Built at commit` |
| `blast-radius` | `graph-cli query --blast-radius <file>:<symbol> --depth 2` | — | `get_impact_radius_tool` | ⚠️ MCP-only; no CLI subcommand |
| `taint-paths` | `graph-cli query --taint-paths <source>` | — | `traverse_graph_tool` (DATA_FLOW filter) | ⚠️ MCP-only |
| `dependency-breaks` | `graph-cli diff --deps --base-graph --head-graph` | `code-review-graph detect-changes --base <rev>` | `detect_changes_tool` | ✅ CLI tested: emits `summary, risk_score, changed_functions, affected_flows, test_gaps, review_priorities` |
| `co-change` | `graph-cli query --co-change <file>` | — | `get_affected_flows_tool` | ⚠️ MCP-only; no CLI flag |
| `feature-partition` | `graph-cli query --feature <file>` | — | `list_communities_tool` + `get_community_tool` | ⚠️ MCP-only (community DB exists — migration v4 `communities` table — but no CLI subcommand reads it) |
| `review-bundle` | (composed) | — | `get_review_context_tool` | ⚠️ MCP-only (composes blast-radius + flows + test-coverage) |

**Honest read after validation:** 2 of 7 Soliton queries have direct CLI equivalents in `code-review-graph`; the other 5 are MCP-only. A full graph-signals dogfood against this backend therefore needs either:

1. An MCP client shim inside Soliton (e.g., a Python `graph-cli` wrapper that connects to `code-review-graph serve` over stdio and exposes the 7 queries), or
2. Degraded Step 2.8 signals — only `info` + `dependencyBreaks` flow through; the rest emit `partial: true`. Still useful (dependencyBreaks is what the `cross-file-impact` agent consumes).

Partial-mode is the minimum viable integration. Full-mode is Phase 6 engineering.

### Installation (tested 2026-04-21 on Python 3.14, Windows)

```bash
pip install code-review-graph
cd <repo>
code-review-graph build          # full parse; ~15-30 s on small repos, watch mode available
code-review-graph status         # verify
code-review-graph detect-changes --base HEAD~1 --brief
```

Optionally `code-review-graph install` auto-registers the MCP server in `~/.claude.json`; skipped for the validation above to avoid mutating global config.

### When to use MCP backend vs `graph-cli`

- **MCP backend**: prefer for interactive local dev, Claude Code native flows, rapid incremental updates, language coverage beyond what `graph-code-indexing` supports today (specifically Java/Ruby/Scala/Kotlin/Swift/C#).
- **`graph-cli` contract**: prefer for CI/CD where a pre-built graph JSON ships as an artifact, for air-gapped / audit-friendly enterprises, and once the sibling repo lands Java + PPR centrality + co-change + feature-partition (`graph-code-indexing` gaps A1/A6/B4/B8).
- **Both**: Step 2.8 can accept either; graceful fallback is `GRAPH_SIGNALS_UNAVAILABLE` when neither is present.

### Bug-list absorbed from the fork

These are pre-registered traps for any provider (MCP server or CLI) to avoid. Enforced in Soliton's adapter when it normalizes provider output:

1. **Qualified-name resolution**: `callers_of(Foo)` must resolve against qualified identifiers (`pkg.Foo`, `module::Foo`) with bare-name fallback. Returning empty for a bare name that has qualified matches is a silent recall failure.
2. **Pagination / bounded output**: any query that can return > 500 nodes/edges must expose `max_results` + a truncation flag; unbounded `list_*` tools have shipped multi-hundred-KB outputs into LLM context and blown latency budgets.
3. **Embedding provider graceful fallback**: if semantic search depends on a cloud embedding provider, offer a local fallback (ONNX `qwen3-embed` pattern). Failing hard on network errors breaks every review.

The Soliton adapter (in `skills/pr-review/graph-signals.md` when it lands for MCP) must wrap provider responses to enforce these contracts regardless of which backend is active.

## Queries — contract

### `info`

```bash
graph-cli info --graph $PATH
```

```json
{
  "commitSha": "abc123...",
  "nodeCount": 42000,
  "edgeCount": 180000,
  "edgeTypes": ["PARENT_CHILD", "CALLS", "DATA_FLOW", "IMPORTS",
                "INHERITS", "IMPLEMENTS", "REFERENCES", "CONFIGURES"],
  "languages": ["typescript", "python", "c", "cpp"],
  "builtAt": "2026-04-18T10:00:00Z",
  "builderVersion": "0.2.1"
}
```

### `query --blast-radius <file>:<symbol>`

Reverse BFS over CALLS + REFERENCES edges.

```json
{
  "file": "src/foo.ts",
  "symbol": "fooHandler",
  "depth": 2,
  "directCallers": [
    {"file": "src/routes.ts", "line": 42, "caller": "registerRoutes"}
  ],
  "transitiveCallers": [
    {"file": "src/routes.ts", "line": 42, "caller": "registerRoutes", "depth": 1},
    {"file": "src/server.ts", "line": 15, "caller": "main", "depth": 2}
  ],
  "totalFiles": 2
}
```

### `query --dep-diff <file>`

Compare exports + signatures pre vs post.

```json
{
  "file": "src/foo.ts",
  "removedExports": [{"name": "oldFunc", "line": 12}],
  "signatureChanges": [
    {"symbol": "fooHandler", "old": "(req, res) => void",
     "new": "(req, res, next) => void", "kind": "parameter_added_required"}
  ],
  "brokenCallers": [
    {"file": "src/routes.ts", "line": 42, "caller": "registerRoutes",
     "reason": "passes 2 args; function requires 3 after signature change"}
  ]
}
```

### `query --taint <file>:<line-range> --sinks <list>`

Forward DATA_FLOW BFS from the source to any sink in `--sinks`.

`--sinks` options: `io`, `auth`, `db`, `shell`, `exec`, `xss`, `ssrf`, `path-traversal`,
`secrets`, `logging`.

```json
{
  "source": "src/handlers/signup.ts:24",
  "sink": "src/db/users.ts:80",
  "kind": "sql",
  "edges": [
    {"from": "src/handlers/signup.ts:24", "to": "src/handlers/signup.ts:30", "type": "assign"},
    {"from": "src/handlers/signup.ts:30", "to": "src/handlers/signup.ts:35", "type": "call_arg"},
    {"from": "src/handlers/signup.ts:35", "to": "src/db/users.ts:80", "type": "CALLS"}
  ],
  "sanitizers": [],
  "confidence": 85
}
```

Multiple paths emitted as an array under top-level `paths: []`. If no path exists, return
`{"paths": []}`.

### `query --co-change <file>`

Mine git log for files modified together over `--window-days`.

```json
{
  "file": "src/foo.ts",
  "cluster": [
    {"file": "src/foo.test.ts", "count": 38, "strength": 0.95},
    {"file": "src/bar.ts", "count": 12, "strength": 0.72}
  ],
  "strength": 0.83
}
```

Strength is Jaccard over commit sets. ≥ 0.7 is high co-change; < 0.3 ignored.

*Note*: co-change requires `CO_CHANGE` edges in the graph. If the graph lacks them
(`graph-code-indexing` Gap A6 not yet landed), `graph-cli` returns an empty array with
`"method": "not_implemented"`.

### `query --feature-of <file>`

Leiden / community detection partition membership.

```json
{
  "file": "src/auth/signup.ts",
  "partitionId": "auth/user-creation",
  "members": [
    "src/auth/signup.ts", "src/auth/password.ts",
    "src/auth/__tests__/signup.test.ts", "src/emails/welcome.ts"
  ],
  "apiSurface": [
    {"file": "src/auth/signup.ts", "export": "signupHandler"},
    {"file": "src/auth/signup.ts", "export": "SignupError"}
  ],
  "dataContracts": ["users.email (unique)", "users.created_at"],
  "confidence": 0.88
}
```

Requires feature-partition extractor (`graph-code-indexing` Gap B8). If unavailable,
returns `{"partitionId": null}` and Soliton falls back to directory-based grouping.

### `query --centrality <file>:<symbol>`

Personalized PageRank / Random Walk with Restart (when implemented); degree fallback.

```json
{
  "symbol": "src/auth/signup.ts:signupHandler",
  "pprScore": 0.032,
  "degree": 18,
  "method": "ppr"
}
```

`method: "degree"` indicates PPR not yet implemented (`graph-code-indexing` Gap A1).

### `query --test-files-for <file>`

Reverse-BFS filtered to nodes flagged as test files.

```json
{
  "file": "src/auth/signup.ts",
  "testFiles": ["src/auth/__tests__/signup.test.ts"],
  "coverage": "full",
  "coverageMethod": "graph"
}
```

### `update --since <sha>`

Incremental graph update. Used by `graph-signals.md` when graph is stale but behind HEAD.

```json
{"status": "ok", "filesAdded": 3, "filesRemoved": 0, "edgesAdded": 42}
```

## Error codes

All queries return JSON on success. On error, `graph-cli` exits non-zero with JSON on stderr:

```json
{"error": "graph_not_found", "message": "path does not exist"}
{"error": "stale_graph", "message": "graph is 14 commits behind base", "age": 14}
{"error": "language_unsupported", "language": "cobol"}
{"error": "timeout", "message": "query exceeded 500ms"}
{"error": "invalid_symbol", "message": "no node for foo.ts:fooHandler"}
```

Soliton's `graph-signals.md` must map each error to a graceful degradation.

## Caching

`graph-cli` maintains an LRU cache in `.soliton/graph-cache/` keyed by
`(commitSha, queryType, args)`. Cache hits return in < 10 ms.

## Security note

`graph-cli` reads the local graph JSON; it does NOT make network calls. Safe to run in
sandboxed CI. No secrets leave the repo.

## Dependency on graph-code-indexing

This contract depends on these features from the sibling repo:

| Feature | Current status | Needed by |
|---|---|---|
| 8 edge types | ✅ shipped in `src/types/EdgeType.ts` | Already usable |
| TS/JS/Py/C/C++ parsers | ✅ shipped | Already usable |
| CLI binary `graph-cli` | ⚠️ needs packaging | Week 3 of pilot |
| Incremental update | ✅ `src/updates/` | Usable |
| Java parser | ❌ Gap B4 | Enterprise rebuild critical |
| PPR centrality | ❌ Gap A1 | Nice to have; degree fallback works |
| Co-change edges | ❌ Gap A6 | Nice to have; empty result if absent |
| Feature partition | ❌ Gap B8 (Leiden + semantic) | Needed for feature-aware chunking |
| SQL analyzer | ❌ Gap B4 | Enterprise rebuild (COBOL monoliths) |

Track these in the sibling repo's issue tracker; the `graph-cli` binary can ship earlier with
"feature unavailable" returns for the missing pieces.
