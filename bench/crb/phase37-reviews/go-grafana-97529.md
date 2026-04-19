## Summary
5 files changed, 25 lines added, 57 lines deleted. 7 findings (5 critical, 2 improvements, 0 nitpicks).
Race condition: concurrent BuildIndex calls for same key leak index handles and corrupt cache.

## Critical

:red_circle: [correctness] Race condition: concurrent BuildIndex calls for same key leak index handles and corrupt cache in pkg/storage/unified/search/bleve.go:85 (confidence: 95)
The lock scope was narrowed from covering the entire BuildIndex function to only wrapping the final cache write (`b.cache[key] = idx`). Two goroutines concurrently calling BuildIndex for the same key will both proceed past any upstream cache-hit check, both call `bleve.New(dir, mapper)` or `bleve.NewMemOnly(mapper)`, and both run `builder(index)` writing all documents. The second write silently overwrites the first; the first-built index is discarded without calling `index.Close()`. For file-backed indices this leaks OS file handles and leaves the bleve directory in an inconsistent state, corrupting the on-disk tenant index and producing a tenant-scoped denial of service of search until an operator intervenes. For in-memory indices the built index and its allocated memory become unreachable garbage. Concurrent reads of `b.cache` without holding `cacheMu` also race with the narrower write, which is a Go data race and runtime-fatal under `-race`.
```suggestion
// Option A: check-then-insert under lock
b.cacheMu.Lock()
if existing, ok := b.cache[key]; ok {
    b.cacheMu.Unlock()
    _ = idx.Close()
    return existing, nil
}
b.cache[key] = idx
b.cacheMu.Unlock()
return idx, nil
// Option B: use golang.org/x/sync/singleflight to dedupe concurrent
// builds for the same key entirely, and convert cacheMu to sync.RWMutex
// with RLock on all read paths.
```
[References: https://cwe.mitre.org/data/definitions/362.html, https://pkg.go.dev/golang.org/x/sync/singleflight]

:red_circle: [correctness] Init called with potentially short-lived startup context, reproducing the cancellation it was meant to fix in pkg/storage/unified/resource/server.go:255 (confidence: 88)
`NewResourceServer` now calls `s.Init(ctx)` using the same `ctx` passed into the constructor. The PR's stated motivation is that building the search index inside a gRPC call context caused "context cancelled errors since the index takes too long to build." However, the constructor's `ctx` in a typical DI/wire framework is often a bounded context, not `context.Background()`. Now that the trace fix correctly threads `ctx` through `search.init(ctx)` and into `build(ctx,...)`, any deadline on the constructor's context will cancel the index build mid-way, causing `NewResourceServer` to return `nil, err` and preventing the server from starting — reproducing the same failure mode the PR was meant to fix.
```suggestion
// Replace: err := s.Init(ctx)
// With:    err := s.Init(context.Background())
// Or use a long-lived application context passed via opts.
```

:red_circle: [testing] No test for the eager-init behavior contract in pkg/storage/unified/resource/server.go:255 (confidence: 95)
The PR's central behavioral change — moving `Init` from lazy per-call to eager at construction — has no test. Nothing verifies that (a) a successfully constructed server has completed init, (b) a failing init prevents construction and surfaces the correct error, or (c) the constructor-time context is appropriate for the work being done. The author's own reviewer question — "Does anything else depend on initializing US lazily?" — is unanswered by any test. Add unit tests for both success and failure paths of `NewResourceServer` to lock in the new contract before merging.

:red_circle: [testing] No concurrency test for the narrowed BuildIndex lock in pkg/storage/unified/search/bleve.go:85 (confidence: 92)
The PR's stated performance goal is "finer grain locking when writing the index cache." No race-detector or concurrent-goroutine test exists for `BuildIndex`. Running the suite under `-race` with concurrent same-key builds would catch the data race introduced by the narrowed lock scope.
```suggestion
func TestBleveBackend_BuildIndex_ConcurrentSameKey(t *testing.T) {
    backend := newTestBleveBackend(t)
    key := resource.NamespacedResource{Namespace: "ns1", Resource: "dashboards", Group: "grafana"}
    var wg sync.WaitGroup
    for i := 0; i < 10; i++ {
        wg.Add(1)
        go func() {
            defer wg.Done()
            _, _ = backend.BuildIndex(context.Background(), key, 1, 0, nil,
                func(idx resource.ResourceIndex) (int64, error) { return 0, nil })
        }()
    }
    wg.Wait()
    // assert: cache has exactly one entry, no orphaned indices, no data race under -race
}
```

:red_circle: [testing] Postgres skip masks an eager-init regression rather than fixing it in pkg/server/module_server_test.go:35 (confidence: 90)
The only test change in this PR adds `t.Skip` for postgres with a TODO: "skipping - test not working with postgres in Drone. Works locally." This skip was not present before; the PR itself introduced the postgres failure, and eager `Init` in `NewResourceServer` is the most likely cause. Skipping rather than fixing hides a real regression in CI and leaves the PR's main lazy-to-eager transition unvalidated on postgres, one of Grafana's supported production databases.
```suggestion
// Remove the t.Skip for postgres and fix the underlying startup failure.
// At minimum, isolate it into a targeted NewResourceServer startup test
// against a postgres test DB so the regression is visible and diagnosable.
```

## Improvements

:yellow_circle: [consistency] Inconsistent trace context propagation — BuildIndex not updated like search.go and sql/backend.go in pkg/storage/unified/search/bleve.go:234 (confidence: 90)
The PR fixes trace propagation by capturing the span's context (`ctx, span := tracer.Start(ctx,...)`) in `search.go` `init`/`build` and `sql/backend.go` `GetResourceStats`. However, `bleve.go` `BuildIndex` still uses `_, span := b.tracer.Start(ctx,...)`. Since `BuildIndex` is called during the same search index initialization workflow, this leaves a gap in the trace: spans started inside `builder()` or downstream bleve operations will not be children of `BuildIndex`'s span.
```suggestion
ctx, span := b.tracer.Start(ctx, tracingPrexfixBleve+"BuildIndex")
```

:yellow_circle: [consistency] Duplicate error logging when Init() fails in NewResourceServer in pkg/storage/unified/resource/server.go:71 (confidence: 85)
`NewResourceServer` calls `s.Init(ctx)` and logs on failure: `s.log.Error("error initializing resource server", "error", err)`. `Init()` itself already logs the identical message at the end of its `sync.Once` block: `s.log.Error("error initializing resource server", "error", s.initErr)`. On failure, both fire, producing duplicate log lines.
```suggestion
err := s.Init(ctx)
if err != nil {
    return nil, err
}
```

## Risk Metadata
Risk Score: 37/100 (MEDIUM) | Blast Radius: ~7 cross-package importers across unified storage (server.go, search.go, bleve.go, sql/backend.go) | Sensitive Paths: none
AI-Authored Likelihood: LOW

(1 additional finding below confidence threshold)
