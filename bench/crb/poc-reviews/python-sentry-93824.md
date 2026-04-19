# PR Review: getsentry/sentry#93824 — ref(span-buffer): Introduce multiprocessed flusher

## Summary
6 files changed, 199 lines added, 50 lines deleted. 11 findings (4 critical, 6 improvements, 1 nitpick).
Refactor of `SpanFlusher` from a single background thread to one process per Redis shard (capped by `--flusher-processes`). Core design is sound and upstream maintainers approved, but the PR ships a concrete `join()` cleanup bug, a dead restart helper, several silent-fallback edge cases, and leaves critical new concurrency paths (restart, multi-process backpressure, deadline-bounded join) without tests.

## Critical

:red_circle: [correctness] `terminate()` never called for processes 2..N when deadline expires during process 0's wait in src/sentry/spans/consumers/process/flusher.py:354 (confidence: 92)
The new `join()` gives each process a slice of the remaining deadline, but the `process.terminate()` call is inside the `for` loop body, and the `break` on `remaining_time <= 0` exits the whole `for` loop rather than just the inner wait. Scenario: 3 processes, 30 s timeout. Process 0 hangs for 30 s → its `while` falls through and `terminate()` runs correctly for index 0. Then for index 1, `remaining_time <= 0` fires `break` — process 1 and 2 never receive `terminate()`. Daemon=True eventually reaps them when the parent exits, but clean shutdown is lost and orphaned children can keep holding Redis connections / producing to Kafka after the consumer believes it shut down.
```suggestion
for process_index, process in self.processes.items():
    if deadline is not None:
        remaining_time = deadline - time.time()
        if remaining_time > 0:
            while process.is_alive() and deadline > time.time():
                time.sleep(0.1)
    else:
        while process.is_alive():
            time.sleep(0.1)

    if isinstance(process, multiprocessing.Process):
        process.terminate()
```

:red_circle: [cross-file-impact] Removed public attributes may break callers outside this diff in src/sentry/spans/consumers/process/flusher.py:94 (confidence: 90)
Scalar attributes `self.buffer`, `self.backpressure_since`, `self.healthy_since`, `self.process` (singular), and `self.process_restarts` (as `int`) were removed/retyped. `test_flusher.py` in this PR is patched from `flusher.backpressure_since.value` → `any(x.value for x in flusher.process_backpressure_since.values())`, confirming the removal is load-bearing. The shim context here does not include the full sentry tree, but any other devserver script, health-check endpoint, metric exporter, or operational tooling referencing the removed attributes will raise `AttributeError` at runtime.
Evidence: `assert flusher.backpressure_since.value` was a valid test assertion pre-PR (tests/sentry/spans/consumers/process/test_flusher.py:83).
References: Before merging run across the full repo — `grep -rn "\.backpressure_since\|\.healthy_since\|flusher\.process\b\|flusher\.buffer\b\|process_restarts\b" src/ tests/` — and verify only the files in this PR hit.

:red_circle: [cross-file-impact] `SpanFlusher.main` signature reorder: multiprocessing path (run_with_initialized_sentry) binding not verified from diff in src/sentry/spans/consumers/process/flusher.py:155 (confidence: 85)
`main` now takes `shards: list[int]` as the second positional parameter. Threading branch uses `partial(SpanFlusher.main, shard_buffer)` which pre-binds `buffer`, so `args=(shards, ...)` resolves correctly. Multiprocessing branch uses `run_with_initialized_sentry(...)` — the diff only shows the final argument changing from `self.buffer` to `shard_buffer` and does not reveal the wrapper's binding semantics. If `run_with_initialized_sentry` does not pre-bind `buffer` the same way `partial` does, `shards` will land in the `buffer` slot when the child process invokes `main`, causing a `TypeError` or silent type confusion in production (the non-test path). Tests use the threading branch via `produce_to_pipe=...`, so this code path is not exercised by CI.
References: Verify `run_with_initialized_sentry` in `src/sentry/utils/arroyo.py` binds `shard_buffer` before forwarding `args`.

:red_circle: [testing] Critical new concurrency paths have zero direct coverage in src/sentry/spans/consumers/process/flusher.py:251 (confidence: 93)
Three new behaviours introduced by this PR have no tests exercising them:
1. **Process restart path**: `_ensure_processes_alive` detects a dead/hung process, increments `process_restarts[process_index]`, kills, and calls `_create_process_for_shards` to recreate. The per-process restart counter, `ValueError`/`AttributeError` catch on `kill()`, and `MAX_PROCESS_RESTARTS` threshold per-process are untested. A regression (e.g. wrong shards re-assigned, counter never incremented) would be silent.
2. **Multi-process backpressure rejection**: `submit()` now iterates `self.process_backpressure_since.values()` and raises `MessageRejected` if any process is stale. Existing `test_flusher.py` only updates the single-assertion line to use `any(...)`. It does not test the case where process 0 is healthy while process 1 is in backpressure, nor the inverse (no processes backpressured → no exception).
3. **Deadline-exhaustion in `join()`**: The critical-1 bug above cannot be regression-tested because no test covers the scenario "one process hangs past the deadline; do the others still get terminated?"
```suggestion
# tests/sentry/spans/consumers/process/test_flusher.py
def test_restart_on_hang(monkeypatch, flusher_factory):
    flusher = flusher_factory(max_processes=2, shards=[0, 1, 2, 3])
    flusher.process_healthy_since[0].value = 0  # ancient
    flusher.submit(make_msg())
    assert flusher.process_restarts[0] == 1
    assert flusher.processes[0].is_alive()

def test_backpressure_on_single_process(flusher_factory):
    flusher = flusher_factory(max_processes=2, shards=[0, 1])
    flusher.process_backpressure_since[0].value = int(time.time()) - 9999
    with pytest.raises(MessageRejected):
        flusher.submit(make_msg())

def test_join_terminates_all_despite_hung_first(flusher_factory, monkeypatch):
    flusher = flusher_factory(max_processes=2, shards=[0, 1], produce_to_pipe=[].append)
    monkeypatch.setattr(flusher.processes[0], "is_alive", lambda: True)
    flusher.join(timeout=0.05)  # must return quickly AND touch both processes
```

## Improvements

:yellow_circle: [correctness] `_create_process_for_shard` (singular) is dead code — never called in src/sentry/spans/consumers/process/flusher.py:190 (confidence: 85)
Defined but has no call sites. `_ensure_processes_alive` calls `_create_process_for_shards` (plural) directly. If it was staged for future use (external shard-level restart trigger), note that it calls `_create_process_for_shards` which resets `process_healthy_since` for the entire process group, not the single shard — confusing semantics for callers. Either delete or wire it up and test it.
```suggestion
# Remove the method entirely unless a concrete caller exists:
# def _create_process_for_shard(self, shard: int): ...
```

:yellow_circle: [correctness] `max_processes=0` silently falls back to one-process-per-shard in src/sentry/spans/consumers/process/flusher.py:103 (confidence: 88)
`self.max_processes = max_processes or len(buffer.assigned_shards)` — `0` is falsy, so `--flusher-processes 0` evaluates to the per-shard fallback instead of either failing or truly disabling. The click option has no `min` validator. This is hard to debug because the "cap" is silently ignored.
```suggestion
# flusher.py
self.max_processes = max_processes if max_processes is not None else len(buffer.assigned_shards)

# consumers/__init__.py
click.Option(
    ["--flusher-processes", "flusher_processes"],
    default=1,
    type=click.IntRange(min=1),
    help="Maximum number of processes for the span flusher. Must be >= 1.",
),
```

:yellow_circle: [correctness] Empty `assigned_shards` produces `num_processes=0` with silent no-op flusher in src/sentry/spans/consumers/process/flusher.py:118 (confidence: 80)
`num_processes = min(self.max_processes, len(buffer.assigned_shards))` is 0 when shards are empty. Dict comprehensions produce empty maps, `_create_processes` loops zero times, and `submit()` runs with no background flush — spans written to Redis are never forwarded to Kafka. No log, metric, or assertion signals this misconfiguration.
```suggestion
self.num_processes = min(self.max_processes, len(buffer.assigned_shards))
if self.num_processes == 0:
    raise ValueError(
        "SpanFlusher initialized with no assigned shards — "
        "buffer.assigned_shards must be non-empty"
    )
```

:yellow_circle: [consistency] Metric tag naming: `"shard"` vs `"shards"` in src/sentry/spans/consumers/process/flusher.py:226 (confidence: 85)
Line 226: `tags={"shard": shard_tag}` for `spans.buffer.flusher.produce`. Line 242: `tags={"shards": shard_tag}` for `spans.buffer.flusher.wait_produce`. The `shard_tag` value is a comma-separated list (multi-shard per process), so plural is semantically correct — but the inconsistency fragments dashboard queries across the two metrics. Also inconsistent with `_ensure_processes_alive` which emits one metric per shard with singular key `{"shard": shard}`.
```suggestion
with metrics.timer("spans.buffer.flusher.produce", tags={"shards": shard_tag}):
```

:yellow_circle: [testing] `test_flusher_processes_limit` only asserts configuration, never submits messages in tests/sentry/spans/consumers/process/test_consumer.py:81 (confidence: 88)
Constructs factory with `flusher_processes=2` and 4 partitions, asserts `len(processes) == 2`, `num_processes == 2`, `total_shards == 4`, then calls `step.join()` — no messages submitted, no poll cycles, no output verified. Does not confirm that the multi-process flusher actually routes and flushes spans end-to-end. Also does not assert the round-robin mapping (`i % num_processes`) — an off-by-one in distribution would go undetected.
```suggestion
# Submit a real span and verify output crosses a multi-process boundary:
step.submit(make_span_message(partition=3))  # shard 3 lives on process 1
step.poll()
fac._flusher.current_drift.value = 9000
step.poll()
# poll until messages appear, then assert they contain the span
assert messages

# Also assert the mapping:
assert len(flusher.process_to_shards_map[0]) == 2
assert len(flusher.process_to_shards_map[1]) == 2
assert sorted(flusher.process_to_shards_map[0] + flusher.process_to_shards_map[1]) == [0, 1, 2, 3]
```

:yellow_circle: [testing] `time.sleep(0.1)` introduces timing-dependent flakiness in tests/sentry/spans/consumers/process/test_consumer.py:60 (confidence: 85)
Bare `time.sleep(0.1)` after advancing `current_drift` to give flusher threads time to process. The prod-path `time.sleep` is monkeypatched to a no-op, but the test-level sleep is unguarded. On loaded CI, 100 ms may be insufficient and `messages` empty at `step.join()`.
```suggestion
import time as real_time
deadline = real_time.time() + 5.0
while not messages and real_time.time() < deadline:
    step.poll()
    real_time.sleep(0.05)
assert messages, "flusher did not produce output within 5 s"
```

## Nitpicks

:white_circle: [consistency] Misleading comment "Check if any shard handled by this process is unhealthy" in src/sentry/spans/consumers/process/flusher.py:278 (confidence: 80)
The code only reads `self.process_healthy_since[process_index].value` — a single per-process timestamp, not any per-shard state. Comment implies finer-grained health tracking that doesn't exist.

## Risk Metadata
Risk Score: 34/100 (MEDIUM) | Blast Radius: flusher.py imported by factory.py + 2 test files; factory.py imported by consumers/__init__.py + test_consumer.py | Sensitive Paths: none
AI-Authored Likelihood: LOW (natural refactor patterns; `CLAUDE.md` presence suggests AI tooling in workflow but no boilerplate code signals)
Recommendation: request-changes — the `join()` terminate() bug and the repo-wide attribute-removal audit should block merge; the missing restart/backpressure/join-deadline tests should be added before rollout; the two upstream reviewers (evanh, jan-auer) approved the overall shape, which is consistent with my read — the design is right, the finishing is not.

(1 finding below confidence threshold 80 suppressed: CLAUDE.md isinstance/hasattr addition unrelated to PR scope — style/review-org nitpick, upstream maintainers accepted.)
