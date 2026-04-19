## Summary
5 files changed, 25 lines added, 57 lines deleted. 5 findings (1 critical, 4 improvements).
Eager-init of the `ResourceServer` plus finer-grained `bleve` cache locking are good directions, but a concurrent-same-key race in `BuildIndex`, an unexplained reorder of watcher-vs-search init, and a postgres test silently skipped with a bare TODO all need to be addressed before merge.

## Critical

:red_circle: [correctness] Concurrent `BuildIndex` calls with the same key can both open `bleve.New` on the same directory and leak indices in `pkg/storage/unified/search/bleve.go`:85 (confidence: 88)
After dropping the function-wide lock, two goroutines that enter `BuildIndex` with the same `key` can both execute `bleve.New(dir, mapper)` against the same on-disk directory and both write to `b.cache[key]`, producing either a bleve open/write error on the second call or a silently overwritten cache entry whose prior `bleve.Index` is never closed. The motivating "many namespaces in parallel" workload is safe, but nothing in this diff guarantees per-key serialization, so any retry/reindex flow that re-enters for the same namespace regresses from "serialized, correct" to "racey, possibly leaking".
```suggestion
	// Fast path: if another caller has already built this index, return it.
	b.cacheMu.RLock()
	if idx, ok := b.cache[key]; ok {
		b.cacheMu.RUnlock()
		return idx, nil
	}
	b.cacheMu.RUnlock()

	// Serialize build per key so two callers don't both call bleve.New on the
	// same directory. A sync.Map of per-key sync.Once (or singleflight.Group)
	// is sufficient; the coarse lock is only unsafe at BuildIndex granularity,
	// not at per-key granularity.
```
<details><summary>More context</summary>

The PR description frames the old lock as "slowing things down when building many namespaces with high concurrency" — i.e. different keys. That scenario is indeed serialized unnecessarily today, and a finer lock fixes it. The concern is the *same-key* scenario: reindex-on-error, reconnect flows, or a warm namespace being rebuilt while another request triggers it.

Two equivalent fixes:
- `golang.org/x/sync/singleflight` keyed by the composite `key` — deduplicates in-flight builds.
- A per-key `sync.Mutex` stored in a `sync.Map`, acquired before `bleve.New` and released after `b.cache[key] = idx`.

Either one preserves the cross-key parallelism this PR wants while keeping BuildIndex safe against same-key concurrency.
</details>

## Improvements

:yellow_circle: [correctness] Watcher-vs-search init order reversed without rationale — events fired during `search.init` may be missed in `pkg/storage/unified/resource/server.go`:300 (confidence: 72)
The old ordering started `initWatcher` before `search.init`; the new ordering reverses them, so any storage events produced during the (explicitly slow, per this PR's own motivation) search-index build land before the watcher is subscribed and will not be delivered to the index. The PR description does not mention this reorder, so either the intent or the safety argument is missing from the change.
```suggestion
		// initialize the search index first so BuildIndex has a consistent RV
		// snapshot to anchor on, then start the watcher from that RV.
		var startRV int64
		if s.initErr == nil && s.search != nil {
			startRV, s.initErr = s.search.init(ctx)
		}

		// Start watching for changes from the RV the index was built at so we
		// don't drop events that fired during search.init.
		if s.initErr == nil {
			s.initErr = s.initWatcherFrom(startRV)
		}
```
<details><summary>More context</summary>

There are two safe designs:
1. Watcher subscribes first with a pending buffer, then `search.init` drains history up to the watcher's starting RV — old order.
2. `search.init` returns the RV it built from, watcher subscribes from that RV — new order, but only safe if both `search.init` and `initWatcher` agree on the RV handoff.

The diff just swaps the two without introducing the RV handoff, so correctness depends on an invariant (watcher is idempotent against historical events, or `search.init` subscribes to its own internal event stream) that is not visible in the diff. Please either add that argument as a comment or restore the old order.
</details>

:yellow_circle: [correctness] `NewResourceServer` now blocks on full index build and surfaces init errors as constructor errors — contract change not captured in callers in `pkg/storage/unified/resource/server.go`:258 (confidence: 80)
`s.Init(ctx)` is now called synchronously inside the constructor, which turns what used to be a cheap object construction into an operation that can take minutes (the very problem this PR is trying to fix) and can fail for reasons — transient storage unavailability, cold indexes — that the previous lazy path recovered from on the next RPC. The PR author's own reviewer note ("Does anything else depend on initializing US lazily? What could this break?") flags this, and the answer is at minimum: tests that construct a server to exercise a narrow code path, servers that want to accept readiness probes before indexing completes, and any container orchestrator that kills long-running `NewResourceServer` calls because they exceed a startup deadline.
```suggestion
	// Init synchronously so the first RPC doesn't pay for index build under a
	// gRPC deadline. Callers that want lazy init (tests, degraded-mode bring-up)
	// should set opts.SkipInit and call Init themselves.
	if !opts.SkipInit {
		if err := s.Init(ctx); err != nil {
			s.log.Error("error initializing resource server", "error", err)
			return nil, err
		}
	}
```
<details><summary>More context</summary>

Concretely worth checking before merge:
- Does the gRPC server's startup path give `NewResourceServer` a context with a deadline? If yes, long index builds will now fail server startup where they previously failed only on the first request.
- Are there tests that instantiate `NewResourceServer` purely to exercise validation logic? Those will now incur full index build time in the unit test suite.
- Readiness probes: does Kubernetes see a healthy pod during the indexing window? Previously the pod was up but RPCs returned errors; now the pod is down entirely. Depending on your deployment model either is defensible, but the choice should be conscious.

An `opts.SkipInit` or separate `Start(ctx) error` method keeps the new default eager while giving callers an escape hatch.
</details>

:yellow_circle: [testing] Postgres skip with "TODO - fix this test for postgres / Works locally" likely masks a regression introduced by this PR in `pkg/server/module_server_test.go`:35 (confidence: 78)
The only behavioral change in this PR that affects `module_server` startup is moving `ResourceServer` init from lazy to synchronous, which plausibly interacts with postgres-backed test harnesses (connection pool / migration timing) in a way it does not with sqlite. Adding a permanent skip with no linked issue and the phrase "works locally" is exactly the shape of a bug that will be forgotten and rediscovered by on-call; it should be root-caused or at least tracked.
```suggestion
	// TODO(#NNNNN) - re-enable once the synchronous ResourceServer init in
	// NewResourceServer is compatible with Drone's postgres harness. The test
	// passes locally against postgres; it fails in Drone only after we moved
	// search.init inside the constructor.
	if dbType == "postgres" {
		t.Skip("skipping - tracked in #NNNNN")
	}
```
<details><summary>More context</summary>

"Works locally" failing in CI is almost always an environmental timing or fixture-ordering difference. In this PR the natural suspect is: `NewResourceServer` now synchronously builds an index during test setup; postgres connection acquisition in Drone is slower than on a dev machine; a context deadline somewhere (test-wide or connection-wide) fires before indexing completes; the test that previously passed because Init was lazy now hits the failure path.

Before merge, at least one of the following should be true:
- A tracked issue number replaces the bare TODO.
- A short reproduction comment explains the actual failure mode.
- The postgres run is verified to fail for reasons unrelated to this PR.
</details>

:yellow_circle: [consistency] New `search index initialized` log fires on every boot regardless of whether indexing did any work in `pkg/storage/unified/resource/search.go`:216 (confidence: 62)
The added `s.log.Info("search index initialized", "duration_secs", end-start, "total_docs", s.search.TotalDocs())` is placed at the tail of `init` after the goroutine that actually performs the build has been spawned, so `total_docs` is measured *before* the async work finishes and `duration_secs` measures wall-clock between `start := time.Now().Unix()` and this point — not the real indexing duration. The numbers in the logs will be systematically wrong.
```suggestion
	// Wait for the async build to finish before logging, or emit the log
	// inside the goroutine so the reported duration and total_docs reflect
	// actual work done rather than spawn latency.
```
<details><summary>More context</summary>

Looking at the surrounding code (lines 213–220 in the diff): there is a `go func() { … }()` just above the log statement, and the log reads `s.search.TotalDocs()` synchronously after spawning. If the goroutine is the one that populates the index (which the structure suggests), then the log is sampling `TotalDocs()` at t≈0 of the background build.

If the log is intended as a "build kicked off" marker, the wording ("search index initialized") overstates what happened. If it's meant to report completion, it needs to move into the goroutine's tail. Either way, the current placement doesn't match the message.
</details>

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: ResourceServer is the gRPC entry point for unified storage — constructor and init-order changes affect every storage consumer; bleve cache lock change affects every index build | Sensitive Paths: none (no auth/payment/secrets)
AI-Authored Likelihood: LOW
