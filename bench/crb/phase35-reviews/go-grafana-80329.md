## Summary
3 files changed, 199 lines added, 62 lines deleted. 6 findings (2 critical, 4 improvements).
Splits MySQL-deadlock-prone subquery DELETEs into SELECT-then-DELETE batches; fix is sound, but ships with a stray 10min→1min ticker change and six `log.Error` calls on success paths.

## Critical
:red_circle: [correctness] Cleanup ticker changed from 10m to 1m — unexplained 10x frequency bump in `pkg/services/cleanup/cleanup.go:77` (confidence: 95)
`cleanup.go` changes `time.NewTicker(time.Minute * 10)` to `time.NewTicker(time.Minute * 1)`. The PR description does not mention this; the stated feature is a MySQL deadlock fix in annotation cleanup queries. This single-line change increases the run frequency of every job returned by `srv.cleanUpJobs(...)` (annotations cleanup, plus tmp-file cleanup, and anything else attached to this service) by 10x in production. At minimum it increases DB load on large Grafana deployments; at worst it is a leftover debug diagnostic for reproducing the deadlock locally and should not ship. Revert to 10 minutes or justify the change explicitly in the PR body and release notes.
```suggestion
	ticker := time.NewTicker(time.Minute * 10)
```

:red_circle: [consistency] Six `r.log.Error(...)` calls log success-path diagnostics, in `pkg/services/annotations/annotationsimpl/xorm_store.go:538,544,560,566,577,583` (confidence: 95)
The new code emits diagnostic traces such as `r.log.Error("Annotations to clean by time", "count", len(ids), "ids", ids, "cond", cond, "err", err)` and `r.log.Error("cleaned annotations by time", "count", len(ids), "affected", x, "err", y)` on every batch iteration of the cleanup loop, regardless of whether `err`/`y` is nil. These are not errors — they are step traces — and logging them at Error severity will spam error dashboards, trip alerting rules, and drown real failures for every Grafana operator. Additionally, `"ids", ids` serializes the entire ID slice; with the new test exercising a batch size of 32,767, that is a ~200 KB+ log line per batch. Downgrade to `r.log.Debug(...)` (or remove) and drop the `ids` field (or truncate) before merging.
```suggestion
			ids, err := r.fetchIDs(ctx, "annotation", cond)
			if err != nil {
				return 0, err
			}
			r.log.Debug("annotations to clean by time", "count", len(ids))

			affected, err := r.deleteByIDs(ctx, "annotation", ids)
			if err != nil {
				r.log.Error("failed to delete annotations by time", "count", len(ids), "err", err)
			} else {
				r.log.Debug("cleaned annotations by time", "count", len(ids), "affected", affected)
			}
			return affected, err
```

## Improvements
:yellow_circle: [correctness] Context cancellation gap between `fetchIDs` and `deleteByIDs` within a batch, in `pkg/services/annotations/annotationsimpl/xorm_store.go:609` (confidence: 85)
`untilDoneOrCancelled` checks `ctx.Done()` only at the top of each iteration. Inside a single iteration the callback now runs `fetchIDs` (one session) and then `deleteByIDs` (a separate session) — a context cancelled between those two calls will still execute the DELETE for the full batch before returning. For a 32k-ID batch this is a long window. Either merge fetch+delete into a single session/transaction (preserving the deadlock-avoidance semantics, which only requires the subquery be split, not the transactions) or check `ctx.Err()` between the two operations and bail out early.
```suggestion
		affected, err := untilDoneOrCancelled(ctx, func() (int64, error) {
			cond := fmt.Sprintf(`%s AND created < %v ORDER BY id DESC %s`, annotationType, cutoffDate, r.db.GetDialect().Limit(r.cfg.AnnotationCleanupJobBatchSize))
			ids, err := r.fetchIDs(ctx, "annotation", cond)
			if err != nil {
				return 0, err
			}
			if err := ctx.Err(); err != nil {
				return 0, err
			}
			return r.deleteByIDs(ctx, "annotation", ids)
		})
```

:yellow_circle: [correctness] O(n²) string concatenation when building SQLite IN-list, in `pkg/services/annotations/annotationsimpl/xorm_store.go:647-650` (confidence: 80)
```go
values := fmt.Sprint(ids[0])
for _, v := range ids[1:] {
    values = fmt.Sprintf("%s, %d", values, v)
}
```
This rebuilds the full string on every iteration. With the new test setting `AnnotationCleanupJobBatchSize = 32767`, this is ~5.3×10⁸ byte copies per batch. Use `strings.Builder` or join a preallocated slice:
```suggestion
		parts := make([]string, len(ids))
		for i, v := range ids {
			parts[i] = strconv.FormatInt(v, 10)
		}
		sql = fmt.Sprintf(`DELETE FROM %s WHERE id IN (%s)`, table, strings.Join(parts, ","))
```

:yellow_circle: [testing] `TestIntegrationOldAnnotationsAreDeletedFirst` cleanup leaks `annotation_tag` rows across runs, in `pkg/services/annotations/annotationsimpl/cleanup_test.go:154-160` (confidence: 80)
The sibling test `TestIntegrationAnnotationCleanUp` was updated to delete both `annotation` and `annotation_tag` in its `t.Cleanup`, but `TestIntegrationOldAnnotationsAreDeletedFirst` still only deletes `annotation`. When this runs as an integration test against a shared MySQL/Postgres instance (which is the whole point of the `TestIntegration*` rename in this PR), leftover `annotation_tag` rows will persist and can collide with `createTestAnnotations`'s new explicit `ID: int64(i+1)` assignments on subsequent runs. Mirror the cleanup change here.
```suggestion
	t.Cleanup(func() {
		err := fakeSQL.WithDbSession(context.Background(), func(session *db.Session) error {
			_, deleteAnnotationErr := session.Exec("DELETE FROM annotation")
			_, deleteAnnotationTagErr := session.Exec("DELETE FROM annotation_tag")
			return errors.Join(deleteAnnotationErr, deleteAnnotationTagErr)
		})
		assert.NoError(t, err)
	})
```

:yellow_circle: [consistency] `fetchIDs` empty-condition guard is unreachable dead code, in `pkg/services/annotations/annotationsimpl/xorm_store.go:629-632` (confidence: 85)
All three call sites build `cond` via `fmt.Sprintf(...)` with a non-empty format, so `condition == ""` can never be hit. Either drop the branch or, if the intent is defence-in-depth against a future caller, document it — the current check is silent dead code that implies the function may be called without a condition, which it won't be.
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

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: all deployments — `cleanup.go` ticker affects every cleanup job, `xorm_store.go` affects annotation cleanup on all SQL backends (MySQL/Postgres/SQLite) | Sensitive Paths: none matched (no auth/payment/migration)
AI-Authored Likelihood: LOW
