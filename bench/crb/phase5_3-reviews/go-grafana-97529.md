## Summary
5 files changed, 25 lines added, 57 lines deleted. 2 findings (1 critical, 1 improvement, 0 nitpicks).
Move unified-storage init from lazy gRPC-time to startup, fix tracer ctx propagation, and shrink the bleve cache lock to allow parallel namespace indexing — concurrency change introduces a same-key race and the init reorder opens an event-loss window.

## Critical
:red_circle: [correctness] Same-key concurrent `BuildIndex` race after lock-scope reduction in `pkg/storage/unified/search/bleve.go`:85 (confidence: 88)
The previous code held `b.cacheMu` for the entire `BuildIndex` body, which serialized index construction. The new code only locks around the final `b.cache[key] = idx` write. That makes parallel builds across distinct keys safe (the stated goal), but two concurrent `BuildIndex` calls with the **same** `key` now race on the directory path `dir = filepath.Join(b.opts.Root, key.Namespace, fmt.Sprintf("%s.%s", key.Resource, key.Group))`. Both goroutines hit `bleve.New(dir, mapper)` — bleve refuses to create an index over an existing on-disk path, so one returns an error and the other proceeds; meanwhile the cache write is now last-writer-wins, so an in-flight builder for the loser may continue writing documents into a `bleve.Index` that has been orphaned out of `b.cache` (file handle / disk leak), and `IndexMetrics.IndexTenants` is double-incremented for the same key. For the memory-only branch (`size <= FileThreshold`), no directory collision protects you: both goroutines build full indexes, both invoke `builder()` (likely re-reading from the SQL backend), and one result is silently discarded. Either keep the wide lock with a per-key `singleflight.Group` (preferred — preserves the parallelism win without re-introducing serialization), or check-and-set in the cache under the narrow lock before doing any `bleve.New(...)` work.
```suggestion
func (b *bleveBackend) BuildIndex(ctx context.Context,
	key resource.NamespacedResource,
	size int64,
	mapper mapping.IndexMapping,
	builder func(index resource.ResourceIndex) (int64, error),
) (resource.ResourceIndex, error) {
	_, span := b.tracer.Start(ctx, tracingPrexfixBleve+"BuildIndex")
	defer span.End()

	v, err, _ := b.buildGroup.Do(key.String(), func() (any, error) {
		// existing build logic...
		// final cache write still under b.cacheMu
		return idx, nil
	})
	if err != nil {
		return nil, err
	}
	return v.(*bleveIndex), nil
}
```

## Improvements
:yellow_circle: [correctness] Watcher subscription moved after `search.init` opens an event-loss window in `pkg/storage/unified/resource/server.go`:300 (confidence: 85)
The init sequence was reordered from `initWatcher` → `search.init` to `search.init` → `initWatcher`. The PR's own description states the index build is the slow path ("the index takes too long to build within the context of the gRPC call"). With the new ordering, any resource mutations that land between the moment `search.init` snapshots a starting RV and the moment `initWatcher` actually subscribes to the change stream are not delivered to `searchSupport.handleEvent`, so the in-memory index will silently lag the source of truth until the next full rebuild. The previous order (subscribe first, then build) was the standard "watch + list" pattern that protects against exactly this race — events arriving during the build are buffered/applied against the just-built snapshot. Restore the original ordering, or have `search.init` capture the starting RV first, then `initWatcher` subscribe from that RV before the index build runs, so no commits in the gap are dropped.
```suggestion
		// Start watching for changes BEFORE building the index so events
		// that arrive during the (potentially long) index build are not lost.
		if s.initErr == nil {
			s.initErr = s.initWatcher()
		}

		// initialize the search index
		if s.initErr == nil && s.search != nil {
			s.initErr = s.search.init(ctx)
		}
```

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: 5 files in `pkg/storage/unified/**` + 1 server-startup test; touches concurrency primitives (bleve cache mutex), startup-init ordering, and gRPC handler entry guards | Sensitive Paths: `pkg/storage/unified/resource/server.go` (storage server), `pkg/storage/unified/search/bleve.go` (index lifecycle)
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold)
