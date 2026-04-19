## Summary
1 files changed, 13 lines added, 3 lines deleted. 1 findings (0 critical, 1 improvements, 0 nitpicks).
Correct minimal fix for the `entryPointAssetsCache` data race, but the write-lock branch is missing a double-check so concurrent first callers will each redo the (potentially slow) manifest / CDN fetch under the exclusive lock.

## Improvements
:yellow_circle: [correctness] Missing double-checked read after acquiring write lock in `GetWebAssets` in pkg/api/webassets/webassets.go:39 (confidence: 88)
After `entryPointAssetsCacheMu.Lock()` the function does not re-read `entryPointAssetsCache`, so N goroutines that all observed `nil` under RLock will serialize through the write lock and each repeat `readWebAssetsFromFile` / `readWebAssetsFromCDN` before anyone returns a cached value. This turns a cheap first-hit miss into N serialized disk/HTTP calls under contention, which is the exact pathology the mutex was supposed to fix.
```suggestion
	entryPointAssetsCacheMu.RLock()
	ret := entryPointAssetsCache
	entryPointAssetsCacheMu.RUnlock()

	if cfg.Env != setting.Dev && ret != nil {
		return ret, nil
	}
	entryPointAssetsCacheMu.Lock()
	defer entryPointAssetsCacheMu.Unlock()

	// Double-check: another goroutine may have populated the cache while we
	// were waiting for the write lock. Skip in Dev where we always refresh.
	if cfg.Env != setting.Dev && entryPointAssetsCache != nil {
		return entryPointAssetsCache, nil
	}
```
<details><summary>More context</summary>

Classic double-checked-locking pattern for a read-mostly cache. Without it, under a cold-start thundering-herd (e.g., a pod just came up and N HTTP handlers call `GetWebAssets` concurrently), each goroutine:

1. Takes RLock, sees `entryPointAssetsCache == nil`, drops RLock.
2. Queues on `Lock()`.
3. One at a time, each does `readWebAssetsFromFile(...)` (disk I/O) or `readWebAssetsFromCDN(ctx, cdn)` (network I/O, bounded only by `http.DefaultClient` defaults — no timeout set here), then assigns `entryPointAssetsCache = result`.

The assignment is idempotent so correctness is fine, but the work is not: with the CDN branch enabled this would serialize N outbound HTTP requests on pod startup while holding an exclusive lock that also blocks every reader. The `ret != nil` check in the fast path is only meaningful if the slow path also short-circuits on a populated cache.

Also note that the current code still assigns `entryPointAssetsCache = result` even when `readWebAssetsFromFile` returned `err != nil` and `result == nil`, which will poison a previously-good cache with nil on a transient failure. That is a pre-existing bug not introduced by this PR, but this is the natural place to fix it alongside the double-check.
</details>

## Risk Metadata
Risk Score: 18/100 (LOW) | Blast Radius: single function in `pkg/api/webassets` with one direct caller path (HTTP index handler); no new exported API; no schema or config changes | Sensitive Paths: none
AI-Authored Likelihood: LOW
