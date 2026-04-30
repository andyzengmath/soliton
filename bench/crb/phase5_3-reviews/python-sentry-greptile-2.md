## Summary
9 files changed, 183 lines added, 29 lines deleted. 10 findings (8 critical, 2 improvements).
Performance "optimization" introduces multiple correctness and DoS regressions: a Django negative-slice crash in `OptimizedCursorPaginator`, a base-class change that breaks every "previous page" cursor in the codebase, removal of the `max_segment_spans` DoS guard, an unbounded `ZPOPMIN` that can stall Redis, a Redis schema migration (`SADD` → `ZADD`) with no migration plan that will WRONGTYPE-error on every pre-deploy key, and a hard `KeyError` on the new `end_timestamp_precise` Kafka field. The opt-in flag for the new paginator is also (a) gated too loosely (`has_global_access` is broader than admin) and (b) silently dropped because `Endpoint.paginate()` does not forward arbitrary kwargs.

## Critical

:red_circle: [correctness] Django queryset does not support negative slicing — `OptimizedCursorPaginator.get_result` will raise `AssertionError` in src/sentry/api/paginator.py:840 (confidence: 95)
When `enable_advanced_features` is True and `cursor.offset < 0`, the new paginator executes `list(queryset[start_offset:stop])` with `start_offset = cursor.offset` (a negative integer). Django's `QuerySet.__getitem__` explicitly rejects this with `AssertionError: Negative indexing is not supported.` This is not Python list semantics — there is no "from the end" behavior. Any request that reaches this branch crashes the endpoint. The `enable_advanced_features` block is the entire reason `OptimizedCursorPaginator` exists, so the new paginator is broken on its primary code path.
```suggestion
        # Negative cursor offsets have no valid meaning for a Django queryset slice.
        # Reverse-direction pagination must be expressed via cursor.is_prev plus a
        # non-negative offset, not by passing a negative integer to the slice.
        start_offset = max(0, cursor.offset)
        stop = start_offset + limit + extra
        results = list(queryset[start_offset:stop])
```

:red_circle: [correctness] `BasePaginator.get_result` allows negative `start_offset` on the `is_prev` path, breaking every paginator subclass in src/sentry/api/paginator.py:178 (confidence: 90)
The new line `start_offset = max(0, offset) if not cursor.is_prev else offset` clamps only the forward path. On any prev-cursor request whose `cursor.offset` is negative (which `OptimizedCursorPaginator` is now seeding into the cursor stream, and which a tampered or malformed cursor can also produce), the raw negative value flows directly into `list(queryset[start_offset:stop])` and triggers the same Django `AssertionError`. This change is in the BASE class, so it affects `DateTimePaginator`, `SequencePaginator`, `OffsetPaginator`, `GenericOffsetPaginator`, and every other downstream paginator — including the non-opted-in fallback path of the audit-log endpoint itself.
```suggestion
        start_offset = max(0, offset)
        stop = start_offset + limit + extra
        results = list(queryset[start_offset:stop])
```

:red_circle: [correctness] `_load_segment_data` removes the `max_segment_spans` cap, eliminating the Python-side DoS guard against runaway segments in src/sentry/spans/buffer.py:446 (confidence: 92)
The previous code emitted `spans.buffer.flush_segments.segment_span_count_exceeded`, logged, and dropped any segment whose span count crossed `self.max_segment_spans`. The new code unconditionally `extend()`s every page from `zscan` into `payloads[key]` with no upper bound. The Lua-side `ZPOPMIN` cap at 1000 is not a substitute: it lives in a different process, can be bypassed by direct Redis writes, by a Lua-script regression, or by any future raise of the constant; the `max_segment_bytes` check that remains only bounds size, not count. A single hot trace can now OOM the flush worker. The unused `self.max_segment_spans` field is also now misleading to operators who tune it.
```suggestion
                payloads[key].extend(span for span, _ in zscan_values)
                if len(payloads[key]) > self.max_segment_spans:
                    metrics.incr("spans.buffer.flush_segments.segment_span_count_exceeded")
                    logger.error("Skipping too large segment, span count %s", len(payloads[key]))
                    del payloads[key]
                    del cursors[key]
                    continue
```

:red_circle: [security] Unbounded single-shot `ZPOPMIN` blocks the Redis instance under load in src/sentry/scripts/spans/add-buffer.lua:64 (confidence: 88)
`redis.call("zpopmin", set_key, span_count - 1000)` is invoked from inside a Lua script with a count derived from current set size. Redis is single-threaded; a single `ZPOPMIN` evicting tens of thousands of members blocks every other command on that node for the duration of the pop, stalling all tenants and consumers sharing the instance. Combined with the removed `max_segment_spans` cap (above), `span_count` is now effectively unbounded — a single misbehaving trace converts a per-segment problem into a cluster-wide availability incident. Eviction inside the hot ingest Lua path is also questionable: it should be moved to a background sweep so a hot segment cannot stall the producer.
```suggestion
-- Bound the per-call eviction so the Lua script never blocks Redis for long.
local EVICT_BATCH = 500
local to_evict = span_count - 1000
if to_evict > EVICT_BATCH then to_evict = EVICT_BATCH end
if to_evict > 0 then redis.call("zpopmin", set_key, to_evict) end
-- Schedule a background sweep elsewhere if the segment is still oversized.
```

:red_circle: [cross-file-impact] Redis schema migration `SADD`→`ZADD` lacks a transition plan; every pre-deploy key triggers WRONGTYPE in src/sentry/scripts/spans/add-buffer.lua:43 (confidence: 95)
The Lua script and `SpansBuffer` flip every operation on `span-buf:s:*` and `span-buf:s:{...}:*` from Set primitives (`SADD`/`SCARD`/`SUNIONSTORE`/`SSCAN`) to Sorted-Set primitives (`ZADD`/`ZCARD`/`ZUNIONSTORE`/`ZSCAN`). On any key written before the deploy, `ZCARD`/`ZUNIONSTORE`/`ZSCAN` does not return 0 — Redis raises `WRONGTYPE Operation against a key holding the wrong kind of value`. Inside the Lua script the error aborts execution mid-way, leaving `main_redirect_key` half-updated and silently dropping the span. In `_load_segment_data` the WRONGTYPE surfaces as a `ResponseError` in the pipeline result list; the bare `for key, (cursor, zscan_values) in zip(...)` unpack then raises and crashes the flush loop, taking down every segment in the batch. All spans buffered before the deploy are lost when their TTL expires.
```suggestion
-- Detect legacy Set-typed keys and skip/migrate them rather than letting WRONGTYPE
-- abort the script. Combine with a one-time migration job that reads remaining
-- `span-buf:*` keys with TYPE==set and rewrites them as sorted sets with a
-- synthetic score (e.g., 0.0) before this deploy is rolled out.
local function safe_zcard(k)
  if redis.call("type", k).ok == "zset" then return redis.call("zcard", k) end
  return 0
end
```

:red_circle: [correctness] `val["end_timestamp_precise"]` is a hard key access; old Kafka messages without the field will crash the consumer in src/sentry/spans/consumers/process/factory.py:141 (confidence: 95)
The new `Span` NamedTuple makes `end_timestamp_precise: float` required (no default), and the consumer reads it via `val["end_timestamp_precise"]` rather than `val.get(...)`. The `cast(SpanEvent, ...)` is a type assertion only — it does no runtime validation. During every rolling deploy, and for every replayed message in retention from before the producer-side change shipped, `KeyError: 'end_timestamp_precise'` will be raised from inside the Arroyo `RunTask` strategy, halting offset commits on the partition and ultimately stalling ingestion until the message ages out. The new field should also be defaulted in the NamedTuple itself so unrelated test fixtures and future construction sites do not break positionally.
```suggestion
# In src/sentry/spans/buffer.py — give the new field a sentinel default
class Span(NamedTuple):
    ...
    end_timestamp_precise: float = 0.0
    is_segment_span: bool = False

# In src/sentry/spans/consumers/process/factory.py
            end_timestamp_precise=val.get("end_timestamp_precise", 0.0),
```

:red_circle: [cross-file-impact] `enable_advanced_features=True` is passed through `self.paginate()` but `Endpoint.paginate` does not forward unknown kwargs to the paginator constructor in src/sentry/api/endpoints/organization_auditlogs.py:79 (confidence: 88)
`Endpoint.paginate` has a fixed signature (`request`, `on_results`, `paginator_cls`, `cursor_cls`, `default_per_page`, `max_per_page`, `cursor`, `response_cls`, `response_kwargs`, `count_hits`, `paginator`, `**paginator_kwargs`) and instantiates the paginator from a known set of arguments derived from `paginator_kwargs` only — `enable_advanced_features` either lands in `paginator_kwargs` (and the code below never reads it) or, depending on the local override, raises `TypeError: __init__() got an unexpected keyword argument`. Either way, `OptimizedCursorPaginator.enable_advanced_features` is `False` at runtime in the only code path that constructs it. The entire negative-offset branch is dead code; the new "performance" feature does nothing for callers and only ever takes the same path as `BasePaginator`. Verify the actual `Endpoint.paginate` signature before deploying — if it raises, the auditlogs endpoint 500s for any superuser passing `optimized_pagination=true`.
```suggestion
# Either instantiate the paginator directly:
paginator = OptimizedCursorPaginator(
    queryset=queryset, order_by="-datetime",
    enable_advanced_features=True,
)
response = self.respond_with_paginator(request, paginator, on_results=...)

# Or, if going through self.paginate(), drop the kwarg and move the flag onto
# a paginator subclass attribute so it survives the call.
```

:red_circle: [security] Opt-in gate uses `has_global_access` — broader than admin and grants regular members the new code path in src/sentry/api/endpoints/organization_auditlogs.py:68 (confidence: 85)
`enable_advanced = request.user.is_superuser or organization_context.member.has_global_access`. In Sentry, `has_global_access` is True for any member on a team granted open membership, or any member of an org with org-wide open membership — not for "admin/owner" only. Audit logs are the security record-of-truth for an organization (SSO config, member changes, API token creation); coupling a different paginator/code path to a non-admin role introduces a privilege boundary error, especially when that path enables negative-offset semantics whose access-control consequences depend on subtle queryset behavior. The query parameter `optimized_pagination=true` should also not be a user-tunable knob on a security-sensitive endpoint — paginator selection should never depend on untrusted client input.
```suggestion
from sentry.roles import organization_roles

member = organization_context.member
is_org_admin = (
    member is not None
    and organization_roles.get(member.role).priority
        >= organization_roles.get("admin").priority
)
# Drop the user-controlled query param entirely; gate purely on role.
if request.user.is_superuser or is_org_admin:
    response = self.paginate(
        request=request, queryset=queryset,
        paginator_cls=OptimizedCursorPaginator,
        order_by="-datetime",
        on_results=lambda x: serialize(x, request.user),
    )
else:
    response = self.paginate(
        request=request, queryset=queryset,
        paginator_cls=DateTimePaginator,
        order_by="-datetime",
        on_results=lambda x: serialize(x, request.user),
    )
```

## Improvements

:yellow_circle: [correctness] Lua `span_count` is sourced from `zunionstore` return value, which is the destination cardinality at that step — verify the fallback ordering in src/sentry/scripts/spans/add-buffer.lua:44 (confidence: 88)
`zunionstore` returns the cardinality of the destination set after the union, so when both branches run, the second assignment correctly reflects the cumulative size. The risk is the fallback `if span_count == 0 then span_count = redis.call("zcard", set_key) end` — `span_count` was initialized to 0 at script start, so this branch only triggers when neither union ran. If `set_key` was just populated by a `ZADD` earlier in the call chain (the producer's per-span insert in `process_spans`), this still reads the correct total. But because the diff splits the eviction logic across two processes, it is worth proving with a test that the eviction fires on the `set_key`-only path (no parent redirect) at exactly the boundary of 1001 spans, not just on the union path. Tighter, equivalent-and-clearer alternative: skip the `zunionstore`-return optimization and unconditionally `zcard` once.
```suggestion
-- Single source of truth, one extra round-trip but no ordering coupling.
local span_count = redis.call("zcard", set_key)
if span_count > 1000 then redis.call("zpopmin", set_key, span_count - 1000) end
```

:yellow_circle: [correctness] `organization_context.member.has_global_access` raises `AttributeError` when `member` is None (non-member superusers, scoped tokens) in src/sentry/api/endpoints/organization_auditlogs.py:68 (confidence: 85)
`request.user.is_superuser or organization_context.member.has_global_access` short-circuits left-to-right, so superusers are safe. But non-superuser callers whose `organization_context.member` is None — staff users, internal integrations, or API tokens scoped to an org the caller is not a member of — trigger `AttributeError: 'NoneType' object has no attribute 'has_global_access'` and a 500. This is a strictly worse error than the previous endpoint produced (it never accessed `member` at all on this branch).
```suggestion
member = organization_context.member
enable_advanced = request.user.is_superuser or (
    member is not None and member.has_global_access
)
```

## Risk Metadata
Risk Score: 78/100 (HIGH) | Blast Radius: paginator.py and cursors.py are foundational utilities imported by every paginated endpoint; buffer.py + add-buffer.lua are core to the spans ingest pipeline | Sensitive Paths: organization_auditlogs.py (security-of-record audit endpoint), buffer.py (Redis state migration with no plan)
AI-Authored Likelihood: HIGH — PR description is "Test 2", commits/title diverge from the actual scope, control-flow and naming carry the hallmarks of a generative-model rewrite (e.g., `OptimizedCursorPaginator` with verbose justification comments and a dead `enable_advanced_features` flag wired through an unmodified `Endpoint.paginate` signature).

(2 additional findings below confidence threshold)
