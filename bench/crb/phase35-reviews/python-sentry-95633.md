## Summary
7 files changed, 1276 lines added, 6 lines deleted. 6 findings (2 critical, 4 improvements).
New `thread-queue-parallel` consumer mode introduces an `OffsetTracker` + `FixedQueuePool` + worker-thread model for the uptime results pipeline. The core offset-tracking logic is sound and well-tested, but the pool uses Python 3.13-only queue primitives without guarding, queues are unbounded (contradicting the advertised backpressure guarantee), and several lifecycle paths can silently drop or fail to commit work.

## Critical
:red_circle: [correctness] Unbounded queues contradict stated "natural backpressure" guarantee in `src/sentry/remote_subscriptions/consumers/queue_consumer.py`:213 (confidence: 92)
`FixedQueuePool.__init__` constructs each per-group queue with `queue.Queue()` — the default `maxsize=0` means the queue is unbounded. This directly contradicts the class docstring on line 281 ("Natural backpressure when queues fill up") and the PR description's premise that slow items should no longer clog processing. With unbounded queues, if one `grouping_fn` subscription is slow, that single queue's depth grows without bound in RSS until OOM; the strategy also never exerts backpressure on the Kafka consumer via a blocking `put()`, so `StreamProcessor` keeps committing new offsets into the pool. This also shows up operationally: the PR's own "Suspect Issues" comment from the Sentry bot reports `RuntimeError: producer has been closed` errors on deploy, consistent with long-lived unbounded queues surviving a producer lifecycle event. Pick a per-queue `maxsize` (e.g. `max_queue_depth` parameter, default ~500–1000) and let `put()` block, so a slow group applies real backpressure instead of silently inflating memory.
```suggestion
        for i in range(num_queues):
            work_queue: queue.Queue[WorkItem[T]] = queue.Queue(maxsize=max_queue_depth)
            self.queues.append(work_queue)
```

:red_circle: [correctness] `queue.ShutDown` and `queue.Queue.shutdown()` are Python 3.13-only, no version guard in `src/sentry/remote_subscriptions/consumers/queue_consumer.py`:160 (confidence: 88)
The worker `run()` method catches `queue.ShutDown` (lines 160 and 170) and the pool's `shutdown()` calls `q.shutdown(immediate=False)` (line 266). Both APIs were added in Python 3.13 (see https://docs.python.org/3.13/whatsnew/3.13.html#queue). On any interpreter < 3.13 — including developer laptops not yet upgraded, CI base images, and third parties that vendor this module — the `except queue.ShutDown:` clause raises `AttributeError` the first time *any* exception tries to propagate through the try block, and `q.shutdown(...)` raises `AttributeError` immediately on shutdown. The `except Exception:` around `q.shutdown` in `FixedQueuePool.shutdown` swallows the `AttributeError` but prevents the real intent (unblocking blocked `get()` calls), so worker threads hang until the daemon-flag kill at interpreter exit. Either gate this module behind an explicit `sys.version_info >= (3, 13)` assertion at import time, or provide a fallback that pushes N sentinel `None` items onto the queues and teaches `run()` to break on the sentinel.
```suggestion
import sys

if sys.version_info < (3, 13):
    raise RuntimeError(
        "queue_consumer requires Python 3.13+ (queue.ShutDown / Queue.shutdown)"
    )
```

## Improvements
:yellow_circle: [correctness] `max_workers=0` silently becomes `num_queues=20` in `src/sentry/remote_subscriptions/consumers/result_consumer.py`:127 (confidence: 90)
`num_queues=max_workers or 20` uses truthiness, so `max_workers=0` — a user-accessible CLI value via `--max-workers 0` — falls back to 20 instead of raising or being rejected. For other modes, `max_workers=0` creates a `ThreadPoolExecutor(max_workers=0)` which Python rejects with `ValueError`, so the behavior becomes inconsistent between modes: batched-parallel rejects it, thread-queue-parallel silently spawns 20. Use an explicit `None` check and validate `max_workers > 0`.
```suggestion
            num_queues = 20 if max_workers is None else max_workers
            if num_queues <= 0:
                raise ValueError("max_workers must be > 0 for thread-queue-parallel mode")
            self.queue_pool = FixedQueuePool(
                result_processor=self.result_processor,
                identifier=self.identifier,
                num_queues=num_queues,
            )
```

:yellow_circle: [correctness] Commit thread can be joined before draining a final commit, losing already-completed work in `src/sentry/remote_subscriptions/consumers/queue_consumer.py`:363 (confidence: 80)
In `SimpleQueueProcessingStrategy.close()`, `shutdown_event.set()` is called, then `commit_thread.join(timeout=5.0)`, then `queue_pool.shutdown()`. The commit loop body runs once more after `shutdown_event.wait(1.0)` returns, which is good — but `queue_pool.shutdown()` then forcibly stops workers while there may still be in-flight items whose offsets have been `add_offset`'d but not `complete_offset`'d. Those offsets block all subsequent commits for that partition on the *next* consumer generation (since nothing ever completes them — they're lost with the worker threads). Drain the queues (`wait_until_empty`) before signaling shutdown, or run one final `get_committable_offsets` → `commit_function` pass *after* `queue_pool.shutdown()` returns.
```suggestion
    def close(self) -> None:
        self.queue_pool.wait_until_empty(timeout=5.0)
        self.shutdown_event.set()
        self.commit_thread.join(timeout=5.0)
        self.queue_pool.shutdown()
        final = self.queue_pool.offset_tracker.get_committable_offsets()
        if final:
            self.commit_function(final)
```

:yellow_circle: [testing] Integration test `test_thread_queue_parallel_kafka_offset_commit` can race and pass by accident in `tests/sentry/uptime/consumers/test_results_consumer.py`:1382 (confidence: 78)
The test busy-loops `processor._run_once(); time.sleep(0.1)` for up to 5 wall-clock seconds, then asserts a specific committed offset of 5. Under a loaded CI host the 5-second budget may elapse before all 5 messages are processed; the assertion would then fail flakily instead of deterministically. Worse, the test uses `processor._run_once()` and `processor._shutdown()` (underscore-prefixed arroyo internals) — these aren't part of arroyo's public API and may change without notice. Drive the loop against an observable condition (`mock_processor.call_count >= 5`) before asserting, and prefer `processor.run()` in a thread with a stop condition over the private hooks.

:yellow_circle: [correctness] Transaction name cardinality blows up with `num_queues` in `src/sentry/remote_subscriptions/consumers/queue_consumer.py`:166 (confidence: 82)
Each worker starts a Sentry transaction named `monitors.{identifier}.worker_{worker_id}`. With the default 20 queues (and potentially more if users set `--max-workers` higher), every message produces one of N distinct transaction names per identifier, which inflates transaction indexing cardinality on the Sentry side for essentially no diagnostic value — worker ID is a process-local implementation detail. Collapse to a single transaction name per identifier and tag the worker id instead.
```suggestion
                with sentry_sdk.start_transaction(
                    op="queue_worker.process",
                    name=f"monitors.{self.identifier}.worker",
                ) as txn:
                    txn.set_tag("worker_id", self.worker_id)
                    self.result_processor(self.identifier, work_item.result)
```

## Risk Metadata
Risk Score: 62/100 (HIGH) | Blast Radius: new 345-line concurrency module + wiring into existing uptime consumer factory; path of new data-plane consumer mode for uptime monitoring | Sensitive Paths: consumers/ (data ingestion, offset management)
AI-Authored Likelihood: MEDIUM
