## Summary
7 files changed, 47 lines added, 34 lines deleted. 2 findings (0 critical, 2 improvements, 0 nitpicks).
Defense-in-depth patch for CVE-2024-9264 that disables SQL Expressions: drops the `go-duck` DuckDB dependency, replaces the DB backend with a stub that returns `errors.New("not implemented")`, and adds an early-return gate in the query reader. The disable path itself is sound, but the gate helper contains dead-code logic that silently makes the feature flag inoperable and will confuse future maintainers who try to re-enable the feature.

## Improvements
:yellow_circle: [correctness] Dead-code logic in `enableSqlExpressions` silently ignores feature flag in pkg/expr/reader.go:194 (confidence: 95)
The helper computes `enabled := !h.features.IsEnabledGlobally(featuremgmt.FlagSqlExpressions)` but then returns `false` unconditionally from both branches of the `if` — the computed value is dead and the feature flag is never consulted. The function is effectively `func(...) bool { return false }`. This is fine for the immediate CVE mitigation (SQL expressions stay disabled even if an operator flips the flag), but it is a minefield for future maintainers: the shape of the code *looks* like a feature-flag gate, so a future change that "fixes the obvious bug" by returning `enabled` would silently re-enable the RCE/LFI-vulnerable code path the moment `FlagSqlExpressions` is turned on. Either collapse to `return false` with a comment documenting that this is an intentional kill-switch pending the full rewrite, or — if the intent is to honor the flag once the stub backend is safe — return `h.features.IsEnabledGlobally(featuremgmt.FlagSqlExpressions)` directly. The negation + double-`false` form is the worst of both worlds.
```suggestion
func enableSqlExpressions(h *ExpressionQueryReader) bool {
	// CVE-2024-9264: SQL Expressions are force-disabled regardless of the
	// feature flag until the in-memory DB backend lands. Do NOT simplify
	// this to honor the flag without also restoring a safe DB backend in
	// pkg/expr/sql/db.go (currently a not-implemented stub).
	_ = h
	return false
}
```

:yellow_circle: [consistency] Stale `TablesList` codepath in pkg/expr/sql still invokes the stub and will always error in pkg/expr/sql/parser.go:22 (confidence: 70)
`TablesList` now calls `NewInMemoryDB().RunCommands(...)`, and the new stub `RunCommands` always returns `errors.New("not implemented")`. The function therefore can never succeed — it only returns an error — yet it remains exported and is still reachable from any caller outside the `expr` package that imports `pkg/expr/sql`. Since the reader-level gate (`enableSqlExpressions`) is the only thing protecting callers inside `expr`, external importers of `pkg/expr/sql.TablesList` will silently regress to a guaranteed-error path with an opaque message (`"not implemented"`) rather than the more specific `"sqlExpressions is not implemented"` error the reader surfaces. Consider either (a) removing `TablesList` if no external caller remains, or (b) returning a typed sentinel error (e.g., `ErrSQLExpressionsDisabled`) from the stub so callers can distinguish "disabled" from "genuinely failed". Same applies to `QueryFramesInto` / `RunCommands` in `db.go`.
```suggestion
// In pkg/expr/sql/db.go:
var ErrSQLExpressionsDisabled = errors.New("sql expressions are disabled")

func (db *DB) TablesList(rawSQL string) ([]string, error) {
	return nil, ErrSQLExpressionsDisabled
}

func (db *DB) RunCommands(commands []string) (string, error) {
	return "", ErrSQLExpressionsDisabled
}

func (db *DB) QueryFramesInto(name string, query string, frames []*data.Frame, f *data.Frame) error {
	return ErrSQLExpressionsDisabled
}
```

## Risk Metadata
Risk Score: 42/100 (MEDIUM) | Blast Radius: medium — 4 Go source files + 3 dependency manifests, changes touch a security-sensitive feature (SQL Expressions, CVE-2024-9264); reader.go gate is the primary control-flow change | Sensitive Paths: none matched configured globs, but `pkg/expr/` is security-relevant by content (emergency CVE fix)
AI-Authored Likelihood: LOW — commit hygiene, dependency-manifest surgery, and surrounding comment style are consistent with human-authored emergency patch work; no characteristic LLM artifacts
