## Summary
7 files changed, 1276 lines added, 6 lines deleted. 5 findings (3 critical, 2 improvements, 0 nitpicks).
New `thread-queue-parallel` consumer mode relies on Python 3.13-only `queue` APIs and has a shutdown-hang hazard; the offset-tracking logic and cross-process grouping have additional correctness concerns.

## Critical
:red_circle: [correctness] Shutdown relies on Python 3.13-only `queue.Queue.shutdown()` and will leak worker threads on older interpreters in `src/sentry/remote_subscriptions/consumers/queue_consumer.py`:259 (confidence: 95)
`FixedQueuePool.shutdown()` calls `q.shutdown(immediate=False)` on every `queue.Queue`, but that method was added in Python 3.13 (PEP 0000 — `queue.Queue.shutdown`). On any earlier interpreter the call raises `AttributeError`, which is swallowed by the bare `except Exception:`, so no `queue.ShutDown` is ever delivered. Workers in `OrderedQueueWorker.run()` are blocked inside `self.work_queue.get()` (no timeout) and never observe `self.shutdown = True`, so `worker.join(timeout=5.0)` on line 270 times out and the daemons linger until process exit. This also means the `except queue.ShutDown:` clause at line 127 would itself raise `AttributeError` at exception-matching time on <3.13. If Sentry's runtime is pinned to 3.13+ this is safe; if anything consumes this module under 3.12 (dev envs, CI matrix, local tooling) shutdown is broken.
```suggestion
    def shutdown(self) -> None:
        """Gracefully shutdown all workers."""
        for worker in self.workers:
            worker.shutdown = True

        # Wake workers blocked in work_queue.get(); prefer Queue.shutdown() on 3.13+
        # and fall back to sentinel items on older interpreters.
        for q in self.queues:
            shutdown_fn = getattr(q, "shutdown", None)
            if callable(shutdown_fn):
                try:
                    shutdown_fn(immediate=False)
                except Exception:
                    logger.exception("Error shutting down queue")
            else:
                q.put(None)  # sentinel; OrderedQueueWorker.run must handle None

        for worker in self.workers:
            worker.join(timeout=5.0)
```

:red_circle: [correctness] `OrderedQueueWorker.run()` cannot observe `self.shutdown` while blocked in `work_queue.get()` in `src/sentry/remote_subscriptions/consumers/queue_consumer.py`:127 (confidence: 90)
The loop header `while not self.shutdown:` is only re-evaluated between iterations, but `self.work_queue.get()` blocks indefinitely with no timeout. Setting `self.shutdown = True` from `FixedQueuePool.shutdown()` has no effect on a worker that is parked inside `get()`; the only defined wakeup path is `queue.ShutDown`, which is Python-3.13-only (see the previous finding). Independent of the 3.13 dependency this is fragile — any future refactor that drops the `Queue.shutdown()` call will silently re-introduce hanging worker threads. Use a timeout so the loop naturally re-checks the shutdown flag.
```suggestion
    def run(self) -> None:
        """Process items from the queue in order."""
        while not self.shutdown:
            try:
                work_item = self.work_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            except getattr(queue, "ShutDown", ()):  # noqa: E721 - 3.13+ only
                break

            try:
                ...
```

:red_circle: [correctness] `hash(group_key) % num_queues` uses randomized string hashing and breaks ordering when a partition moves between processes in `src/sentry/remote_subscriptions/consumers/queue_consumer.py`:225 (confidence: 88)
Python's built-in `hash()` for `str` is seeded per-process via `PYTHONHASHSEED` (default random). Two Sentry consumer processes — or the same process after a restart — will map the same `subscription_id` to different queue indices. The docstring on `FixedQueuePool` promises "Items within a queue are processed in FIFO order" so that "Items for the same group are processed in order", but if a Kafka partition rebalances onto a different worker, in-flight items that were queued on (say) queue 7 in process A may be re-consumed into queue 3 in process B — breaking the intra-group ordering contract that the whole design rests on. Use a deterministic hash derived from the group key bytes.
```suggestion
    def get_queue_for_group(self, group_key: str) -> int:
        """Get queue index for a group using consistent hashing."""
        digest = hashlib.blake2b(group_key.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.num_queues
```

## Improvements
:yellow_circle: [testing] `test_thread_queue_parallel_error_handling` asserts the opposite of the code's actual behaviour in `tests/sentry/uptime/consumers/test_results_consumer.py`:1816 (confidence: 82)
The test name and docstring claim to verify that "errors in processing don't block offset commits for other messages", but the final assertion is `assert len(committed_offsets) == 0 or test_partition not in committed_offsets`. The production code calls `self.offset_tracker.complete_offset(work_item.partition, work_item.offset)` inside a `finally:` block (queue_consumer.py:147), so the offset for the failing message *is* completed and the commit loop should pick it up within ~1 s — i.e. the assertion should expect the partition to appear with offset 101, not be absent. As written, the test either passes only because of a timing race (commit loop hadn't fired before the 2 s max_wait elapsed) or silently masks a regression if the commit semantics ever change. Either tighten the wait and assert the committed offset, or rewrite the test to actually reproduce a blocked-commit scenario.
```suggestion
            assert mock_processor_call.call_count == 2
            assert committed_offsets.get(test_partition) == 101, (
                "complete_offset runs in finally, so failing messages must not "
                "block commits for subsequent successful messages"
            )
```

:yellow_circle: [correctness] `OffsetTracker.mark_committed` prunes `all_offsets` but leaves stale entries in `outstanding` in `src/sentry/remote_subscriptions/consumers/queue_consumer.py`:121 (confidence: 72)
`mark_committed()` filters `all_offsets[partition]` but does not apply the same pruning to `self.outstanding[partition]`. In normal operation every committed offset has already transitioned through `complete_offset`, so `outstanding` does not contain it — but there is no invariant check enforcing that. If `commit_function` is ever invoked from the `_commit_loop` with offsets the caller thinks are committable, and an external path (e.g. a rebalance recovery) later adds the same offsets back to `outstanding`, those ghost entries will block all future commits on that partition until a process restart. Defensive pruning is cheap and keeps the two sets in sync.
```suggestion
    def mark_committed(self, partition: Partition, offset: int) -> None:
        """Update the last committed offset for a partition."""
        with self._get_partition_lock(partition):
            self.last_committed[partition] = offset
            self.all_offsets[partition] = {o for o in self.all_offsets[partition] if o > offset}
            self.outstanding[partition] = {o for o in self.outstanding[partition] if o > offset}
```

## Risk Metadata
Risk Score: 68/100 (HIGH) | Blast Radius: new 345-LOC module in uptime/remote-subscriptions ingest path; +488 LOC of tests (one requires real Kafka) | Sensitive Paths: ingestion/consumer critical path
AI-Authored Likelihood: MEDIUM
