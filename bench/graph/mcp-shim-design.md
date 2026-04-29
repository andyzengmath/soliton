# MCP shim — design notes

Closes the no-Java-and-no-sibling-repo-needed half of `POST_V2_FOLLOWUPS §B2`.

## Why a shim

Soliton's `skills/pr-review/graph-signals.md` Mode B contract expects 7 query subcommands from a `graph-cli` binary:

| Soliton query | Provider: `code-review-graph` |
|---|---|
| `info` | CLI: `code-review-graph status` ✅ |
| `dependency-breaks` | CLI: `code-review-graph detect-changes` ✅ |
| `blast-radius` | MCP-only: `get_impact_radius_tool` |
| `taint-paths` | MCP-only: `traverse_graph_tool` (DATA_FLOW) |
| `co-change` | MCP-only: `get_affected_flows_tool` |
| `feature-partition` | MCP-only: `list_communities_tool` + `get_community_tool` |
| `review-bundle` | MCP-only: `get_review_context_tool` |

5 of 7 queries are MCP-only on `code-review-graph`'s 28-tool surface. `bench/graph/mcp_shim.py` forks `code-review-graph serve` once, speaks JSON-RPC over stdio, and exposes all 7 queries as `graph-cli`-compatible subcommands. Mode B can then run end-to-end against the OSS backend without waiting for the sibling `graph-code-indexing` repo's `graph-cli` packaging.

## Architecture

```
soliton orchestrator (Step 2.8 graph-signals)
        │
        ▼
   bench/graph/mcp_shim.py  ──[fork]──>  code-review-graph serve
        │                                       │
        │      JSON-RPC (stdio)                 │
        ├─────────[initialize]─────────────────▶│
        │◀────────[result]──────────────────────│
        ├─────────[notif: initialized]─────────▶│
        │                                       │
        ├──[tools/call get_impact_radius_tool]─▶│
        │◀────────[result: content]─────────────│
        │
        ▼
  Soliton-shaped JSON
```

Single subprocess for the lifetime of one shim invocation; multiple `tools/call` round-trips amortise the ~1s server startup cost. Long-lived shim daemonisation is out of scope for v0 — each Soliton review re-forks.

## Status (this PR — starter)

**Wired end-to-end:**
- `info` (calls `list_graph_stats_tool` via MCP)
- `blast-radius` (calls `get_impact_radius_tool` via MCP, with field-name translation `direct_callers`/`directCallers` + `transitive_callers`/`transitiveCallers`)
- `dependency-breaks` (shells out to the existing `code-review-graph detect-changes` CLI; doesn't go through MCP — CLI output is the canonical shape and we avoid a second translation layer)

**All 7 queries wired** as of follow-up commit on this branch:
- `taint-paths` → `traverse_graph_tool` with edge_type=DATA_FLOW (per-source-node loop, sink-category filter, tolerant retry on schema-validation errors)
- `co-change` → `get_affected_flows_tool` (`window_days=180` default matches Step 6)
- `feature-partition` → `list_communities_tool` → `get_community_tool` (client-side join: list → first community → get details)
- `review-bundle` → `get_review_context_tool` (`--file` for single, `--files` for comma-separated list; pass-through of server output for downstream extraction)

**MCP tool input schemas are not formally documented** in `code-review-graph`'s repo; the handlers assume reasonable key names (`file`, `start`, `edge_type`, `max_depth`, `window_days`, `community_id`) and use tolerant fallback chains on response keys (`directCallers` ↔ `direct_callers` ↔ `directCallers`). The `taint-paths` handler retries with a minimal arg set if the server rejects extra args. End-to-end runtime validation against a populated graph will surface any schema drift; today's runtime smoke is gated on getting a real Soliton review through `mcp_shim.py` end-to-end.

**Still missing in this PR:** the `criticality` query from graph-signals.md Step 8. Soliton's signal type is `criticalityScore: [{symbol, pprScore, degree, method}, ...]`. The closest `code-review-graph` MCP tool is centrality-related; no direct match in the recon table. Filed as the next gap to close.

## What follows in subsequent PRs

1. **Implement the 4 stubbed handlers** (~1-2 days). Each is a `client.call_tool(<tool>, args)` + a Soliton-shape translation. Schema matching may require small clarification PRs against `code-review-graph` if its tool output drifts from Soliton's expected fields.
2. **Latency characterisation.** Prior memory notes ~8-11s per `graph-cli` call on Windows + OneDrive (deemed acceptable). Want to validate the shim's overhead is < 100ms per round-trip after server warmup.
3. **Backend-detection in `skills/pr-review/graph-signals.md`.** Add an "if `bench/graph/mcp_shim.py` is on PATH or runnable, prefer it over CLI partial-mode" rule so Mode B activates the full 7-query path automatically.
4. **CRB measurement.** Re-run Phase 5.2-style with graphSignals enabled end-to-end; compare F1 to current 0.313 baseline.

## Smoke test

`python bench/graph/mcp_shim.py info --graph .` should emit a JSON object containing at least `{nodes, edges}` keys (or the server's native naming for those — `list_graph_stats_tool` doesn't follow Soliton's `{nodes, edges, files, languages}` schema verbatim, so the shim's `cmd_info` returns server output as-is for v0). Real translation will land alongside the 4 stubs.

`python bench/graph/mcp_shim.py dependency-breaks --base HEAD~3` should emit the same JSON `code-review-graph detect-changes --base HEAD~3` does — `{summary, risk_score, changed_functions, affected_flows, test_gaps, review_priorities}`.

## Risks + open questions

- **MCP protocol version.** Pinned to `2024-11-05` per spec; if `code-review-graph` is built against a newer revision, the `initialize` handshake may fail. Mitigation: server stderr is captured + emitted to caller's stderr on error.
- **JSON-RPC id collisions.** The shim is single-threaded for writes; if multiple Soliton agents call the shim concurrently from a parallelised orchestrator, each one forks its own subprocess (no shared state across processes), so id collisions are intra-process only and the lock prevents them.
- **Stdout pollution from server logs.** Some MCP servers write log lines to stdout instead of using the `notifications/log` channel. The reader loop currently silently skips non-JSON lines (`json.JSONDecodeError` → continue), which is robust but could mask startup banners. Stderr is buffered and surfaceable via `client.stderr_tail()` on error paths.
- **Long-lived daemon mode.** Future PR could add a `--daemon` flag that listens on a Unix socket / named pipe and amortises the server startup across many Soliton reviews. Out of v0 scope.
