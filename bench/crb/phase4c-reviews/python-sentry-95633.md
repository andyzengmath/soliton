## Summary
7 files changed, 1276 lines added, 6 lines deleted. 8 findings (3 critical, 5 improvements).
Introduces a thread-pool queue-based Kafka consumer (`FixedQueuePool` + `OffsetTracker` + `SimpleQueueProcessingStrategy`) to replace batch-style parallelism. Core threading primitives are sound but shutdown drains items unsafely, several error-path branches are dead or inverted, and a test asserts the opposite of what the implementation does.

## Critical
:red_circle: [correctness] In-flight items silently lost at shutdown; offsets never committed after worker join timeout in src/sentry/remote_subscriptions/consumers/queue_consumer.py:165 (confidence: 93)
`FixedQueuePool.shutdown()` calls `worker.join(timeout=5.0)` but does not check whether the worker actually finished, and `SimpleQueueProcessingStrategy.close()` joins the commit thread immediately after. Any worker still processing a slow item when shutdown is signaled (which is the exact failure mode the PR claims to fix) will: (1) miss the join deadline, (2) eventually call `offset_tracker.complete_offset(...)` in its `finally` block, but (3) find no commit thread left to publish those offsets. Kafka re-delivers those messages on next start, causing duplicate processing. The commit thread does not run a final `get_committable_offsets()` pass after workers are joined — it just exits when `shutdown_event` is set.
```suggestion
def shutdown(self) -> None:
    for worker in self.workers:
        worker.shutdown = True
    for q in self.queues:
        try:
            q.shutdown(immediate=False)
        except Exception:
            logger.exception("Error shutting down queue")
    for worker in self.workers:
        worker.join(timeout=5.0)
        if worker.is_alive():
            logger.warning("Worker %s did not stop within timeout", worker.worker_id)
    # Final commit pass for any offsets completed during shutdown
    final = self.offset_tracker.get_committable_offsets()
    if final and self._commit_function is not None:
        self._commit_function(final)
        for p, o in final.items():
            self.offset_tracker.mark_committed(p, o)
```
Hold a `commit_function` reference on the pool (or invoke a final commit from `SimpleQueueProcessingStrategy.close()` after `queue_pool.shutdown()` returns and before joining the commit thread).

:red_circle: [correctness] `OrderedQueueWorker.run()` shutdown guard is dead weight; workers rely solely on `q.shutdown()` to exit in src/sentry/remote_subscriptions/consumers/queue_consumer.py:97 (confidence: 90)
`run()` loops on `while not self.shutdown:` and immediately calls `self.work_queue.get()` (no timeout). Once a worker is parked inside `get()` the `self.shutdown` flag cannot unblock it — only `q.shutdown(immediate=False)` (Python 3.13+) raising `queue.ShutDown` breaks the call. If `q.shutdown()` ever raises — and `FixedQueuePool.shutdown()` wraps it in a bare `except Exception: logger.exception(...)` that swallows the error — the worker will sit in `get()` forever and the subsequent `join(timeout=5.0)` returns with the thread still alive but no diagnostic beyond a log line.
```suggestion
def run(self) -> None:
    while not self.shutdown:
        try:
            work_item = self.work_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        except queue.ShutDown:
            break
        try:
            with sentry_sdk.start_transaction(
                op="queue_worker.process",
                name=f"monitors.{self.identifier}.worker_{self.worker_id}",
            ):
                self.result_processor(self.identifier, work_item.result)
        except Exception:
            logger.exception(
                "Unexpected error in queue worker", extra={"worker_id": self.worker_id}
            )
        finally:
            self.offset_tracker.complete_offset(work_item.partition, work_item.offset)
            metrics.gauge(
                "remote_subscriptions.queue_worker.queue_depth",
                self.work_queue.qsize(),
                tags={"identifier": self.identifier},
            )
```
Also drop the bare `except queue.ShutDown:` around `result_processor(...)` — it is dead because `sentry_sdk.start_transaction` and `result_processor` never raise that exception.

:red_circle: [testing] `test_thread_queue_parallel_error_handling` asserts no commit, but the implementation always marks the offset complete in `finally` in tests/sentry/uptime/consumers/test_results_consumer.py:1154 (confidence: 92)
The test injects `mock_processor_call.side_effect = [Exception("Processing failed"), None]` on two messages for the same subscription (same queue) and then asserts `assert len(committed_offsets) == 0 or test_partition not in committed_offsets`. But `OrderedQueueWorker.run()` calls `offset_tracker.complete_offset(...)` in a `finally` block regardless of whether `result_processor` raised — so both offsets become committable, and the commit thread (which wakes every 1 s) will publish offset 101. The test passes only because the polling loop may return before the commit fires; under any CI load it flips to a failure. Either the code is wrong (errors should block commits so Kafka re-delivers) or the test is wrong (errors are expected to commit-and-drop). Current production semantics silently drop poison messages; the test codifies the opposite contract.
```suggestion
# If the intended semantics is "errors still commit offset" (current code):
assert self.commit_event.wait(timeout=2.0), "Commit should fire"
assert committed_offsets.get(test_partition) == 102  # 101 + 1 (next-to-read)

# If the intended semantics is "errors block commit":
# 1. In OrderedQueueWorker.run(), track success/failure and only call
#    offset_tracker.complete_offset when processing completed normally.
# 2. Keep the current assertion.
```
Pick one semantic and align the `finally` block and the test to it; either way the current pair is inconsistent.

## Improvements
:yellow_circle: [correctness] `_commit_loop` waits first then commits, delaying the first commit by 1 s and risking a lost final commit at shutdown in src/sentry/remote_subscriptions/consumers/queue_consumer.py:188 (confidence: 92)
The loop starts every iteration with `self.shutdown_event.wait(1.0)` before calling `get_committable_offsets()`. This means the first commit after startup is always delayed by 1 s even when offsets are already complete, and on shutdown the loop exits as soon as `shutdown_event` is set without re-checking for offsets that became committable during the `wait(1.0)` window between the last commit and the shutdown signal.
```suggestion
def _commit_loop(self) -> None:
    while True:
        try:
            committable = self.queue_pool.offset_tracker.get_committable_offsets()
            if committable:
                metrics.incr(
                    "remote_subscriptions.queue_pool.offsets_committed",
                    len(committable),
                    tags={"identifier": self.queue_pool.identifier},
                )
                self.commit_function(committable)
                for partition, offset in committable.items():
                    self.queue_pool.offset_tracker.mark_committed(partition, offset)
        except Exception:
            logger.exception("Error in commit loop")
        if self.shutdown_event.is_set():
            break
        self.shutdown_event.wait(1.0)
```

:yellow_circle: [correctness] `OffsetTracker.partition_locks` is mutated from worker threads while `get_committable_offsets()` iterates `all_offsets.keys()` — risk of RuntimeError in src/sentry/remote_subscriptions/consumers/queue_consumer.py:54 (confidence: 90)
Two smaller issues and one real race: (1) `_get_partition_lock` does `lock = self.partition_locks.get(partition); if lock: return lock` — `threading.Lock` instances are always truthy, so the `if lock:` check effectively degrades to "is the key present", which is fine but misleading. (2) More importantly, `get_committable_offsets()` iterates `list(self.all_offsets.keys())` without any registry-level lock; `self.all_offsets` is a `defaultdict(set)` and is mutated on first `add_offset` for a new partition. If a worker registers a brand-new partition concurrently with the commit loop running `get_committable_offsets`, the `list(...)` snapshot happens before the GIL is released, so it's safe in CPython — but `self.partition_locks` is read/written without the registry lock as well, and `setdefault` on a plain dict is only atomic under the GIL, which is not guaranteed under Python 3.13t free-threaded builds (which Sentry has begun experimenting with).
```suggestion
class OffsetTracker:
    def __init__(self) -> None:
        self.all_offsets: dict[Partition, set[int]] = defaultdict(set)
        self.outstanding: dict[Partition, set[int]] = defaultdict(set)
        self.last_committed: dict[Partition, int] = {}
        self.partition_locks: dict[Partition, threading.Lock] = {}
        self._registry_lock = threading.Lock()

    def _get_partition_lock(self, partition: Partition) -> threading.Lock:
        with self._registry_lock:
            lock = self.partition_locks.get(partition)
            if lock is None:
                lock = threading.Lock()
                self.partition_locks[partition] = lock
            return lock
```

:yellow_circle: [correctness] `assert isinstance(message.value, BrokerValue)` is compiled out under `PYTHONOPTIMIZE`, causing silent offset loss in src/sentry/remote_subscriptions/consumers/queue_consumer.py:219 (confidence: 85)
If Python runs with `-O` (PYTHONOPTIMIZE≥1) the assert disappears; a non-`BrokerValue` message then raises `AttributeError` on `message.value.partition`, which the outer `except Exception` catches. The fallback `if isinstance(message.value, BrokerValue):` inside the handler is False, so neither `add_offset` nor `complete_offset` is called — the message is silently dropped and never retried. Outside optimize mode, the assert fails loudly, which is the more useful behavior. Replace with an explicit guard so behavior is the same under both builds.
```suggestion
def submit(self, message: Message[KafkaPayload | FilteredPayload]) -> None:
    if not isinstance(message.value, BrokerValue):
        # FilteredPayload or unexpected wrapper — nothing to commit, nothing to enqueue.
        return
    partition = message.value.partition
    offset = message.value.offset
    try:
        result = self.decoder(message.payload)
        if result is None:
            self.queue_pool.offset_tracker.add_offset(partition, offset)
            self.queue_pool.offset_tracker.complete_offset(partition, offset)
            return
        group_key = self.grouping_fn(result)
        work_item = WorkItem(
            partition=partition, offset=offset, result=result, message=message,
        )
        self.queue_pool.submit(group_key, work_item)
    except Exception:
        logger.exception("Error submitting message to queue")
        self.queue_pool.offset_tracker.add_offset(partition, offset)
        self.queue_pool.offset_tracker.complete_offset(partition, offset)
```

:yellow_circle: [correctness] `queue.Queue()` is unbounded; claim of "natural backpressure when queues fill up" is incorrect in src/sentry/remote_subscriptions/consumers/queue_consumer.py:136 (confidence: 85)
Each of the 20 queues is created with no `maxsize`, so `submit()` never blocks and the queues will grow to consume all process memory if consumers slow down relative to the Kafka partition read rate. The class docstring explicitly promises "Natural backpressure when queues fill up" — that is not true for an unbounded queue. Set a bounded maxsize (per-queue or sum-across-queues) so `put()` blocks the caller and, transitively, Arroyo stops reading from Kafka; emit a metric when the cap is hit so capacity tuning is observable.
```suggestion
def __init__(
    self,
    result_processor: Callable[[str, T], None],
    identifier: str,
    num_queues: int = 20,
    queue_maxsize: int = 1000,  # per-queue cap; tune per consumer
) -> None:
    ...
    for i in range(num_queues):
        work_queue: queue.Queue[WorkItem[T]] = queue.Queue(maxsize=queue_maxsize)
        ...
```
And update the docstring to reflect the actual bound.

:yellow_circle: [testing] Six `test_thread_queue_parallel_*` tests use `time.sleep(0.1)` polling instead of `threading.Event`, at risk of flakiness under CI in tests/sentry/uptime/consumers/test_results_consumer.py:969 (confidence: 88)
Every thread-queue test in this file uses the same `for _ in range(max_wait): ... time.sleep(0.1)` pattern, whereas `TestSimpleQueueProcessingStrategy` in `test_queue_consumer.py` uses `threading.Event.wait(timeout=5.0)` correctly. The polling version wakes up every 100 ms even on fast machines and exhausts its budget first on slow ones; under CI contention the worker thread may not have run by the time the outer loop gives up, causing spurious failures. Switch to `threading.Event`-based synchronization so the test wakes on the actual event rather than the next tick.
```suggestion
done = threading.Event()
original = factory.result_processor.__call__

def counting_call(identifier, result):
    # track whatever state the test needs
    if call_count[0] >= expected:
        done.set()

mock_processor_call.side_effect = counting_call
# ... submit messages ...
assert done.wait(timeout=5.0), "Processing did not complete in time"
```

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: new threading/offset-commit code path for uptime Kafka consumer — affects reliability of uptime monitoring pipeline; shutdown and error paths can silently drop/duplicate messages | Sensitive Paths: consumer pipeline, offset commit semantics, shutdown lifecycle
AI-Authored Likelihood: MEDIUM-HIGH — structured module with defensive boilerplate (dead `except queue.ShutDown` branch around non-queue code, assert used as type-guard, 20-wrap polling loops in tests, docstring claim that doesn't match unbounded `queue.Queue()`), extensive but synchronization-wise inconsistent test suite (Event-based in one file, sleep-poll in another), four cursor-bot "Bug:" reviews on record all flagging the same Python 3.13-specific APIs — patterns characteristic of agent-assisted implementation.

(7 additional findings below confidence threshold: OffsetTracker gap-scan documentation (82), OffsetTracker unit test gaps (82), shutdown drain/ShutDown-path test coverage (78/75), factory construction-only integration test (85 retained above), `wait_until_empty` naming (72), unsynchronized `committed_offsets` dict in tests (72), submit() double-add race (88 retained above), class-level `__call__` patch leakage (65), and `create_with_partitions` bare-`else` routing fallback to serial (72).)
