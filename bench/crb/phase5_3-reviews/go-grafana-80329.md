## Summary
3 files changed, 199 lines added, 62 lines deleted. 6 findings (2 critical, 4 improvements).
Splits annotation cleanup into separate SELECT + DELETE statements to avoid MySQL deadlocks, but ships with leftover debug artifacts (10x cleanup-ticker frequency increase and Error-level diagnostic logging) that must not merge.

## Critical

:red_circle: [correctness] Cleanup ticker changed from 10 minutes to 1 minute is unrelated debug code in pkg/services/cleanup/cleanup.go:77 (confidence: 95)
The change `time.NewTicker(time.Minute * 10)` → `time.NewTicker(time.Minute * 1)` is in `pkg/services/cleanup/cleanup.go`, the global `CleanUpService.Run` loop. It accelerates *every* cleanup job (tmp files, expired sessions, orphaned tags, etc.) by 10x, not just annotations, and is wholly unrelated to the deadlock fix described in the PR body. This is almost certainly a value used for local reproduction of the deadlock that wasn't reverted before opening the PR. Restore to 10 minutes before merge.
```suggestion
	ticker := time.NewTicker(time.Minute * 10)
```

:red_circle: [correctness] Diagnostic logs emitted at Error level in pkg/services/annotations/annotationsimpl/xorm_store.go:533 (confidence: 95)
Six new `r.log.Error(...)` calls in `CleanAnnotations` and `CleanOrphanedAnnotationTags` log routine progress ("Annotations to clean by time", "cleaned annotations by time", "Tags to clean", "cleaned tags", and the by-count variants) at Error severity, and three of them include the full slice of IDs being processed. With the new SQLite test exercising 32 767 IDs in a single batch, a single line can be ~250 KB. In production these will (a) trigger error-level alerts/paging on every cleanup run, and (b) dwarf surrounding log volume. These look like debug breadcrumbs added during deadlock investigation. Either delete them, or downgrade to `r.log.Debug` and drop the `"ids", ids` field from the message.
```suggestion
		// remove all six r.log.Error(...) calls in CleanAnnotations and CleanOrphanedAnnotationTags,
		// or change them to r.log.Debug and omit the raw IDs:
		//   r.log.Debug("Annotations to clean by time", "count", len(ids))
		//   r.log.Debug("cleaned annotations by time", "affected", x)
```

## Improvements

:yellow_circle: [correctness] O(n²) string concatenation when batch size exceeds SQLite parameter limit in pkg/services/annotations/annotationsimpl/xorm_store.go:611 (confidence: 90)
On the SQLite-large-batch branch of `deleteByIDs`, the IN-list is built with repeated `fmt.Sprintf("%s, %d", values, v)` over `ids[1:]`. This re-allocates and re-copies the entire accumulated string on every iteration — quadratic in `len(ids)`. The new test deliberately exercises this path with `annotationCleanupJobBatchSize: 32767`, which makes ~32k allocations totalling on the order of GB of intermediate string copies. Use `strings.Builder` (or `strings.Join` over a pre-formatted slice) so the cleanup run is O(n) instead of O(n²).
```suggestion
		var b strings.Builder
		fmt.Fprintf(&b, "%d", ids[0])
		for _, v := range ids[1:] {
			fmt.Fprintf(&b, ", %d", v)
		}
		sql = fmt.Sprintf(`DELETE FROM %s WHERE id IN (%s)`, table, b.String())
```

:yellow_circle: [correctness] Failure to insert annotation batches loses the error in pkg/services/annotations/annotationsimpl/cleanup_test.go:282 (confidence: 80)
Inside the `WithDbSession` callbacks for batched `InsertMulti` of annotations and annotation tags, the test calls `require.NoError(t, err)` from inside the session callback but then unconditionally `return nil`. `require.FailNow` exits the test goroutine via `runtime.Goexit`, which leaves the surrounding `WithDbSession` to commit its (now incomplete) transaction without seeing the failure. Returning `err` from the callback would let the session layer roll back. While `require.NoError` will still fail the test, the transactional behaviour is wrong and may surface later if test parallelism is added. Return `err` (or use `assert.NoError` and `return err`) to preserve session-level rollback semantics.
```suggestion
	err := store.WithDbSession(context.Background(), func(sess *db.Session) error {
		batchsize := 500
		for i := 0; i < len(newAnnotations); i += batchsize {
			if _, err := sess.InsertMulti(newAnnotations[i:min(i+batchsize, len(newAnnotations))]); err != nil {
				return err
			}
		}
		return nil
	})
	require.NoError(t, err)
```

:yellow_circle: [correctness] Tests share fakeSQL state but assign deterministic primary keys in pkg/services/annotations/annotationsimpl/cleanup_test.go:241 (confidence: 70)
`createTestAnnotations` now sets `a.ID = int64(i + 1)` explicitly, and the new sub-test loop in `TestIntegrationAnnotationCleanUp` shares one `fakeSQL` across all sub-tests. The per-subtest `t.Cleanup` deletes from `annotation` and `annotation_tag`, but `t.Cleanup` runs *after* the sub-test finishes, while the next sub-test's `createTestAnnotations` runs sequentially before the next cleanup — fine for `t.Run` (sub-tests are sequential by default). However, if anyone later adds `t.Parallel()` to a sub-test, the explicit ID re-use will surface as duplicate-PK insert failures that are confusing to debug. Either drop the explicit `ID` assignment and let the DB allocate, or scope the table reset to a `t.Setup`/pre-test step so the invariant is documented.
```suggestion
		a := &annotations.Item{
			DashboardID: 1,
			OrgID:       1,
			UserID:      1,
```

:yellow_circle: [correctness] fetchIDs builds SQL via fmt.Sprintf with caller-supplied table and condition strings in pkg/services/annotations/annotationsimpl/xorm_store.go:586 (confidence: 65)
`fetchIDs(ctx, table, condition)` and `deleteByIDs(ctx, table, ids)` both interpolate `table` directly into the SQL with `fmt.Sprintf`, and `fetchIDs` does the same with `condition`. All current callers in this file pass hard-coded table names ("annotation", "annotation_tag") and conditions composed from internal values (`cutoffDate` integer, dialect's `Limit`/`LimitOffset` strings, plus the constant `annotationType` predicate), so this is not exploitable today. But the helper signatures invite future callers to thread external strings in, and there is no allowlist on `table`. Either keep the helpers private to this file with a doc comment forbidding external input, or validate `table` against a small allowlist before formatting.
```suggestion
func (r *xormRepositoryImpl) fetchIDs(ctx context.Context, table, condition string) ([]int64, error) {
	switch table {
	case "annotation", "annotation_tag":
	default:
		return nil, fmt.Errorf("fetchIDs: table %q not allowlisted", table)
	}
```

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: cleanup loop runs for every Grafana instance; ticker change affects all cleanup jobs, not just annotations | Sensitive Paths: none matched
AI-Authored Likelihood: LOW
