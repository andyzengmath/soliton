Warning: consistency timed out (1/2 specialty agents completed)

## Summary
4 files changed, 48 lines added, 15 lines deleted. 3 findings (0 critical, 2 improvements, 1 nitpick).
Query-splitting interpolation fix switches from `interpolateVariablesInQueries` to per-query `applyTemplateVariables` — correctly fixes `$__auto`/`$step` (issue #107530) but drops the `datasource.getRef()` stamp and inverts filter-vs-interpolation order in one file.

## Improvements

:yellow_circle: [correctness] Missing datasource ref assignment — applyTemplateVariables does not replicate interpolateVariablesInQueries behavior in public/app/plugins/datasource/loki/shardQuerySplitting.ts:46 (confidence: 78)
The PR replaces `datasource.interpolateVariablesInQueries(request.targets, request.scopedVars)` with a direct call to `datasource.applyTemplateVariables(query, ...)` per query. The `DataSourceWithBackend` default implementation of `interpolateVariablesInQueries` wraps each result with `{ ...applyTemplateVariables(q, scopedVars, filters), datasource: this.getRef() }`. The new code omits the `datasource: this.getRef()` stamp. If `splitQueriesByStreamShard` or the downstream `runQuery` path inspects `query.datasource` on individual targets for routing or proxying, queries will now arrive without that field and could be mis-routed or silently dropped. The same gap exists in the parallel change in `querySplitting.ts:293-302` (new interpolation site with no prior baseline) — both files share the same root cause and the same fix.
```suggestion
const queries = request.targets
  .filter((query) => !query.hide)
  .filter((query) => query.expr)
  .map((query) => ({
    ...datasource.applyTemplateVariables(query, request.scopedVars, request.filters),
    datasource: datasource.getRef(),
  }));
```
Alternative: keep calling `interpolateVariablesInQueries` but pass the previously-missing `request.filters` argument (which was the real cause of issue #107530) — that one-line fix preserves the ref-stamp contract.

:yellow_circle: [correctness] Filter-before-interpolation allows empty-expr queries to pass through when expr is itself a variable in public/app/plugins/datasource/loki/shardQuerySplitting.ts:119 (confidence: 72)
Old `shardQuerySplitting.ts` interpolated first, then filtered on `query.expr`. The new code filters on `query.expr` before interpolation. A query with `expr: '$MYVAR'` where `$MYVAR` resolves to an empty string passes the pre-interpolation truthy check (`'$MYVAR'` is non-empty), is interpolated to `expr: ''`, and is forwarded to `splitQueriesByStreamShard` with an empty expression. If downstream code does not guard against empty `expr`, this may produce malformed or zero-result Loki requests without surfacing an error.
```suggestion
const queries = request.targets
  .filter((query) => !query.hide)
  .map((query) => datasource.applyTemplateVariables(query, request.scopedVars, request.filters))
  .filter((query) => query.expr);
```

## Nitpicks

:white_circle: [style] Inconsistent filter-chain ordering between the two modified files in public/app/plugins/datasource/loki/querySplitting.ts:293 (confidence: 80)
After the PR, `querySplitting.ts` applies filters in `hide -> expr` order, while `shardQuerySplitting.ts` applies them in `expr -> hide` order. Functionally equivalent, but the asymmetry makes the two splitting paths harder to read as a pair. Standardize both files to the same filter order (aligning with the filter-after-interpolation fix above would also address nitpick #1).

## Risk Metadata
Risk Score: 9/100 (LOW) | Blast Radius: 0 (no external importers; Loki-plugin-internal modules) | Sensitive Paths: none hit
AI-Authored Likelihood: LOW

Factors:
- blast_radius: 0 — no importers found, internal Loki-plugin modules
- change_complexity: 15 — 48 added lines, ~7 control-flow, mostly reformatting + one `.map()` per production file
- sensitive_paths: 0 — none of the 4 changed files match sensitive patterns
- file_size_scope: 30 — 63 total lines changed across 4 files (50–200 range)
- ai_authored_signals: 10 — no AI co-author signatures; test blocks mirror each other but within normal human patterns
- test_coverage_gap: 0 — both production files have corresponding test changes

Recommendation: approve with suggested follow-ups. No critical issues. Both improvement findings relate to a contract difference between `applyTemplateVariables` (per-query) and `interpolateVariablesInQueries` (batch): missing `datasource.getRef()` stamp and inverted filter-vs-interpolation order. The one-line alternative fix (keep `interpolateVariablesInQueries`, add the missing `request.filters` third argument) would resolve issue #107530 without introducing either regression.

(0 additional findings below confidence threshold)
