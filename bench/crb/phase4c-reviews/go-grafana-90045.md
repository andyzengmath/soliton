## Summary
5 files changed, 524 lines added, 131 lines deleted. 7 findings (4 critical, 3 improvements).
Mode 3 makes Legacy an async fire-and-forget writer, but the goroutines use the cancelled request context, metric helpers are swapped on error paths, and an `updateWrapper` shim was removed — production mode 3 will silently fail to mirror to legacy and will emit mislabelled metrics.

## Critical

:red_circle: [correctness] Async legacy writes inherit the request context and will be cancelled immediately in `pkg/apiserver/rest/dualwriter_mode3.go`:40 (confidence: 92)
All four fire-and-forget goroutines (`Create`, `Update`, `Delete`, `DeleteCollection`) build their timeout context from the incoming request `ctx`: `ctx, cancel := context.WithTimeoutCause(ctx, time.Second*10, ...)`. Kubernetes apiserver (and `net/http` in general) cancels the request context as soon as the handler returns a response. Because mode 3 returns to the client immediately after the synchronous Storage call, the parent `ctx` is cancelled before `d.Legacy.Create/Update/Delete/DeleteCollection` actually runs. `Legacy.*` will almost always return `context canceled`, meaning mode 3's stated "write to legacy asynchronously, just as a safe measure" does not happen in practice. The `recordLegacyDuration(errObjectSt != nil, ...)` call will record a near-zero duration with `err=true` for every request, which is also how this would surface in monitoring.
```suggestion
go func() {
	bgCtx := context.WithoutCancel(ctx) // keep logger/trace values, drop cancellation
	bgCtx, cancel := context.WithTimeoutCause(bgCtx, 10*time.Second, errors.New("legacy create timeout"))
	defer cancel()
	defer func() {
		if r := recover(); r != nil {
			d.Log.Error(fmt.Errorf("panic: %v", r), "legacy create panicked")
		}
	}()

	startLegacy := time.Now()
	_, errObjectSt := d.Legacy.Create(bgCtx, obj, createValidation, options)
	d.recordLegacyDuration(errObjectSt != nil, mode3Str, options.Kind, method, startLegacy)
}()
```

:red_circle: [correctness] Prometheus label cardinality blow-up — per-object name passed as `kind` in `pkg/apiserver/rest/dualwriter_mode3.go`:92 (confidence: 95)
In `Delete`, the success path records `d.recordStorageDuration(false, mode3Str, name, method, startStorage)`. The third argument is the `kind` label, but `name` is the per-object name (e.g. a playlist UID). Every distinct object name creates a new Prometheus series, so this counter/histogram will grow unbounded, OOMing the metrics endpoint and/or the scraper. Every other call site in this file correctly passes `options.Kind`, so this looks like a copy-paste slip.
```suggestion
d.recordStorageDuration(false, mode3Str, options.Kind, method, startStorage)
```

:red_circle: [correctness] Metric helpers swapped on error paths (Storage latency recorded as Legacy and vice versa) in `pkg/apiserver/rest/dualwriter_mode3.go`:40,115,150 (confidence: 90)
Three locations pair the wrong helper with the wrong timer, making the `legacy_*` and `storage_*` duration metrics unreliable during the rollout:
- `Create` error path: `d.recordLegacyDuration(true, mode3Str, options.Kind, method, startStorage)` — `startStorage` is the Storage-side timer and this is the Storage error path.
- `Update` error path: `d.recordLegacyDuration(true, mode3Str, options.Kind, method, startStorage)` — same pattern.
- `DeleteCollection` async goroutine: `d.recordStorageDuration(err != nil, mode3Str, options.Kind, method, startLegacy)` — the goroutine is the Legacy call, timed by `startLegacy`, so it must be `recordLegacyDuration`.
All three should be flipped. Dashboards built on these metrics will attribute failures to the wrong backend, hiding Storage regressions behind "legacy" error spikes.
```suggestion
// Create error path
d.recordStorageDuration(true, mode3Str, options.Kind, method, startStorage)
// Update error path
d.recordStorageDuration(true, mode3Str, options.Kind, method, startStorage)
// DeleteCollection async goroutine
d.recordLegacyDuration(err != nil, mode3Str, options.Kind, method, startLegacy)
```

:red_circle: [correctness] `Update` now passes raw `objInfo` to `Legacy.Update`, re-invoking a stateful `UpdatedObjectInfo` in `pkg/apiserver/rest/dualwriter_mode3.go`:115 (confidence: 82)
The previous code deliberately called `objInfo.UpdatedObject(ctx, old)` once, stored the result in `obj`, and then passed `&updateWrapper{upstream: objInfo, updated: obj}` to `Legacy.Update` so the legacy store saw exactly the same post-transform object that was committed to unified storage. The new code drops that shim and hands the original `objInfo` to both backends, so `UpdatedObject` is called twice. Standard `rest.DefaultUpdatedObjectInfo` (and every implementation that applies patches or admission-mutating transforms) is not idempotent under a double-invocation — the second call sees a different `old` object (the fresh read from legacy storage) and may produce a different merged result, or fail validation. This silently desynchronises unified and legacy storage and was explicitly handled in the code the PR replaces.
```suggestion
go func() {
	bgCtx := context.WithoutCancel(ctx)
	bgCtx, cancel := context.WithTimeoutCause(bgCtx, 10*time.Second, errors.New("legacy update timeout"))
	defer cancel()

	startLegacy := time.Now()
	_, _, errObjectSt := d.Legacy.Update(bgCtx, name, &updateWrapper{
		upstream: objInfo,
		updated:  res,
	}, createValidation, updateValidation, forceAllowCreate, options)
	d.recordLegacyDuration(errObjectSt != nil, mode3Str, options.Kind, method, startLegacy)
}()
```

## Improvements

:yellow_circle: [correctness] `Delete` attaches logger fields to `log` but installs `d.Log` into the context in `pkg/apiserver/rest/dualwriter_mode3.go`:85 (confidence: 88)
```go
log := d.Log.WithValues("name", name, "kind", options.Kind, "method", method)
ctx = klog.NewContext(ctx, d.Log)   // <-- should be `log`
```
Downstream code that recovers the logger via `klog.FromContext(ctx)` will not see `name`/`kind`/`method`. Every sibling method (`Create`, `Get`, `List`, `Update`, `DeleteCollection`) uses `log` here. Likely a typo.
```suggestion
ctx = klog.NewContext(ctx, log)
```

:yellow_circle: [correctness] Fire-and-forget goroutines have no panic recovery in `pkg/apiserver/rest/dualwriter_mode3.go`:40,95,115,150 (confidence: 80)
If any legacy backend panics, the bare `go func() { ... }()` tears down the whole apiserver. In mode 3 the caller has already returned 2xx to the client, so the recovery is purely for process stability. Add `defer func(){ if r := recover(); r != nil { d.Log.Error(...) } }()` at the top of each goroutine, and preferably a dedicated counter so a buried legacy panic is observable.

:yellow_circle: [testing] Mode 3 tests never await the async legacy write — `setupLegacyFn` expectations may not run in `pkg/apiserver/rest/dualwriter_mode3_test.go`:20-80 (confidence: 78)
`TestMode3_Create` / `TestMode3_Update` register `m.On("Create"/"Update", ...)` on the legacy mock, but the legacy call happens in a detached goroutine that is (a) never signalled or awaited, and (b) given the Critical #1 bug, never runs successfully at all. The tests pass whether or not the async path fires, so they cannot detect the context-cancellation regression. Either synchronise (a channel in the mock, or `assert.Eventually`) or extract the "schedule legacy write" step into a seam that the test can drive deterministically. Also consider `mock.AssertExpectations(t)` at the end of each subtest to catch unexpected/missing calls.

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: core unified-storage dual-writer path (mode 3 rollout), Prometheus metrics pipeline | Sensitive Paths: none (no auth/secret/migration)
AI-Authored Likelihood: MEDIUM — repeated copy-paste slips (swapped metric helpers in 3 places, `d.Log`/`log` typo, `name` used as `kind` label) and removal of the `updateWrapper` shim without replacing its invariant are characteristic of mechanical-edit regressions.
