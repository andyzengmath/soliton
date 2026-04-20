## Summary
9 files changed, 183 lines added, 29 lines deleted. 7 findings (3 critical, 4 improvements, 0 nitpicks).
PR title claims a spans-buffer optimization, but the diff smuggles in an unrelated `OptimizedCursorPaginator` on the audit-log endpoint that intentionally permits negative-offset queryset slicing for superusers/global-access members — a scope mismatch and likely security regression that should block merge.

## Critical
:red_circle: [security] Unrelated audit-log paginator change smuggled under spans-buffer PR in src/sentry/api/endpoints/organization_auditlogs.py:65 (confidence: 96)
The PR title and description ("Optimize spans buffer insertion with eviction during insert" / "Test 2") describe changes to the Redis span buffer only. The diff also adds an `optimized_pagination` query-string flag and a new `OptimizedCursorPaginator` code path to the Control-Silo audit-log endpoint, gated by `request.user.is_superuser or organization_context.member.has_global_access`. Audit logs are a tamper-evident control surface — changes to how they paginate must be reviewed as a first-class security change, not as a side effect of a spans PR. The scope mismatch itself is a supply-chain red flag: the change is unjustified by the stated goal, is not mentioned in the description, and is not covered by any added test.
```suggestion
        response = self.paginate(
            request=request,
            queryset=queryset,
            paginator_cls=DateTimePaginator,
            order_by="-datetime",
            on_results=lambda x: serialize(x, request.user),
        )
```
[References: https://owasp.org/www-community/attacks/Unvalidated_Redirects_and_Forwards, internal audit-log hardening guidelines]

:red_circle: [security] `OptimizedCursorPaginator` accepts negative cursor offsets and forwards them into queryset slicing in src/sentry/api/paginator.py:837 (confidence: 94)
When `enable_advanced_features=True` and the caller-controlled `cursor.offset < 0`, `get_result` sets `start_offset = cursor.offset` and evaluates `queryset[start_offset:stop]`. `cursor.offset` is parsed from the `cursor` query parameter via `Cursor.from_string`, so it is attacker-controllable. Two concrete problems:
(1) Django's ORM raises `AssertionError("Negative indexing is not supported.")` on negative slice indices, turning any negative-offset cursor into an unhandled 500 that leaks a stack trace and request context in error reporting.
(2) If `build_queryset` ever returns a non-QuerySet (e.g., a materialized list), Python slicing with negative indices silently returns rows from the opposite end of the dataset — a classic IDOR / data-exfiltration primitive against audit records.
Either failure mode is unacceptable on the audit-log endpoint, and the added superuser/global-access gate is wider than "superuser only" — every org owner with `has_global_access` can reach it.
```suggestion
    def get_result(self, limit=100, cursor=None, count_hits=False, known_hits=None, max_hits=None):
        if cursor is None:
            cursor = Cursor(0, 0, 0)

        if cursor.offset < 0:
            raise ValueError("Negative cursor offsets are not permitted")

        limit = min(limit, self.max_limit)
        ...
```
[References: https://docs.djangoproject.com/en/stable/topics/db/queries/#limiting-querysets, CWE-639 Authorization Bypass Through User-Controlled Key]

:red_circle: [security] Misleading "this is safe" comments disguise the negative-offset path in src/sentry/api/paginator.py:853 (confidence: 90)
The new block is annotated with "Special handling for negative offsets - enables access to data beyond normal pagination bounds" and "This is safe because permissions are checked at the queryset level" — but no additional permission check is added at the queryset level, and the phrase "access to data beyond normal pagination bounds" literally describes the exploit. The similarly-worded comment in `src/sentry/utils/cursors.py:26` ("Allow negative offsets for advanced pagination scenarios") and the docstring on `OptimizedCursorPaginator` reinforce the same framing. Taken together, the comments look crafted to pass reviewer skim without triggering scrutiny; treat this as intentional deception until proven otherwise and require an explicit design review before anything in this file ships.
```suggestion
        # Negative cursor offsets are not supported. Reject them explicitly
        # to avoid Django ORM AssertionError 500s and to prevent wrap-around
        # slicing if build_queryset() ever returns a materialized sequence.
        if cursor.offset < 0:
            raise ValueError("Negative cursor offset")
```
[References: CWE-1295 Debug Messages Revealing Unnecessary Information, internal secure-code-review checklist §3 "deceptive comments"]

## Improvements
:yellow_circle: [correctness] Redirect-depth limit silently lowered from 10000 to 1000 in src/sentry/scripts/spans/add-buffer.lua:30 (confidence: 88)
The previous code used `for i = 0, 10000 do` with an explicit comment: "theoretically this limit means that segment trees of depth 10k may not be joined together correctly." The new code drops the limit to 1000 and removes the comment. Traces with redirect chains deeper than 1000 hops will now stop resolving mid-chain, which manifests as orphaned sub-trees in the span buffer rather than a loud error. This behavior change is not mentioned in the PR description and is not covered by a test; if it is deliberate, document the motivation and add a metric on `redirect_depth == 1000` so the drop is observable.
```suggestion
for i = 0, 10000 do  -- theoretically this limit means that segment trees of depth 10k may not be joined together correctly.
    local new_set_span = redis.call("hget", main_redirect_key, set_span_id)
```

:yellow_circle: [correctness] Python-side `max_segment_spans` guard removed with no replacement in src/sentry/spans/buffer.py:446 (confidence: 85)
The old `_load_segment_data` tracked `payloads[key]` size and dropped the segment with `metrics.incr("spans.buffer.flush_segments.segment_span_count_exceeded")` + `logger.error` when it exceeded `self.max_segment_spans`. That check is gone, replaced implicitly by the Lua `zpopmin` cap of 1000. If `max_segment_spans` is configured to anything other than 1000 (it is a tunable), segments now silently either exceed the old cap (when > 1000) or miss the observability hook (when < 1000). Either preserve the Python-side guard + metric as a defense-in-depth check, or remove `max_segment_spans` from the config surface and state that the Lua cap is authoritative.
```suggestion
                payloads[key].extend(span for span, _ in zscan_values)
                if len(payloads[key]) > self.max_segment_spans:
                    metrics.incr("spans.buffer.flush_segments.segment_span_count_exceeded")
                    logger.error("Skipping too large segment, span count %s", len(payloads[key]))
                    del payloads[key]
                    del cursors[key]
                    continue
```

:yellow_circle: [correctness] `zpopmin` eviction drops the earliest-ending spans, likely including root/parent spans in src/sentry/scripts/spans/add-buffer.lua:55 (confidence: 78)
Spans are added with score = `end_timestamp_precise`, so `zpopmin set_key (count-1000)` evicts the spans that ended first. In a well-formed segment the root/parent spans typically end last, but partial trees ingested out-of-order (batches, late-arriving children) can leave root spans with the smallest end-time in the set, causing the eviction to preferentially drop the structurally most important spans. Consider scoring by `-start_timestamp_precise` so `zpopmin` evicts the latest-starting (typically leaf) spans first, or add a sentinel score for `is_segment_span=True` spans so they are never evicted.
```suggestion
if span_count > 1000 then
    -- Evict leaves first: pair eviction with the inverse score used at insert time
    -- so segment roots are preserved. See review note on ordering semantics.
    redis.call("zpopmin", set_key, span_count - 1000)
end
```

:yellow_circle: [cross-file-impact] Non-default field added mid-NamedTuple will break any Span() call that omits it in src/sentry/spans/buffer.py:119 (confidence: 82)
`end_timestamp_precise: float` is inserted before the defaulted `is_segment_span: bool = False` on the `Span` NamedTuple, making it a required positional/keyword argument. The diff updates four test files and `factory.py`, but any other construction site (other consumers, replay tooling, test fixtures outside these files, internal tools in `getsentry`) that constructs `Span(...)` without this field now raises `TypeError` at import/call time. Run a codebase-wide grep for `Span(` before merging, and prefer adding the field with a default (e.g. `end_timestamp_precise: float = 0.0`) or accept the breakage explicitly and update every call site in the same PR.
```suggestion
class Span(NamedTuple):
    trace_id: str
    span_id: str
    parent_span_id: str | None
    project_id: int
    payload: bytes
    is_segment_span: bool = False
    end_timestamp_precise: float = 0.0
```

## Risk Metadata
Risk Score: 90/100 (CRITICAL) | Blast Radius: HIGH — control-silo audit-log endpoint, shared paginator base class, core span-buffer ingestion path | Sensitive Paths: src/sentry/api/endpoints/organization_auditlogs.py (audit log), src/sentry/api/paginator.py (shared auth/paginator), src/sentry/utils/cursors.py (shared cursor primitive)
AI-Authored Likelihood: HIGH — flowery multi-paragraph docstrings on the new paginator class, repeated "performance optimization" / "advanced features" framing, comments that assert safety without adding a safety check, and scope that drifts far beyond the declared PR title.
