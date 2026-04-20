## Summary
1 file changed, 13 lines added, 3 lines deleted. 2 findings (1 critical, 1 improvement, 0 nitpicks).
Broken double-checked locking in GetWebAssets causes redundant I/O and an unbounded goroutine stampede on startup.

## Critical
:red_circle: [correctness] Broken double-checked locking — missing second cache check after acquiring write lock in pkg/api/webassets/webassets.go:35 (confidence: 97)
The pattern is: (1) RLock, read cache, RUnlock; (2) if cache is non-nil, return; (3) acquire write Lock; (4) do I/O; (5) write cache. Between steps 1 and 3, any number of goroutines can observe a nil cache and all pass the fast-path guard. They then queue on the write lock. The first goroutine performs I/O and populates `entryPointAssetsCache`. The second goroutine acquires the lock but does NOT re-check `entryPointAssetsCache` — so it repeats the full I/O (disk read and possibly a CDN call) and overwrites the cache unconditionally. Every subsequent queued goroutine does the same. This is the standard broken double-checked-locking failure mode: the inner guard is absent. In Dev the fast-path is never taken so this path is always reached; in non-Dev under concurrent load at startup this causes redundant I/O and widens the lock contention window.
```suggestion
entryPointAssetsCacheMu.Lock()
defer entryPointAssetsCacheMu.Unlock()

// Second check: another goroutine may have populated the cache
// while we were waiting for the write lock.
if cfg.Env != setting.Dev && entryPointAssetsCache != nil {
    return entryPointAssetsCache, nil
}

// ... rest of the I/O logic unchanged ...
```

## Improvements
:yellow_circle: [testing] No concurrent regression test for the data race fix in GetWebAssets in pkg/api/webassets/webassets.go:40 (confidence: 85)
The PR fixes a real data race on the package-level `entryPointAssetsCache` pointer by adding an `RWMutex`, but no test file exists for the webassets package. Without a `go test -race` test that calls `GetWebAssets` from multiple goroutines, there is no automated guarantee that the race cannot regress if the mutex is ever removed or weakened.
```suggestion
// pkg/api/webassets/webassets_test.go
package webassets

import (
    "context"
    "sync"
    "testing"

    "github.com/grafana/grafana/pkg/setting"
)

func resetCache() {
    entryPointAssetsCacheMu.Lock()
    entryPointAssetsCache = nil
    entryPointAssetsCacheMu.Unlock()
}

// Run with: go test -race ./pkg/api/webassets/...
func TestGetWebAssets_ConcurrentCallsDoNotRace(t *testing.T) {
    resetCache()
    t.Cleanup(resetCache)

    cfg := setting.NewCfg()
    cfg.Env = setting.Prod
    cfg.StaticRootPath = "testdata" // must contain build/assets-manifest.json

    const goroutines = 20
    var wg sync.WaitGroup
    wg.Add(goroutines)
    for i := 0; i < goroutines; i++ {
        go func() {
            defer wg.Done()
            _, _ = GetWebAssets(context.Background(), cfg, nil)
        }()
    }
    wg.Wait()
}
```

## Risk Metadata
Risk Score: 17/100 (LOW) | Blast Radius: single file, single package; one importer chain (HTTP index handler) | Sensitive Paths: none
AI-Authored Likelihood: LOW

(1 additional finding below confidence threshold)
