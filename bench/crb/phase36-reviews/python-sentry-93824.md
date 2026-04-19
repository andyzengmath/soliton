## Summary
6 files changed, 199 lines added, 50 lines deleted. 7 findings (1 critical, 6 improvements).
Multi-process flusher refactor is functional but has a shutdown resource leak, a metric tag typo, a test sleep that is silently a no-op, a dead-code method, and thin test coverage of the new restart/join paths.

## Critical
:red_circle: [correctness] `join()` leaves unreached multiprocessing.Process instances running when the deadline expires mid-loop in src/sentry/spans/consumers/process/flusher.py:329 (confidence: 95)
When the per-process `remaining_time <= 0` check fires, the outer `for` loop executes `break`, so every process not yet reached in `self.processes.items()` never has `process.terminate()` called on it. Daemon processes prevent indefinite hangs at parent exit, but within a graceful-shutdown window (consumer rebalance) multiple flusher processes can linger, hold Redis connections, and produce duplicate segments on the next startup — the old single-process `join()` could not hit this.
```suggestion
        # Wait for all processes up to the deadline
        for process_index, process in self.processes.items():
            if deadline is not None and deadline - time.time() <= 0:
                break
            while process.is_alive() and (deadline is None or deadline > time.time()):
                time.sleep(0.1)

        # Always terminate Process instances regardless of whether the deadline was hit
        for process in self.processes.values():
            if isinstance(process, multiprocessing.Process):
                try:
                    process.terminate()
                except (ValueError, AttributeError):
                    pass
```

## Improvements
:yellow_circle: [consistency] Metric tag key typo: `shards` (plural) in `wait_produce` timer while every sibling metric uses `shard` (singular) in src/sentry/spans/consumers/process/flusher.py:196 (confidence: 95)
The `spans.buffer.flusher.wait_produce` timer is tagged `{"shards": shard_tag}` but `spans.buffer.flusher.produce`, `spans.buffer.segment_size_bytes`, and `spans.buffer.flusher_unhealthy` all use `{"shard": ...}`. Per-shard dashboards and alerts grouped on `shard` will silently drop wait-produce latency, masking a primary signal for the new multi-process topology.
```suggestion
                with metrics.timer("spans.buffer.flusher.wait_produce", tags={"shard": shard_tag}):
```

:yellow_circle: [testing] `time.sleep(0.1)` added to fix flusher-drift timing is a no-op because `time.sleep` is already monkey-patched in tests/sentry/spans/consumers/process/test_consumer.py:60 (confidence: 95)
The test body runs `monkeypatch.setattr("time.sleep", lambda _: None)` near the top of `test_basic`, so the newly added `time.sleep(0.1)` commented "Give flusher threads time to process after drift change" returns immediately without waiting. Whatever flake motivated the change is unfixed and will resurface under CI load, and reviewers will trust a non-existent synchronization barrier.
```suggestion
    # Patch only the flusher module's time.sleep so the test body can still sleep
    monkeypatch.setattr("sentry.spans.consumers.process.flusher.time.sleep", lambda _: None)
```
<details><summary>More context</summary>

Alternative: keep the broad monkeypatch but replace the sleep with a bounded polling loop keyed off an observable condition (e.g. `messages` length or a per-process `healthy_since` advancing) so the test waits on actual progress rather than wall-clock time.
</details>

:yellow_circle: [correctness] `MessageRejected` raised inside the backpressure loop causes `spans.buffer.flusher.backpressure` to undercount when multiple processes are simultaneously backpressured in src/sentry/spans/consumers/process/flusher.py:283 (confidence: 88)
The `for backpressure_since in self.process_backpressure_since.values()` loop raises on the first match, so the metric increments by 1 per submit call regardless of how many processes are pinned — N backpressured processes look identical to 1 in alerting. This hides fan-out failures that the multi-process topology specifically introduces.
```suggestion
        backpressure_secs = options.get("spans.buffer.flusher.backpressure-seconds")
        backpressured_count = 0
        for backpressure_since in self.process_backpressure_since.values():
            if (
                backpressure_since.value > 0
                and int(time.time()) - backpressure_since.value > backpressure_secs
            ):
                backpressured_count += 1
        if backpressured_count > 0:
            metrics.incr("spans.buffer.flusher.backpressure", amount=backpressured_count)
            raise MessageRejected()
```

:yellow_circle: [correctness] `_create_process_for_shard` (singular) is dead code in src/sentry/spans/consumers/process/flusher.py:118 (confidence: 90)
The new method walks `process_to_shards_map` to find a shard's owning process and calls `_create_process_for_shards`, but there is no caller — `_ensure_processes_alive` already has `(process_index, shards)` in hand and invokes the plural variant directly. Its presence falsely implies per-shard restart granularity and, if ever called, it silently no-ops on an unknown shard.
```suggestion
    # Delete _create_process_for_shard entirely — never invoked.
```

:yellow_circle: [testing] Process-restart path in `_ensure_processes_alive` has no coverage in src/sentry/spans/consumers/process/flusher.py:220 (confidence: 88)
None of the new branches — dead-process restart, hang detection (`cause == "hang"`), or the `RuntimeError` raised once `process_restarts[i] > MAX_PROCESS_RESTARTS` — are exercised by any test in this PR. A regression in the restart bookkeeping (wrong key, lost counter, missing shard mapping update) would ship unobserved.
```suggestion
# Add thread-mode tests using produce_to_pipe: substitute a dead mock
# Thread/Process into flusher.processes[i], call _ensure_processes_alive(),
# assert a fresh live process is installed under the same index and that
# RuntimeError is raised once process_restarts[i] exceeds MAX_PROCESS_RESTARTS.
```

:yellow_circle: [testing] `test_flusher_processes_limit` verifies data-structure shape only, never asserts that flushing works across the mapped shards in tests/sentry/spans/consumers/process/test_consumer.py:414 (confidence: 92)
The test checks `len(flusher.processes) == 2`, `flusher.max_processes == 2`, `flusher.num_processes == 2`, and `total_shards == 4`, then calls `step.join()` without ever submitting spans or asserting on `messages` or `commits`. A bug that drops shard 3 from `process_to_shards_map` or routes its work to the wrong buffer would pass the test unchanged.
```suggestion
    # After the structural asserts, submit one span per partition,
    # advance drift, poll, and assert end-to-end delivery:
    fac._flusher.current_drift.value = 9000
    step.poll()
    step.join()
    assert commits, "flusher produced no commits"
    assert len(messages) >= 4, f"expected >=4 produced messages, got {len(messages)}"
```

## Risk Metadata
Risk Score: 34/100 (MEDIUM) | Blast Radius: spans ingestion hot path (consumer registry + factory + flusher), no external importers removed | Sensitive Paths: none
AI-Authored Likelihood: LOW

(5 additional findings below confidence threshold)
