## Summary
5 files changed, 524 lines added, 131 lines deleted. 10 findings (6 critical, 4 improvements).
Mode 3 introduces an async legacy-write path with timeout contexts and instrumentation, but the goroutines inherit the request context (so they cancel as soon as the API response is sent), several call sites record the wrong duration metric on error, and one Delete path leaks the object name into a metric label — all of which silently undermine the dual-write safety net the PR is meant to provide.

## Critical

:red_circle: [correctness] Async legacy writes inherit the request context and will be cancelled when the API response returns in `pkg/apiserver/rest/dualwriter_mode3.go:113` (confidence: 96)
Every async branch (Create, Delete, Update, DeleteCollection) does `ctx, cancel := context.WithTimeoutCause(ctx, time.Second*10, ...)` where `ctx` is the inbound `context.Context` from the API handler. As soon as `DualWriterMode3.Create` (etc.) returns the unified-storage result, the kube-apiserver request handler completes and the parent `ctx` is cancelled, which cascades into the child timeout context. The goroutine then races against that cancellation: under any non-trivial legacy latency the legacy `Create`/`Update`/`Delete`/`DeleteCollection` call will be aborted with `context.Canceled` instead of running to completion, defeating the whole point of "write to legacy as a safe measure." Use `context.WithoutCancel(ctx)` (Go 1.21+) or build a fresh `context.Background()` and forward only the values you need (logger, user/auth metadata, request ID) before applying the 10s timeout.
```suggestion
	go func() {
		legacyCtx, cancel := context.WithTimeoutCause(context.WithoutCancel(ctx), time.Second*10, errors.New("legacy create timeout"))
		defer cancel()

		startLegacy := time.Now()
		_, errObjectSt := d.Legacy.Create(legacyCtx, obj, createValidation, options)
		d.recordLegacyDuration(errObjectSt != nil, mode3Str, options.Kind, method, startLegacy)
	}()
```

:red_circle: [correctness] `Create` records `recordLegacyDuration` when storage failed in `pkg/apiserver/rest/dualwriter_mode3.go:104` (confidence: 99)
On the storage-error branch the metric call is `d.recordLegacyDuration(true, mode3Str, options.Kind, method, startStorage)` — but the timing was started for the unified-storage call and `d.Legacy.Create` was never invoked. This pollutes legacy-store error/latency metrics with unified-storage failures and hides the real signal in storage-store metrics, which would mask a unified-storage outage during the dual-write rollout. Switch to `d.recordStorageDuration(true, ...)`.
```suggestion
	if err != nil {
		log.Error(err, "unable to create object in storage")
		d.recordStorageDuration(true, mode3Str, options.Kind, method, startStorage)
		return created, err
	}
```

:red_circle: [correctness] `Update` records `recordLegacyDuration` when storage failed in `pkg/apiserver/rest/dualwriter_mode3.go:219` (confidence: 99)
Same bug pattern as Create: when `d.Storage.Update` returns an error the code emits `recordLegacyDuration(true, ...)` even though the timer wraps the storage call and the legacy goroutine has not yet started. This is a metric-correctness regression that will produce false-positive alerts on the legacy backend during real unified-storage failures. Replace with `recordStorageDuration(true, ...)`.
```suggestion
	if err != nil {
		log.Error(err, "unable to update in storage")
		d.recordStorageDuration(true, mode3Str, options.Kind, method, startStorage)
		return res, async, err
	}
```

:red_circle: [correctness] `DeleteCollection` async goroutine records `recordStorageDuration` for the legacy call in `pkg/apiserver/rest/dualwriter_mode3.go:275` (confidence: 99)
After running `d.Legacy.DeleteCollection(...)` the goroutine calls `d.recordStorageDuration(err != nil, mode3Str, options.Kind, method, startLegacy)` — wrong dimension. Storage success/error counters get incremented once for the synchronous storage call (line 265) and a second time for the async legacy call, while legacy metrics are never recorded for delete-collection at all. Use `recordLegacyDuration`.
```suggestion
		_, err := d.Legacy.DeleteCollection(ctx, deleteValidation, options, listOptions)
		d.recordLegacyDuration(err != nil, mode3Str, options.Kind, method, startLegacy)
```

:red_circle: [correctness] `Delete` injects the bare base logger into the request context, dropping the per-call fields in `pkg/apiserver/rest/dualwriter_mode3.go:173` (confidence: 97)
```go
log := d.Log.WithValues("name", name, "kind", options.Kind, "method", method)
ctx = klog.NewContext(ctx, d.Log)   // <-- should be `log`
```
The locally-derived `log` (with `name`, `kind`, `method`) is used at the top-level `log.Error` calls but `klog.NewContext` is given `d.Log`, so anything downstream that does `klog.FromContext(ctx).Info(...)` (the storage and legacy implementations) loses every field added at this layer. Every other method in the file (`Create`, `Update`, `DeleteCollection`) correctly passes `log`; this is almost certainly a typo.
```suggestion
	log := d.Log.WithValues("name", name, "kind", options.Kind, "method", method)
	ctx = klog.NewContext(ctx, log)
```

:red_circle: [security] `Delete` storage-success path uses the object name as a metric label, exploding cardinality in `pkg/apiserver/rest/dualwriter_mode3.go:182` (confidence: 95)
```go
d.recordStorageDuration(false, mode3Str, name, method, startStorage)
```
The third positional argument across every other call site is `options.Kind` (a low-cardinality resource type like `"playlist"`), but here it is `name` — the per-object identifier. In a Prometheus registry this creates a new time series for every distinct object name ever deleted, which (a) silently breaks the dashboard/alerting that joins on `kind`, and (b) is a textbook way to OOM the metrics scraper / TSDB once Mode 3 is enabled in production. This is also the reason the metric is not symmetric with the error branch on the previous line. Replace with `options.Kind`.
```suggestion
	d.recordStorageDuration(false, mode3Str, options.Kind, method, startStorage)
```

## Improvements

:yellow_circle: [correctness] `Get` will panic if a caller passes a nil `*metav1.GetOptions` in `pkg/apiserver/rest/dualwriter_mode3.go:129` (confidence: 86)
The previous implementation forwarded `&metav1.GetOptions{}` regardless of caller input, so `options.Kind` was always safe. The new code dereferences `options` for both logging (`options.Kind`) and the downstream `d.Storage.Get(ctx, name, options)`. The kube generic registry usually populates `options`, but defensive callers (and unit tests like the ones added in this PR, which pass `&metav1.GetOptions{}` directly) will work — anything that calls through with `nil` will now nil-deref. A one-line guard (`if options == nil { options = &metav1.GetOptions{} }`) at the top of `Get` removes the foot-gun without changing behavior.
```suggestion
func (d *DualWriterMode3) Get(ctx context.Context, name string, options *metav1.GetOptions) (runtime.Object, error) {
	if options == nil {
		options = &metav1.GetOptions{}
	}
	var method = "get"
```

:yellow_circle: [testing] New `TestMode3_*` tests assert mock expectations on the legacy goroutine but never wait for it in `pkg/apiserver/rest/dualwriter_mode3_test.go:380` (confidence: 92)
`TestMode3_Create`, `TestMode3_Delete`, `TestMode3_Update`, and `TestMode3_DeleteCollection` configure `setupLegacyFn` mock expectations and then return as soon as `dw.Create`/`dw.Update`/etc. returns. Because Mode 3 now writes to legacy from a goroutine, the legacy mock call may execute before, during, or after `t.Run` finishes. Three concrete consequences: (1) `m.AssertExpectations` would be flaky if added; (2) the goroutine can call `t.Errorf` indirectly via mock after the test has completed, which Go's testing pkg flags as a panic in newer versions; (3) the bug in finding C1 above (cancelled context) is not exercised because the test never observes the legacy outcome. Add a synchronization point — for example, expose a `sync.WaitGroup` on the dual writer for tests, or have the goroutine signal a per-test channel — so each test can deterministically `wg.Wait()` before asserting.

:yellow_circle: [testing] Removing `p := prometheus.NewRegistry()` from `TestMode1_Get` makes tests share a package-level `p` and risks `prometheus.DuplicateRegistration` flakes in `pkg/apiserver/rest/dualwriter_mode1_test.go:138` (confidence: 84)
The diff deletes the local `p := prometheus.NewRegistry()` but the call site `NewDualWriter(Mode1, ls, us, p)` still references `p`, so the test now depends on a package-scoped `p` defined in another test file. Each `dualWriterMetrics` registers gauges/histograms with that registry; once a second test in the same package (e.g., the new `TestMode3_*` tests, which do call `NewDualWriter` with the same `p`) runs in the same process, you'll either get duplicate-registration panics or the metric counts from prior tests will leak in. Restore a fresh registry per test or move `p` into a `t.Helper()` constructor.

:yellow_circle: [correctness] Async legacy failures are logged-and-forgotten with no replay or backpressure in `pkg/apiserver/rest/dualwriter_mode3.go:113` (confidence: 81)
The PR description frames Mode 3 as "write to legacy asynchronously, just as a safe measure." With the current implementation a 10-second timeout, a transient legacy DB outage, or the C1 cancellation bug all silently drop the legacy write — the only signal is `recordLegacyDuration(err=true)` and a `klog.Error`. There is no retry, no DLQ, and no surface to detect ongoing divergence between unified storage and legacy. Before this is enabled in production, consider: (a) at minimum, an error counter alert wired to the dashboards used by Mode 1/2; (b) a periodic reconciliation job, or a per-resource divergence metric, so operators can detect and remediate drift. This is a design concern flagged for reviewer discussion rather than a code-line fix.

## Risk Metadata
Risk Score: 75/100 (HIGH) | Blast Radius: persistent storage / dual-writer; affects every resource opting into `Mode3` via `DualWriterDesiredModes` (currently the playlist integration test exercises this) | Sensitive Paths: `pkg/apiserver/rest/dualwriter_mode3.go` (data-persistence layer)
AI-Authored Likelihood: LOW
