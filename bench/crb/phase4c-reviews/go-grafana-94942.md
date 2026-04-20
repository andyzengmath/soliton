## Summary
7 files changed, 47 lines added, 34 lines deleted. 4 findings (1 critical, 3 improvements).
CVE-2024-9264 remediation successfully closes the DuckDB code path, but the new `enableSqlExpressions` helper is tautological (always returns `false`) and silently defeats the retained `FlagSqlExpressions` feature flag; the security-critical gate ships with no tests.

## Critical

:red_circle: [correctness] `enableSqlExpressions` always returns `false` — `FlagSqlExpressions` is dead code in pkg/expr/reader.go:193 (confidence: 99)
Both branches of the new helper return `false`, so the feature-flag read is discarded and the gate in `ReadQuery` unconditionally rejects `QueryTypeSQL`:

```go
func enableSqlExpressions(h *ExpressionQueryReader) bool {
    enabled := !h.features.IsEnabledGlobally(featuremgmt.FlagSqlExpressions)
    if enabled {
        return false
    }
    return false
}
```

Three compounding defects:
1. `if enabled { return false }` is a dead branch — no code path ever returns `true`.
2. The variable `enabled` is bound to `!IsEnabledGlobally(...)` — an inverted-sense name. A maintainer "fixing" the dead branch to `return true` would re-enable the code path only when the flag is **off**, i.e. exactly when it should stay closed.
3. The PR description states the flag is retained "so future re-enable is possible" — that promise cannot be kept with this implementation.

The always-closed outcome matches the security intent, but the code is fragile: any well-intentioned cleanup touching this helper can reopen the CVE-2024-9264 attack surface with a one-line change, and the stub DB in `pkg/expr/sql/db.go` is then the only remaining barrier.

```suggestion
// SQL expressions are disabled following CVE-2024-9264 (RCE/LFI via DuckDB).
// FlagSqlExpressions is retained for config compatibility; re-enabling the
// code path requires a full security review of pkg/expr/sql/*.
// See https://grafana.com/blog/2024/10/17/grafana-security-release-critical-severity-fix-for-cve-2024-9264/
func enableSqlExpressions(h *ExpressionQueryReader) bool {
    return h.features.IsEnabledGlobally(featuremgmt.FlagSqlExpressions)
}
```
[References: https://grafana.com/blog/2024/10/17/grafana-security-release-critical-severity-fix-for-cve-2024-9264/]

## Improvements

:yellow_circle: [testing] Security gate in reader.go ships with zero tests in pkg/expr/reader.go:193 (confidence: 95)
The `enableSqlExpressions` tautology would have been caught by a single table-driven test asserting that `ReadQuery` with `QueryTypeSQL` returns an error for both `FlagSqlExpressions=true` and `FlagSqlExpressions=false`. No such test exists, and none were added in this PR. For a CVE remediation this is the minimum regression safety net: any future edit that flips one `return false` to `return true` should turn the build red.

```suggestion
func TestReadQuery_SQLAlwaysBlocked(t *testing.T) {
    for _, flagEnabled := range []bool{true, false} {
        features := featuremgmt.WithFeatures(featuremgmt.FlagSqlExpressions, flagEnabled)
        reader := NewExpressionQueryReader(features)
        raw := simplejson.NewFromAny(map[string]any{"type": "sql", "expression": "SELECT 1"})
        _, err := reader.ReadQuery(Query{}, raw)
        require.ErrorContains(t, err, "sqlExpressions is not implemented",
            "SQL must be blocked regardless of flag state (flag=%v)", flagEnabled)
    }
}
```

:yellow_circle: [testing] New stub DB has no tests — silent re-enable if any method starts returning nil in pkg/expr/sql/db.go:1 (confidence: 92)
`pkg/expr/sql/db.go` is the belt-and-suspenders defense behind `reader.go`'s gate. All three methods (`TablesList`, `RunCommands`, `QueryFramesInto`) must return a non-nil error to preserve CVE-2024-9264 protection. A regression test suite here is the canary that catches someone adding a real implementation that returns early without wiring the error path.

```suggestion
package sql_test

func TestStubDB_MethodsAlwaysError(t *testing.T) {
    db := sql.NewInMemoryDB()
    require.NotNil(t, db)

    _, err := db.TablesList("SELECT 1")
    require.Error(t, err)

    _, err = db.RunCommands([]string{"SELECT 1"})
    require.Error(t, err)

    err = db.QueryFramesInto("ref", "SELECT 1", nil, &data.Frame{})
    require.Error(t, err)
}
```

:yellow_circle: [cross-file-impact] Removal of `github.com/scottlepp/go-duck` from go.mod risks a broken build if any `_test.go` under pkg/expr/** still imports it in go.mod:145 (confidence: 80)
The diff removes `github.com/scottlepp/go-duck` from `go.mod` and rewires only two source files (`pkg/expr/sql/parser.go`, `pkg/expr/sql_command.go`). No test files appear in the changeset. If any sibling test file (e.g., `pkg/expr/sql_command_test.go`, `pkg/expr/sql/parser_test.go`) previously imported `github.com/scottlepp/go-duck/duck` to mock `duck.DB` or call `duck.NewInMemoryDB()`, the package will now fail to build, and `go test ./pkg/expr/...` will not even run the remaining tests. Confirm with `grep -r "go-duck\\|scottlepp" --include="*.go" pkg/` before merge.

```suggestion
# Before merging, run from repo root:
grep -rn "go-duck\|scottlepp" --include="*.go" pkg/ && echo "^^ must be cleaned up"
go build ./...
go test ./pkg/expr/...
```

## Risk Metadata
Risk Score: 61/100 (HIGH) | Blast Radius: ~8 internal importers of pkg/expr/reader.go + pkg/expr/sql_command.go (central expression dispatch) | Sensitive Paths: CVE-2024-9264 remediation touching the pkg/expr/sql/ execution path (implied sensitive despite no literal glob match)
AI-Authored Likelihood: LOW

(8 additional findings below confidence threshold 85: stale `duckDB` variable name in parser.go; residual `fmt.Sprintf` SQL-string-concatenation pattern latent behind the stub; unreachable `SQLCommand.Execute` still constructs a DB; pointer receivers on empty stub struct; camelCase/sentence-case mix in error message; missing user-facing documentation update; `errors.New` vs `fmt.Errorf` style drift; error message does not reference CVE-2024-9264 for operability)
