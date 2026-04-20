## Summary
6 files changed, 199 lines added, 50 lines deleted. 8 findings (3 critical, 5 improvements, 0 nitpicks).
Multi-process flusher refactor introduces shutdown and restart-loop correctness bugs (deadline handling, dict-mutation race) and has large untested surface area in the crash-restart and backpressure paths — fix before merge.

## Critical

:red_circle: [correctness] join() exhausts deadline on next_step, leaves child processes un-terminated in src/sentry/spans/consumers/process/flusher.py:326 (confidence: 97)
`join()` computes `deadline = time.time() + timeout` then immediately calls `self.next_step.join(timeout)` with the full original budget. If `next_step.join` consumes most or all of that budget, `deadline - time.time() <= 0` at the first iteration of the process loop, the `break` fires, and none of the N flusher processes are waited on or terminated. They are left running as orphaned daemons holding Redis connections open and continuing to write to shared `multiprocessing.Value` slots after the consumer has nominally shut down. The `break` also skips termination for every subsequent process in the dict, even for ones that have already exited and would be trivially cleanable.
```suggestion
    def join(self, timeout: float | None = None):
        self.stopped.value = True
        deadline = time.time() + timeout if timeout is not None else None

        self.next_step.join(timeout)

        for process_index, process in list(self.processes.items()):
            remaining = (deadline - time.time()) if deadline is not None else None
            if remaining is not None and remaining <= 0:
                if isinstance(process, multiprocessing.Process):
                    process.terminate()
                continue

            while process.is_alive() and (deadline is None or deadline > time.time()):
                time.sleep(0.1)

            if isinstance(process, multiprocessing.Process):
                process.terminate()
```

:red_circle: [correctness] _ensure_processes_alive mutates self.processes while iterating it in src/sentry/spans/consumers/process/flusher.py:215 (confidence: 91)
The loop `for process_index, process in self.processes.items()` calls `self._create_process_for_shards(process_index, shards)` inside the loop body, which reassigns `self.processes[process_index]` to the freshly-spawned process. In CPython, reassigning an existing key during iteration does not raise, but the behaviour is implementation-defined and fragile. More importantly, when two processes are unhealthy in the same tick, the first restart installs a new process into the dict mid-iteration; on the next iteration the same slot can be re-read and the freshly-started process passes the `is_alive()` check, so the second unhealthy process may have its restart counter not incremented and its crash masked. Snapshot the dict with `list(...)` before iterating.
```suggestion
    def _ensure_processes_alive(self) -> None:
        max_unhealthy_seconds = options.get("spans.buffer.flusher.max-unhealthy-seconds")

        for process_index, process in list(self.processes.items()):
            if not process:
                continue
            ...
```

:red_circle: [testing] _ensure_processes_alive crash-restart path has zero test coverage in src/sentry/spans/consumers/process/flusher.py:215 (confidence: 95)
The entire `_ensure_processes_alive` method — dead-process detection, hang detection, per-process `process_restarts` counter increment, the `MAX_PROCESS_RESTARTS` `RuntimeError`, and the `_create_process_for_shards` restart call — is not exercised by any test in the PR. This is the core safety net of the multi-process design: if any flusher process crashes silently, the consumer will continue running without ever restarting it, data will stop flushing for the affected shards, and no alert will fire. Codecov reports 66% patch coverage with 26 missed lines in flusher.py concentrated here.
```suggestion
def test_ensure_processes_alive_restarts_dead_process():
    messages = []
    buffer = SpansBuffer([0, 1])
    flusher = SpanFlusher(buffer, next_step=mock.MagicMock(), produce_to_pipe=messages.append)

    dead = threading.Thread(target=lambda: None)
    dead.start(); dead.join()
    flusher.processes[0] = dead

    restart_count_before = flusher.process_restarts[0]
    msg = mock.MagicMock(); msg.value.payload = 1
    flusher.submit(msg)

    assert flusher.process_restarts[0] == restart_count_before + 1
    assert flusher.processes[0] is not dead
    assert flusher.processes[0].is_alive()
```

## Improvements

:yellow_circle: [correctness] _create_process_for_shard (singular) is dead code with no caller and no test in src/sentry/spans/consumers/process/flusher.py:190 (confidence: 92)
`_create_process_for_shard(self, shard: int)` is defined but has no caller anywhere in the changed code — `_ensure_processes_alive` calls `_create_process_for_shards` (plural) directly. Unreachable code creates a false restart API that a future monitoring hook or per-shard recovery path could call, where it would silently restart the entire process group for that shard rather than just the failing shard. Either remove it or rename it to reflect the group-restart semantics.
```suggestion
    # Option A: remove the method entirely.
    # Option B: rename to reflect semantics and add a unit test:
    def _restart_process_group_for_shard(self, shard: int) -> None:
        """Restart the entire process group that owns the given shard."""
        for process_index, shards in self.process_to_shards_map.items():
            if shard in shards:
                self._create_process_for_shards(process_index, shards)
                break
```

:yellow_circle: [consistency] Inconsistent metric tag key: `shards` vs `shard` in src/sentry/spans/consumers/process/flusher.py:198 (confidence: 85)
`spans.buffer.flusher.wait_produce` emits `tags={"shards": shard_tag}` (plural) while `spans.buffer.flusher.produce` and `spans.buffer.segment_size_bytes` on the surrounding lines emit `tags={"shard": shard_tag}` (singular), and `spans.buffer.flusher_unhealthy` also uses the singular `shard`. This fragments monitoring dashboards and alert thresholds across two tag keys and will surprise dashboard authors correlating flusher metrics across the pipeline. Pick one and use it everywhere — the prevailing convention in this file is singular `shard`.
```suggestion
                with metrics.timer("spans.buffer.flusher.wait_produce", tags={"shard": shard_tag}):
```

:yellow_circle: [testing] Per-process backpressure in submit() not tested for multi-process scenario in src/sentry/spans/consumers/process/flusher.py:264 (confidence: 90)
The new `submit()` loops over all `process_backpressure_since` values and raises `MessageRejected` if any single process is over the threshold. The only test update is a one-liner in `test_flusher.py` changing `flusher.backpressure_since.value` to `any(x.value for x in flusher.process_backpressure_since.values())`. This confirms a value is set but does not test the critical behavioral property: that backpressure from **one** process blocks **all** submissions while others are healthy. An off-by-one or a refactor to `continue`/`break` would go undetected.
```suggestion
def test_submit_raises_when_one_process_has_backpressure(monkeypatch):
    buffer = SpansBuffer([0, 1, 2, 3])
    flusher = SpanFlusher(buffer, next_step=mock.MagicMock(), max_processes=2,
                          produce_to_pipe=[].append)
    monkeypatch.setattr("sentry.options.get", lambda key: {
        "spans.buffer.flusher.backpressure-seconds": 0,
        "spans.buffer.flusher.max-unhealthy-seconds": 9999,
        "spans.buffer.max-memory-percentage": 1.0,
    }.get(key))
    flusher.process_backpressure_since[0].value = 0
    flusher.process_backpressure_since[1].value = int(time.time()) - 10
    msg = mock.MagicMock(); msg.value.payload = 1
    with pytest.raises(MessageRejected):
        flusher.submit(msg)
```

:yellow_circle: [testing] Memory aggregation across multiple buffers not tested in src/sentry/spans/consumers/process/flusher.py:297 (confidence: 88)
`submit()` now iterates `self.buffers.values()` and calls `buffer.get_memory_info()` on each, aggregating `used` and `available`. With a single buffer (all existing tests), this is identical to the old code. The multi-buffer aggregation path — that pressure from any one buffer contributes to the total and can trigger `MessageRejected` — is untested. A regression where `extend` becomes `append`, or the loop skips index > 0, would not be caught.
```suggestion
def test_submit_memory_backpressure_aggregated_across_buffers(monkeypatch):
    buffer = SpansBuffer([0, 1, 2, 3])
    flusher = SpanFlusher(buffer, next_step=mock.MagicMock(), max_processes=2,
                          produce_to_pipe=[].append)
    healthy = ServiceMemory(used=1, available=100)
    full = ServiceMemory(used=99, available=100)
    flusher.buffers[0].get_memory_info = lambda: [healthy]
    flusher.buffers[1].get_memory_info = lambda: [full]
    monkeypatch.setattr("sentry.options.get", lambda key: {
        "spans.buffer.max-memory-percentage": 0.5,
        "spans.buffer.flusher.max-unhealthy-seconds": 9999,
        "spans.buffer.flusher.backpressure-seconds": 9999,
    }.get(key))
    msg = mock.MagicMock(); msg.value.payload = 1
    with pytest.raises(MessageRejected):
        flusher.submit(msg)
```

:yellow_circle: [testing] join() with timeout and deadline exhaustion across multiple processes not tested in src/sentry/spans/consumers/process/flusher.py:326 (confidence: 85)
Both `test_basic` and `test_flusher_processes_limit` call `step.join()` with no argument, exercising only the `deadline is None` branch. The early-break / per-process `terminate()` paths are uncovered — which is especially consequential given the deadline-exhaustion bug flagged above. A test that calls `join(timeout=<short>)` against multiple live processes would both regress-guard the fix and verify it does not hang.
```suggestion
def test_join_with_timeout_does_not_hang():
    buffer = SpansBuffer([0, 1])
    flusher = SpanFlusher(buffer, next_step=mock.MagicMock(), max_processes=2,
                          produce_to_pipe=[].append)
    start = time.time()
    flusher.join(timeout=0.05)
    elapsed = time.time() - start
    assert elapsed < 1.0
    for p in flusher.processes.values():
        assert not p.is_alive()
```

## Risk Metadata
Risk Score: 62/100 (MEDIUM) | Blast Radius: span ingestion hot path, multi-process shutdown & restart loops, Redis shard ownership — any regression can stall segment flushing or leak processes across consumer restarts | Sensitive Paths: none (no auth/secret/payment surfaces touched)
AI-Authored Likelihood: LOW — style and error-handling shape are consistent with human Python authorship; the only AI-adjacent artifact is the new CLAUDE.md style rule appended in the same PR.

(3 additional findings below confidence threshold 85 suppressed: `max_processes=0` silent coercion via falsy `or` [correctness, 78]; `spans.buffer.flusher.backpressure` metric lacks a shard tag [consistency, 70, nit]; `test_flusher_processes_limit` asserts only internal state, no end-to-end observable output [testing, 80].)
