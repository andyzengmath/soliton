## Summary
1 file changed, 13 lines added, 3 lines deleted. 3 findings (0 critical, 3 improvements, 0 nitpicks).
Correct race fix on the read path, but the implementation misses the standard double-checked-locking re-check and holds the write lock across network/disk I/O, so it will still stampede and serialize cold-cache fills.

## Improvements
:yellow_circle: [correctness] Missing re-check after lock upgrade lets N goroutines all re-populate the cache in `pkg/api/webassets/webassets.go:40` (confidence: 90)
The fast path does `RLock -> read -> RUnlock` and then unconditionally takes the write `Lock`. Between `RUnlock` and `Lock`, another goroutine can populate the cache, but this code never re-checks under the write lock — every racing caller will redundantly do the HTTP/disk read and overwrite the cache. This is the classic double-checked-locking bug. The bug is harmless for correctness (the result is deterministic) but defeats the cache on burst cold-start traffic, which is exactly the scenario the cache exists for.
```suggestion
	entryPointAssetsCacheMu.RLock()
	ret := entryPointAssetsCache
	entryPointAssetsCacheMu.RUnlock()

	if cfg.Env != setting.Dev && ret != nil {
		return ret, nil
	}
	entryPointAssetsCacheMu.Lock()
	defer entryPointAssetsCacheMu.Unlock()

	// Re-check after lock upgrade: another goroutine may have populated the cache
	// while we were waiting for the write lock.
	if cfg.Env != setting.Dev && entryPointAssetsCache != nil {
		return entryPointAssetsCache, nil
	}
```

:yellow_circle: [correctness] Write lock is held across network and disk I/O, serializing all readers in `pkg/api/webassets/webassets.go:53` (confidence: 88)
`entryPointAssetsCacheMu.Lock()` is acquired before `readWebAssetsFromCDN` (HTTP fetch) and `ReadWebAssetsFromFile` (disk read). Any concurrent caller — including readers that would have hit the fast `RLock` path — blocks behind the I/O-bound goroutine holding the exclusive lock. In Dev mode (`cfg.Env == setting.Dev`), every request takes the write lock because the fast-path guard skips Dev, so Dev-mode request handling is fully serialized on this mutex. Compute `result` and `err` outside the lock, then take the lock only to publish, so readers are never blocked on I/O.
```suggestion
func GetWebAssets(ctx context.Context, cfg *setting.Cfg, license licensing.Licensing) (*dtos.EntryPointAssets, error) {
	entryPointAssetsCacheMu.RLock()
	ret := entryPointAssetsCache
	entryPointAssetsCacheMu.RUnlock()

	if cfg.Env != setting.Dev && ret != nil {
		return ret, nil
	}

	// Do the expensive I/O outside the lock so concurrent readers are not blocked.
	result, err := loadWebAssets(ctx, cfg, license)

	entryPointAssetsCacheMu.Lock()
	defer entryPointAssetsCacheMu.Unlock()
	if result != nil {
		entryPointAssetsCache = result
	}
	return entryPointAssetsCache, err
}
```

:yellow_circle: [correctness] Cache is overwritten with `nil` on I/O failure, poisoning subsequent reads in `pkg/api/webassets/webassets.go:92` (confidence: 86)
`entryPointAssetsCache = result` is unconditional. If `ReadWebAssetsFromFile` fails (`err != nil`), `result` is `nil`, and this line replaces a previously valid cached value with `nil`. The next caller then re-runs the full I/O pipeline, and if it also fails, the error is amplified into a cold-cache storm instead of the cached value riding out transient disk/CDN issues. This behavior pre-dates the PR, but the PR is the first change that makes the write race-free, and leaves this semantic bug visible under the new lock. Guard the assignment on `result != nil`.
```suggestion
	if result != nil {
		entryPointAssetsCache = result
	}
	return entryPointAssetsCache, err
```

## Risk Metadata
Risk Score: 20/100 (LOW) | Blast Radius: 1 file, 1 exported function, concurrency-only change | Sensitive Paths: none
AI-Authored Likelihood: LOW
