# PR Review: Query splitting — Interpolate queries at the start of the process

**PR #107534** — Fixes #107530
**Recommendation: request-changes**

## Summary
4 files changed, 48 lines added, 15 lines deleted. 7 findings (2 critical, 5 improvements, 0 nitpicks).
The refactor moves variable interpolation to the top of both splitting pipelines and adds `request.filters` to the interpolation call, but it passes raw `request.targets` references into `applyTemplateVariables` without cloning (mutation hazard) and doubles up with an existing per-shard interpolation inside `splitQueriesByStreamShard` (test assertion jumped from `toHaveBeenCalledTimes(1)` to `toHaveBeenCalledTimes(5)`).

## Critical

:red_circle: [correctness] applyTemplateVariables mutates original request.targets objects in place — double-interpolation risk on request re-use in `public/app/plugins/datasource/loki/querySplitting.ts:293` (confidence: 92)

Both sibling functions pass the original query object references from `request.targets` directly into `applyTemplateVariables`. The test mock (and the real `LokiDatasource` implementation it mirrors) mutates `query.expr` on the passed object and returns the same reference. Because `.filter()` does not clone objects, `request.targets[i]` is the same object that gets mutated. If the Grafana query runner reuses the same `DataQueryRequest` across retries, live-tail reconnections, or interval refreshes, the already-interpolated `expr` gets interpolated again on each invocation — `$__interval` becomes a literal duration string on the first call, and subsequent calls attempt to interpolate a string with no variable placeholders, silently returning a stale or incorrect expression. The previous `interpolateVariablesInQueries` approach returned new query objects, avoiding this hazard. The same defect exists verbatim in `shardQuerySplitting.ts:46-52`.

```suggestion
  const queries = request.targets
    .filter((query) => !query.hide)
    .filter((query) => query.expr)
    .map((query) =>
      datasource.applyTemplateVariables({ ...query }, request.scopedVars, request.filters)
    );
```

:red_circle: [correctness] applyTemplateVariables called once per shard instead of once per query — double-interpolation architecture in `public/app/plugins/datasource/loki/shardQuerySplitting.ts:46` (confidence: 87)

The test assertion change from `toHaveBeenCalledTimes(1)` to `toHaveBeenCalledTimes(5)` — where 5 matches the shard count from the mock `fetchLabelValues` return `['1', '10', '2', '20', '3']` — indicates that `splitQueriesByStreamShard` internally calls `applyTemplateVariables` again on each shard's sub-request, in addition to the new up-front call in `runShardSplitQuery`. This creates a double-interpolation architecture: interpolation runs once at the call site and again inside the splitter for every shard. Combined with the mutation hazard above, the first shard's call transforms `expr`, and subsequent shard calls re-interpolate an already-interpolated expression. The original design called interpolation exactly once (hence the old `toHaveBeenCalledTimes(1)`); the new design silently breaks that invariant. Audit `splitQueriesByStreamShard` and remove the per-shard internal interpolation call now that interpolation happens at the call site. Contract should be: interpolate once, then split.

## Improvements

:yellow_circle: [test-quality] Magic number 5 in toHaveBeenCalledTimes(5) is brittle and unexplained in `public/app/plugins/datasource/loki/shardQuerySplitting.test.ts:112` (confidence: 92)

The assertion `expect(datasource.applyTemplateVariables).toHaveBeenCalledTimes(5)` replaced `toHaveBeenCalledTimes(1)` with no comment explaining the count. The number comes from the shard count in the mock `fetchLabelValues` return value, meaning this assertion will silently break whenever the mock shard list changes length, and it conflates interpolation semantics with internal shard fan-out implementation details. The old count tested a meaningful contract (interpolation happens exactly once, up front); the new count tests an implementation accident.

```suggestion
      // applyTemplateVariables should be called once per query target, not once per shard
      const targetCount = request.targets.filter((q) => q.expr && !q.hide).length;
      expect(datasource.applyTemplateVariables).toHaveBeenCalledTimes(targetCount);
```

:yellow_circle: [test-quality] Mock for applyTemplateVariables mutates input in place, masking immutability bugs in `public/app/plugins/datasource/loki/shardQuerySplitting.test.ts:56` (confidence: 88)

The replacement mock does `query.expr = query.expr.replace(...); return query;` — it mutates the original query object. Because the mock is mutation-based, it actively hides the correctness defect described in the first critical finding: tests that should catch double-interpolation pass silently because the mutated query still looks correct on first inspection. The same pattern exists in both the `beforeEach` block and the `Sends the whole stream selector` test.

```suggestion
    datasource.applyTemplateVariables = jest.fn().mockImplementation((query: LokiQuery) => ({
      ...query,
      expr: query.expr.replace('$SELECTOR', '{a="b"}'),
    }));
```

:yellow_circle: [cross-file-impact] interpolateVariablesInQueries may be dead code and remaining callers are filter-blind in `public/app/plugins/datasource/loki/datasource.ts:1` (confidence: 85)

After this PR both `runShardSplitQuery` and `runSplitQuery` call `datasource.applyTemplateVariables` directly. If these were the only callers of `interpolateVariablesInQueries`, the method is now dead code on `LokiDatasource`. Additionally, `interpolateVariablesInQueries` historically wraps `applyTemplateVariables` without passing `filters`, so any remaining callers are filter-blind while the new split paths are filter-aware — producing behavioral divergence across query execution paths. Audit remaining callers: if none remain, remove the method; otherwise migrate them to pass `request.filters` for consistency (or document the intentional divergence).

:yellow_circle: [test-quality] New interpolation test uses a string replacer that does not reflect real variable substitution behavior in `public/app/plugins/datasource/loki/querySplitting.test.ts:78` (confidence: 85)

The `replace` mock chains two `.replace()` calls on a single string input. But `applyTemplateVariables` calls `replace` separately for each field of the query object (expr, step, etc.), so each call receives only one field value, not a combined string. The test works coincidentally because `$__auto` only appears in `expr` and `$step` only appears in `step` — a refactor that passes fields differently would not be caught. The same pattern is duplicated in `shardQuerySplitting.test.ts`.

```suggestion
    await expect(runSplitQuery(datasource, request)).toEmitValuesWith(() => {
      expect(jest.mocked(datasource.applyTemplateVariables)).toHaveBeenCalledWith(
        expect.objectContaining({ expr: 'count_over_time({a="b"}[$__auto])', step: '$step' }),
        request.scopedVars,
        request.filters
      );
      expect(jest.mocked(datasource.runQuery).mock.calls[0][0].targets[0].expr).toBe('count_over_time({a="b"}[5m])');
      expect(jest.mocked(datasource.runQuery).mock.calls[0][0].targets[0].step).toBe('5m');
    });
```

:yellow_circle: [consistency] Inconsistent filter ordering between sibling query-splitting functions in `public/app/plugins/datasource/loki/shardQuerySplitting.ts:48` (confidence: 85)

`querySplitting.ts` applies filters as `filter(!hide) → filter(expr) → map`. `shardQuerySplitting.ts` applies them as `filter(expr) → filter(!hide) → map`. Final semantics are equivalent but sibling functions performing the same conceptual operation (introduced in the same "interpolate at start of process" refactor) should maintain consistent ordering.

```suggestion
  const queries = request.targets
    .filter((query) => !query.hide)
    .filter((query) => query.expr)
    .map((query) => datasource.applyTemplateVariables(query, request.scopedVars, request.filters));
```

## Risk Metadata
Risk Score: 22/100 (LOW) | Blast Radius: moderate — 2 core Loki datasource modules with ~4-8 estimated importers | Sensitive Paths: none
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold)
