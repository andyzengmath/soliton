## Summary
6 files changed, 199 lines added, 50 lines deleted. 6 findings (0 critical, 6 improvements).
Multiprocessed flusher refactor is largely sound, but a handful of concurrency, observability, and stale-reference issues should be addressed before merge.

## Improvements

:yellow_circle: [cross-file-impact] `test_flusher.py` lines 1–79 may still reference removed `SpanFlusher` attributes in tests/sentry/spans/consumers/process/test_flusher.py:1 (confidence: 85)
The diff only updates a single line at the end of `test_flusher.py` (`flusher.backpressure_since.value` → `any(x.value for x in flusher.process_backpressure_since.values())`). The remaining 79 lines of the test file are not shown by the diff. The PR removes/renames several public-ish instance attributes — `flusher.buffer` → `flusher.buffers` (dict), `flusher.process` → `flusher.processes` (dict), `flusher.healthy_since` → `flusher.process_healthy_since` (dict), and `flusher.process_restarts` changed from `int` to `dict[int, int]`. A flusher test of 80+ lines almost certainly asserts on at least one of those, so the partial rename sweep is a likely source of `AttributeError`/`TypeError` once the test runs against the new code. Verify the full file and update any stale references.
```suggestion
# in tests/sentry/spans/consumers/process/test_flusher.py — typical fixups
# flusher.backpressure_since         -> any(x.value for x in flusher.process_backpressure_since.values())
# flusher.healthy_since.value        -> flusher.process_healthy_since[0].value
# flusher.process                    -> flusher.processes[0]
# flusher.buffer                     -> flusher.buffers[0]
# flusher.process_restarts (int)     -> flusher.process_restarts[0]   # now a dict
```

:yellow_circle: [correctness] Backpressure loop raises on first stuck process and silently skips remaining processes in src/sentry/spans/consumers/process/flusher.py:332 (confidence: 88)
The new loop `for backpressure_since in self.process_backpressure_since.values()` calls `metrics.incr("spans.buffer.flusher.backpressure")` and then `raise MessageRejected()` inside the body, so iteration terminates on the first backpressured process. With N flusher processes simultaneously congested, only one backpressure metric event is emitted per `submit()` call, making the metric systematically undercount the true number of stuck shards. The single-process predecessor never had this problem because there was only one value to check. This is an observability regression that will hide the scope of incidents from on-call.
```suggestion
backpressure_secs = options.get("spans.buffer.flusher.backpressure-seconds")
any_backpressure = False
for backpressure_since in self.process_backpressure_since.values():
    if (
        backpressure_since.value > 0
        and int(time.time()) - backpressure_since.value > backpressure_secs
    ):
        metrics.incr("spans.buffer.flusher.backpressure")
        any_backpressure = True
if any_backpressure:
    raise MessageRejected()
```

:yellow_circle: [correctness] `time.sleep(0.1)` in `test_basic` is a no-op due to the global `time.sleep` monkeypatch, leaving the test reliant on a thread-scheduling race in tests/sentry/spans/consumers/process/test_consumer.py:60 (confidence: 88)
At the top of `test_basic`, `monkeypatch.setattr("time.sleep", lambda _: None)` patches the real `time.sleep` for the duration of the test. The new line `time.sleep(0.1)  # Give flusher threads time to process after drift change` therefore does nothing — `time.sleep` resolves to the same patched no-op via `import time`. The test depends on the background flusher threads happening to be scheduled and complete a flush iteration between `step.poll()` and `step.join()` without any deliberate yield. This is a flake source under loaded CI runners. Either patch `time.sleep` only inside the flusher module, or use a real synchronization primitive.
```suggestion
# Patch the flusher-internal sleep only, leave the test's own sleep real:
monkeypatch.setattr("sentry.spans.consumers.process.flusher.time.sleep", lambda _: None)

# ...later...
step.poll()
time.sleep(0.1)  # now this is a real wait that yields to flusher threads
step.join()
```

:yellow_circle: [correctness] `_create_process_for_shard` (singular) is dead code — never called in src/sentry/spans/consumers/process/flusher.py:120 (confidence: 85)
A new helper `_create_process_for_shard(self, shard: int)` is introduced but has no caller anywhere in the diff. Both `_create_processes()` and `_ensure_processes_alive()` call `_create_process_for_shards` (plural) directly with `(process_index, shards)`. In a multiprocessing supervisor, an unused-but-plausible-looking restart helper is a maintenance trap: a future contributor may call it and end up double-restarting a process group. Either remove it, or add a real call site (e.g., partition-rebalance event) and tests.
```suggestion
# Remove _create_process_for_shard entirely — _ensure_processes_alive
# already restarts at the process-group granularity via _create_process_for_shards.
```

:yellow_circle: [consistency] Class docstring claims "one process per shard" but the actual fan-out is `min(max_processes, num_shards)` in src/sentry/spans/consumers/process/flusher.py:30 (confidence: 90)
The updated docstring says "Creates one process per shard for parallel processing." The implementation sets `self.num_processes = min(self.max_processes, len(buffer.assigned_shards))` and then round-robins shards across that many processes. With the default `--flusher-processes=1`, a single process handles all assigned shards — i.e., the default is essentially the old single-process behavior, not "one process per shard". The docstring should describe the actual policy.
```suggestion
"""
A background multiprocessing manager that polls Redis for new segments to flush
and to produce to Kafka. Spawns up to ``max_processes`` worker processes and
distributes the consumer's assigned shards across them round-robin (each shard
is owned by exactly one process). Defaults to a single process for all shards.
"""
```

:yellow_circle: [consistency] Comment "Update healthy_since for all shards handled by this process" misdescribes a single shared `Value` write in src/sentry/spans/consumers/process/flusher.py:178 (confidence: 85)
The comment implies per-shard updates, but `healthy_since.value = system_now` writes one timestamp shared by every shard in this process. Liveness is therefore tracked at process granularity, not shard granularity — which matches how `_ensure_processes_alive` consumes it (per-process), but the comment misleads a reader into thinking shard-level health is recorded.
```suggestion
# Update this process's heartbeat. All shards owned by the process share the
# same liveness signal — they live or die together with their owning process.
healthy_since.value = system_now
```

## Risk Metadata
Risk Score: 60/100 (MEDIUM) | Blast Radius: production span ingestion consumer; multiprocess supervision in `flusher.py` (+127 / -47), CLI surface in `consumers/__init__.py`, factory wiring in `factory.py`. | Sensitive Paths: none matched.
AI-Authored Likelihood: LOW

(7 additional findings below confidence threshold)
