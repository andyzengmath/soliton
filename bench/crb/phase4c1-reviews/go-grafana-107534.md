## Summary
4 files changed, 48 lines added, 15 lines deleted. 1 finding (0 critical, 1 improvement, 0 nitpicks).
The PR swaps batch `interpolateVariablesInQueries` for per-query `applyTemplateVariables(query, scopedVars, filters)` at the start of both `runSplitQuery` and `runShardSplitQuery`. The production change is small and consistent with the PR's stated intent, but the new interpolation test does not actually enforce the ordering invariant the PR title claims.

## Improvements

:yellow_circle: [testing] New interpolation test does not verify ordering invariant in public/app/plugins/datasource/loki/querySplitting.test.ts:75 (confidence: 85)
The test "Interpolates queries before execution" asserts that `runQuery` eventually receives an interpolated `expr` and `step`, but it does not prove that interpolation happens BEFORE splitting begins. If interpolation were shifted to run per-chunk after splitting, the test would still pass because the final `runQuery` arguments look identical. The stated PR goal is "interpolating queries as the initial step" — the test as written does not enforce that invariant, so a regression that moved interpolation back inside the splitting loop would go undetected.
```suggestion
test('Interpolates queries before splitting begins', async () => {
  const request = createRequest([{ expr: 'count_over_time({a="b"}[$__auto])', refId: 'A', step: '$step' }]);
  datasource = createLokiDatasource({
    replace: (input = '') => input.replace('$__auto', '5m').replace('$step', '5m'),
    getVariables: () => [],
  });
  const callOrder: string[] = [];
  jest.spyOn(datasource, 'applyTemplateVariables').mockImplementation((q) => {
    callOrder.push('interpolate');
    return { ...q, expr: q.expr.replace('$__auto', '5m'), step: '5m' };
  });
  jest.spyOn(datasource, 'runQuery').mockImplementation(() => {
    callOrder.push('runQuery');
    return of({ data: [] });
  });
  await expect(runSplitQuery(datasource, request)).toEmitValuesWith(() => {
    // interpolation must precede the first runQuery call
    expect(callOrder.indexOf('interpolate')).toBeLessThan(callOrder.indexOf('runQuery'));
    // and happen exactly once per refId, not per chunk
    expect(callOrder.filter((v) => v === 'interpolate')).toHaveLength(1);
  });
});
```

## Risk Metadata
Risk Score: 15/100 (LOW) | Blast Radius: internal leaf modules in Loki plugin (~3 importers) | Sensitive Paths: none
AI-Authored Likelihood: LOW

Advisory (below confidence threshold, not a formal finding):

- **Potential `$__auto` instability across shard iterations** (test-quality, confidence 80). The production code now calls `applyTemplateVariables` inside a `.map()` that is invoked once per shard group (the shard test's call-count assertion jumps from 1 to 5, matching the 5 mock shards). A range variable like `$__auto` that resolves from the current time window context can yield divergent step sizes per shard invocation. The existing mock always returns `'5m'` and cannot detect this class of bug. Worth a human audit of whether `splitQueriesByStreamShard` genuinely needs per-shard interpolation, or whether the intent was truly "interpolate once up front" — in the latter case the 5-call assertion is wrong and a once-per-refId assertion would surface the regression. If `splitQueriesByStreamShard` also interpolates internally, the new upfront `.map()` is redundant and causes double-interpolation.
- **Mock mutates input query object** (correctness/cross-file-impact/test-quality, merged confidence 78). The updated `applyTemplateVariables` mock mutates `query.expr` in place rather than returning a new object, which both diverges from the real implementation's immutable contract and could mask a production bug where `request.targets` is silently modified as a side effect of `runShardSplitQuery`. Suggest returning `{ ...query, expr: ... }`.
- **Filter-before-interpolate ordering change** (cross-file-impact, confidence 68). The old `shardQuerySplitting.ts` filtered `!hide` and `expr` AFTER interpolation; the new code filters BEFORE. A target whose `hide` field is template-controlled would be classified differently. Unlikely in practice for Loki but a latent semantic change.
- **Filter order inconsistency between sibling functions** (consistency, confidence 75). `runSplitQuery` filters `!hide` then `expr`; `runShardSplitQuery` filters `expr` then `!hide`. Each file preserves its own prior order, but the two near-identical functions should probably agree.

(9 additional findings below confidence threshold)
