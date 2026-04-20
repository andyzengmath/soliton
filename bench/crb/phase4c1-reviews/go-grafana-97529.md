## Summary
5 files changed, 25 lines added, 57 lines deleted. 5 findings (2 critical, 3 improvements, 0 nitpicks).
No tests for new eager-init path in `NewResourceServer`, and the narrowed `BuildIndex` lock exposes a same-key race on `bleve.New`.

## Critical

:red_circle: [testing] No tests for the new eager-init path in `NewResourceServer` in pkg/storage/unified/resource/server.go:255 (confidence: 95)
`NewResourceServer` now calls `s.Init(ctx)` eagerly, and the 12 per-method `Init` guards on `Create`, `Update`, `Delete`, `Read`, `List`, `Watch`, `Search`, `History`, `Origin`, `IsHealthy`, `PutBlob`, `GetBlob` were removed. This is the highest-risk behavioral change in the PR: a regression in the init path silently affects every resource-server operation. No tests cover (a) `Init` failure propagating out of the constructor, (b) concurrent construction, or (c) context cancellation while `Init` is running. The test file touched in this PR (`module_server_test.go`) is unrelated to this code path.
```suggestion
// pkg/storage/unified/resource/server_test.go
func TestNewResourceServer_InitSuccess(t *testing.T) {
    srv, err := NewResourceServer(validOpts(t))
    require.NoError(t, err)
    require.NotNil(t, srv)
}

func TestNewResourceServer_InitFailurePropagates(t *testing.T) {
    srv, err := NewResourceServer(optsThatFailInit(t))
    require.Error(t, err)
    require.Nil(t, srv)
}

func TestNewResourceServer_ContextCancelledDuringInit(t *testing.T) {
    ctx, cancel := context.WithCancel(context.Background())
    cancel()
    opts := validOpts(t)
    opts.Ctx = ctx
    _, err := NewResourceServer(opts)
    require.Error(t, err)
}
```

:red_circle: [correctness] Narrowed mutex allows concurrent `BuildIndex` calls on the same key to race on the same bleve directory in pkg/storage/unified/search/bleve.go:85 (confidence: 92)
Before this PR, `cacheMu` was held for the entire `BuildIndex` body, which serialized all concurrent calls and prevented two goroutines from simultaneously opening or building the same on-disk index. The PR narrows the lock to only protect the final `b.cache[key] = idx` assignment — this removes per-key serialization entirely. When `search.init()` builds indexes for many namespaces concurrently, two goroutines sharing the same key both pass the cache-miss check (there is no per-key in-progress guard), both call `bleve.New(dir, mapper)` on the identical filesystem directory, and both call `builder(index)` to populate it. `bleve.New` writes metadata and segment files to the directory; running it twice concurrently on the same path races on those files — either corrupting the index or causing the second caller to error. Even if both succeed, document writes are doubled into whichever handle wins the final cache assignment, and the losing handle is never closed, leaking an open bleve index. The stated goal (parallelism across different keys while serializing same-key calls) requires a per-key singleflight or per-key mutex, not global lock removal. No concurrency test was added, so the hazard ships uncovered.
```suggestion
// Use golang.org/x/sync/singleflight keyed on the index path: serializes
// same-key callers while allowing parallel builds across different keys.
sfKey := fmt.Sprintf("%s/%s.%s", key.Namespace, key.Resource, key.Group)
result, err, _ := b.sfGroup.Do(sfKey, func() (interface{}, error) {
    // existing BuildIndex body; keep narrow b.cacheMu around b.cache[key] = idx
    return idx, nil
})
if err != nil {
    return nil, err
}
return result.(resource.ResourceIndex), nil

// Also add TestBuildIndex_ConcurrentCalls_NoDuplicateCreation: spawn N
// goroutines calling BuildIndex with the same key and assert a single
// on-disk directory and zero errors returned.
```

## Improvements

:yellow_circle: [testing] No tests for span-context propagation fix in pkg/storage/unified/resource/search.go:170 (confidence: 88)
The change from `_, span := s.tracer.Start(ctx, ...)` to `ctx, span := s.tracer.Start(ctx, ...)` in `init()`, `build()`, and `sql/backend.go:GetResourceStats` correctly fixes trace propagation so inner spans are nested under the caller's span. Because spans are usually a no-op in tests, nothing prevents a future refactor from silently reverting this fix — there is no compile-time or test-time signal.
```suggestion
// Test using an OTel in-memory span exporter / span recorder.
tp, rec := newTestTracerProvider(t)
tracer := tp.Tracer("test")
rootCtx, rootSpan := tracer.Start(context.Background(), "root")
defer rootSpan.End()

// Invoke the code path under test with rootCtx.
_ = s.build(rootCtx, nsr, 0, 0)

spans := rec.Ended()
require.NotEmpty(t, spans)
require.Equal(t, rootSpan.SpanContext().SpanID(), spans[0].Parent().SpanID(),
    "inner span must be a child of the caller's span")
```

:yellow_circle: [correctness] Reordering `search.init()` before `initWatcher()` may cause a missed-events window in pkg/storage/unified/resource/server.go:300 (confidence: 85)
The previous order was watcher-first, then index build. The new order is index-build first, then watcher. Starting the watcher first means writes observed during the index build are queued on the watcher channel and not missed. Starting the index first leaves a window between the index's last-RV watermark and `initWatcher` subscription during which writes to the store are not observed by either mechanism. Whether this is real data loss depends on whether `initWatcher` replays from a stored watermark (safe) or only delivers events that arrive after subscription (unsafe). If no replay occurs, events written during the index-build phase are permanently missed in the search index until a full re-index. The PR description notes index builds can take a long time, so this gap could span seconds to minutes in production.
```suggestion
// Option A: restore watcher-first ordering so the watcher buffers events
// that arrive during the (potentially long) index build.
if s.initErr == nil {
    s.initErr = s.initWatcher()
}
if s.initErr == nil && s.search != nil {
    s.initErr = s.search.init(ctx)
}

// Option B (if initWatcher cannot replay from a watermark): after
// subscribing, explicitly replay from s.search.LastRV() so the gap is
// always closed. Document the chosen approach in a comment next to Init.
```

:yellow_circle: [security] Path traversal via unvalidated `key.Namespace`/`Resource`/`Group` in bleve index path (pre-existing) in pkg/storage/unified/search/bleve.go:99 (confidence: 85)
The index directory path is built with `filepath.Join(b.opts.Root, key.Namespace, fmt.Sprintf("%s.%s", key.Resource, key.Group))` and passed to `bleve.New(dir, mapper)`, which creates a directory on disk. `filepath.Join` cleans the result but does NOT prevent `..` traversal when individual segments themselves contain `..`. If any of `key.Namespace`, `key.Resource`, or `key.Group` is attacker-controllable (they originate from gRPC request identifiers), an authenticated caller could supply e.g. `Namespace="../../etc"` to cause bleve to create index directories outside `b.opts.Root`, enabling tampering with adjacent tenants' indexes or overwriting unrelated files writable by the server process. **Note:** this vulnerability is pre-existing and not introduced by this PR — only the locking around this code was changed — but the PR touches these lines directly and the lock narrowing increases the frequency/concurrency of calls, widening the exposure window. Flagging here for awareness and potential follow-up.
```suggestion
// Validate each segment against a strict allowlist, and verify the joined
// path stays within b.opts.Root.
var segRe = regexp.MustCompile(`^[a-zA-Z0-9_-]{1,63}$`)
for _, seg := range []string{key.Namespace, key.Resource, key.Group} {
    if !segRe.MatchString(seg) {
        return nil, fmt.Errorf("invalid path segment %q", seg)
    }
}
dir := filepath.Join(b.opts.Root, key.Namespace, fmt.Sprintf("%s.%s", key.Resource, key.Group))
absRoot, err1 := filepath.Abs(b.opts.Root)
absDir, err2 := filepath.Abs(dir)
if err1 != nil || err2 != nil || !strings.HasPrefix(absDir, absRoot+string(os.PathSeparator)) {
    return nil, fmt.Errorf("resolved index path escapes root")
}
```
[References: https://owasp.org/Top10/A01_2021-Broken_Access_Control/, https://cwe.mitre.org/data/definitions/22.html]

## Risk Metadata
Risk Score: 34/100 (MEDIUM) | Blast Radius: ~5-11 importers across unified-storage wiring (estimated, shim repo has no source) | Sensitive Paths: none matched
AI-Authored Likelihood: LOW

(4 additional findings below confidence threshold)
