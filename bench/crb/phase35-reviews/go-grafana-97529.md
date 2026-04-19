## Summary
5 files changed, 25 lines added, 57 lines deleted. 3 findings (0 critical, 3 improvements, 0 nitpicks).
Eager init + trace-ctx + bleve lock-scope refactor; the bleve lock-scope change is the highest-risk piece and deserves a concurrent-same-key guard before merge.

## Improvements
:yellow_circle: [correctness] Concurrent `BuildIndex` for the same key can race on the on-disk index directory and orphan an index in `b.cache` in pkg/storage/unified/search/bleve.go:85 (confidence: 80)
The old code held `b.cacheMu` for the entire `BuildIndex` body, so two callers with the same `key` serialized: the second caller would (typically) find the cache already populated by the first. The new code only locks around the final `b.cache[key] = idx` write. Two goroutines that miss the cache for the same key now both enter `BuildIndex`, both run `bleve.New(dir, mapper)` against the same on-disk `filepath.Join(b.opts.Root, key.Namespace, fmt.Sprintf("%s.%s", key.Resource, key.Group))` directory, and both write to `b.cache[key]` — last writer wins, the other `idx` is dropped without `Close()`, and the two concurrent `bleve.New` calls against the same directory can corrupt the index or return an error on the loser. The PR description calls out "high concurrency" across namespaces (distinct keys), which is fine, but nothing in the visible diff prevents same-key concurrency from the caller side (`searchSupport.getOrCreateIndex` / `build`). Add a per-key singleflight (or check-then-build under `cacheMu` with a sentinel) so same-key callers coalesce; keep the finer-grained lock for cross-key concurrency.
```suggestion
	// coalesce same-key builders so the disk dir and cache entry have a single writer
	b.cacheMu.Lock()
	if existing, ok := b.cache[key]; ok {
		b.cacheMu.Unlock()
		return existing, nil
	}
	b.cacheMu.Unlock()
	// (consider singleflight.Group here if build() is expensive enough that the
	// check-then-build race window matters in practice)
```

:yellow_circle: [correctness] `Init()` removed from every RPC handler — non-`NewResourceServer` construction paths will now nil-panic instead of self-healing in pkg/storage/unified/resource/server.go:255 (confidence: 78)
Previously every RPC entrypoint (`Create`, `Update`, `Delete`, `Read`, `List`, `Watch`, `Search`, `History`, `Origin`, `IsHealthy`, `PutBlob`, `GetBlob`) started with `if err := s.Init(ctx); err != nil { return nil, err }`. That was the authoritative guarantee that `s.search`, `s.initWatcher()` state, and `s.initErr` were set before any handler ran — independent of how `*server` was constructed. The PR replaces that guarantee with a single eager `s.Init(ctx)` call inside `NewResourceServer`. Any code path that constructs `*server` directly (tests, alternate factories, embedding) no longer gets this safety net, and handlers will dereference `s.search`/`s.blob` without initialization. The PR author's own reviewer note — *"Does anything else depend on initializing US lazily? What could this break?"* — is effectively asking exactly this. Either (a) keep a cheap `s.Init(ctx)` call at the top of each handler (it's idempotent via `s.initOnce`/`s.initErr`, so the cost is a single atomic-load after the first call), or (b) unexport `*server` / gate construction so `NewResourceServer` is provably the only path.
```suggestion
// Keep a cheap guard at each handler entry; Init is idempotent via initOnce,
// so this is an atomic load after startup but prevents nil-deref if *server
// is ever constructed outside NewResourceServer.
if err := s.Init(ctx); err != nil {
    return nil, err
}
```

:yellow_circle: [correctness] Reordering `search.init` before `initWatcher` opens a gap where writes landing between index-build completion and watcher-start are invisible to the index until the next full rebuild in pkg/storage/unified/resource/server.go:300 (confidence: 68)
The old order was `initWatcher()` → `search.init(ctx)`: the watcher was running (buffering / streaming change events) by the time the index finished building, so events that occurred during index build could be replayed/observed after. The new order is `search.init(ctx)` → `initWatcher()`: any write that commits between the moment `search.init` snapshots the backend and the moment `initWatcher` subscribes is not observed by either path and therefore will not be reflected in the index until something else triggers a rebuild. Whether this is acceptable depends on whether `initWatcher` re-bootstraps from a persisted `ResourceVersion` (in which case events after that RV are replayed and the gap is closed) or subscribes to "live" events only (in which case the gap is a real correctness bug on a busy cluster). Please confirm the watcher starts from the max RV observed during `search.init` — if it doesn't, pass that RV down, or restore the original order.

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: `pkg/storage/unified/resource/*` and `pkg/storage/unified/search/bleve.go` are core storage-layer packages consumed by every resource server caller; the `Init`-removal change in particular touches all 12 RPC entrypoints. | Sensitive Paths: none matched (no auth/, security/, *.env, secrets).
AI-Authored Likelihood: LOW
