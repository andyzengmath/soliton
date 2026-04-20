## Summary
3 files changed, 199 lines added, 62 lines deleted. 4 findings (2 critical, 2 improvements, 0 nitpicks).
Core MySQL-deadlock fix (split subquery into SELECT + DELETE, wrapped in `untilDoneOrCancelled`) is sound, but two apparent debug artifacts leaked into the PR — cleanup ticker reduced from 10 min to 1 min, and six `r.log.Error` calls used for routine progress messages — both should be reverted or downgraded before merge.

## Critical

:red_circle: [correctness] Cleanup ticker interval changed from 10 minutes to 1 minute — likely debug artifact in pkg/services/cleanup/cleanup.go:77 (confidence: 95)
The cleanup service ticker was changed from `time.Minute * 10` to `time.Minute * 1`. This change is not mentioned anywhere in the PR description, which is entirely about splitting annotation cleanup queries to avoid MySQL deadlocks. A 10x increase in cleanup job frequency has significant production impact: the annotation cleanup queries (now SELECT + DELETE batches) will run every minute instead of every 10 minutes, multiplying DB load and contention proportionally. No test asserts the new interval and no commit message or review comment justifies it, strongly suggesting this was a debugging artifact left in accidentally while reproducing the deadlock locally. If the change is actually intentional it needs an explicit justification in the PR description and ideally a separate PR so reviewers can evaluate it on its own merits.
```suggestion
	ticker := time.NewTicker(time.Minute * 10)
```

:red_circle: [correctness] r.log.Error() used for routine diagnostic output — will generate false error signals in production in pkg/services/annotations/annotationsimpl/xorm_store.go:533 (confidence: 92)
Six `r.log.Error(...)` calls were added in `CleanAnnotations` (MaxAge and MaxCount branches) and `CleanOrphanedAnnotationTags` for routine progress logging: "Annotations to clean by time", "cleaned annotations by time", "Annotations to clean by count", "cleaned annotations by count", "Tags to clean", "cleaned tags". None of these represent error conditions — they log counts and IDs of records being processed on the happy path. In Grafana's logging infrastructure, ERROR-level messages are typically surfaced to alerting dashboards and on-call monitors; emitting ERROR logs on every successful cleanup batch will cause alert fatigue and make it impossible to distinguish real errors from routine operation. Combined with the ticker change above, this would produce six ERROR-level log entries per annotation type per minute. Each call also logs the full `ids` slice (up to `BatchSize` int64 values, 32767 in the new test case), which will bloat log pipelines. These are almost certainly leftover debug traces from local repro and should be `r.log.Debug` (or removed entirely; `count` + `affected` is enough if kept).
```suggestion
			r.log.Debug("Annotations to clean by time", "count", len(ids), "cond", cond)
			x, y := r.deleteByIDs(ctx, "annotation", ids)
			r.log.Debug("cleaned annotations by time", "count", len(ids), "affected", x, "err", y)
```

## Improvements

:yellow_circle: [correctness] O(N^2) string concatenation in deleteByIDs SQLite fallback in pkg/services/annotations/annotationsimpl/xorm_store.go:600 (confidence: 88)
The SQLite branch of `deleteByIDs` (taken when `AnnotationCleanupJobBatchSize > 999`) builds the `IN (...)` value list using repeated `fmt.Sprintf("%s, %d", values, v)` calls in a loop. Each iteration allocates a new string of growing length, making total allocation cost O(N^2) in the number of IDs. The new test exercises batchSize=32767 — at that size this loop performs ~32K allocations and copies on the order of hundreds of MB of cumulative string data per batch call, and the cleanup loop calls `deleteByIDs` repeatedly until done. The non-SQLite branch correctly uses `strings.Repeat` for a single allocation; the SQLite branch should use `strings.Builder` for the same O(N) cost.
```suggestion
		var sb strings.Builder
		sb.WriteString(strconv.FormatInt(ids[0], 10))
		for _, v := range ids[1:] {
			sb.WriteString(", ")
			sb.WriteString(strconv.FormatInt(v, 10))
		}
		sql = fmt.Sprintf(`DELETE FROM %s WHERE id IN (%s)`, table, sb.String())
```

:yellow_circle: [testing] SQLite parameter-limit test case does not assert the chunked-delete code path is actually taken in pkg/services/annotations/annotationsimpl/cleanup_test.go:97 (confidence: 85)
The new "should not fail if batch size is larger than SQLITE_MAX_VARIABLE_NUMBER" test creates 40003 annotations with `annotationCleanupJobBatchSize=32767` and asserts the end count, but does not verify that the deletion was actually routed through the inline-values SQLite branch of `deleteByIDs` (the whole point of the change). If a future refactor regresses that branch — e.g. the SQLite check is inverted or the threshold drifts — the test will still pass on databases whose driver supports large parameter counts while silently breaking on SQLite. There is also no coverage for the classic SQLite `SQLITE_MAX_VARIABLE_NUMBER=999` boundary (pre-3.32.0), which is the exact limit `sqliteParameterLimit = 999` in the new code guards against. Adding a test case at batchSize=999 with >999 annotations would pin down that boundary, and adding a session-level spy/mock (or at minimum a log assertion) would pin down the code path.
```suggestion
		{
			name:                          "should handle >999 annotations with SQLite classic variable limit",
			createAnnotationsNum:          1500,
			createOldAnnotationsNum:       0,
			annotationCleanupJobBatchSize: 999,
			cfg: &setting.Cfg{
				AlertingAnnotationCleanupSetting:   settingsFn(0, 1),
				DashboardAnnotationCleanupSettings: settingsFn(0, 1),
				APIAnnotationCleanupSettings:       settingsFn(0, 1),
			},
			alertAnnotationCount:     1,
			dashboardAnnotationCount: 1,
			APIAnnotationCount:       1,
			affectedAnnotations:      1497,
		},
```

## Risk Metadata
Risk Score: 25/100 (LOW) | Blast Radius: 2 estimated importers (internal service packages; wire/provider fan-out not verified in shim) | Sensitive Paths: none
AI-Authored Likelihood: MEDIUM

(3 additional findings below confidence threshold)
