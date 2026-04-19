## Summary
7 files changed, 47 lines added, 34 lines deleted. 6 findings (1 critical, 2 improvements, 3 nitpicks).
Security kill-switch for CVE-2024-9264 (SQL-expressions RCE/LFI). Functionally correct, but the feature-flag helper is written as obfuscated dead code that a future contributor could naively "fix" and reintroduce the vulnerability.

## Critical

:red_circle: [correctness] Dead-code feature-flag that always returns `false` in `pkg/expr/reader.go`:193 (confidence: 95)
`enableSqlExpressions` computes `enabled := !h.features.IsEnabledGlobally(featuremgmt.FlagSqlExpressions)`, then returns `false` on both branches of the `if`. The local variable is dead, both branches are identical, and the negation (`!`) makes the intent look inverted. For a kill-switch guarding a disclosed RCE/LFI (CVE-2024-9264), this is exactly the kind of code a later maintainer will "simplify" by deleting the guard or flipping a sign — accidentally re-enabling the vulnerable path. State the invariant explicitly so a future reader cannot misread it.
```suggestion
// enableSqlExpressions is intentionally hard-disabled as the kill switch for
// CVE-2024-9264 (SQL Expressions RCE/LFI). Do NOT re-enable via feature flag
// until the underlying DuckDB integration has been replaced and re-reviewed.
// See https://grafana.com/blog/2024/10/17/grafana-security-release-critical-severity-fix-for-cve-2024-9264/
func enableSqlExpressions(_ *ExpressionQueryReader) bool {
	return false
}
```
[References: CVE-2024-9264]

## Improvements

:yellow_circle: [security] SQL-escaping anti-pattern preserved in unreachable stub path in `pkg/expr/sql/parser.go`:22 (confidence: 80)
`TablesList` still does `rawSQL = strings.Replace(rawSQL, "'", "''", -1)` and then string-concatenates `rawSQL` into `SELECT json_serialize_sql('%s')`. Currently unreachable because `duckDB.RunCommands` is a stub that errors immediately, but this is precisely the escaping pattern that contributed to the CVE being fixed. Leaving it here encodes a broken fix as the template for whoever re-implements the real DB later. Either delete `TablesList` along with the DuckDB removal, or replace the manual quote-doubling with a proper parameterized-query path — don't leave a hand-rolled escape as the seed for the replacement implementation.
```suggestion
// TablesList is currently unreachable because the in-memory DB is a stub
// (see db.go). When a real implementation replaces the stub, DO NOT revive
// this manual-escape pattern — use parameterized queries or an explicit
// SQL AST parser instead. The hand-rolled `'` -> `''` escape is the class
// of bug that caused CVE-2024-9264.
func TablesList(rawSQL string) ([]string, error) {
	return nil, errors.New("not implemented")
}
```

:yellow_circle: [consistency] Divergent disabled-error messages between reader and stub in `pkg/expr/reader.go`:129 vs `pkg/expr/sql/db.go`:12 (confidence: 85)
Three call sites gate access to SQL expressions and each returns a different error string: `reader.go` returns `"sqlExpressions is not implemented"`, and the stub in `db.go` returns `"not implemented"` for every method. Callers that try to differentiate "feature disabled" from "transient DB error" will key on the string; logs and metrics will fracture across three labels for one kill switch. Centralize one sentinel error (`expr.ErrSQLExpressionsDisabled = errors.New("sql expressions are disabled (CVE-2024-9264)")`) and have every stub method and the reader gate return it.

## Nitpicks

:white_circle: [correctness] Verify `sql` package import is still present in `pkg/expr/sql_command.go` (confidence: 70)
The diff shows `"github.com/scottlepp/go-duck/duck"` removed and a new `sql.NewInMemoryDB()` call introduced, but the accompanying `"github.com/grafana/grafana/pkg/expr/sql"` import is not visible in the shown context. If the `sql` import is only picked up transitively and not declared in the file's import block, the build would fail — the PR landing on `main` strongly suggests it compiles, but confirming the import is explicit in the file (not relying on an aliased re-export) would close the loop.

:white_circle: [maintainability] Stub methods discard their parameters in `pkg/expr/sql/db.go`:12-22
`TablesList`, `RunCommands`, and `QueryFramesInto` never use their arguments. Rename to `_` to signal intent and silence lint warnings: `func (db *DB) TablesList(_ string) ([]string, error)`, etc. Minor, but keeps `staticcheck` / `unparam` quiet and makes the stub-ness obvious at the signature.

:white_circle: [historical-context] `go.work.sum` re-adds `h1:` lines for removed deps
`github.com/apache/thrift v0.20.0`, `github.com/klauspost/asmfmt`, `github.com/JohnCGriffin/overflow`, `github.com/minio/asm2plan9s`, and `github.com/minio/c2goasm` move from `go.sum` into `go.work.sum`. This is the expected pattern when a direct dep is removed but the workspace still references it transitively — no action needed, but worth noting in case a cleanup pass later wonders why `go.work.sum` grew while `go.mod` shrank.

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: narrow — isolates to `pkg/expr/sql/*` plus kill switch in reader; entry points: `ReadQuery`, `SQLCommand.Execute`, `TablesList` | Sensitive Paths: security patch for a disclosed CVE touching an SQL execution surface
AI-Authored Likelihood: LOW — hand-written minimal patch with a known backport story and manual QA confirmation on an ephemeral instance

---

**Recommendation: approve with follow-up** — the patch achieves its stated goal (hard-disable the vulnerable SQL-expressions path). The highest-value change is rewriting `enableSqlExpressions` to be unambiguous about its role as a CVE kill switch so a future refactor does not silently re-open the vulnerability.

## Review Metadata
- Review mode: single-pass direct analysis (budget-constrained; full swarm skipped on a 298-line diff)
- Agents dispatched: none (orchestrator acted as reviewer)
- Completed agents: n/a
- Failed agents: none
- PR: grafana/grafana#94942 (ServerSideExpressions: Disable SQL Expressions to prevent RCE and LFI vulnerability)
- Base: `main` ← Head: `sj/disable-sql-expressions`
- Review duration: ~174s
