## Summary
1 files changed, 13 lines added, 3 lines deleted. 1 findings (0 critical, 1 improvements, 0 nitpicks).
1 file changed. 1 finding (1 improvement). Double-checked locking missing re-check of cache under write lock in webassets.go:44-50

## Improvements
:yellow_circle: [correctness] Double-checked locking missing re-check of cache under write lock in pkg/api/webassets/webassets.go:44 (confidence: 95)
The new double-checked locking pattern reads the cache under RLock, releases it, then acquires the write lock — but never re-reads entryPointAssetsCache after holding the write lock. Any number of goroutines that observed a nil cache during the RLock phase will all queue up behind the write lock and each redundantly reload assets from disk/CDN in sequence. In non-dev environments this means after goroutine A completes the expensive load and writes entryPointAssetsCache, goroutine B (which already passed the nil check) still proceeds with a full reload and overwrites the cache. The standard double-checked locking fix is a second nil check immediately after acquiring the write lock. Without it the serialisation provided by the write lock does not prevent redundant work and repeated writes to the global cache.
```suggestion
	entryPointAssetsCacheMu.Lock()
	defer entryPointAssetsCacheMu.Unlock()

	// Re-check under write lock: another goroutine may have populated the cache
	// while we were waiting to acquire the lock.
	if cfg.Env != setting.Dev && entryPointAssetsCache != nil {
		return entryPointAssetsCache, nil
	}
```

## Risk Metadata
Risk Score: 22/100 (LOW) | Blast Radius: no importers observable in shim | Sensitive Paths: none
AI-Authored Likelihood: LOW
