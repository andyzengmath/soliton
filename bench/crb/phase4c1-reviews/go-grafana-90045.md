## Summary
5 files changed, 524 lines added, 131 lines deleted. 9 findings (4 critical, 5 improvements).
Mode 3 dual-writer introduces async legacy writes, but the goroutines inherit a request-scoped context that is cancelled on handler return, defeating the "safety net" guarantee and causing several metric-label bugs.

## Critical

:red_circle: [correctness] Async legacy writes are cancelled when the HTTP handler returns in pkg/apiserver/rest/dualwriter_mode3.go:113 (confidence: 95)
Each of the four async goroutines (`Create`, `Delete`, `Update`, `DeleteCollection`) derives its context from the request `ctx` via `context.WithTimeoutCause(ctx, time.Second*10, ...)`. The parent functions return immediately after launching the goroutine, which means the caller's request context (e.g., the kube-apiserver HTTP handler context) can be cancelled before the goroutine completes. Cancellation propagates to the derived context, so the Legacy store write is aborted mid-flight. This defeats the stated PR goal: "write to unified storage and then asynchronously, write to legacy, just as a safe measure." You need a detached context (e.g., `context.WithoutCancel(ctx)` if available, or a fresh `context.Background()` copying only request-scoped values like user/tenant/trace IDs).
```suggestion
	go func() {
		// Detach from the request context so the async legacy write is not
		// cancelled when the HTTP handler returns.
		bgCtx, cancel := context.WithTimeoutCause(context.WithoutCancel(ctx), time.Second*10, errors.New("legacy create timeout"))
		defer cancel()

		startLegacy := time.Now()
		_, errObjectSt := d.Legacy.Create(bgCtx, obj, createValidation, options)
		d.recordLegacyDuration(errObjectSt != nil, mode3Str, options.Kind, method, startLegacy)
	}()
```

:red_circle: [correctness] Storage failures recorded via `recordLegacyDuration`, mislabelling which backend failed in pkg/apiserver/rest/dualwriter_mode3.go:104 (confidence: 97)
In the `Create` error path (line 103-106), `d.Storage.Create` failed but the code records `d.recordLegacyDuration(true, mode3Str, options.Kind, method, startStorage)`. The same bug exists in `Update` at line 219: storage failed, yet the error is recorded on the legacy histogram. Dashboards/SLOs built on these metrics will blame Legacy for Unified Storage outages, masking real incidents and triggering false pages.
```suggestion
	created, err := d.Storage.Create(ctx, obj, createValidation, options)
	if err != nil {
		log.Error(err, "unable to create object in storage")
		d.recordStorageDuration(true, mode3Str, options.Kind, method, startStorage)
		return created, err
	}
	d.recordStorageDuration(false, mode3Str, options.Kind, method, startStorage)
```

:red_circle: [correctness] Unbounded metric cardinality — resource name used as `kind` label in pkg/apiserver/rest/dualwriter_mode3.go:182 (confidence: 95)
`d.recordStorageDuration(false, mode3Str, name, method, startStorage)` passes the resource `name` (user-controlled, per-object) as the `kind` label on the storage-duration histogram. Every unique playlist/dashboard name produces a new Prometheus timeseries — this is a classic cardinality-explosion footgun that can OOM the scraper or your Mimir backend. Every other call site uses `options.Kind`; this one is the outlier.
```suggestion
	d.recordStorageDuration(false, mode3Str, options.Kind, method, startStorage)
```

:red_circle: [correctness] DeleteCollection async goroutine records legacy latency on the storage histogram in pkg/apiserver/rest/dualwriter_mode3.go:275 (confidence: 96)
Inside the async legacy-delete-collection goroutine, the latency is recorded with `d.recordStorageDuration(err != nil, mode3Str, options.Kind, method, startLegacy)` instead of `d.recordLegacyDuration`. Combined with the other two metric-label bugs above, three out of four async paths have miscategorised metrics — no one will be able to trust this histogram.
```suggestion
		d.recordLegacyDuration(err != nil, mode3Str, options.Kind, method, startLegacy)
```

## Improvements

:yellow_circle: [correctness] `Delete` uses `d.Log` instead of the request-scoped `log` when attaching the logger to context in pkg/apiserver/rest/dualwriter_mode3.go:173 (confidence: 90)
`ctx = klog.NewContext(ctx, d.Log)` attaches the bare `d.Log` — but the line above built `log := d.Log.WithValues("name", name, "kind", options.Kind, "method", method)`. Downstream callers using `klog.FromContext(ctx)` will not see the name/kind/method fields. Every other method in this file uses `klog.NewContext(ctx, log)`; this is an inconsistency that will cause log-correlation gaps.
```suggestion
	ctx = klog.NewContext(ctx, log)
```

:yellow_circle: [correctness] `Update` now writes legacy without synchronising against the storage-updated object in pkg/apiserver/rest/dualwriter_mode3.go:229 (confidence: 80)
Previously, `Update` read the current object from Storage, called `objInfo.UpdatedObject(ctx, old)` once to compute the new object, then passed an `updateWrapper{updated: obj}` to Legacy so both stores wrote the same bytes. The new code drops that wrapper and passes the raw `objInfo` to Legacy inside the goroutine. `UpdatedObject` can have side effects (timestamp stamping, resource-version bumps, validation mutation) and is not generally idempotent — calling it twice against different "old" objects will produce divergent records in the two stores, which is the exact drift Mode 3 is meant to avoid. Consider capturing the storage-updated object and passing it to Legacy via `updateWrapper`, as the pre-change code did.
```suggestion
	go func() {
		bgCtx, cancel := context.WithTimeoutCause(context.WithoutCancel(ctx), time.Second*10, errors.New("legacy update timeout"))
		defer cancel()

		startLegacy := time.Now()
		_, _, errObjectSt := d.Legacy.Update(bgCtx, name, &updateWrapper{upstream: objInfo, updated: res}, createValidation, updateValidation, forceAllowCreate, options)
		d.recordLegacyDuration(errObjectSt != nil, mode3Str, options.Kind, method, startLegacy)
	}()
```

:yellow_circle: [testing] Async legacy writes are never awaited in Mode3 tests — race-prone and cannot verify correctness in pkg/apiserver/rest/dualwriter_mode3_test.go:380 (confidence: 88)
All four of `TestMode3_Create`, `TestMode3_Delete`, `TestMode3_Update`, and `TestMode3_DeleteCollection` configure `setupLegacyFn` expectations against the shared `mock.Mock`, but the production code invokes Legacy inside `go func(){}` and returns immediately. The tests assert and return before the goroutine runs, so: (a) `mock.Mock` is read from two goroutines concurrently — this is a data race that `-race` will flag; (b) the legacy expectations are never verified because `mock.AssertExpectations` is never called, and the goroutine may complete after `t.Run` tears down; (c) failure modes of the legacy write (the whole point of Mode 3) are never asserted. Add a sync point (e.g., a `chan struct{}` closed inside the goroutine, or expose a `WaitGroup` for tests) and call `mock.AssertExpectations(t)` after waiting.

:yellow_circle: [consistency] go.work.sum contains dependency bumps unrelated to Mode 3 in go.work.sum:402 (confidence: 85)
The added entries (grafana-azure-sdk-go/v2 v2.1.0, prometheus-alertmanager, otel/exporters/otlp/otlptrace v1.26.0, otel/sdk v1.26.0, otel/sdk/metric v0.39.0, otlp/proto v1.2.0, genproto rpc 20240513) have nothing to do with dual-writer Mode 3. They appear to have leaked in from a stale `go mod tidy` against another branch. These bytes will show up in `git blame` for the dual-writer feature forever; either drop them or split into a separate chore PR so the dualwriter history stays clean.

:yellow_circle: [cross-file-impact] Unqualified `p` in mode1 test relies on package-level declaration that is not introduced by this PR in pkg/apiserver/rest/dualwriter_mode1_test.go:138 (confidence: 70)
This PR deletes `p := prometheus.NewRegistry()` from `TestMode1_Get` but the very next line `NewDualWriter(Mode1, ls, us, p)` still references `p`. For the test to compile, a package-level `var p = prometheus.NewRegistry()` must already exist in the `rest` package's test files — confirm this is the case, otherwise the build breaks. (A quick `grep -n "^var p" pkg/apiserver/rest/*_test.go` will verify.)

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: kube-apiserver data path for all Mode 3 resources (playlists today; extensible to all Grafana resources) | Sensitive Paths: none
AI-Authored Likelihood: LOW
