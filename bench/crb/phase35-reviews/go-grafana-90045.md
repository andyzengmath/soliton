## Summary
5 files changed, 524 lines added, 131 lines deleted. 16 findings (8 critical, 8 improvements, 0 nitpicks).
Mode 3's async legacy-write design is broken: the goroutines inherit the request context, so legacy writes are canceled the moment the handler returns; multiple metric-recording sites record the wrong series or use unbounded labels; and the test suite never actually verifies the async path.

## Critical

:red_circle: [correctness] Goroutine-launched legacy writes derive from the canceled request context, silently dropping every async write in pkg/apiserver/rest/dualwriter_mode3.go:113 (confidence: 97)
All four goroutines in `Create`, `Update`, `Delete`, and `DeleteCollection` derive their timeout context from the inbound request `ctx` via `context.WithTimeoutCause(ctx, 10*time.Second, ...)`. After the synchronous `d.Storage.*` call returns to the caller, the Kubernetes API server cancels the request context; that cancellation propagates to the child context, so by the time the goroutine is scheduled, `d.Legacy.Create/Update/Delete/DeleteCollection` is either aborted before starting or cut off mid-transaction. This defeats the entire stated purpose of Mode 3 ("write to unified storage and then asynchronously, write to legacy, just as a safe measure") — every asynchronous legacy write will fail with `context.Canceled` under normal operation, with no retry queue and no dead-letter log. Worse, a partial-progress cancellation (e.g., the legacy DB has issued DELETE but not COMMIT) can leave the two stores in divergent states with no reconciliation. Apply the same fix to Update (line ~229), Delete (line ~185), and DeleteCollection (line ~270).
```suggestion
go func() {
    defer runtime.HandleCrash() // prevent process exit on panic
    // Detach from request cancellation so the write survives the handler returning.
    bgCtx := context.WithoutCancel(ctx)
    ctx, cancel := context.WithTimeoutCause(bgCtx, 10*time.Second, errors.New("legacy create timeout"))
    defer cancel()

    startLegacy := time.Now()
    _, errObjectSt := d.Legacy.Create(ctx, obj, createValidation, options)
    d.recordLegacyDuration(errObjectSt != nil, mode3Str, options.Kind, method, startLegacy)
}()
```
[References: https://pkg.go.dev/context#WithoutCancel, https://owasp.org/Top10/A08_2021-Software_and_Data_Integrity_Failures/]

:red_circle: [correctness] Update goroutine passes raw `objInfo` to Legacy.Update, causing `UpdatedObject` to re-run against stale legacy state and the two stores to diverge in pkg/apiserver/rest/dualwriter_mode3.go:242 (confidence: 96)
The pre-PR code explicitly wrapped the storage result before handing it to Legacy.Update: `&updateWrapper{upstream: objInfo, updated: obj}`. The new goroutine drops that wrapper and passes the raw `rest.UpdatedObjectInfo` — so `Legacy.Update` internally calls `objInfo.UpdatedObject(ctx, legacyOld)` a second time, against the legacy store's own old object. Any non-deterministic or time-sensitive field in the update closure (timestamps, UUIDs, resourceVersion fixups, slice-append patches, conditional strategic-merge-patch branches) will produce a different final object on the legacy side than what storage already persisted. For permission-bearing fields (roles, ACLs, datasource URLs), a double-applied patch against a stale base can re-introduce a value that storage just overwrote — effectively a silent authorization-rollback primitive. This violates the core invariant that both stores hold identical data.
```suggestion
go func() {
    defer runtime.HandleCrash()
    bgCtx := context.WithoutCancel(ctx)
    ctx, cancel := context.WithTimeoutCause(bgCtx, 10*time.Second, errors.New("legacy update timeout"))
    defer cancel()

    startLegacy := time.Now()
    _, _, errObjectSt := d.Legacy.Update(ctx, name, &updateWrapper{
        upstream: objInfo,
        updated:  res,
    }, createValidation, updateValidation, forceAllowCreate, options)
    d.recordLegacyDuration(errObjectSt != nil, mode3Str, options.Kind, method, startLegacy)
}()
```

:red_circle: [correctness] Delete's success-path metric passes `name` (object name) where every other call passes `options.Kind` — unbounded label causes Prometheus cardinality explosion in pkg/apiserver/rest/dualwriter_mode3.go:182 (confidence: 99)
`d.recordStorageDuration(false, mode3Str, name, method, startStorage)` uses the resource instance name (e.g. `"my-playlist-abc123"`) as the third argument, which is the kind/resource label elsewhere. The error-path call one line above correctly uses `options.Kind`, and every other recording site in this file is also `options.Kind`-based. Object names are unbounded and client-controlled, so every unique name creates a new Prometheus time series; an authenticated caller can trivially drive this by scripting `DELETE /playlists/<random-uuid>` and exhaust the scrape endpoint or downstream TSDB memory — a classic DoS-via-cardinality vector.
```suggestion
d.recordStorageDuration(false, mode3Str, options.Kind, method, startStorage)
```

:red_circle: [correctness] `recordLegacyDuration` called when Storage.Create fails — storage errors attributed to the legacy metric in pkg/apiserver/rest/dualwriter_mode3.go:104 (confidence: 98)
When `d.Storage.Create` returns an error at line 101, the error path records `d.recordLegacyDuration(true, mode3Str, options.Kind, method, startStorage)`. The failing operation is a storage write — Legacy.Create has not even been attempted yet. The success path on the very next logical line correctly calls `recordStorageDuration`, making this clearly a copy-paste mistake. Result: dashboards/alerts on storage error rate never fire, while legacy error rate spikes on purely storage-side failures.
```suggestion
d.recordStorageDuration(true, mode3Str, options.Kind, method, startStorage)
```

:red_circle: [correctness] `recordLegacyDuration` called when Storage.Update fails — same attribution bug as Create in pkg/apiserver/rest/dualwriter_mode3.go:219 (confidence: 98)
Mirror of the Create bug above. On storage-side update failure, the code records a legacy-side error duration. Storage error metric never moves, legacy error metric records false positives.
```suggestion
d.recordStorageDuration(true, mode3Str, options.Kind, method, startStorage)
```

:red_circle: [correctness] DeleteCollection goroutine records `recordStorageDuration` for a legacy call — legacy metric never populated for this operation in pkg/apiserver/rest/dualwriter_mode3.go:275 (confidence: 99)
Inside the goroutine that invokes `d.Legacy.DeleteCollection`, the outcome is recorded via `d.recordStorageDuration(err != nil, ...)`. The correct helper is `recordLegacyDuration`. As written, legacy DeleteCollection latency/error is double-counted into the storage metric and the legacy metric is silent for DeleteCollection altogether.
```suggestion
d.recordLegacyDuration(err != nil, mode3Str, options.Kind, method, startLegacy)
```

:red_circle: [security] Missing `defer recover()` in all four async legacy-write goroutines — any panic crashes the apiserver process in pkg/apiserver/rest/dualwriter_mode3.go:113 (confidence: 90)
Goroutines spawned from the request handler do not participate in the API server's request-scoped panic recovery middleware. A panic inside `d.Legacy.Create/Update/Delete/DeleteCollection` — e.g. nil-deref on a malformed `runtime.Object`, a SQL driver asserting on a canceled ctx, a validator panicking on user-controlled input — will terminate the whole Grafana process. Because the payload is caller-controlled, an authenticated attacker who finds a panic path in any legacy storage implementation has a remote crash / DoS primitive that did not exist in the pre-PR synchronous code. Apply the same guard to Update, Delete, and DeleteCollection goroutines.
```suggestion
import "k8s.io/apimachinery/pkg/util/runtime"

go func() {
    defer runtime.HandleCrash() // logs + optional metric, prevents process exit
    // ... existing body ...
}()
```
[References: https://pkg.go.dev/k8s.io/apimachinery/pkg/util/runtime#HandleCrash, https://cwe.mitre.org/data/definitions/248.html]

:red_circle: [correctness] Delete installs `d.Log` into the context instead of the enriched local `log` — structured name/kind/method fields dropped from downstream logs in pkg/apiserver/rest/dualwriter_mode3.go:173 (confidence: 97)
Line 172 builds `log := d.Log.WithValues("name", name, "kind", options.Kind, "method", method)`, but line 173 calls `ctx = klog.NewContext(ctx, d.Log)` — re-installing the un-enriched struct logger. Any downstream code that pulls the logger from context (including the `Storage.Delete` implementation and the goroutine's own `log.Error` if it were rebuilt from ctx) loses the per-call key/value pairs. Every other method in this PR uses `klog.NewContext(ctx, log)`.
```suggestion
ctx = klog.NewContext(ctx, log)
```

## Improvements

:yellow_circle: [testing] Tests for Create/Update/Delete/DeleteCollection never verify `Legacy.*` was actually called — the async path is silently untested in pkg/apiserver/rest/dualwriter_mode3_test.go:410 (confidence: 97)
Each `TestMode3_*` test registers `setupLegacyFn` expectations on the shared `*mock.Mock`, calls the dual writer, asserts on the return value, and exits. The production code launches `Legacy.Create/Update/Delete/DeleteCollection` in a goroutine and returns to the caller immediately. The test never waits for the goroutine and never calls `m.AssertExpectations(t)`, so an implementation that silently drops all legacy writes (which, per the ctx-cancellation finding above, is exactly what the current code does in production) would still pass every test. Under `-race`, the goroutine writing to the shared mock after `t.Cleanup` runs is also a data race.
```suggestion
obj, err := dw.Create(context.Background(), tt.input, ...)
assert.NoError(t, err)

// Wait for the legacy goroutine to complete before asserting.
assert.Eventually(t, func() bool {
    return m.AssertExpectations(t)
}, 2*time.Second, 10*time.Millisecond)
```

:yellow_circle: [testing] TestMode3_Delete success case never registers a Legacy.Delete expectation — goroutine will hit an unregistered mock call and panic/flake in pkg/apiserver/rest/dualwriter_mode3_test.go:561 (confidence: 96)
The "deleting an object in the unified store" case sets up only `setupStorageFn` but no `setupLegacyFn`. `legacyStoreMock` and `storageMock` share a single `*mock.Mock` instance, so when the goroutine calls `d.Legacy.Delete`, testify/mock sees an unregistered method and calls `t.Fatal` / panics — potentially after the test function has already returned, corrupting state for concurrently running tests.
```suggestion
setupStorageFn: func(m *mock.Mock, name string) {
    m.On("Delete", mock.Anything, name, mock.Anything, mock.Anything).Return(exampleObj, false, nil)
},
setupLegacyFn: func(m *mock.Mock, name string) {
    m.On("Delete", mock.Anything, name, mock.Anything, mock.Anything).Return(exampleObj, false, nil)
},
```

:yellow_circle: [testing] No test covers "storage succeeds, legacy fails" — the core safety guarantee of Mode 3 is unverified in pkg/apiserver/rest/dualwriter_mode3_test.go:380 (confidence: 95)
Mode 3's stated rationale is that a storage write is authoritative and a failing legacy write must not surface to the caller. Not one test case in `TestMode3_Create`, `TestMode3_Update`, `TestMode3_Delete`, or `TestMode3_DeleteCollection` mocks storage success with legacy failure. A regression that starts propagating legacy errors back to the caller would pass the entire test suite.
```suggestion
{
    name:  "storage succeeds but legacy create fails — caller still gets success",
    input: exampleObj,
    setupStorageFn: func(m *mock.Mock) {
        m.On("Create", mock.Anything, mock.Anything, mock.Anything, mock.Anything).Return(exampleObj, nil)
    },
    setupLegacyFn: func(m *mock.Mock, input runtime.Object) {
        m.On("Create", mock.Anything, input, mock.Anything, mock.Anything).Return(nil, errors.New("legacy unavailable"))
    },
    wantErr: false,
},
```

:yellow_circle: [testing] TestMode3_Create's error case has no `setupStorageFn` but Storage.Create runs first — the test exercises the wrong path in pkg/apiserver/rest/dualwriter_mode3_test.go:401 (confidence: 90)
The `wantErr: true` case ("error when creating object in the unified store fails") registers only a `setupLegacyFn` returning an error. The new `DualWriterMode3.Create` calls `d.Storage.Create` synchronously first, so with no storage expectation registered testify/mock returns zero values or panics — and the legacy mock's error is never reached synchronously (it now only runs in a goroutine after storage succeeded). The test either fails for the wrong reason or passes vacuously against a zero-value return.
```suggestion
{
    name:  "error when creating object in the unified store fails",
    input: failingObj,
    setupStorageFn: func(m *mock.Mock) {
        m.On("Create", mock.Anything, failingObj, mock.Anything, mock.Anything).Return(nil, errors.New("error"))
    },
    wantErr: true,
},
```

:yellow_circle: [testing] TestMode3_List and TestMode3_Get never assert that Legacy.List/Get is NOT called — the key Mode 3 read invariant is untested in pkg/apiserver/rest/dualwriter_mode3_test.go:524 (confidence: 92)
The PR description commits Mode 3 to reading only from unified storage. The tests assert the return value matches `exampleList`/`exampleObj` but never verify that the legacy mock received zero calls. The old commented-out test did assert `lsSpy.Counts("LegacyStorage.List") == 0`; that invariant is now lost. A future refactor that accidentally delegates List/Get to legacy would pass.
```suggestion
res, err := dw.List(context.Background(), tt.options)
assert.NoError(t, err)
assert.Equal(t, exampleList, res)

// Mode 3 read invariant: Legacy must never be consulted.
m.AssertNotCalled(t, "List", mock.Anything, mock.Anything)
```

:yellow_circle: [cross-file-impact] `dualwriter_mode1_test.go` drops its local `p` declaration but still references `p`; five of six new TestMode3_* functions do the same — hidden cross-file compile dependency in pkg/apiserver/rest/dualwriter_mode1_test.go:138 (confidence: 92)
The diff removes `p := prometheus.NewRegistry()` from `TestMode1_Get` while the next line still calls `NewDualWriter(Mode1, ls, us, p)`. This compiles only because a package-scoped `p` is declared in another test file in `package rest`. The new `dualwriter_mode3_test.go` inherits the same pattern: `TestMode3_Create/List/Delete/DeleteCollection/Update` all reference `p` without declaring it locally, while `TestMode3_Get` inconsistently declares its own. Any removal or rename of the package-level `p` breaks every one of these tests simultaneously, with no local indication of the dependency.
```suggestion
// In each affected Mode3 test function, mirror TestMode3_Get's pattern:
p := prometheus.NewRegistry()
dw := NewDualWriter(Mode3, ls, us, p)
```

:yellow_circle: [security] Request-scoped audit/log identity is lost once the legacy write moves off-request in pkg/apiserver/rest/dualwriter_mode3.go:113 (confidence: 85)
The API server's audit middleware emits events bound to the request lifetime. With legacy writes moved into detached goroutines, any log/audit emitted by the legacy store (success, failure, impersonation checks, authorization denial) fires after the request-scoped audit stage has closed, so it is not correlated with the originating request-id/user. Combined with the ctx-cancellation bug, legacy authorization failures may not log at all. Operators lose the ability to answer "did user X's delete of resource Y succeed in both stores?" from logs. Fixing the ctx-lifecycle issue with `context.WithoutCancel` (rather than a fresh `context.Background`) preserves user/trace values so the legacy store's audit hooks still fire; additionally emit an explicit structured record in the goroutine on every terminal outcome and add a `dualwriter_legacy_dropped_total{reason=...}` counter.
```suggestion
defer func() {
    d.Log.Info("legacy write complete",
        "kind", options.Kind, "name", name,
        "storageErr", err != nil, "legacyErr", errObjectSt != nil,
        "requestID", audit.RequestIDFrom(parentCtx))
}()
```
[References: https://owasp.org/Top10/A09_2021-Security_Logging_and_Monitoring_Failures/]

:yellow_circle: [cross-file-impact] Playlist integration tests may flake on mode 3 because legacy writes now land asynchronously in pkg/tests/apis/playlist/playlist_test.go:728 (confidence: 85)
The two active new sub-tests ("with dual write (file, mode 3)" and "with dual write (unified storage, mode 3)") call the shared `doPlaylistTests` helper used by modes 1 and 2, where every write was fully synchronous. In mode 3, legacy writes land inside a goroutine with a 10-second timeout (and, per the ctx bug, often not at all). Any read-back assertion in `doPlaylistTests` that cross-checks legacy state after a mutation will race. Either parameterize `doPlaylistTests` with the dual-write mode and skip legacy-consistency checks for mode 3, or wrap those assertions in `require.Eventually` with a sufficiently long timeout.
```suggestion
// In doPlaylistTests (or a mode-3-aware variant), replace direct reads with:
require.Eventually(t, func() bool {
    got, err := legacyClient.Get(ctx, name, metav1.GetOptions{})
    return err == nil && got.Name == name
}, 15*time.Second, 100*time.Millisecond, "legacy write did not land within timeout")
```

## Risk Metadata
Risk Score: 78/100 (HIGH) | Blast Radius: core dual-write path on every mutation for any resource opted into Mode 3 (playlist is first; more resources will follow); changes goroutine lifecycle, metric labels, and test contract at once | Sensitive Paths: pkg/apiserver/rest (API server write path), pkg/tests/apis/playlist (integration contract)
AI-Authored Likelihood: LOW
