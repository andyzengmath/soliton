## Summary
7 files changed, 47 lines added, 34 lines deleted. 2 findings (1 critical, 1 improvement, 0 nitpicks).
Security backport for CVE-2024-9264: disables SQL Expressions and removes the go-duck dependency. The disable itself is belt-and-suspenders (rejected at `ReadQuery` and again in the stub `*DB`), but the gating helper in `pkg/expr/reader.go` is written in a way that is dead-code-equivalent and will mislead future maintainers who try to re-enable the feature via the existing flag.

## Critical
:red_circle: [correctness] `enableSqlExpressions` returns false on every path — feature-flag branch is dead code in pkg/expr/reader.go:193 (confidence: 92)
The `enabled := !h.features.IsEnabledGlobally(...)` computation and the `if enabled { return false }` branch are both unreachable side-effects: the function unconditionally returns `false`, so `FlagSqlExpressions` has no influence on behavior. A future maintainer flipping the feature flag to re-enable SQL Expressions (exactly the scenario the flag exists for) will silently not re-enable anything, and log / config audits that key off the flag will be misleading.
```suggestion
func enableSqlExpressions(h *ExpressionQueryReader) bool {
	// SQL Expressions are disabled pending a safe re-implementation
	// (see CVE-2024-9264). The feature flag is intentionally ignored here;
	// do not add a flag check without also restoring a vetted execution path.
	return false
}
```
<details><summary>More context</summary>

After macro expansion the current body is:

```go
enabled := !h.features.IsEnabledGlobally(featuremgmt.FlagSqlExpressions)
if enabled {
    return false
}
return false
```

Truth table:

| `IsEnabledGlobally` | `enabled` (`!`) | taken branch | return |
|---|---|---|---|
| false | true  | `if` body   | false |
| true  | false | fall-through | false |

Both branches return `false`; the computed `enabled` value is used only to pick which `return false` statement runs. Two reasonable intents are observable in the code but neither is what was shipped:

1. **"Always disabled regardless of flag"** — intended for the CVE mitigation. Collapse to `return false` with a comment pointing at CVE-2024-9264 so the next reader understands why the flag is intentionally ignored.
2. **"Enabled iff the flag is on"** — i.e. `return h.features.IsEnabledGlobally(featuremgmt.FlagSqlExpressions)`. If this was the intent, the current code inverts the flag check (via the `!`) *and* shadows the result, which is a two-fault bug.

The function is also misnamed for its actual behavior: "enableSqlExpressions" reads as an enabler, whereas it is effectively a permanent `false` sentinel. Renaming to `sqlExpressionsDisabled` (and inverting the caller check) or to a simpler constant would further reduce footguns.
</details>

## Improvements
:yellow_circle: [consistency] Caller and stub disagree on the error surfaced when SQL Expressions are rejected in pkg/expr/reader.go:128 (confidence: 86)
`reader.go` returns `fmt.Errorf("sqlExpressions is not implemented")` at the query-read boundary, while the stub methods in `pkg/expr/sql/db.go` (`TablesList`, `RunCommands`, `QueryFramesInto`) all return a bare `errors.New("not implemented")`. Operators triaging a rejected query after the CVE-2024-9264 mitigation will see two subtly different strings depending on which layer aborted, which complicates log-based alerting and makes it harder to confirm the mitigation is the cause of the failure rather than an unrelated plugin regression.
```suggestion
return eq, fmt.Errorf("sqlExpressions is disabled (see CVE-2024-9264)")
```
<details><summary>More context</summary>

The two strings in play:

- `pkg/expr/reader.go:129` — `fmt.Errorf("sqlExpressions is not implemented")`
- `pkg/expr/sql/db.go:12,16,20` — `errors.New("not implemented")`

Either is fine in isolation, but being inconsistent across the two layers that both fire as part of the same mitigation makes it harder to write a single grep / alert rule ("SQL Expressions disabled in the wild"). Prefer one canonical message — ideally one that cites the CVE so an operator reading the log does not need to cross-reference the PR to understand why a previously-working query now returns an error. Note also that `pkg/expr/sql/parser.go:TablesList` is a still-public symbol that now always returns this bare `"not implemented"` error via `RunCommands`; anything that imports it outside the changed call sites will inherit the vaguer message.
</details>

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: exported `sql.NewInMemoryDB` / `sql.DB` replacement for the removed `github.com/scottlepp/go-duck` package (API-shape compatible stub, silently fails every call); 2 execution-path call sites rerouted (`pkg/expr/sql_command.go`, `pkg/expr/sql/parser.go`); feature-flag gating inserted at `pkg/expr/reader.go` read path; `go.mod` / `go.sum` / `go.work.sum` churn from dropping 8 transitive deps | Sensitive Paths: `pkg/expr/sql/*` (expression execution surface, CVE-2024-9264 remediation)
AI-Authored Likelihood: LOW
