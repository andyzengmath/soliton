## Summary
3 files changed, 199 lines added, 62 lines deleted. 5 findings (2 critical, 2 improvements, 1 nitpick).
The MySQL-deadlock workaround (two-step fetch-IDs + delete-by-IDs with bounded batches) is sound and correctly addresses the root cause described in issue #64979, but the PR also ships two unrelated regressions that look like debugging leftovers: the cleanup ticker interval is silently reduced 10x (10 min → 1 min) and every batch emits `log.Error` on the happy path while echoing the full ID slice. Both must be reverted/downgraded before merge.

## Critical

:red_circle: [correctness] Cleanup ticker interval reduced 10x — will hammer the database every minute in `pkg/services/cleanup/cleanup.go`:77 (confidence: 98)
The ticker was changed from `time.Minute * 10` to `time.Minute * 1`. `CleanUpService.Run` drives not just annotation cleanup but also tmp-file removal, login-attempt cleanup, expired-share cleanup and several other maintenance jobs on every tick. Running the entire maintenance loop 10× more frequently significantly increases DB write load in production. There is no config flag, comment, or PR-description mention of this change — it reads as an accidental debugging artifact that slipped in alongside the deadlock fix. The PR title and body describe only the query-splitting fix.
```suggestion
	ticker := time.NewTicker(time.Minute * 10)
```

:red_circle: [correctness] `log.Error` called on every successful batch and dumps the full ID slice — will spam production error logs and pager in `pkg/services/annotations/annotationsimpl/xorm_store.go`:239 (confidence: 94)
Six `r.log.Error(...)` calls are placed on the normal (non-error) execution path — one pair per cleanup type (by-time / by-count / orphaned-tags). For example:
```go
r.log.Error("Annotations to clean by time", "count", len(ids), "ids", ids, "cond", cond, "err", err)
```
At that point `err` is always `nil` (the early `return 0, err` above handles the failure case), so every healthy cleanup cycle will raise an ERROR-level log. The `"ids"` field serializes the full `[]int64` slice, which can be up to `AnnotationCleanupJobBatchSize` entries — the new test case exercises 32767. With a 1-minute (or even 10-minute) ticker, this produces multi-megabyte ERROR entries continuously, filling log storage, tripping alerting, and waking on-call engineers for non-errors. This is the same pattern repeated on lines 239, 243, 263, 266-267, 289, 292.
```suggestion
			ids, err := r.fetchIDs(ctx, "annotation", cond)
			if err != nil {
				return 0, err
			}
			r.log.Debug("annotations to clean by time", "count", len(ids), "cond", cond)

			x, y := r.deleteByIDs(ctx, "annotation", ids)
			if y != nil {
				r.log.Error("failed to clean annotations by time", "count", len(ids), "affected", x, "err", y)
			} else {
				r.log.Debug("cleaned annotations by time", "count", len(ids), "affected", x)
			}
			return x, y
```
Apply the same pattern to the by-count and orphaned-tags call sites.

## Improvements

:yellow_circle: [correctness] SQLite inline-ID branch guards on configured batch size instead of actual `len(ids)` in `pkg/services/annotations/annotationsimpl/xorm_store.go`:322 (confidence: 82)
The branch decision is:
```go
if r.db.GetDBType() == migrator.SQLite && r.cfg.AnnotationCleanupJobBatchSize > sqliteParameterLimit {
```
`fetchIDs` returns a slice whose length is bounded by the batch size (via `LIMIT`) but is frequently smaller — on the last partial batch, or when the cleanup condition matches only a handful of rows. Guarding on the configured batch size means:
1. With `AnnotationCleanupJobBatchSize = 1000`, the inline path is taken for *every* batch including ones that return 1–10 rows — harmless but semantically wrong (unnecessary inlining loses parameter benefits such as query-plan caching).
2. With `AnnotationCleanupJobBatchSize = 999`, the placeholder path is used, generating a DELETE with exactly 999 `?` markers. SQLite's historical default `SQLITE_MAX_VARIABLE_NUMBER` is 999 and the limit is inclusive on some builds (≤ 3.32.0), so this edge case can be rejected with "too many SQL variables" on the very configurations the branch is meant to protect.

The guard should key off the runtime count, not the configured cap.
```suggestion
	const sqliteParameterLimit = 999
	if r.db.GetDBType() == migrator.SQLite && len(ids) > sqliteParameterLimit {
		var b strings.Builder
		b.WriteString(fmt.Sprint(ids[0]))
		for _, v := range ids[1:] {
			fmt.Fprintf(&b, ", %d", v)
		}
		sql = fmt.Sprintf(`DELETE FROM %s WHERE id IN (%s)`, table, b.String())
	} else {
		placeholders := "?" + strings.Repeat(",?", len(ids)-1)
		sql = fmt.Sprintf(`DELETE FROM %s WHERE id IN (%s)`, table, placeholders)
		args = asAny(ids)
	}
```
The suggestion also replaces the O(n²) `fmt.Sprintf("%s, %d", values, v)` accumulator with a `strings.Builder`, which matters when `len(ids)` reaches 32767 on the test path.

:yellow_circle: [correctness] `t.Cleanup` is registered after the assertions it depends on in `pkg/services/annotations/annotationsimpl/cleanup_test.go`:127 (confidence: 75)
In the subtest body, `createTestAnnotations` + `assertAnnotationCount` + `assertAnnotationTagCount` run before `t.Cleanup` is registered. The subtests share `fakeSQL`, so if an early assertion fails (e.g. `assertAnnotationTagCount` mismatches after an `InsertMulti` regression) the rows from the failed subtest are never deleted — cleanup was never registered. The next subtest's `assertAnnotationCount` then sees stale rows and fails with a misleading count, masking the original root cause.
```suggestion
		t.Run(test.name, func(t *testing.T) {
			t.Cleanup(func() {
				err := fakeSQL.WithDbSession(context.Background(), func(session *db.Session) error {
					_, deleteAnnotationErr := session.Exec("DELETE FROM annotation")
					_, deleteAnnotationTagErr := session.Exec("DELETE FROM annotation_tag")
					return errors.Join(deleteAnnotationErr, deleteAnnotationTagErr)
				})
				assert.NoError(t, err)
			})
			createTestAnnotations(t, fakeSQL, test.createAnnotationsNum, test.createOldAnnotationsNum)
			assertAnnotationCount(t, fakeSQL, "", int64(test.createAnnotationsNum))
			assertAnnotationTagCount(t, fakeSQL, 2*int64(test.createAnnotationsNum))
```

## Nitpicks

:white_circle: [consistency] `asAny([]int64) []any` may duplicate an existing Grafana/Go 1.21+ utility in `pkg/services/annotations/annotationsimpl/xorm_store.go`:346 (confidence: 60)
A one-off `[]int64 → []any` converter is fine, but it's a common shape; worth a quick grep across `pkg/util` / `pkg/infra` for an existing helper before merging so we don't accrete yet another copy. If no helper exists, the function is fine as-is but consider making it generic (`func toAny[T any](vs []T) []any`) for reuse.

## Risk Metadata
Risk Score: 62/100 (MEDIUM) | Blast Radius: cleanup scheduler (all maintenance jobs) + annotation cleanup hot path | Sensitive Paths: none (no auth/secret/migration files touched)
AI-Authored Likelihood: LOW — diff style, incremental refactoring pattern, reviewer dialogue in PR history, and the explicit deadlock root-cause comment all read as human-authored.
