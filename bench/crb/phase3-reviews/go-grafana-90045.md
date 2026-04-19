## Summary
5 files changed, 524 lines added, 131 lines deleted. 11 findings (4 critical, 5 improvements, 2 nitpicks).
Mode 3 dual-writer makes legacy writes asynchronous, but async goroutines inherit the request-scoped ctx (they will be canceled when the handler returns), metric-recording helpers are mis-routed (Storage vs Legacy swapped, and one site labels metrics by object name — cardinality risk), and Update no longer forwards the storage-computed object to Legacy, which can diverge the two stores.

## Critical

:red_circle: [correctness] Async legacy writes use request-scoped context — canceled as soon as handler returns in `pkg/apiserver/rest/dualwriter_mode3.go`:43 (confidence: 95)
All four async `go func() { ... }()` blocks in `Create`, `Delete`, `Update`, and `DeleteCollection` capture the incoming request `ctx` and derive a 10 s timeout from it. That incoming `ctx` is the apiserver's request context — it is canceled the moment the handler returns the synchronous Storage result to the client. Because `context.WithTimeoutCause(ctx, …)` inherits cancellation from its parent, the derived ctx will be `Done` by the time the legacy call is dispatched in the common case, and `Legacy.Create/Update/Delete/DeleteCollection` will observe `context.Canceled` instead of running to completion. The stated goal of Mode 3 ("write to legacy asynchronously as a safe measure") is therefore silently not achieved in most production calls.
```suggestion
go func() {
    ctx := context.WithoutCancel(ctx)
    ctx, cancel := context.WithTimeoutCause(ctx, 10*time.Second, errors.New("legacy create timeout"))
    defer cancel()
    …
}()
```
[References: https://pkg.go.dev/context#WithoutCancel]

:red_circle: [correctness] `Update` forwards raw `objInfo` to Legacy instead of the storage-computed object in `pkg/apiserver/rest/dualwriter_mode3.go`:148 (confidence: 90)
The previous implementation wrapped `objInfo` with `updateWrapper{upstream: objInfo, updated: obj}` so that Legacy received the exact object that Storage just produced (after defaulting, admission, status sub-resource logic, etc.). The new async path does `d.Legacy.Update(ctx, name, objInfo, …)` with the *original* `objInfo`, which will re-run `UpdatedObject` against whatever Legacy reads as "old" — defaulting/admission can diverge, and any `ResourceVersion` / server-side-apply semantics applied by Storage are lost. Over time Storage and Legacy will drift, defeating Mode 3's "safe measure" guarantee.
```suggestion
go func() {
    ctx := context.WithoutCancel(ctx)
    ctx, cancel := context.WithTimeoutCause(ctx, 10*time.Second, errors.New("legacy update timeout"))
    defer cancel()

    startLegacy := time.Now()
    _, _, errObjectSt := d.Legacy.Update(ctx, name, &updateWrapper{
        upstream: objInfo,
        updated:  res,
    }, createValidation, updateValidation, forceAllowCreate, options)
    d.recordLegacyDuration(errObjectSt != nil, mode3Str, options.Kind, method, startLegacy)
}()
```

:red_circle: [correctness] Metric helpers are mis-routed: storage-error path records `recordLegacyDuration`; async legacy-DeleteCollection records `recordStorageDuration` in `pkg/apiserver/rest/dualwriter_mode3.go`:44, 154, 199 (confidence: 98)
Three metric sites are wired to the wrong helper:
1. `Create` storage-failure path calls `d.recordLegacyDuration(true, …)` even though Storage is what failed (line ~44).
2. `Update` storage-failure path calls `d.recordLegacyDuration(true, …)` for the same reason (line ~154).
3. `DeleteCollection` async goroutine calls `d.recordStorageDuration(err != nil, …)` to record a *Legacy* DeleteCollection duration (line ~199).

These silently corrupt the `dualwriter_*` Prometheus series: Storage failures show up as Legacy failures and vice versa, so on-call dashboards and alerts based on these metrics will misattribute outages between the two stores.
```suggestion
// Create / Update error path:
d.recordStorageDuration(true, mode3Str, options.Kind, method, startStorage)
// DeleteCollection async legacy path:
d.recordLegacyDuration(err != nil, mode3Str, options.Kind, method, startLegacy)
```

:red_circle: [correctness] `Delete` records the per-object `name` as the metric label instead of `options.Kind` — metric cardinality explosion in `pkg/apiserver/rest/dualwriter_mode3.go`:95 (confidence: 95)
```go
d.recordStorageDuration(false, mode3Str, name, method, startStorage)
```
Every other call site in this file passes `options.Kind`; this one passes the object name. If the helper uses that argument as a Prometheus label (consistent with its signature being shared across all methods), every unique object name becomes a unique label value — a classic high-cardinality landmine that can blow up the scrape target's memory and the Prometheus TSDB.
```suggestion
d.recordStorageDuration(false, mode3Str, options.Kind, method, startStorage)
```

## Improvements

:yellow_circle: [correctness] `Delete` attaches the bare logger (not `log` with per-request fields) to ctx in `pkg/apiserver/rest/dualwriter_mode3.go`:85 (confidence: 92)
```go
log := d.Log.WithValues("name", name, "kind", options.Kind, "method", method)
ctx = klog.NewContext(ctx, d.Log)   // should be `log`, not `d.Log`
```
All peers in this file (`Create`, `Update`, `List`, `DeleteCollection`) correctly propagate the enriched `log`; `Delete` downstream callers receive `d.Log` and lose the `name`/`kind`/`method` fields, making delete logs noticeably harder to correlate.
```suggestion
ctx = klog.NewContext(ctx, log)
```

:yellow_circle: [correctness] No `recover()` in the four async legacy goroutines — a panic in `Legacy.*` crashes the apiserver in `pkg/apiserver/rest/dualwriter_mode3.go`:43,96,148,198 (confidence: 80)
Mode 3's whole premise is that the legacy write is best-effort and must not affect the main request. Right now an unrecovered panic from a Legacy driver (nil deref, closed pool, …) will terminate the whole apiserver process, because Go's runtime does not associate the panic with the originating handler. Wrap each goroutine body in `defer func() { if r := recover(); r != nil { log.Error(…) } }()`.

:yellow_circle: [testing] Tests don't wait for, or assert, the async legacy write — the headline behavior of Mode 3 is uncovered in `pkg/apiserver/rest/dualwriter_mode3_test.go`:45,266 (confidence: 90)
`TestMode3_Create` and `TestMode3_Update` configure `setupLegacyFn`, but the goroutine that would call `Legacy.Create/Update` may not have run — or may even still be running — when the test returns. Two concrete problems:
1. Coverage gap: nothing asserts that Legacy was actually called, so regressions that silently drop the async path (e.g. the ctx-cancellation bug above) would pass these tests.
2. Race: `mock.Mock.On` / internal call tracking is not concurrency-safe against assertions run on the main goroutine after the test body ends — `go test -race` will eventually flag this.

Add a synchronization point (e.g. a `done` channel the goroutine closes, or inject a `sync.WaitGroup` via a test hook) and then `m.AssertCalled(t, "Create", …)`. Deleting the unused `setupLegacyFn` in the error-path sub-test while you're there will also remove the dead setup.

:yellow_circle: [consistency] `var method = "create"` is misleading — it is never reassigned, and every other short local uses `const method = …` or `method := …` in `pkg/apiserver/rest/dualwriter_mode3.go`:35,55,70,84,129,170 (confidence: 70)
Six method bodies do `var method = "create"`, `var method = "get"`, etc. `var` on a constant string reads as "this can change later," which it cannot, and `go vet`/`staticcheck` will nudge toward `const`.
```suggestion
const method = "create"
```

:yellow_circle: [consistency] Duplicate `go.work.sum` line for `pkg/apimachinery` in `go.work.sum`:4 (confidence: 85)
```
+github.com/grafana/grafana/pkg/apimachinery v0.0.0-20240701135906-559738ce6ae1/go.mod h1:DkxMin+qOh1Fgkxfbt+CUfBqqsCQJMG9op8Os/irBPA=
```
This exact line already exists at line 8 of the pre-change file (see context). A `go work sync` should have collapsed these; the duplicate suggests the sum file was hand-edited or an old `go work sync` was run. Either run `go work sync` from a clean state or drop the duplicate — reviewers downstream will otherwise get noisy merge conflicts.

## Nitpicks

:white_circle: [correctness] Removed `p := prometheus.NewRegistry()` in `dualwriter_mode1_test.go` but `p` is still referenced on the very next line in `pkg/apiserver/rest/dualwriter_mode1_test.go`:138 (confidence: 70)
```go
-            p := prometheus.NewRegistry()
             dw := NewDualWriter(Mode1, ls, us, p)
```
This compiles only if `p` is now a package-level variable — verify that is intentional and not a stale remnant of a refactor. (If so, the Mode 3 tests that do `p := prometheus.NewRegistry()` inside `TestMode3_Get` should arguably be consistent and use the package var too.)

:white_circle: [consistency] `mode3Str` placement between `Mode()` and `Create()` is unusual in `pkg/apiserver/rest/dualwriter_mode3.go`:32 (confidence: 60)
Prefer hoisting package-scoped constants to the top of the file (near `type DualWriterMode3`) rather than wedging them between two methods.

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: storage/persistence layer in apiserver, affects any resource whose group is configured to Mode 3 (e.g. playlists behind `FlagKubernetesPlaylists`) | Sensitive Paths: `pkg/apiserver/rest/` (dual-write persistence)
AI-Authored Likelihood: LOW
