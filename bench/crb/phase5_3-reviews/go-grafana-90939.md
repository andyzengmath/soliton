## Summary
1 files changed, 13 lines added, 3 lines deleted. 1 findings (1 critical, 0 improvements, 0 nitpicks).
1 file changed. 1 finding (1 critical). Incomplete double-checked locking — cache not re-validated after acquiring write lock in webassets.go:36

## Critical
:red_circle: [correctness] Incomplete double-checked locking — cache not re-validated after acquiring write lock in pkg/api/webassets/webassets.go:36 (confidence: 95)
The function reads entryPointAssetsCache under RLock, releases it, then acquires the write lock without re-checking whether another goroutine already populated the cache during the window between RUnlock and Lock. Multiple goroutines can simultaneously observe ret == nil under RLock, all pass the nil guard, then queue up on the write lock. The first goroutine computes and stores the result correctly. Each subsequent goroutine acquires the write lock and finds no early-return guard, so it redundantly re-reads the manifest from disk (or HTTP), overwrites the already-valid cache, and does unnecessary I/O for every goroutine that was in the queue. In the worst case this causes repeated and possibly inconsistent cache overwrites if the manifest changes between reads, or simply wastes significant I/O under high concurrency at startup. This also keeps the write lock held during slow I/O, blocking all concurrent readers; performing the load outside the lock and re-checking inside resolves both concerns.
```suggestion
	entryPointAssetsCacheMu.RLock()
	ret := entryPointAssetsCache
	entryPointAssetsCacheMu.RUnlock()
	if cfg.Env != setting.Dev && ret != nil {
		return ret, nil
	}

	// Do the expensive work outside any lock so concurrent readers are not blocked.
	result, err := loadEntryPointAssets(ctx, cfg)
	if err != nil {
		return nil, err
	}

	entryPointAssetsCacheMu.Lock()
	defer entryPointAssetsCacheMu.Unlock()
	// Re-check inside the write lock: another goroutine may have already
	// populated the cache while we were waiting to acquire the lock.
	if cfg.Env != setting.Dev && entryPointAssetsCache != nil {
		return entryPointAssetsCache, nil
	}
	entryPointAssetsCache = result
	return result, nil
```

## Risk Metadata
Risk Score: 22/100 (LOW) | Blast Radius: 1 file modified, no detected importers in scoped repo (web-asset hot path on every request) | Sensitive Paths: none
AI-Authored Likelihood: LOW

(1 additional findings below confidence threshold)
