## Summary
6 files changed, 199 lines added, 50 lines deleted. 9 findings (1 critical, 7 improvements, 1 nitpick).
Refactor of `SpanFlusher` from a single background process into one process per Redis shard (capped by `--flusher-processes`). Mechanics are largely right, but shutdown has a genuine regression, a metric tag-key is inconsistent, and two pieces of newly-added code are untested or unreachable.

## Critical

:red_circle: [correctness] `join()` can skip `terminate()` for later processes when the deadline expires mid-loop in src/sentry/spans/consumers/process/flusher.py:366 (confidence: 93)
The new `join()` loops `for process_index, process in self.processes.items()` and executes `break` when `deadline - time.time() <= 0`. That exits the outer `for` loop before `process.terminate()` is called for any process not yet iterated. The old single-process code called `terminate()` unconditionally after the wait loop, so this is a regression: on a slow consumer shutdown some flusher processes can linger, holding Kafka producer / Redis connections until the parent eventually dies. Consistent with the pre-existing "Commit failed: Local: Broker handle destroyed" issue already surfaced on `submit` (IC_kwDOAA1TcM6x6E8M) — a leaked producer is an easy way to reproduce that class of error.
```suggestion
        for process_index, process in self.processes.items():
            while process.is_alive() and (deadline is None or deadline > time.time()):
                time.sleep(0.1)

            # Always terminate (mirrors old unconditional cleanup)
            if isinstance(process, multiprocessing.Process):
                try:
                    process.terminate()
                except (ValueError, AttributeError):
                    pass
```

## Improvements

:yellow_circle: [consistency] Metric tag key is `"shards"` on `wait_produce` but `"shard"` elsewhere in src/sentry/spans/consumers/process/flusher.py:242 (confidence: 95)
`spans.buffer.flusher.produce` and `spans.buffer.segment_size_bytes` emit `tags={"shard": shard_tag}`. `spans.buffer.flusher.wait_produce` uses plural `tags={"shards": shard_tag}`. Any dashboard/alert/rollup that groups by `"shard"` will silently skip `wait_produce` — the Kafka-producer round-trip timer, the most latency-sensitive of the three.
```suggestion
                with metrics.timer("spans.buffer.flusher.wait_produce", tags={"shard": shard_tag}):
```

:yellow_circle: [consistency] `_create_process_for_shard` (singular) is dead — never called in src/sentry/spans/consumers/process/flusher.py:190 (confidence: 90)
`_create_process_for_shard(self, shard: int)` is introduced but has no call sites. `_ensure_processes_alive` and `_create_processes` both use the plural `_create_process_for_shards`. If it were ever wired in, it would also bypass the `process_restarts` counter / `MAX_PROCESS_RESTARTS` guard in `_ensure_processes_alive`, allowing unbounded restarts for a single shard. Delete it.
```suggestion
    # remove _create_process_for_shard entirely
```

:yellow_circle: [testing] `_ensure_processes_alive` restart path and the deadline-aware `join` loop have no tests in src/sentry/spans/consumers/process/flusher.py:215 (confidence: 88)
Codecov reports 26 uncovered lines on `flusher.py` (66.23% patch coverage). The two highest-consequence new paths — per-process crash detection/restart in `_ensure_processes_alive`, and the deadline-respecting multi-process `join` — are unexercised. `test_flusher_processes_limit` only asserts construction topology. Add at least (a) one process externally killed → verify `process_restarts[idx]` increments and a new live process replaces it, and (b) one process hanging past `max-unhealthy-seconds` → verify the per-shard `spans.buffer.flusher_unhealthy` metric.
```suggestion
# tests/sentry/spans/consumers/process/test_flusher.py
def test_ensure_processes_alive_restarts_dead_process(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    buffer = SpansBuffer(assigned_shards=[0, 1])
    flusher = SpanFlusher(buffer, next_step=mock.MagicMock(), max_processes=2,
                          produce_to_pipe=[].append)
    old = flusher.processes[0]
    flusher.processes[0] = mock.MagicMock(is_alive=lambda: False, exitcode=1)
    flusher._ensure_processes_alive()
    assert flusher.processes[0] is not old
    assert flusher.process_restarts[0] == 1
```

:yellow_circle: [testing] `time.sleep(0.1)` added to `test_basic` is a no-op (global `time.sleep` is monkeypatched earlier) in tests/sentry/spans/consumers/process/test_consumer.py:60 (confidence: 92)
The test runs `monkeypatch.setattr("time.sleep", lambda _: None)` on line 1. The newly-added `time.sleep(0.1)` therefore does nothing, and the comment "Give flusher threads time to process after drift change" is misleading. Whatever synchronization the author intended is absent — the test is implicitly relying on the extra `step.poll()` plus raw thread scheduling, which invites CI flakes.
```suggestion
import time as _real_time
# ...
step.poll()
_real_time.sleep(0.1)  # real sleep; module-level time.sleep is patched to no-op
```

:yellow_circle: [testing] `test_flusher_processes_limit` never flushes anything — construction-only assertion in tests/sentry/spans/consumers/process/test_consumer.py:414 (confidence: 85)
The new test asserts `len(flusher.processes) == 2`, `flusher.max_processes == 2`, `flusher.num_processes == 2`, `total_shards == 4`, then `step.join()`. It never submits a payload, so the two flusher threads do no work. The feature's core invariant — that spans across 4 shards actually flush when consolidated into 2 processes — is unverified. Also because `produce_to_pipe` is set, the code path uses `threading.Thread`, not `mp_context.Process`, so real multiprocessing semantics (pickling `SpansBuffer`, `spawn` context) go entirely untested.
```suggestion
fac._flusher.current_drift.value = 9000
step.poll()
step.poll()
assert messages, "spans should flush when 4 shards are consolidated into 2 processes"
```

:yellow_circle: [testing] Shard-distribution assertion does not check uniqueness or completeness in tests/sentry/spans/consumers/process/test_consumer.py:450 (confidence: 82)
`total_shards = sum(len(shards) for shards in flusher.process_to_shards_map.values())` would pass if the map were `{0: [0, 0, 1, 2], 1: []}` — shard 0 duplicated, shard 3 missing. Round-robin `i % num_processes` should produce distinct shards, but the test does not verify it.
```suggestion
all_assigned = [s for shards in flusher.process_to_shards_map.values() for s in shards]
assert len(all_assigned) == 4
assert set(all_assigned) == set(range(4)), "every shard must be assigned exactly once"
```

:yellow_circle: [cross-file-impact] New positional `flusher_processes` parameter in `ProcessSpansStrategyFactory.__init__` shifts `produce_to_pipe` in src/sentry/spans/consumers/process/factory.py:41 (confidence: 80)
The signature goes from `..., output_block_size, produce_to_pipe=None` to `..., output_block_size, flusher_processes=None, produce_to_pipe=None`. Keyword callers (this PR's tests) are fine. Any positional caller — integration tests, devserver helpers, fixtures — that previously passed `produce_to_pipe` positionally will now bind a callable into `flusher_processes`, which flows into `min(callable, len(...))` and raises `TypeError` at `create_with_partitions` time. Grep `ProcessSpansStrategyFactory(` across the real tree to confirm all callers are keyword-based, or make `produce_to_pipe` keyword-only.
```suggestion
def __init__(
    self,
    ...
    output_block_size: int | None,
    flusher_processes: int | None = None,
    *,
    produce_to_pipe: Callable[[KafkaPayload], None] | None = None,
):
```

## Nitpicks

:white_circle: [consistency] `@pytest.mark.django_db` → `@pytest.mark.django_db(transaction=True)` is a meaningful test-speed regression in tests/sentry/spans/consumers/process/test_consumer.py:11 (confidence: 60)
`transaction=True` disables the default per-test transaction rollback and truncates between tests. If the flusher subprocesses genuinely need visibility into committed DB rows across the process boundary (multiprocess isolation), this is correct — but document it with a comment so the next maintainer does not revert it as a perceived cleanup.

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: ingest-spans flusher hot path, per-shard multiprocessing, Redis + Kafka producers (~5 files in sentry) | Sensitive Paths: none
AI-Authored Likelihood: LOW (reviewers engaged with substantive comments; no AI-authored trailers)

(3 additional findings below confidence threshold: deterministic ordering of `buffer.assigned_shards` across restarts, redundant `AttributeError` in `except` clause already guarded by `isinstance(..., multiprocessing.Process)`, `SpansBuffer` built twice per launch — parent copy immediately discarded)
