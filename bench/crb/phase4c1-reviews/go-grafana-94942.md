## Summary
7 files changed, 47 lines added, 37 lines deleted. 4 findings (1 critical, 3 improvements, 0 nitpicks).
Security mitigation for CVE-2024-9264 (SQL Expressions RCE/LFI via DuckDB). Removes the `scottlepp/go-duck` dependency and replaces the DuckDB-backed runtime with an always-erroring in-memory stub. The behavioral intent (SQL expressions hard-disabled) is correct, but the disable gate in `reader.go` contains dead control flow that both obscures intent and wastes a feature-flag lookup.

## Critical
:red_circle: [correctness] `enableSqlExpressions` is dead-code — both branches return `false`, making the feature-flag lookup useless in pkg/expr/reader.go:193 (confidence: 95)
The helper introduced in this PR reads:

```go
func enableSqlExpressions(h *ExpressionQueryReader) bool {
    enabled := !h.features.IsEnabledGlobally(featuremgmt.FlagSqlExpressions)
    if enabled {
        return false
    }
    return false
}
```

Both the `if` branch and the fallthrough return `false` unconditionally, so `h.features.IsEnabledGlobally(...)` and the `enabled` local are computed-but-unused. Static analyzers (`go vet`, `staticcheck` SA4006 / SA9003) will flag this, and a reviewer cannot tell from the diff alone whether the author intended a hard-disable (leaving dead debugging scaffolding) or intended the flag to actually gate behavior and mis-wired the inverted negation (`!IsEnabledGlobally`). For a CVE mitigation landing on a release branch, the gate must be unambiguous — a maintainer who later "cleans this up" could easily introduce a regression by reintroducing the vulnerable path. Replace the body with an explicit hard-disable and drop the flag lookup (or, if flag-gating is genuinely desired, remove the double-negation and the redundant branch).
```suggestion
func enableSqlExpressions(h *ExpressionQueryReader) bool {
	// SQL expressions are hard-disabled in this branch as mitigation for
	// CVE-2024-9264. The featuremgmt.FlagSqlExpressions flag is deliberately
	// ignored here; removing the flag entirely requires a follow-up.
	return false
}
```
[References: CVE-2024-9264, SA4006, SA9003]

## Improvements
:yellow_circle: [testing] No regression test for the QueryTypeSQL disable path in pkg/expr/reader.go:129 (confidence: 90)
The PR adds a hard-disable in the `QueryTypeSQL` case of `ReadQuery` but includes no test asserting that `ReadQuery` returns an error for a SQL-typed query. Because the only guard against the vulnerable code path is the `if !enabled` early-return, a future refactor that accidentally inverts or removes the gate would silently re-enable CVE-2024-9264. Add a table-driven test in `pkg/expr/reader_test.go` that constructs an `ExpressionQueryReader` with the flag both set and unset, issues a `QueryTypeSQL` payload, and asserts an error. This makes the security invariant executable rather than prose-only.
```suggestion
func TestReadQuery_SQLExpressionsDisabled(t *testing.T) {
	for _, flagOn := range []bool{false, true} {
		h := &ExpressionQueryReader{features: featuremgmt.WithFeatures(featuremgmt.FlagSqlExpressions, flagOn)}
		_, err := h.ReadQuery(commonQuery(QueryTypeSQL), jsoniter.NewIterator(jsoniter.ConfigDefault))
		require.Error(t, err, "SQL expressions must be disabled regardless of feature flag (flagOn=%v)", flagOn)
	}
}
```

:yellow_circle: [consistency] Error message "sqlExpressions is not implemented" misrepresents the disable as a missing-feature in pkg/expr/reader.go:131 (confidence: 80)
The error surfaces to API consumers as `sqlExpressions is not implemented`, but the feature *was* implemented — it has been deliberately disabled for a published CVE. Operators debugging dashboards will search for an "implement" task and find none, masking the real reason. The same mismatch appears in the `pkg/expr/sql/db.go` stubs (`errors.New("not implemented")`). Align both sites on a single sentinel error and message that reflects the actual state so logs and user-visible errors are consistent and greppable.
```suggestion
// pkg/expr/sql/db.go
var ErrSQLExpressionsDisabled = errors.New("SQL expressions are disabled")

func (db *DB) TablesList(rawSQL string) ([]string, error) { return nil, ErrSQLExpressionsDisabled }
func (db *DB) RunCommands(commands []string) (string, error) { return "", ErrSQLExpressionsDisabled }
func (db *DB) QueryFramesInto(name string, query string, frames []*data.Frame, f *data.Frame) error {
	return ErrSQLExpressionsDisabled
}

// pkg/expr/reader.go — case QueryTypeSQL:
return eq, sql.ErrSQLExpressionsDisabled
```

:yellow_circle: [consistency] `TablesList` in pkg/expr/sql/parser.go still runs string manipulation and builds a query that can never execute in pkg/expr/sql/parser.go:23 (confidence: 70)
After this PR, `TablesList` calls `NewInMemoryDB().RunCommands(...)`, which unconditionally returns `errors.New("not implemented")`. The surrounding body still performs `strings.Replace(rawSQL, "'", "''", -1)` and `fmt.Sprintf("SELECT json_serialize_sql('%s')", rawSQL)` before discovering the stub always errors. The wasted work is harmless in the happy path, but (a) the single-quote doubling looks like an SQL-escape defense that now has no backend to defend against, inviting cargo-cult copies into future code, and (b) any future reintroduction of a real DB would inherit this half-measure escape rather than a proper parameterized API. Short-circuit the function to return `sql.ErrSQLExpressionsDisabled` immediately, or delete the body entirely — it is unreachable behavior masquerading as an implementation.
```suggestion
func TablesList(rawSQL string) ([]string, error) {
	// Intentionally disabled as part of CVE-2024-9264 mitigation.
	return nil, ErrSQLExpressionsDisabled
}
```

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: security-critical path (expression query router + SQL command execution), 7 files, 4 code files + 3 module manifests | Sensitive Paths: pkg/expr/sql/, security-release backport
AI-Authored Likelihood: LOW
