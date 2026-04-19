# PR Review: getsentry/sentry #95633 — feat(uptime): Add ability to use queues to manage parallelism

## Summary
7 files changed, 1276 lines added, 6 lines deleted. 9 findings (2 critical, 5 improvements, 2 nitpicks).
New `thread-queue-parallel` consumer mode introduces a `FixedQueuePool` of per-group SPSC queues with a thread-per-queue worker and a lock-based `OffsetTracker`. Correctness hinges on Python-3.13 `queue.Queue.shutdown()` semantics; Sentry's `pyproject.toml` pins `python_version = "3.13"`, so the 3.13 APIs are valid here. Main concerns are unbounded queues (contradicting the "natural backpressure" design claim), a shutdown race that can drop unprocessed messages, and a few thread-safety / lifecycle edges.

## Critical

:red_circle: [correctness] Unbounded queues contradict stated backpressure guarantee in `src/sentry/remote_subscriptions/consumers/queue_consumer.py:213` (confidence: 92)
`FixedQueuePool.__init__` constructs each worker queue as `queue.Queue()` (no `maxsize`), but the class docstring on `SimpleQueueProcessingStrategy` claims "Natural backpressure when queues fill up." With unbounded queues there is no backpressure — a slow group cannot throttle Kafka consumption, so the process will grow memory until OOM under sustained skew. This is exactly the "one slow item clogs a batch" failure mode the PR description says it's solving, except it now manifests as unbounded per-group growth instead of a stalled batch.
```suggestion
        for i in range(num_queues):
            # Bound each queue so that a slow group exerts backpressure upstream
            # rather than growing memory without limit. Tune via config if needed.
            work_queue: queue.Queue[WorkItem[T]] = queue.Queue(maxsize=1000)
            self.queues.append(work_queue)
```
Also consider surfacing `maxsize` as a constructor argument and emitting a metric when `put()` blocks. References: [Python queue.Queue](https://docs.python.org/3/library/queue.html#queue.Queue)

:red_circle: [correctness] Messages silently dropped during shutdown race in `src/sentry/remote_subscriptions/consumers/queue_consumer.py:321` (confidence: 85)
`SimpleQueueProcessingStrategy.submit()` wraps the whole body in `try/except Exception` and, on failure, calls `add_offset` + `complete_offset` — marking the offset committable. If `FixedQueuePool.shutdown()` has already closed the underlying queue (via `q.shutdown(immediate=False)`), any concurrent `work_queue.put(work_item)` in `FixedQueuePool.submit` raises `queue.ShutDown`. That exception propagates up, is swallowed by the `except Exception` in the strategy, and the offset is committed **without the message ever being processed**. This turns graceful shutdown into silent at-most-once for in-flight messages, violating the "No item is lost or processed out of order" guarantee in the class docstring.
```suggestion
        except Exception:
            logger.exception("Error submitting message to queue")
            if isinstance(message.value, BrokerValue):
                # Do NOT complete the offset here — it was never processed.
                # Let the offset remain outstanding so the commit loop cannot
                # advance past this message. On restart Kafka will redeliver.
                pass
```
Alternatively, detect `queue.ShutDown` explicitly and re-raise so arroyo surfaces the shutdown. References: [arroyo strategies](https://getsentry.github.io/arroyo/strategies/index.html)

## Improvements

:yellow_circle: [correctness] `_commit_loop` ordering can double-commit on failure in `src/sentry/remote_subscriptions/consumers/queue_consumer.py:315` (confidence: 78)
In `_commit_loop`, `self.commit_function(committable)` runs before `mark_committed(...)`. If `commit_function` raises (e.g., broker reject), the generic `except Exception` swallows it and the next tick recomputes the same `committable` set and tries again — that's OK. But if `commit_function` succeeds and **then** `mark_committed` raises (unlikely, but possible under pathological lock contention), the next tick will recompute and re-commit the same offset. Kafka tolerates re-committing the same offset, so this is benign today, but the loop should still reflect the intent explicitly.
```suggestion
                if committable:
                    self.commit_function(committable)
                    # mark_committed is local state only; do it after the network
                    # commit so a failure keeps us retrying instead of advancing.
                    for partition, offset in committable.items():
                        self.queue_pool.offset_tracker.mark_committed(partition, offset)
                    metrics.incr(
                        "remote_subscriptions.queue_pool.offsets_committed",
                        len(committable),
                        tags={"identifier": self.queue_pool.identifier},
                    )
```
(Also: emitting the metric *after* success is more honest than before.)

:yellow_circle: [correctness] `hash()` for group routing is per-process randomized in `src/sentry/remote_subscriptions/consumers/queue_consumer.py:230` (confidence: 72)
`get_queue_for_group` uses Python's built-in `hash(group_key) % num_queues`. For strings, `hash()` is randomized per interpreter (PYTHONHASHSEED). In a single-replica, single-process consumer that's fine because each replica owns its own `FixedQueuePool`. But if you ever migrate to multiple processes in one container or rely on "this group always lands on queue N" for debugging/metrics, the assumption breaks. Prefer a stable hash.
```suggestion
    def get_queue_for_group(self, group_key: str) -> int:
        import hashlib
        digest = hashlib.blake2b(group_key.encode(), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.num_queues
```
`blake2b` with 8-byte digest is fast (faster than sha256) and deterministic. References: [PEP 456 hash randomization](https://peps.python.org/pep-0456/)

:yellow_circle: [correctness] `OrderedQueueWorker.run` completes offset even when work_item failed catastrophically in `src/sentry/remote_subscriptions/consumers/queue_consumer.py:172` (confidence: 70)
On any exception from `self.result_processor()` the `except Exception: logger.exception(...)` swallows it and the `finally` block calls `complete_offset`. This means a crashing processor still advances the Kafka commit, i.e., **exceptions cause message loss** with no retry. This is arguably the intended semantics (log-and-continue) but should be an explicit policy, because it makes "processed successfully" and "processed once and raised" indistinguishable from the consumer's perspective. Consider emitting a `result_processor.failed` metric tagged by `identifier` and/or exposing a DLQ hook.
```suggestion
            except Exception:
                logger.exception(
                    "Unexpected error in queue worker", extra={"worker_id": self.worker_id}
                )
                metrics.incr(
                    "remote_subscriptions.queue_worker.processing_failed",
                    tags={"identifier": self.identifier},
                )
```

:yellow_circle: [testing] No tests cover shutdown semantics or error paths in `tests/sentry/remote_subscriptions/consumers/test_queue_consumer.py:1` (confidence: 82)
The new test suite exercises happy-path ordering, stats, multi-partition tracking, and `wait_until_empty`. It does **not** cover: (a) `FixedQueuePool.shutdown()` while items are still queued (does `immediate=False` drain correctly?); (b) `result_processor` raising an exception (is `complete_offset` still called, is the worker alive?); (c) `submit()` after `shutdown()` (what happens to the offset?); (d) the commit loop's behavior when `commit_function` raises; (e) the `queue.ShutDown` exit path in `run()`. Given the core complaint in the PR body ("one slow item clogs the whole batch"), a slow-worker-doesn't-block-others test would also be load-bearing evidence.
```suggestion
    def test_shutdown_drains_pending_items(self):
        # Submit N items, call shutdown(), assert all N are processed before join returns.
        ...

    def test_worker_survives_processor_exception(self):
        # result_processor raises for one item; subsequent items must still be processed
        # AND the failing offset must still be marked complete.
        ...

    def test_submit_after_shutdown_does_not_lose_offset_visibility(self):
        # Submit before shutdown, then submit after -> the post-shutdown offset must not
        # be silently marked as committable without processing.
        ...
```

:yellow_circle: [consistency] `terminate()` skips commit-thread join in `src/sentry/remote_subscriptions/consumers/queue_consumer.py:368` (confidence: 74)
`SimpleQueueProcessingStrategy.close()` sets `shutdown_event`, joins the commit thread with a 5s timeout, then shuts down the queue pool. `terminate()` sets the event and shuts down the queue pool but never joins the commit thread. Even though the thread is a daemon and will die with the process, `terminate` is called from arroyo on hard-stop paths where we **do** want the final commit flush to run (so we don't replay a big chunk of offsets). Either join with a short timeout, or call `self._commit_loop_once()` synchronously before returning.
```suggestion
    def terminate(self) -> None:
        self.shutdown_event.set()
        self.commit_thread.join(timeout=1.0)
        self.queue_pool.shutdown()
```

## Nitpicks

:white_circle: [consistency] Redundant `except queue.ShutDown: break` inside processing try in `src/sentry/remote_subscriptions/consumers/queue_consumer.py:170` (confidence: 88)
The inner `try:` block wraps `sentry_sdk.start_transaction(...)` + `result_processor(...)`. Neither of those calls raises `queue.ShutDown` (the queue API is not touched inside the transaction). The outer `try` that surrounds `self.work_queue.get()` already handles the shutdown path. The inner `except queue.ShutDown: break` is dead code — remove it for clarity.

:white_circle: [consistency] `thread_queue_parallel` is a class-level mutable default in `src/sentry/remote_subscriptions/consumers/result_consumer.py:93` (confidence: 65)
`thread_queue_parallel = False` and `queue_pool: FixedQueuePool | None = None` are declared as class attributes alongside `multiprocessing_pool`. That's consistent with the existing style but still a footgun: instances that don't set `self.queue_pool` in `__init__` will share the class attribute. Today every mode path sets the instance attribute, so it's fine — flagging for future readers.

## Conflicts
No agent conflicts — this review is a direct synthesis, not a multi-agent dispatch (budget-bounded run). The existing Cursor bot reviewer on the PR flagged `queue.ShutDown` / `queue.shutdown()` as "Python <3.13 NameError/AttributeError" three separate times. **Those findings are invalid for this repo**: Sentry's `pyproject.toml` pins `python_version = "3.13"`, so those APIs are first-class. The more interesting concerns (unbounded queues, shutdown race, exception-swallowing) were not raised by the existing reviewers.

## Risk Metadata
Risk Score: 62/100 (MEDIUM) | Blast Radius: new file + 1 consumer-entry edit; `consumers/__init__.py` CLI choice addition; no existing call sites change behavior when `mode != "thread-queue-parallel"` | Sensitive Paths: none (no auth/secrets/migrations)
AI-Authored Likelihood: MEDIUM — symmetric docstrings, defensive `except queue.ShutDown` pairs (including one dead-code instance), and "natural backpressure" language that doesn't match the unbounded-`queue.Queue()` implementation all read as LLM-generated scaffolding that wasn't tightened against the real code. The offset-tracking logic is more carefully structured than that.

(Note: this review was produced without the full `soliton:*` agent swarm due to a constrained evaluation budget. Findings reflect direct diff reading, Python-3.13 `queue` API semantics, Sentry's pinned Python version, and existing reviewer context. Recommendation: **needs-discussion** — the unbounded-queue / backpressure issue and the shutdown-race both warrant a follow-up commit before relying on this mode for high-throughput uptime traffic.)
