## Summary
9 files changed, ~184 lines added, ~21 lines deleted. 13 findings (8 critical, 5 improvements).
PR frames two unrelated changes as one: (a) a span-buffer SET→ZSET migration with a 1000-span cap, and (b) an "optimized pagination" path on the org audit-log endpoint that gates a Django-incompatible negative-offset code path on a misused `has_global_access` flag — with confabulated "safety" comments throughout. The pagination half should not merge in any form.

## Critical

:red_circle: [security] `has_global_access` misused as authorization gate for audit-log pagination bypass in src/sentry/api/endpoints/organization_auditlogs.py:27 (confidence: 95)
The new branch gates `OptimizedCursorPaginator` on `request.user.is_superuser or organization_context.member.has_global_access`. `has_global_access` is the per-member mirror of the org's open-membership setting (teams joinable without approval) — it is not an administrative signal. Any ordinary member of an open-membership org satisfies the gate, so the PR effectively lets regular members reach a code path whose own comments advertise "access to data beyond normal pagination bounds" on the product's most security-sensitive read endpoint. The comment "Enable advanced pagination features for authorized administrators" misrepresents what the predicate actually matches (OWASP A01).
```suggestion
        response = self.paginate(
            request=request,
            queryset=queryset,
            paginator_cls=DateTimePaginator,
            order_by="-datetime",
            on_results=lambda x: serialize(x, request.user),
        )
```
References: OWASP A01 Broken Access Control, CWE-285, CWE-863

:red_circle: [correctness] Django QuerySet negative slice raises `TypeError` / `AssertionError` in src/sentry/api/paginator.py:133 (confidence: 99)
`OptimizedCursorPaginator.get_result` executes `queryset[cursor.offset:stop]` with `cursor.offset < 0` when `enable_advanced_features=True`. Django's QuerySet `__getitem__` explicitly asserts non-negative indices and raises `AssertionError: Negative indexing is not supported.` — the inline comment "The underlying Django ORM properly handles negative slicing automatically" is a hallucinated API behavior. Any attacker-shaped cursor (cursor strings are parsed into `Cursor(value, offset, is_prev)` where `offset` is attacker-controllable) routed through this paginator produces a 500 with stack-trace-bearing logs on the audit-log endpoint.
```suggestion
        start_offset = max(0, cursor.offset)
        stop = start_offset + limit + extra
        results = list(queryset[start_offset:stop])
```
References: https://docs.djangoproject.com/en/stable/topics/db/queries/#limiting-querysets, CWE-209, CWE-20

:red_circle: [correctness] Base `get_result` still passes negative offset through when `cursor.is_prev=True` in src/sentry/api/paginator.py:64 (confidence: 97)
The new `start_offset = max(0, offset) if not cursor.is_prev else offset` clamps only the forward branch. On prev-cursors, a negative `offset` is forwarded untouched and the same Django negative-slice assertion fires. Since the `Cursor` itself was made permissive in this PR (see next finding), an attacker can still crash any paginator in Sentry that inherits `BasePaginator.get_result` by submitting `?cursor=<value>:-1:1`. The asymmetric clamp "fixes" one branch while hiding the root cause.
```suggestion
        start_offset = max(0, offset)
        stop = start_offset + limit + extra
        results = list(queryset[start_offset:stop])
```
References: OWASP A04 Insecure Design, CWE-754

:red_circle: [security] `Cursor` now silently accepts negative offsets, globalizing the attack surface in src/sentry/utils/cursors.py:23 (confidence: 88)
The diff adds a comment explicitly endorsing negative offsets and keeps `self.offset = int(offset)` with no validation. Because `Cursor.from_string` parses user-supplied cursor query params, every paginator in Sentry — not just `OptimizedCursorPaginator` — now sees user-controlled negative offsets. Input validation belongs at this boundary.
```suggestion
        self.value: CursorValue = value
        offset = int(offset)
        if offset < 0:
            raise ValueError("Cursor offset must be non-negative")
        self.offset = offset
```
References: OWASP A04 Insecure Design, CWE-20

:red_circle: [correctness] KeyError crash on Kafka messages missing `end_timestamp_precise` in src/sentry/spans/consumers/process/factory.py:305 (confidence: 95)
`val["end_timestamp_precise"]` is a hard dict lookup. `cast(SpanEvent, ...)` performs zero runtime validation — it is a typing no-op. Any producer still emitting the prior schema (rolling deploys, legacy producers, replays) will raise `KeyError` out of `process_batch`, which crash-loops the Arroyo consumer and halts ingestion for the affected partition until someone intervenes.
```suggestion
            end_timestamp_precise=val.get("end_timestamp_precise", 0.0),
```
References: https://getsentry.github.io/sentry-kafka-schemas/

:red_circle: [correctness] `max_segment_spans` memory-safety check removed with no equivalent on the read path in src/sentry/spans/buffer.py:431 (confidence: 92)
The previous block that detected oversized segments (`if len(payloads[key]) > self.max_segment_spans: ... del payloads[key]; continue`) is gone. The Lua-side `zpopmin` cap only applies at write time and only to keys written under the new script — any pre-existing oversized keys or mixed-deploy writes produce segments that `_load_segment_data` now accumulates unbounded into `payloads[key]` across multiple `zscan` pipeline pages. A single pathological segment can OOM the flush worker silently (no metric, no log).
```suggestion
                payloads[key].extend(span for span, _ in zscan_values)
                if len(payloads[key]) > self.max_segment_spans:
                    metrics.incr("spans.buffer.flush_segments.segment_span_count_exceeded")
                    logger.error("Skipping too large segment, span count %s", len(payloads[key]))
                    del payloads[key]
                    del cursors[key]
                    continue
```

:red_circle: [cross-file-impact] Redis SET→ZSET migration has no cutover strategy — WRONGTYPE on rolling deploy in src/sentry/scripts/spans/add-buffer.lua:191 (confidence: 85)
All `span-buf:s:{...}:...` keys move from plain sets to sorted sets (`sadd/scard/sunionstore/sscan` → `zadd/zcard/zunionstore/zscan`). Any key written by an older pod during a rolling deploy is a `set`; the new Lua calls `zcard` / `zunionstore` on it, Redis returns `WRONGTYPE`, and the whole script aborts. No dual-write path, no TTL-based cutover, no `TYPE` guard. This also breaks any out-of-band reader that still uses `smembers` / `sscan` on the same keys.
```suggestion
-- Guard against pre-migration SET-typed keys during rolling deploy.
local function zcard_safe(key)
    local t = redis.call("type", key)["ok"]
    if t == "none" then return 0 end
    if t ~= "zset" then
        redis.call("unlink", key)
        return 0
    end
    return redis.call("zcard", key)
end
-- use zcard_safe(...) in place of zcard for span_key / parent_key / set_key.
```

:red_circle: [testing] `OptimizedCursorPaginator` and the new audit-log branch ship with zero tests in src/sentry/api/paginator.py:77 (confidence: 98)
~90 lines of new production code plus a new `optimized_pagination=true` branch on the audit-log endpoint are added with no accompanying test changes. Neither the happy path, the permission gate (superuser vs. `has_global_access` vs. neither), nor the negative-offset branch are exercised. Given the high security sensitivity of the endpoint and the confirmed Django-incompatibility of the negative-offset path, test coverage is a hard prerequisite — the safer action is to delete the code rather than write tests for it.
```suggestion
# tests/sentry/api/test_optimized_paginator.py
def test_negative_offset_is_rejected():
    paginator = OptimizedCursorPaginator(Model.objects.all(), key="id", enable_advanced_features=True)
    with pytest.raises((AssertionError, TypeError, ValueError)):
        paginator.get_result(limit=10, cursor=Cursor(value=0, offset=-5, is_prev=True))

def test_audit_log_optimized_pagination_requires_superuser(self):
    self.login_as(user=self.user)  # regular member, not superuser
    response = self.get_success_response(self.organization.slug,
                                         qs_params={"optimized_pagination": "true"})
    assert response.status_code == 200  # silent fallback is the current behavior
```

## Improvements

:yellow_circle: [correctness] Lua loop limit reduced 10000→1000 silently breaks deep segment-tree joins in src/sentry/scripts/spans/add-buffer.lua:30 (confidence: 90)
The original comment read "theoretically this limit means that segment trees of depth 10k may not be joined together correctly." The new limit of 1000 means chains of depth 1001–10000 now stop traversal mid-chain, use whatever intermediate `set_span_id` is current at that point, and produce split/mis-joined segments with no error, no metric, and no log. If the cut is intentional, it needs observability; if it is not, it needs to be reverted.
```suggestion
for i = 0, 10000 do
    local new_set_span = redis.call("hget", main_redirect_key, set_span_id)
    redirect_depth = i
    if not new_set_span or new_set_span == set_span_id then
        break
    end
    set_span_id = new_set_span
end
if redirect_depth >= 10000 then
    redis.call("incr", "spans.buffer.redirect_depth_limit_hit")
end
```

:yellow_circle: [correctness] `zpopmin` drops earliest-ending (typically child/leaf) spans with no metric in src/sentry/scripts/spans/add-buffer.lua:209 (confidence: 88)
The cap uses `zpopmin` which removes members with the *lowest* scores; scores are `end_timestamp_precise`, so child/leaf spans (which end before their parents) are evicted preferentially while roots/parents survive. Downstream consumers relying on child spans for latency attribution or error grouping then see incomplete segments. The data loss is silent — no metric and no log.
```suggestion
if span_count > 1000 then
    redis.call("incr", "spans.buffer.segment_capped")
    redis.call("zpopmin", set_key, span_count - 1000)
end
```

:yellow_circle: [hallucination] `OptimizedCursorPaginator` is LLM-template duplication of `BasePaginator`, not an extension in src/sentry/api/paginator.py:77 (confidence: 90)
~90 lines of near-verbatim copy of `BasePaginator.get_result` plus one inserted conditional, topped with a three-bullet docstring ("Negative offset support / Streamlined boundary condition handling / Optimized query path for large datasets") where only the first bullet corresponds to any actual code. Real refactors extract a hook method (e.g., override only the slice step); this pattern — copy parent, insert one branch, write marketing-style docstring — is characteristic AI output and compounds maintenance cost. Given the feature rests on a false Django premise, the class should be deleted rather than refactored.
```suggestion
# Delete OptimizedCursorPaginator entirely from src/sentry/api/paginator.py
# and revert the import + branch in organization_auditlogs.py.
```

:yellow_circle: [cross-file-impact] `Span` NamedTuple insertion position silently miscasts positional callers in src/sentry/spans/buffer.py:116 (confidence: 82)
`end_timestamp_precise: float` is inserted as field #6, before the default-carrying `is_segment_span: bool = False`. Any existing call site that constructed `Span(trace_id, span_id, parent_span_id, project_id, payload, True)` positionally now passes `True` (a bool) into `end_timestamp_precise` — Python NamedTuples do not type-check at runtime, so this silently assigns `1.0` as the ZSET score and leaves `is_segment_span` at its default. Audit every `Span(...)` construction site or, better, append the new field at the end (or convert to a validated dataclass).
```suggestion
class Span(NamedTuple):
    payload: bytes
    trace_id: str
    span_id: str
    parent_span_id: str | None
    project_id: int
    is_segment_span: bool = False
    end_timestamp_precise: float = 0.0  # appended to preserve positional compatibility
```

:yellow_circle: [correctness] `organization_context.member.has_global_access` unguarded against `member is None` in src/sentry/api/endpoints/organization_auditlogs.py:28 (confidence: 80)
Superusers acting across orgs (or certain API-token contexts) can reach this line with `organization_context.member is None`, raising `AttributeError` for the exact privileged users the branch targets. The fix is cheap — rely on `or` short-circuiting with a None-safe access — but the whole branch should be removed per the first critical finding anyway.
```suggestion
        enable_advanced = request.user.is_superuser or (
            organization_context.member is not None
            and organization_context.member.has_global_access
        )
```

## Risk Metadata
Risk Score: 75/100 (HIGH) | Blast Radius: 100 (paginator.py + cursors.py are foundational, imported across the entire API surface; buffer.py / factory.py are core span-pipeline) | Sensitive Paths: `organization_auditlogs.py` (audit*), `paginator.py` (paginator*)
AI-Authored Likelihood: HIGH — confabulated Django-ORM negative-slicing claim, stacked buzzword comments ("performance optimization", "advanced features", "enhanced cursor support"), near-verbatim duplication of `BasePaginator.get_result` into a new class, a comment-only no-op edit to `cursors.py` that makes the diff look more coherent than it is, and self-reassuring "this is safe because..." justifications that reference permission layers not actually in play.
