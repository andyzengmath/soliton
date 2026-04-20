## Summary
6 files changed, 199 lines added, 50 lines deleted. 10 findings (1 critical, 9 improvements).
Multi-process flusher refactor is largely sound but ships with an untested process-lifecycle path, a `join()` regression that can leak processes on timeout, a metric tag-key mismatch that will silently break shard-level aggregation, and a piece of dead `_create_process_for_shard` scaffolding.

## Critical
:red_circle: [testing] `_ensure_processes_alive` and the multi-process `join` loop have zero test coverage in src/sentry/spans/consumers/process/flusher.py:215 (confidence: 90)
Codecov reports 26 uncovered lines in `flusher.py` (66.23% patch coverage). The two most consequential new code paths — `_ensure_processes_alive` (per-process crash detection and restart) and the deadline-aware multi-process `join` loop — are never exercised by any test in this PR. The old single-process equivalents had at least partial coverage; the new paths introduce a per-process restart counter and an early `break` on deadline expiry that are entirely untested.
```suggestion
# test_flusher.py — simulate a dead process and assert restart
def test_ensure_processes_alive_restarts_dead_process(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    messages = []
    buffer = SpansBuffer(assigned_shards=[0, 1])
    flusher = SpanFlusher(buffer, next_step=mock.MagicMock(),
                          max_processes=2, produce_to_pipe=messages.append)
    old = flusher.processes[0]
    flusher.processes[0] = mock.MagicMock(is_alive=lambda: False, exitcode=1)
    flusher._ensure_processes_alive()
    assert flusher.processes[0] is not old
    assert flusher.process_restarts[0] == 1

# and a deadline-respecting join test
def test_join_respects_deadline():
    messages = []
    buffer = SpansBuffer(assigned_shards=[0, 1])
    flusher = SpanFlusher(buffer, next_step=mock.MagicMock(),
                          max_processes=2, produce_to_pipe=messages.append)
    start = time.time()
    flusher.join(timeout=0.3)
    assert time.time() - start < 2.0
```

## Improvements
:yellow_circle: [correctness] `join()` skips `terminate()` for remaining processes when the deadline expires in src/sentry/spans/consumers/process/flusher.py:326 (confidence: 95)
The new `join()` iterates `self.processes.items()` and, if `deadline - time.time() <= 0`, executes `break` — exiting the `for` loop before the trailing `process.terminate()` runs for any subsequent process. The old single-process code always called `terminate()` unconditionally after the wait loop, so this is a regression. Additionally, `self.next_step.join(timeout)` is invoked with the full original timeout, so by the time the process loop starts the deadline may already be in the past and **no** process gets terminated. Children are `daemon=True`, so they eventually die with the parent, but during a graceful consumer shutdown they can keep Kafka producers and Redis connections open for the remainder of the parent's life.
```suggestion
for process_index, process in self.processes.items():
    if deadline is not None:
        while process.is_alive() and deadline > time.time():
            time.sleep(0.1)
    else:
        while process.is_alive():
            time.sleep(0.1)

    # Always terminate — mirrors the old code's unconditional cleanup
    if isinstance(process, multiprocessing.Process):
        process.terminate()
```

:yellow_circle: [consistency] Metric tag key `"shards"` on `spans.buffer.flusher.wait_produce` is inconsistent with `"shard"` used by the other two shard-tagged metrics in src/sentry/spans/consumers/process/flusher.py:242 (confidence: 95)
Inside `SpanFlusher.main`, three metrics carry the shard identifier:
- `spans.buffer.flusher.produce` → `tags={"shard": shard_tag}`
- `spans.buffer.segment_size_bytes` → `tags={"shard": shard_tag}`
- `spans.buffer.flusher.wait_produce` → `tags={"shards": shard_tag}` (plural)

Any dashboard, alert, or rollup that groups or filters by tag key `"shard"` will silently miss the `wait_produce` series — which is the Kafka producer round-trip timer, arguably the most latency-sensitive of the three.
```suggestion
with metrics.timer("spans.buffer.flusher.wait_produce", tags={"shard": shard_tag}):
```

:yellow_circle: [consistency] `_create_process_for_shard` (singular) is dead code — defined but never called in src/sentry/spans/consumers/process/flusher.py:190 (confidence: 90)
The method `_create_process_for_shard(self, shard: int)` is introduced in this PR but has no call sites. `_ensure_processes_alive` and `_create_processes` both use the plural `_create_process_for_shards(process_index, shards)`. Beyond being unreachable, if it were ever wired up it would also bypass the `process_restarts` increment and the `MAX_PROCESS_RESTARTS` guard in `_ensure_processes_alive`, allowing unbounded restarts for a single shard.
```suggestion
# Delete the singular method entirely:
# def _create_process_for_shard(self, shard: int):
#     for process_index, shards in self.process_to_shards_map.items():
#         if shard in shards:
#             self._create_process_for_shards(process_index, shards)
#             break
```

:yellow_circle: [testing] `time.sleep(0.1)` added to `test_basic` is a no-op because `time.sleep` is monkeypatched globally earlier in the test in tests/sentry/spans/consumers/process/test_consumer.py:60 (confidence: 92)
The test starts with `monkeypatch.setattr("time.sleep", lambda _: None)`. The newly-added `time.sleep(0.1)` therefore does nothing, and the comment "Give flusher threads time to process after drift change" is misleading. Whatever synchronization the PR author intended is not actually happening — the test is implicitly relying on the extra `step.poll()` call and raw thread scheduling, which is a flakiness source under slow CI.
```suggestion
import time as _real_time
# ...
step.poll()
_real_time.sleep(0.1)  # real sleep; time.sleep is patched to no-op above
```

:yellow_circle: [testing] `test_flusher_processes_limit` asserts only construction topology, never that messages actually flush with a capped process count in tests/sentry/spans/consumers/process/test_consumer.py:414 (confidence: 88)
The new test verifies `len(flusher.processes) == 2`, `flusher.max_processes == 2`, `flusher.num_processes == 2`, and `total_shards == 4`, then calls `step.join()`. It never submits a payload, so the two flusher threads are started but do no meaningful work. The feature's core invariant — that spans submitted across 4 shards are correctly produced when consolidated into 2 processes — is unverified. Combined with the fact that the `produce_to_pipe` path switches from `mp_context.Process` to `threading.Thread`, real multiprocessing semantics (pickling `SpansBuffer`, spawn context) are exercised by nothing in this PR.
```suggestion
# After the topology assertions, drive a flush and verify output:
fac._flusher.current_drift.value = 9000  # force flush
step.poll()
step.poll()
assert messages or commits, "spans should flush when consolidated into 2 processes"
```

:yellow_circle: [testing] Shard distribution assertion (`total_shards == 4`) does not check shard uniqueness or completeness in tests/sentry/spans/consumers/process/test_consumer.py:450 (confidence: 85)
`total_shards = sum(len(shards) for shards in flusher.process_to_shards_map.values())` would pass if the map were `{0: [0, 0, 1, 2], 1: []}` — shard 0 duplicated, shard 3 missing. The round-robin assignment `i % num_processes` should produce distinct shards, but the test does not verify it.
```suggestion
all_assigned = [s for shards in flusher.process_to_shards_map.values() for s in shards]
assert len(all_assigned) == 4
assert set(all_assigned) == set(range(4)), "every shard must be assigned exactly once"
```

:yellow_circle: [consistency] `except (ValueError, AttributeError)` on `process.kill()` is redundant — the `isinstance(process, multiprocessing.Process)` guard already excludes threads in src/sentry/spans/consumers/process/flusher.py:303 (confidence: 85)
The previous code caught only `ValueError` (raised when a Process is already closed). The new code widens the clause to `(ValueError, AttributeError)`, apparently to tolerate threads (which do not have `.kill()`). But the enclosing `if isinstance(process, multiprocessing.Process):` on the line above already guarantees we never call `.kill()` on a thread, so `AttributeError` is unreachable.
```suggestion
try:
    if isinstance(process, multiprocessing.Process):
        process.kill()
except ValueError:
    pass  # Process already closed, ignore
```

:yellow_circle: [cross-file-impact] `SpansBuffer(shards)` is a newly-introduced constructor call — verify the real `SpansBuffer.__init__` signature in src/sentry/spans/consumers/process/flusher.py:151 (confidence: 72)
Previously the flusher received a pre-built `SpansBuffer` from the factory. This PR has the flusher construct its own per-process buffers via `shard_buffer = SpansBuffer(shards)` — a single positional list-of-ints. The diff keeps `buffer.assigned_shards` access, confirming `assigned_shards` is an attribute on the class, which suggests the matching constructor parameter is named `assigned_shards` (keyword) rather than a bare positional `shards`. If the real signature requires a Redis client/URL, topic name, or a keyword-only `assigned_shards`, this call will raise `TypeError` at runtime. Also verify that a `SpansBuffer` instantiated here (parent process) and then spawn-pickled to the child is stateless w.r.t. Redis — otherwise `submit()`'s parent-side iteration of `self.buffers.values()` for `record_stored_segments()` / `get_memory_info()` operates on a divergent parent-side copy while the real work happens in the child.
```suggestion
# Verify in sentry/spans/buffer.py; if the parameter is named assigned_shards:
shard_buffer = SpansBuffer(assigned_shards=shards)
```

:yellow_circle: [cross-file-impact] `ProcessSpansStrategyFactory.__init__` inserts `flusher_processes` before `produce_to_pipe` — any positional caller that previously passed `produce_to_pipe` as the 6th positional argument now silently binds a callable to `flusher_processes` in src/sentry/spans/consumers/process/factory.py:38 (confidence: 80)
The previous signature ended `..., output_block_size, produce_to_pipe=None`. The new one is `..., output_block_size, flusher_processes=None, produce_to_pipe=None`. Callers using keyword arguments (like the tests in this PR) are unaffected, but any integration test, fixture, or devserver helper that passes `produce_to_pipe` positionally will bind a callable into `flusher_processes`, which then feeds into `min(callable, len(...))` at construction time and raises `TypeError` when `create_with_partitions` runs. Grep for `ProcessSpansStrategyFactory(` in the real sentry tree to confirm all callers are keyword-based; consider making `produce_to_pipe` keyword-only to prevent recurrence.
```suggestion
def __init__(
    self,
    ...,
    output_block_size: int | None,
    *,
    flusher_processes: int | None = None,
    produce_to_pipe: Callable[[KafkaPayload], None] | None = None,
):
```

## Risk Metadata
Risk Score: 32/100 (MEDIUM) | Blast Radius: ~5 files in sentry (factory → consumers/__init__ → consumer runner); SpanFlusher not broadly re-exported | Sensitive Paths: none hit
AI-Authored Likelihood: LOW (human reviewers engaged with substantive comments; no AI-authored commit trailers)

(8 additional findings below confidence threshold: zero-shard no-op silent case, deployment config never sets `--flusher-processes`, misleading "any shard ... unhealthy" comment, CLAUDE.md style example mixed with functional change, parent/child SpansBuffer state split concern, `buffer` parameter used only for `assigned_shards`, `step.join()` called without timeout in new test, `flusher_processes > len(shards)` capping path untested)
