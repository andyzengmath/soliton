## Summary
4 files changed, 48 lines added, 15 lines deleted. 2 findings (0 critical, 2 improvements, 0 nitpicks).
Refactor moves template-variable interpolation to the start of `runSplitQuery` / `runShardSplitQuery` and switches from the array-form `interpolateVariablesInQueries` to per-query `applyTemplateVariables`. Behavior is mostly preserved, but the filter-then-interpolate ordering and a multiplied call count introduce two latent concerns worth verifying before merge.

## Improvements
:yellow_circle: [correctness] Filter order swap can let empty-expr queries through after interpolation in `public/app/plugins/datasource/loki/shardQuerySplitting.ts:46` (confidence: 85)
The previous code interpolated first and then ran `.filter((query) => query.expr)`, so any target whose `expr` resolved to an empty string after variable substitution was dropped before reaching `splitQueriesByStreamShard`. The new code filters on the *pre-interpolation* expression and only then maps through `applyTemplateVariables`. If a query has a non-empty literal expr like `'$VAR'` and `$VAR` resolves to `""` (e.g., an unset/empty template variable, or an ad-hoc filter that nullifies the selector), the query passes the filter and an empty expression is forwarded into the shard-splitting pipeline. Suggest moving the `.filter((query) => query.expr)` call so it runs after interpolation, mirroring the prior semantics:
```suggestion
  const queries = request.targets
    .filter((query) => !query.hide)
    .map((query) => datasource.applyTemplateVariables(query, request.scopedVars, request.filters))
    .filter((query) => query.expr);
```
The same ordering concern applies to the equivalent block in `querySplitting.ts:296-299`; previously there was no interpolation step so the order didn't matter, but with the new map() call empty-after-interpolation queries are now possible there too.

:yellow_circle: [correctness] `applyTemplateVariables` call count jumps from 1 to 5 for a single-target request — possible duplicated interpolation in `public/app/plugins/datasource/loki/shardQuerySplitting.test.ts:112` (confidence: 72)
The updated assertion `expect(datasource.applyTemplateVariables).toHaveBeenCalledTimes(5)` (was `interpolateVariablesInQueries ... toHaveBeenCalledTimes(1)`) for the same one-target request implies that downstream shard-splitting code is invoking `applyTemplateVariables` again on queries that have already been interpolated by `runShardSplitQuery`. Re-interpolating an already-interpolated expression is usually a no-op, but it can mis-fire when the resolved string contains literal `$` sequences that happen to match a variable name (e.g., regex patterns, log content, or values from ad-hoc filters). It is also wasted work per shard. Please confirm whether the interior call sites still need to interpolate after this PR, and if not, drop them so each query is interpolated exactly once. If they must remain, consider gating with a "already-interpolated" marker on the query so the second pass is skipped.
```suggestion
      // Verify intent: is the count of 5 expected because each shard re-interpolates,
      // or is one of the inner callers now redundant after moving interpolation up?
      expect(datasource.applyTemplateVariables).toHaveBeenCalledTimes(5);
```

## Risk Metadata
Risk Score: 28/100 (LOW) | Blast Radius: 4 files, all under `public/app/plugins/datasource/loki/`, 2 production + 2 test | Sensitive Paths: none
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold)
