## Summary
4 files changed, 48 lines added, 15 lines deleted. 1 findings (1 critical, 0 improvements, 0 nitpicks).
4 files changed. 1 finding (1 critical). Possible double interpolation in shardQuerySplitting.ts:46 — applyTemplateVariables called upfront AND per-shard, risking duplicate label matchers in production.

## Critical
:red_circle: [correctness] Possible double interpolation — applyTemplateVariables called once upfront AND per-shard internally in public/app/plugins/datasource/loki/shardQuerySplitting.ts:46 (confidence: 85)
The test "Interpolates queries before running" asserts `datasource.applyTemplateVariables` is called 5 times for a single query with 5 shards. The new code calls `applyTemplateVariables` once per query via `.map()` in `runShardSplitQuery` before handing queries to `splitQueriesByStreamShard`. If `splitQueriesByStreamShard` also invokes `applyTemplateVariables` once per shard (which the 5-call assertion implies), then in production each query is interpolated once upfront AND once per shard group. For ad-hoc filters injected into the LogQL expression on the first pass, re-injecting them on a second pass could add duplicate label matchers. The test mock uses `.replace` which is a no-op after the first substitution, masking this problem in tests while it can manifest in production.
```suggestion
// Determine whether splitQueriesByStreamShard calls applyTemplateVariables internally.
// If it does, remove the .map() call from runShardSplitQuery and let interpolation
// happen inside splitQueriesByStreamShard. If interpolation is meant to happen once
// upfront, remove the applyTemplateVariables call from inside splitQueriesByStreamShard.
// The test expectation of 5 calls for 1 query is the key diagnostic — if upfront-only,
// it should be 1.
export function runShardSplitQuery(datasource: LokiDatasource, request: DataQueryRequest<LokiQuery>) {
  const queries = request.targets
    .filter((query) => !query.hide)
    .filter((query) => query.expr)
    .map((query) => datasource.applyTemplateVariables(query, request.scopedVars, request.filters));

  return splitQueriesByStreamShard(datasource, request, queries);
}
```

## Risk Metadata
Risk Score: 21/100 (LOW) | Blast Radius: ~4 internal importers of querySplitting/shardQuerySplitting in Loki datasource (estimated; full Grafana tree not in shim) | Sensitive Paths: none matched
AI-Authored Likelihood: LOW

(4 additional findings below confidence threshold)
