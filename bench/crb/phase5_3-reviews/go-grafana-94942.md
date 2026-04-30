## Summary
7 files changed, 47 lines added, 34 lines deleted. 3 findings (2 critical, 1 improvements, 0 nitpicks).
Tautological `enableSqlExpressions` always returns false — dead feature-flag code leaves a silent foot-gun next to the CVE-2024-9264 mitigation gate, and a possibly-missing import for `pkg/expr/sql` in `sql_command.go` may break the build.

## Critical
:red_circle: [correctness] `enableSqlExpressions` is tautological — always returns false regardless of the feature flag in pkg/expr/reader.go:193 (confidence: 99)
Four independent agents (correctness, hallucination, cross-file-impact, security) converged on the same root defect. The function computes `enabled := !h.features.IsEnabledGlobally(featuremgmt.FlagSqlExpressions)` — naming the *negation* of the flag value `enabled` — and then both branches `return false`, so the function never returns `true` under any input. The flag is read but never meaningfully observed. For the CVE-2024-9264 mitigation this is functionally correct today (SQL expressions remain disabled), but for the wrong reason: a future maintainer who sees "the flag check is broken" and "fixes" only the obvious missing return-true branch will silently re-enable the RCE/LFI path without realising the negation is also inverted. Two independent latent bugs must be corrected together for any future re-enable to work safely, and nothing in the code signals this trap. The caller pattern `if !enabled { return error }` matches PR intent only because both bugs cancel out.
```suggestion
// CVE-2024-9264: SQL expressions permanently disabled. Do not change without security review.
func enableSqlExpressions(_ *ExpressionQueryReader) bool {
	return false
}
```
[References: https://nvd.nist.gov/vuln/detail/CVE-2024-9264, https://owasp.org/Top10/A04_2021-Insecure_Design/]

:red_circle: [cross-file-impact] `sql.NewInMemoryDB()` call may be missing its import in package expr in pkg/expr/sql_command.go:96 (confidence: 88)
The diff removes the `github.com/scottlepp/go-duck/duck` import and introduces a `sql.NewInMemoryDB()` call qualified with the `sql.` package prefix, indicating this file lives in package `expr` (not package `sql`). The visible diff context does not show a corresponding `+ "github.com/grafana/grafana/pkg/expr/sql"` import addition. If the internal `pkg/expr/sql` package was not already imported by this file prior to the diff, this is a compile error. Note that `pkg/expr/sql/parser.go` is in package `sql` itself and calls `NewInMemoryDB()` unqualified — that call is unambiguous. The risk is scoped to `sql_command.go`.
```suggestion
import (
	"github.com/grafana/grafana/pkg/expr/sql"
)
```

## Improvements
:yellow_circle: [security] Latent SQL injection construction site retained in `TablesList` despite the feature being disabled in pkg/expr/sql/parser.go:23 (confidence: 92)
`TablesList` still constructs a SQL string via `strings.Replace(rawSQL, "'", "''", -1)` followed by `fmt.Sprintf("SELECT json_serialize_sql('%s')", rawSQL)`. Today the string never reaches an engine because `RunCommands` is a stub that always errors, and the `reader.go` gate prevents SQL expressions from being invoked at all. However, the unsafe concatenation site persists in the codebase. Naive single-quote doubling is not a safe parameterization substitute — it does not handle backslash sequences, comment markers, dollar-quoting, or multi-byte boundary edge cases. If any future change wires `RunCommands` to a real engine without auditing this function, the injection sink re-activates silently. Either delete the SQL construction entirely or replace the body with the stub error.
```suggestion
func TablesList(rawSQL string) ([]string, error) {
	return nil, errors.New("sqlExpressions is not implemented")
}
```
[References: https://owasp.org/Top10/A03_2021-Injection/, https://cwe.mitre.org/data/definitions/89.html]

## Risk Metadata
Risk Score: 35/100 (MEDIUM) | Blast Radius: pkg/expr core query-routing pipeline; go.mod removes scottlepp/go-duck and 7 transitive deps | Sensitive Paths: none matched
AI-Authored Likelihood: LOW

(5 additional findings below confidence threshold)
