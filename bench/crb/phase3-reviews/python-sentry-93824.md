# PR Review — getsentry/sentry#93824

**Title:** ref(span-buffer): Introduce multiprocessed flusher
**Base:** master · **Head:** spans-buffer-multiprocessing
**Files changed:** 6 · **Lines:** +199 / −50

## Summary
6 files changed, 199 lines added, 50 lines deleted. 11 findings (2 critical, 7 improvements, 2 nitpicks).
Per-shard flusher multiprocessing refactor is mostly sound, but thread-mode restart can leave the old thread running alongside its replacement, creating duplicate Redis/Kafka work and corrupting shared health counters — and the new test is purely structural.

## Critical

:red_circle: [correctness] Hung thread is not stopped before restart, causing two threads to flush the same Redis shards concurrently in src/sentry/spans/consumers/process/flusher.py:264-308 (confidence: 90)
In `_ensure_processes_alive`, when `cause == "hang"` and the worker is a `threading.Thread` (the `produce_to_pipe`/test path), the code only calls `process.kill()` for `multiprocessing.Process` instances. There is no mechanism to stop the old thread. `_create_process_for_shards` then starts a new thread for the same `process_index` and shards. Two live threads now call `buffer.flush_segments()` on overlapping Redis shard ranges simultaneously — producing duplicate Kafka messages for the same span segment. Both threads also write to the same `self.process_backpressure_since[process_index]` and `self.process_healthy_since[process_index]` shared `Value`s concurrently; the stale thread's writes to `healthy_since` immediately mask the hang, triggering an accelerating restart loop that will hit `MAX_PROCESS_RESTARTS` without the underlying hang being resolved. Production currently runs the `Process` path, but the test-mode thread path is exercised by CI and hides the real semantics of the restart logic.
```suggestion
# For threads, either introduce a per-process stop event or skip restart entirely
# in the hang case (daemon threads will die with the parent):
if cause == "hang" and not isinstance(process, multiprocessing.Process):
    metrics.incr(
        "spans.buffer.flusher_hang_unrecoverable",
        tags={"shards": ",".join(map(str, shards))},
    )
    continue
# ... existing kill() + _create_process_for_shards call
```

:red_circle: [testing] No test covers process crash recovery paths (`_ensure_processes_alive` unhealthy branches, MAX_PROCESS_RESTARTS exhaustion) in src/sentry/spans/consumers/process/flusher.py:215-262 (confidence: 92)
The restart / health-check / restart-budget paths are the most operationally dangerous in this PR: a bug means silent span drop or consumer hang after a worker crash. Neither the new `test_flusher_processes_limit` nor the updated `test_basic` kills a worker and verifies recovery or the MAX_PROCESS_RESTARTS backstop. Codecov confirms 26 missing lines in flusher.py (66.23% patch coverage) — almost certainly this logic.
```suggestion
@pytest.mark.django_db(transaction=True)
def test_flusher_restarts_crashed_process(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    # ... build factory with flusher_processes=1, 1 partition
    original_pid = flusher.processes[0].pid
    flusher.processes[0].kill()
    flusher.processes[0].join()
    step.poll()  # triggers _ensure_processes_alive
    assert flusher.processes[0].is_alive()
    assert flusher.processes[0].pid != original_pid
```
Also add a test that exhausts `MAX_PROCESS_RESTARTS + 1` kills and asserts the expected RuntimeError.

## Improvements

:yellow_circle: [correctness] `next_step.join(timeout)` consumes entire timeout budget before per-process waits, so later processes are never terminated in src/sentry/spans/consumers/process/flusher.py:326-378 (confidence: 88)
`join` computes `deadline = time.time() + timeout`, then calls `next_step.join(timeout)` with the full budget. If `next_step.join` takes close to the full timeout (the normal graceful-shutdown case), `deadline - time.time()` is already ≤ 0 when the per-process loop starts. The first process with `remaining_time <= 0` hits `break`, skipping *all* remaining processes — they are never `terminate()`-ed. Daemon=True means they die with the parent eventually, but the shutdown supervisor may SIGKILL them mid-produce, losing batches. Additionally, `remaining_time` is computed but unused inside the `while` loop (the loop uses `deadline` directly).
```suggestion
def join(self, timeout: float | None = None):
    self.stopped.value = True
    deadline = time.time() + timeout if timeout is not None else None
    self.next_step.join(timeout)
    for process_index, process in self.processes.items():
        while process.is_alive() and (deadline is None or time.time() < deadline):
            time.sleep(0.1)
        if isinstance(process, multiprocessing.Process):
            process.terminate()  # always terminate, even if deadline passed
```

:yellow_circle: [correctness] `max_processes or len(buffer.assigned_shards)` silently treats `max_processes=0` as "use all shards" in src/sentry/spans/consumers/process/flusher.py:103 (confidence: 82)
`self.max_processes = max_processes or len(buffer.assigned_shards)` uses Python truthiness. The CLI option `--flusher-processes` has `type=int` and accepts 0 legally. If an operator passes `--flusher-processes 0` by mistake, it silently becomes "one process per shard" — the exact opposite of the likely intent. More subtly, if an empty-shard rebalance makes `len(assigned_shards) == 0`, `num_processes = 0`, no processes start, and `submit()` silently drops everything.
```suggestion
if max_processes is not None and max_processes <= 0:
    raise ValueError(f"max_processes must be positive or None, got {max_processes}")
self.max_processes = (
    max_processes if max_processes is not None else len(buffer.assigned_shards)
)
if len(buffer.assigned_shards) == 0:
    raise RuntimeError("SpanFlusher initialized with no assigned shards")
```

:yellow_circle: [correctness] Per-process restart counter check is off-by-one (>= vs >) in src/sentry/spans/consumers/process/flusher.py:264-308 (confidence: 75)
`if self.process_restarts[process_index] > MAX_PROCESS_RESTARTS: raise …` fires only when the counter already exceeds the constant. Counter increments *after* the check, so each process actually gets `MAX_PROCESS_RESTARTS + 1` restarts. Same pattern existed in the old single-process code, but now the budget-blowout is multiplied by the number of processes.
```suggestion
if self.process_restarts[process_index] >= MAX_PROCESS_RESTARTS:
    raise RuntimeError(...)
self.process_restarts[process_index] += 1
```

:yellow_circle: [testing] New `test_flusher_processes_limit` asserts only structural state, never exercises actual span processing in tests/sentry/spans/consumers/process/test_consumer.py:79-123 (confidence: 95)
The test builds the factory, inspects `flusher.processes`, `max_processes`, `num_processes`, and `process_to_shards_map`, then calls `step.join()`. It never `submit()`s a message, never advances drift, never asserts output on `messages`. It validates that `__init__` wires up dicts correctly — nothing about the flusher actually flushing, routing spans to the right shard, or producing output. Every new code path beyond the constructor (fan-out loop, per-process backpressure aggregation, per-shard memory_info aggregation in `submit`) remains unexercised.
```suggestion
# Submit one message per partition, advance drift past batch time, poll, and assert
# that len(messages) > 0 and commits were produced. See test_basic for the template.
```

:yellow_circle: [testing] No test covers per-shard backpressure aggregation in `submit()` in src/sentry/spans/consumers/process/flusher.py:264-290 (confidence: 88)
The rewrite iterates `self.process_backpressure_since.values()` and raises `MessageRejected` if *any* process is backpressured past the threshold. This is an OR across processes — one slow shard blocks the entire consumer, which is a deliberate semantic change worth validating under test. No test fills a shard queue to capacity and asserts the MessageRejected is raised. Incorrect aggregation (AND instead of OR, or a missing threshold check) would either cause silent data loss or spurious consumer stalls.

:yellow_circle: [testing] `time.sleep(0.1)` in updated `test_basic` is a non-deterministic race window in tests/sentry/spans/consumers/process/test_consumer.py:60-62 (confidence: 85)
The "give flusher threads time to process after drift change" comment plus a 100 ms sleep will flake on loaded CI runners and wastes time on fast hardware. Replace with a bounded poll on observable state (`messages` becoming non-empty, or `commits` being called).
```suggestion
def _wait_until(pred, timeout=5.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred(): return
        time.sleep(interval)
    raise TimeoutError()

step.poll()
_wait_until(lambda: len(messages) > 0)
```

## Nitpicks

:white_circle: [correctness] `_create_process_for_shard` (singular) is dead code in the diff in src/sentry/spans/consumers/process/flusher.py:190-195 (confidence: 65)
The new method is defined but no call site invokes it. `_ensure_processes_alive` calls `_create_process_for_shards` (plural) directly. If intended for future external per-shard restart, its semantics are misleading — it actually restarts the entire process group that owns the given shard, not just that shard. Either delete it or document the group-restart behavior in the docstring.

:white_circle: [testing] `@pytest.mark.django_db(transaction=True)` escalation is unexplained in tests/sentry/spans/consumers/process/test_consumer.py:12,82 (confidence: 75)
Both tests now use `transaction=True`, which disables savepoint rollback and slows tests. If this is required because spawned subprocesses open their own DB connections (cannot share the parent's test transaction), document it in a one-line comment. If not required, revert to `@pytest.mark.django_db`.

## Risk Metadata
Risk Score: 32/100 (MEDIUM) | Blast Radius: internal to spans-ingest consumer pipeline (~6–8 importing files est.); critical infrastructure | Sensitive Paths: none
AI-Authored Likelihood: LOW (no AI-authorship markers; repo has CLAUDE.md but diff style/variable naming look human-authored; 2 human MEMBER approvals)

## Metadata
- Agents dispatched: 5 (risk-scorer, correctness, hallucination, cross-file-impact, test-quality)
- Completed: 5 / 5
- Hallucination agent: FINDINGS_NONE (shim repo has no full source tree to verify external APIs; internal-consistency checks all passed)
- Cross-file-impact agent: FINDINGS_NONE (all callers visible in diff are correctly updated; callers outside the diff could not be searched in this shim)
- Existing reviewer note (jan-auer, APPROVED): "The backpressure and healthy signals could be reduced to one per process" — already the case in the final diff (per-process `Value` dicts).
- Codecov: patch coverage 74.25%, 26 lines missing in flusher.py (66.23%).

**Recommendation:** needs-discussion — ship-blocking concern is the hung-thread double-flush path (Critical #1), which only affects the test/`produce_to_pipe` code path in CI but hides a genuine invariant violation and should be either fixed or explicitly scoped to "production uses Process mode only; thread-mode hang is unrecoverable by design".
