## Summary
7 files changed, 1276 lines added, 6 lines deleted. 4 findings (0 critical, 4 improvements, 0 nitpicks).
Adds a new `thread-queue-parallel` consumer mode using a fixed pool of per-queue worker threads with consistent-hash group affinity and an `OffsetTracker` that commits the highest contiguous processed offset per partition. The threading model and offset-tracking algorithm are sound, but several concurrency / lifecycle concerns deserve attention before this is enabled on a high-volume topic.

## Improvements
:yellow_circle: [correctness] Shared `result_processor` instance across worker threads with no documented thread-safety contract in src/sentry/remote_subscriptions/consumers/result_consumer.py:115 (confidence: 88)
`self.result_processor = self.result_processor_cls()` is constructed once in `__init__` and the *same* instance is then invoked concurrently by every `OrderedQueueWorker` thread (one per queue, default 20). The existing `parallel` mode uses `MultiprocessingPool` so each worker has its own copy; `batched-parallel` invokes it from a single ThreadPoolExecutor task at a time. `thread-queue-parallel` is the first mode that calls a single processor instance from many threads in parallel, so any per-instance state (cached subscription lookups, metrics counters, DB session attributes, lazily-initialised attributes) is now a race. Either document a thread-safety requirement on `ResultProcessor`, or instantiate one processor per worker.
```suggestion
# In FixedQueuePool.__init__, accept a processor *factory* and call it per worker:
worker = OrderedQueueWorker[T](
    worker_id=i,
    work_queue=work_queue,
    result_processor=result_processor_factory(),  # fresh instance per thread
    identifier=identifier,
    offset_tracker=self.offset_tracker,
)
```

:yellow_circle: [correctness] `OffsetTracker` permanently stalls on legitimate Kafka offset gaps in src/sentry/remote_subscriptions/consumers/queue_consumer.py:117 (confidence: 85)
`get_committable_offsets()` walks `range(start, max_offset + 1)` and breaks on the first offset that is missing from `all_offsets`. This requires the consumed offset stream to be strictly contiguous. On a transactional Kafka topic, transaction-commit/abort markers consume offsets that the consumer never sees; on a log-compacted topic, deleted keys leave gaps. The `test_thread_queue_parallel_offset_gaps` test bakes this behaviour in, but in production this means a single missing offset would block all subsequent commits for that partition forever, growing consumer lag without bound. Track only the **outstanding** set and commit `max(received) - outstanding_below_max`, or explicitly skip offsets that haven't appeared within a window.
```suggestion
# Treat any offset not currently outstanding (whether seen or skipped) as committable:
highest_committable = last_committed
for offset in range(start, max_offset + 1):
    if offset in outstanding:
        break
    highest_committable = offset
```

:yellow_circle: [correctness] `partition_locks`, `all_offsets`, and `last_committed` grow unbounded across rebalances in src/sentry/remote_subscriptions/consumers/queue_consumer.py:71 (confidence: 86)
`OffsetTracker` keeps three dicts keyed by `Partition` and never removes entries when a partition is revoked. `mark_committed()` prunes individual offsets but never the partition key itself, and there is no hook tied to the consumer's `revoke` callback. A long-lived consumer that experiences periodic rebalances (Kubernetes redeploys, scaling, broker failovers) will accumulate dead partition entries — small per-entry, but the per-partition `Lock` objects and the empty `set()` containers leak forever. Add an explicit `forget_partition(partition)` method and wire it into the strategy's partition-revocation path.
```suggestion
def forget_partition(self, partition: Partition) -> None:
    self.all_offsets.pop(partition, None)
    self.outstanding.pop(partition, None)
    self.last_committed.pop(partition, None)
    self.partition_locks.pop(partition, None)
```

:yellow_circle: [correctness] `WorkItem.message` is never read; holds a reference to the entire KafkaPayload for the lifetime of every queued item in src/sentry/remote_subscriptions/consumers/queue_consumer.py:53 (confidence: 90)
`WorkItem` carries `partition`, `offset`, `result`, **and** the full `message: Message[KafkaPayload | FilteredPayload]`. The worker only ever reads `result`, `partition`, and `offset`; nothing in the code path touches `work_item.message`. The docstring claims the field exists "for offset tracking" but the offset is already on the dataclass directly. With a default `num_queues=20` and Python's unbounded `queue.Queue`, this can pin meaningful memory on a backlog. Drop the field.
```suggestion
@dataclass
class WorkItem(Generic[T]):
    """Work item with offset metadata for ordered processing."""
    partition: Partition
    offset: int
    result: T
```

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: production uptime consumer hot path, new threading model with offset commit semantics | Sensitive Paths: none matched
AI-Authored Likelihood: MEDIUM

(3 additional findings below confidence threshold)
