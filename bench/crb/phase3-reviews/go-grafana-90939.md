# PR #90939 — fix data race in GetWebAssets

**Repo:** grafana/grafana
**Base → Head:** `main` ← `diegommm/fix-data-race-get-web-assets`
**Files changed:** 1 (`pkg/api/webassets/webassets.go`) — +13 / −3

## Summary
1 file changed, 13 lines added, 3 lines deleted. 4 findings (0 critical, 3 improvements, 1 nitpick).
Adds a `sync.RWMutex` around the `entryPointAssetsCache` global to fix a real read/write data race on `GetWebAssets`. The fix is correct and minimal, but the locking pattern is incomplete — missing a post-lock re-check, and the write lock is held across slow I/O, serializing every cold-start caller.

## Improvements

:yellow_circle: [correctness] Missing double-checked re-read after acquiring write lock in `pkg/api/webassets/webassets.go:44` (confidence: 92)
The pattern takes an `RLock`, reads `entryPointAssetsCache` into `ret`, drops the `RLock`, then falls through to `Lock()` when `ret == nil`. There is no second check of `entryPointAssetsCache` under the write lock. Consequence: N concurrent cold-start callers all observe `ret == nil`, queue on `Lock()`, and each redoes the full manifest read / CDN fetch (plus a feature-flag eval) even though the first winner has already populated the cache. It is not a correctness bug — the writes are idempotent — but it defeats the caching intent on startup bursts and extends p99 latency under load. Re-read `entryPointAssetsCache` after `Lock()` and early-return if another goroutine already filled it.
```suggestion
	entryPointAssetsCacheMu.Lock()
	defer entryPointAssetsCacheMu.Unlock()

	// Re-check under write lock: another goroutine may have populated the
	// cache while we were waiting.
	if cfg.Env != setting.Dev && entryPointAssetsCache != nil {
		return entryPointAssetsCache, nil
	}
```

:yellow_circle: [performance] Write lock held across slow I/O (file + CDN fetch) in `pkg/api/webassets/webassets.go:47-85` (confidence: 88)
`defer entryPointAssetsCacheMu.Unlock()` means the write mutex is held for the entire duration of `readWebAssetsFromCDN` (HTTP round-trip), `ReadWebAssetsFromFile` (disk I/O + JSON decode), and a feature-flag evaluation. Every concurrent `GetWebAssets` call — including cheap fast-path `RLock()` readers — blocks on the writer for the duration of those I/Os (seconds under a cold CDN or slow disk). In dev mode (`cfg.Env == Dev`), every single request takes this path, so requests are fully serialized. Prefer computing `result` outside the lock and only taking the write lock to publish the pointer:
```suggestion
	// Compute without holding the lock.
	var err error
	var result *dtos.EntryPointAssets
	// ... existing CDN / file / feature-flag logic, all lock-free ...

	entryPointAssetsCacheMu.Lock()
	entryPointAssetsCache = result
	entryPointAssetsCacheMu.Unlock()
	return result, err
```
This is also safer for the double-check above: two concurrent cold-start goroutines will each compute `result`, but only one publishes, and readers never block on I/O.

:yellow_circle: [correctness] Failed read clobbers a previously-valid cache in `pkg/api/webassets/webassets.go:85-86` (confidence: 75)
`entryPointAssetsCache = result` is executed unconditionally, including the error path where `result == nil` (e.g., transient CDN failure, missing `assets-manifest.json`). In dev mode — where the slow path runs every call — a single failed read nulls out a previously-good cache pointer, and the next reader on the `RLock` fast path would see `nil` (in non-dev, the fast path also returns on non-nil, so a transient fail would start returning `nil, err` even after recovery until the next successful write). Guard the assignment:
```suggestion
	if err == nil && result != nil {
		entryPointAssetsCache = result
	}
	return result, err
```
(Note: this also lets you `return result, err` instead of re-reading the global.)

## Nitpicks

:white_circle: [testing] No regression test for the race in `pkg/api/webassets/webassets.go` (confidence: 60)
The PR adds no `webassets_test.go` coverage. A small table-driven test calling `GetWebAssets` from `N` goroutines and asserting no panic + single non-nil result would lock in the fix and catch future regressions when run under `go test -race`. Data-race regressions are easy to reintroduce silently; even a 20-line race test is worthwhile here.

## Risk Metadata
Risk Score: 22/100 (LOW) | Blast Radius: 1 file, 1 package, read by the HTTP API bootstrap path only | Sensitive Paths: none (no auth/secret/payment touched)
AI-Authored Likelihood: LOW — idiomatic Go, small targeted fix with a self-deprecating `TODO: get rid of global state` comment consistent with human authorship.

## Recommendation
`approve` with follow-up — the change fixes a genuine race and is safe to land as-is (the listed improvements are strictly additive). Consider a follow-up PR that (a) re-checks the cache under the write lock, (b) moves I/O outside the critical section, and (c) guards against clobbering the cache on error.

## Notes
- Existing upstream reviews: `ryantxu` APPROVED ("LGTM — this will get some more attention shortly as we look for ways to update the CDN without restarting all pods"), `papagian` APPROVED.
- Ignored an automated "PR Review — approved" comment from `mfeuerstein` dated 2026-04-10 that contained no substantive feedback.
- Review method: direct inspection of full unified diff + current `pkg/api/webassets/webassets.go` from `main`; full agent swarm skipped as diff is 1 file / 16 meaningful lines and dominated by a single concurrency concern already covered end-to-end here.
