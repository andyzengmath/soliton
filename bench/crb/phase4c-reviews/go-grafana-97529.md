## Summary
5 files changed, 25 lines added, 57 lines deleted. 4 findings (1 critical, 3 improvements, 0 nitpicks).
TOCTOU race in bleve.go BuildIndex allows concurrent index creation on the same directory.

## Critical

:red_circle: [correctness] Unprotected cache read creates TOCTOU race — concurrent bleve.New on same directory in pkg/storage/unified/search/bleve.go:85 (confidence: 95)

The mutex that previously wrapped the entire `BuildIndex` function has been narrowed to only protect the cache write (`b.cache[key] = idx`). The cache read — the check for an existing entry before building — is now outside the lock. Two concurrent goroutines calling `BuildIndex` with the same key will both see a cache miss, both proceed to call `bleve.New(dir, mapper)` on the same filesystem directory, and both attempt to write to `b.cache[key]`.

For file-backed indexes (the `size > b.opts.FileThreshold` branch), `bleve.New` creates or opens a directory on disk. Calling it concurrently on the same path produces an error or filesystem corruption. For memory-backed indexes, the second goroutine silently overwrites the first index in the cache, orphaning any callers holding a reference to the first-built index.

The on-disk path is namespaced per tenant (`filepath.Join(b.opts.Root, key.Namespace, ...)`), so this is not a cross-tenant data leak, but it degrades availability and can return incomplete search results. CWE-362.

```suggestion
// Use singleflight to coalesce concurrent builds for the same key while
// allowing concurrent builds for different keys.
import "golang.org/x/sync/singleflight"

type bleveBackend struct {
    // ...
    sf      singleflight.Group
    cacheMu sync.RWMutex
    cache   map[resource.NamespacedResource]resource.ResourceIndex
}

func (b *bleveBackend) BuildIndex(ctx context.Context, key resource.NamespacedResource, size int64, ...) (resource.ResourceIndex, error) {
    // Fast path: check cache under read lock.
    b.cacheMu.RLock()
    if idx, ok := b.cache[key]; ok {
        b.cacheMu.RUnlock()
        return idx, nil
    }
    b.cacheMu.RUnlock()

    v, err, _ := b.sf.Do(key.String(), func() (interface{}, error) {
        // Re-check under singleflight in case another caller just built it.
        b.cacheMu.RLock()
        if idx, ok := b.cache[key]; ok {
            b.cacheMu.RUnlock()
            return idx, nil
        }
        b.cacheMu.RUnlock()

        // ... existing build logic, including bleve.New(dir, mapper) ...

        b.cacheMu.Lock()
        b.cache[key] = idx
        b.cacheMu.Unlock()
        return idx, nil
    })
    if err != nil {
        return nil, err
    }
    return v.(resource.ResourceIndex), nil
}
```
A regression test exercising concurrent `BuildIndex` calls under `go test -race` should accompany the fix.

[References: CWE-362 (Concurrent Execution Using Shared Resource with Improper Synchronization); golang.org/x/sync/singleflight]

## Improvements

:yellow_circle: [testing] Postgres integration test silently skipped with no resolution plan in pkg/server/module_server_test.go:32 (confidence: 92)

A `TODO - fix this test for postgres` comment with a "works locally" note skips the entire integration test for postgres. There is no linked issue, no deadline, and no owner. This PR's core change is a reorder of initialization steps; the fact that the test passes locally but fails in CI under postgres timing may be a real symptom of the new eager-init path behaving differently under postgres's slower connection and schema setup. Skipping the test without investigating removes all postgres integration coverage for the changed initialization path.

```suggestion
// Investigate root cause before merging. If a temporary skip is truly
// required, reference a tracked issue so the skip does not become permanent:
// TODO: https://github.com/grafana/grafana/issues/XXXXX
if dbType == "postgres" {
    t.Skip("skipping - flaky in Drone CI, tracked in #XXXXX")
}
```

:yellow_circle: [testing] Eager-init error path has no test coverage in pkg/storage/unified/resource/server.go:1 (confidence: 90)

The PR introduces an eager initialization path where the constructor can now return an error. This is a new error contract. No test verifies error propagation from `Init`, cleanup on partial failure, or absence of goroutine leaks when `Init` fails partway through (for example, after starting a background watcher goroutine). A crash-looping pod that repeatedly hits an init failure has no test-confirmed behavior.

```suggestion
func TestNewResourceServer_EagerInitFailure(t *testing.T) {
    defer goleak.VerifyNone(t)

    backend := &fakeBackend{initErr: errors.New("db unavailable")}
    _, err := NewResourceServer(ResourceServerOptions{Backend: backend /* ... */})
    require.Error(t, err)
    require.ErrorContains(t, err, "db unavailable")
}
```

:yellow_circle: [testing] Mutex scope narrowing has no concurrent regression test in pkg/storage/unified/search/bleve.go:85 (confidence: 88)

Narrowing a mutex scope is one of the highest-risk refactors in concurrent code. No test exercises concurrent `BuildIndex` calls with the race detector enabled. Without such a test, the TOCTOU window documented in the critical finding above is not caught automatically, and a future change could silently re-introduce similar issues.

```suggestion
func TestBuildIndex_ConcurrentSameKeyIsSafe(t *testing.T) {
    b := newTestBleveBackend(t)
    key := resource.NamespacedResource{Namespace: "ns1", Group: "g", Resource: "r"}

    const n = 8
    var wg sync.WaitGroup
    results := make([]resource.ResourceIndex, n)
    errs := make([]error, n)
    for i := 0; i < n; i++ {
        wg.Add(1)
        go func(i int) {
            defer wg.Done()
            results[i], errs[i] = b.BuildIndex(context.Background(), key, /* ... */)
        }(i)
    }
    wg.Wait()

    for i := range errs {
        require.NoError(t, errs[i])
    }
    // All callers should observe exactly one index instance.
    for i := 1; i < n; i++ {
        require.Same(t, results[0], results[i])
    }
}
// Ensure CI runs the package with `go test -race ./pkg/storage/unified/search/...`.
```

## Risk Metadata
Risk Score: 36/100 (MEDIUM) | Blast Radius: HIGH (70/100 — resource/server.go is a central unified-storage server referenced by multiple backend packages) | Sensitive Paths: none matched
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold)
