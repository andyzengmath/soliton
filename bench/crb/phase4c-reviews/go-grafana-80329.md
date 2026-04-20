## Summary
3 files changed, 218 lines added, 49 lines deleted. 8 findings (2 critical, 6 improvements).
PR splits the annotation cleanup `DELETE ... WHERE id IN (SELECT ...)` into separate fetch-then-delete statements to avoid MySQL deadlocks. Refactor is sound in principle, but two debugging leftovers (a global 10m→1m ticker change and six `log.Error` calls on happy-path flow that dump IDs) must be reverted before merge.

## Critical
:red_circle: [correctness] Cleanup ticker lowered from 10m to 1m affecting every cleanup job (debug leftover) in pkg/services/cleanup/cleanup.go:77 (confidence: 98)
`time.NewTicker(time.Minute * 10)` was changed to `time.NewTicker(time.Minute * 1)`. This service drives *every* cleanup job registered with `CleanUpService` (annotations, dashboard versions, temp files, expired user invites, expired short URLs, login attempts, etc.), not just the annotation cleanup this PR targets. Shipping this change would increase cleanup-job wall-clock work by roughly 10×, which is directly counter to the PR's goal of reducing contention on MySQL. The PR description and title make no mention of a ticker change, strongly suggesting this is a local test-iteration leftover.
```suggestion
	ticker := time.NewTicker(time.Minute * 10)
```

:red_circle: [consistency] Six `r.log.Error` calls on happy-path flow log at Error severity and dump full ID lists in pkg/services/annotations/annotationsimpl/xorm_store.go:532 (confidence: 97)
`CleanAnnotations` / `CleanOrphanedAnnotationTags` contain six calls of the form `r.log.Error("Annotations to clean by time", "count", len(ids), "ids", ids, "cond", cond, "err", err)` on normal control flow (both before and after successful delete). These are clearly debugging instrumentation: (a) they log at `Error` level with no error present, which poisons error dashboards, SLO alerts, and Loki error-rate panels for any operator running this build; (b) they serialize the full `ids` slice, which is bounded by `AnnotationCleanupJobBatchSize` — the new test case exercises 32,767 IDs per batch, meaning each cleanup tick could emit dozens of KB of structured-log payload per line; (c) they duplicate the message (one "to clean" + one "cleaned") on every iteration of `untilDoneOrCancelled`, amplifying the volume. Remove all six, or downgrade to `log.Debug` without the `ids` field if any telemetry is desired.
```suggestion
		affected, err := untilDoneOrCancelled(ctx, func() (int64, error) {
			cond := fmt.Sprintf(`%s AND created < %v ORDER BY id DESC %s`, annotationType, cutoffDate, r.db.GetDialect().Limit(r.cfg.AnnotationCleanupJobBatchSize))
			ids, err := r.fetchIDs(ctx, "annotation", cond)
			if err != nil {
				return 0, err
			}
			return r.deleteByIDs(ctx, "annotation", ids)
		})
```

## Improvements
:yellow_circle: [correctness] Fetch-then-delete across two independent sessions breaks the batch-size contract in pkg/services/annotations/annotationsimpl/xorm_store.go:549 (confidence: 86)
The old implementation held fetch and delete inside a single `DELETE ... IN (SELECT ... LIMIT N)` statement. The new code runs `fetchIDs` and `deleteByIDs` in two separate `WithDbSession` calls (two transactions). Between the two, concurrent inserts that match the cutoff can land with IDs *outside* the fetched set — fine, that's the documented "may under-delete" trade-off. But concurrent deletes (e.g., user-initiated annotation delete from the UI) can vanish rows between fetch and delete; the resulting `RowsAffected()` then drops below batch size, which causes `untilDoneOrCancelled` to terminate early for the `MaxAge` path even though more deletable rows exist. On a busy instance this can leave aged annotations un-cleaned indefinitely. Consider: (1) terminating the loop only when `len(ids) == 0`, not when `affected == 0`; or (2) snapshotting expected count vs actual and continuing when the delta is explained by concurrency.
```suggestion
func untilDoneOrCancelled(ctx context.Context, batchWork func() (fetched int64, affected int64, err error)) (int64, error) {
	var totalAffected int64
	for {
		select {
		case <-ctx.Done():
			return totalAffected, ctx.Err()
		default:
			fetched, affected, err := batchWork()
			totalAffected += affected
			if err != nil {
				return totalAffected, err
			}
			if fetched == 0 {
				return totalAffected, nil
			}
		}
	}
}
```

:yellow_circle: [correctness] MySQL and Postgres parameter limits are not handled, only SQLite's in pkg/services/annotations/annotationsimpl/xorm_store.go:598 (confidence: 82)
`deleteByIDs` special-cases SQLite at 999 placeholders, falling back to inline integer concatenation. MySQL (`max_allowed_packet`, default ~4 MB → roughly 500k parameters) and Postgres (65,535 parameter hard limit) get no such branch. If an operator raises `AnnotationCleanupJobBatchSize` above 65,535 on Postgres (plausible for large installations chasing throughput), the prepared statement will fail with `too many parameters`. Either cap the batch size at ingest, apply the inline path for all backends above a shared threshold, or chunk the delete inside `deleteByIDs` into placeholder-safe sub-batches.
```suggestion
	const postgresParameterLimit = 65535
	const maxSafePlaceholders = 999 // SQLite's limit; safe for MySQL and Postgres too
	if len(ids) > maxSafePlaceholders {
		// Fall through to inline integer concatenation for all backends.
		values := strconv.FormatInt(ids[0], 10)
		for _, v := range ids[1:] {
			values += ", " + strconv.FormatInt(v, 10)
		}
		sql = fmt.Sprintf(`DELETE FROM %s WHERE id IN (%s)`, table, values)
	} else {
		placeholders := "?" + strings.Repeat(",?", len(ids)-1)
		sql = fmt.Sprintf(`DELETE FROM %s WHERE id IN (%s)`, table, placeholders)
		args = asAny(ids)
	}
```

:yellow_circle: [correctness] O(n²) string build when concatenating IDs for SQLite inline path in pkg/services/annotations/annotationsimpl/xorm_store.go:603 (confidence: 88)
```go
values := fmt.Sprint(ids[0])
for _, v := range ids[1:] {
    values = fmt.Sprintf("%s, %d", values, v)
}
```
Each iteration reallocates and re-copies `values`. For the new 32,767-ID test case this is ~537M bytes copied per batch — measurable CPU cost on a job that is supposed to be reducing DB pressure. Use `strings.Builder` with `strconv.AppendInt`, or `strings.Join` over a pre-converted slice.
```suggestion
		var b strings.Builder
		b.Grow(len(ids) * 8) // rough estimate; int64 decimal fits in ~19 digits max
		for i, v := range ids {
			if i > 0 {
				b.WriteString(", ")
			}
			b.WriteString(strconv.FormatInt(v, 10))
		}
		sql = fmt.Sprintf(`DELETE FROM %s WHERE id IN (%s)`, table, b.String())
```

:yellow_circle: [testing] Explicit `ID: int64(i + 1)` in test-fixture inserts conflicts with DB autoincrement across the table-driven run in pkg/services/annotations/annotationsimpl/cleanup_test.go:237 (confidence: 80)
`createTestAnnotations` is now called once per subtest, and each invocation sets `a.ID = int64(i + 1)`. The subtests share one `fakeSQL` instance; the `t.Cleanup` wiping `annotation` only runs *after* the subtest finishes, but cleanup is registered *inside* `t.Run` *after* `createTestAnnotations` — so the first subtest inserts IDs 1..N, the second subtest reuses IDs 1..N. On SQLite this is allowed; on MySQL/Postgres with autoincrement the explicit PK is ignored by the sequence advance, which means cross-backend parity (normally part of integration test matrices) breaks and you silently test different ID orderings per backend. Drop the explicit `ID` assignment and let the backend allocate, then use `a.ID` (populated by xorm after `InsertMulti`) when building `newAnnotationTags`. If explicit IDs are needed for deterministic ORDER BY id DESC behavior, allocate the tag entries *after* the insert completes so they reference the DB-assigned values.
```suggestion
	for i := 0; i < expectedCount; i++ {
		a := &annotations.Item{
			DashboardID: 1,
			OrgID:       1,
			UserID:      1,
```

:yellow_circle: [consistency] `fetchIDs` guard `if condition == ""` is unreachable dead code in pkg/services/annotations/annotationsimpl/xorm_store.go:583 (confidence: 84)
All three call sites build `cond` by `fmt.Sprintf` with a non-empty format string that includes `%s ORDER BY id DESC %s` or `NOT EXISTS (...)` — even if `annotationType` is empty, the formatted condition always contains literal text. The guard never fires. Either drop it, or promote it to a type-safe signature (e.g., accept a struct with mandatory `Where string` and optional `Limit string` fields) that makes the invariant a compile-time property.
```suggestion
func (r *xormRepositoryImpl) fetchIDs(ctx context.Context, table, condition string) ([]int64, error) {
	sql := fmt.Sprintf(`SELECT id FROM %s WHERE %s`, table, condition)
	ids := make([]int64, 0)
	err := r.db.WithDbSession(ctx, func(session *db.Session) error {
		return session.SQL(sql).Find(&ids)
	})
	return ids, err
}
```

:yellow_circle: [testing] New 32,767-row batch test only exercises SQLite path; MySQL dialect branch is untested in pkg/services/annotations/annotationsimpl/cleanup_test.go:97 (confidence: 78)
The new test case `"should not fail if batch size is larger than SQLITE_MAX_VARIABLE_NUMBER for SQLite >= 3.32.0"` validates the SQLite-specific inline-IDs branch in `deleteByIDs`. It does not exercise the prepared-statement (MySQL/Postgres) path at that scale, nor does it exercise the whole point of the PR — the concurrent-insert-under-cleanup scenario that caused the MySQL deadlocks per issue #64979. Consider adding: (a) a `db.InitTestDB(t, db.WithMySQL())` variant of the same batch-size case, and (b) a goroutine-driven concurrent-insert test that reproduces #64979 and now passes.
```suggestion
		{
			name:                          "should delete via prepared-statement path when batch size is under placeholder limit",
			createAnnotationsNum:          1500,
			createOldAnnotationsNum:       0,
			annotationCleanupJobBatchSize: 500,
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
Risk Score: 72/100 (HIGH) | Blast Radius: cleanup.go ticker change affects every registered cleanup job across the instance (dashboard versions, temp files, expired logins, short URLs, etc.), not only annotations | Sensitive Paths: none matched
AI-Authored Likelihood: LOW
