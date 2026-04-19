## Summary
5 files changed, 25 lines added, 57 lines deleted. 9 findings (3 critical, 3 improvements, 3 nitpicks).
Moves `ResourceServer` init from per-request to constructor, reworks `bleve.BuildIndex` locking, and fixes trace propagation — the locking change and init-order swap introduce subtle concurrency/lifecycle risks that are not covered by new tests.

## Critical

:red_circle: [correctness] `bleve.BuildIndex` races and duplicates work for concurrent calls with the same key in pkg/storage/unified/search/bleve.go:85 (confidence: 92)
The old code held `b.cacheMu` for the entire function, serializing all builds per backend. The new code releases the lock across the `bleve.New(...)` / `bleve.NewMemOnly(...)` / `builder(index)` path and only re-acquires it for the `b.cache[key] = idx` write. Consequences when two callers request the same `key` concurrently:
1. Both enter `bleve.New(dir, mapper)` pointing at the *same* filesystem path, so the second open either fails or corrupts the first index's on-disk state.
2. `resource.IndexMetrics.IndexTenants.WithLabelValues(key.Namespace, "file").Inc()` fires twice for a single tenant.
3. One of the two `idx` values overwrites the other in `b.cache[key]`; the loser leaks (no `Close()`).
4. If any caller reads `b.cache[key]` without holding `b.cacheMu`, the combined read+write is a Go map data race (will panic under `-race`).

Introduce per-key deduplication (e.g. `golang.org/x/sync/singleflight` keyed by `key`) or a two-step "check cache under lock → build outside lock → re-check cache before insert" pattern. Also verify every reader of `b.cache` takes `b.cacheMu`.
```suggestion
// Sketch — adapt types/signatures to your style.
func (b *bleveBackend) BuildIndex(ctx context.Context, key resource.NamespacedResource, size int64, ...) (resource.ResourceIndex, error) {
    _, span := b.tracer.Start(ctx, tracingPrexfixBleve+"BuildIndex")
    defer span.End()

    // Fast path: already cached.
    b.cacheMu.RLock()
    if idx, ok := b.cache[key]; ok {
        b.cacheMu.RUnlock()
        return idx, nil
    }
    b.cacheMu.RUnlock()

    // Dedup concurrent builds for the same key.
    v, err, _ := b.buildGroup.Do(cacheKey(key), func() (any, error) {
        // ... existing build logic (bleve.New / NewMemOnly, builder(index)) ...
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
[References: https://pkg.go.dev/golang.org/x/sync/singleflight, https://go.dev/blog/maps#concurrency]

:red_circle: [correctness] Init-order swap in `server.Init()` can drop events between snapshot and watcher-start in pkg/storage/unified/resource/server.go:302 (confidence: 80)
The old order was `initWatcher()` → `search.init(ctx)`. The new order reverses these so the search index is built first, then the watcher is attached. Unless `search.init()` captures the snapshot resource-version (RV) and `initWatcher()` resumes from that RV, any resource events that land in the window `(snapshot_rv, watcher_start_rv]` are permanently missing from the index until the next full rebuild.

The PR description argues the reverse order is fine, but there is no test asserting the "no lost events" invariant, and the stated motivation ("init was slow inside gRPC calls") does not require changing the relative order of watcher vs. index init. If the original order was chosen deliberately to ensure watcher-before-snapshot semantics, this is a regression.

Please either (a) restore the original order, or (b) document and test the RV-handoff contract in `search.init()` so a reviewer can confirm no events are dropped.
```suggestion
// initialize the search index (captures snapshot RV internally)
if s.initErr == nil && s.search != nil {
    s.initErr = s.search.init(ctx)
}

// Start watching for changes *after* confirming the snapshot RV is recorded
// so no events in (snapshot_rv, now] are missed. If that contract is not
// enforced by search.init, restore the original ordering instead.
if s.initErr == nil {
    s.initErr = s.initWatcher()
}
```
[References: https://kubernetes.io/docs/reference/using-api/api-concepts/#resource-versions]

:red_circle: [correctness] `NewResourceServer` calls `s.Init(ctx)` with the caller-supplied context, which can leave the server permanently broken if that ctx is cancelled mid-init in pkg/storage/unified/resource/server.go:258 (confidence: 85)
`Init` uses `s.initOnce.Do`, so once the first (failed) invocation runs, `s.initErr` is sticky for the lifetime of this instance. If the caller of `NewResourceServer` passes a request-scoped or startup-timeout context that expires before the index finishes building, the server is now unusable *and* the constructor returns an error — but any code path that might retry with a longer context is foreclosed.

The PR description explicitly asks "What could this break?" — this is one such case. Use a lifecycle context for Init inside the constructor; propagate the caller ctx only for tracing.
```suggestion
// Run startup init under the server's own lifecycle context.
// The caller ctx controls tracing/logging, not cancellation, during bootstrap.
initCtx := trace.ContextWithSpanContext(context.Background(), trace.SpanContextFromContext(ctx))
if err := s.Init(initCtx); err != nil {
    s.log.Error("error initializing resource server", "error", err)
    return nil, err
}
```
[References: https://pkg.go.dev/context#WithCancel]

## Improvements

:yellow_circle: [correctness] Handlers that lost their `s.Init(ctx)` guard no longer surface `s.initErr` in pkg/storage/unified/resource/server.go:916 (confidence: 75)
`Search`, `History`, `Origin`, `IsHealthy`, `PutBlob`, `GetBlob`, `Create`, `Update`, `Delete`, `Read`, `List`, and `Watch` all dropped their `if err := s.Init(ctx); err != nil { return nil, err }` guard. The reasoning is that `NewResourceServer` now fails fast if `Init` fails. That's sound as long as `Init` is truly "all-or-nothing" — but `server.Init` sets `s.initErr` and keeps going; it does not set `s.search = nil` or similar. So `Search`'s subsequent `if s.search == nil { return ..., "search index not configured" }` check only covers the "never configured" case, not "configured but failed to initialize."

Either (a) have `server.Init` null out half-built subsystems on error so the nil-checks in handlers remain correct, or (b) add an `if s.initErr != nil { return nil, s.initErr }` short-circuit at the top of each handler. Option (a) is cleaner and keeps handlers allocation-free.
```suggestion
// In server.Init, on error make the broken subsystems observably nil:
if s.initErr != nil {
    s.log.Error("error initializing resource server", "error", s.initErr)
    if errors.Is(s.initErr, searchInitErr) {
        s.search = nil
    }
    return s.initErr
}
```

:yellow_circle: [correctness] Trace ctx reassignment in `searchSupport.init` leaks a potentially-ended span into the async indexing goroutine in pkg/storage/unified/resource/search.go:173 (confidence: 70)
Changing `_, span := s.tracer.Start(ctx, ...)` to `ctx, span := s.tracer.Start(ctx, ...)` is the right fix for child-span propagation within the synchronous body. But `init()` also spawns a goroutine (`go func() { ... }` around line 213) that now inherits this `ctx`. The parent function returns, `defer span.End()` fires, and the goroutine continues to create child spans under an already-ended parent. In most OTel SDKs that still works but shows up as orphaned/truncated spans in the backend.

Detach the goroutine from the parent span (`trace.ContextWithSpan(context.Background(), span)` is usually wrong too — use a fresh span/context for the async phase, or call `span.End()` explicitly once the goroutine is done).
```suggestion
// For the async phase, start a new root span tied to the tracer, not the
// constructor's span which will have ended by the time the goroutine runs.
go func() {
    asyncCtx, asyncSpan := s.tracer.Start(context.Background(), tracingPrexfixSearch+"InitAsync")
    defer asyncSpan.End()
    // ... existing goroutine body, using asyncCtx ...
}()
```
[References: https://opentelemetry.io/docs/languages/go/instrumentation/#creating-spans]

:yellow_circle: [testing] No regression tests for the three substantive changes in pkg/storage/unified/resource/server.go:0 (confidence: 88)
The PR changes three behaviors that are easy to get wrong and hard to catch in production:
1. `bleve.BuildIndex` under concurrent same-key load (a `t.Parallel()` / goroutine fan-out test would expose the duplicate-build race).
2. `NewResourceServer` failure path (stub a search backend that fails `init`; assert the constructor returns error and the returned server is nil).
3. Init ordering invariant (write an event that arrives between `search.init` returning and `initWatcher` attaching; assert it ends up in the index — this may already fail).

None of these are exercised by the one test-file change in the PR (which only adds a skip). Please add at least the concurrent-build test before merging.

## Nitpicks

:white_circle: [testing] `module_server_test.go` adds a `Skip` with a bare `TODO - fix this test for postgres` and "Works locally" in pkg/server/module_server_test.go:35 (confidence: 95)
A "works on my machine" skip with no linked issue tends to be permanent. Add a link to the tracking issue so future maintainers can find context and so this doesn't silently rot.

:white_circle: [consistency] Log line demoted to bare comment loses operational signal in pkg/storage/unified/search/bleve.go:99 (confidence: 80)
The old `b.log.Info("TODO, check last RV so we can see if the numbers have changed", "dir", dir)` at least emitted the `dir` to the log when an on-disk index was created. The replacement is a bare `// TODO, check last RV so we can see if the numbers have changed` comment — same TODO, no runtime visibility. If the intent is to reduce log noise, drop the comment entirely; if the intent is to signal the open item, keep the log at `Debug` level.

:white_circle: [consistency] Removal of the global `cmd/grafana-cli/logger` import from `search.go` is good cleanup in pkg/storage/unified/resource/search.go:11 (confidence: 90)
Replacing `logger.Warn(...)` with the struct-scoped `s.log.Warn(...)` is the right move. Consider grepping the rest of `pkg/storage/unified/...` for other stray `cmd/grafana-cli/logger` imports from non-CLI code paths and cleaning them up in a follow-up.

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: storage/unified core (ResourceServer is the single gRPC entrypoint for unified storage) + bleve index backend, 5 files touched across 3 concurrency-sensitive subsystems | Sensitive Paths: none matching auth/secret patterns, but lifecycle + locking code is security-adjacent (indexing correctness determines search results and, transitively, access decisions in downstream consumers)
AI-Authored Likelihood: LOW (concrete Drone/postgres context in the skip, author-flagged uncertainty in the PR description, in-place tech-debt TODOs, and domain-specific reasoning about tracing and lock contention — all consistent with human authorship)

## Notes

- Existing reviews on the PR (owensmallwood, leonorfmartins, scottlepp, ryantxu) are mostly approvals with one comment questioning why lazy init existed in the first place. A prior automated "approved" comment from `mfeuerstein` dated 2026-04-10 with generic "looks good" per-file summaries provides no evidence-backed analysis and should not be treated as signal.
- Recommendation: **needs-discussion** — the bleve locking change and the init-order swap warrant explicit confirmation (ideally with tests) before merging. The rest is fine.
