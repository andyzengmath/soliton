## Summary
9 files changed, 183 lines added, 29 lines deleted. 12 findings (5 critical, 6 improvements, 1 nitpick).
PR mixes a span-buffer eviction refactor with an audit-log "optimized pagination" feature that introduces a negative-offset code path that will crash Django querysets; the empty PR body ("Test 2") and verbose justificatory comments around suspicious permission/pagination logic strongly suggest AI-authored, under-reviewed code.

## Critical

:red_circle: [security] Suspicious "optimized pagination" backdoor on audit-log endpoint in src/sentry/api/endpoints/organization_auditlogs.py:68 (confidence: 92)
A new query parameter `optimized_pagination=true`, gated by `request.user.is_superuser or organization_context.member.has_global_access`, swaps the audit-log paginator for `OptimizedCursorPaginator` with `enable_advanced_features=True`, which then permits **negative cursor offsets**. This is a security-sensitive endpoint (audit logs) and the new path weakens pagination constraints via a hidden query flag. Comments such as "This is safe because permissions are checked at the queryset level" are not supported by the diff — `OptimizedCursorPaginator` inherits from `BasePaginator` and applies no additional scoping. Additionally, `organization_context.member` may be `None` for superusers on open-membership orgs, which would raise `AttributeError` → 500. Remove the feature flag entirely, or restrict strictly to `is_superuser` and add an explicit 403 when `use_optimized` is true but `enable_advanced` is false.
```suggestion
        response = self.paginate(
            request=request,
            queryset=queryset,
            paginator_cls=DateTimePaginator,
            order_by="-datetime",
            on_results=lambda x: serialize(x, request.user),
        )
```

:red_circle: [hallucination] False claim that Django QuerySet supports negative slicing in src/sentry/api/paginator.py:881 (confidence: 98)
`OptimizedCursorPaginator.get_result` passes a negative `start_offset` directly into `list(queryset[start_offset:stop])`. The comment states "The underlying Django ORM properly handles negative slicing automatically" — this is factually incorrect. Django raises `AssertionError: Negative indexing is not supported. Use django.db.models.expressions.OrderBy.desc() ...` for any QuerySet sliced with a negative start or stop (`django/db/models/query.py`). The `enable_advanced_features and cursor.offset < 0` branch is therefore an unconditional runtime crash in production the moment it is exercised. Either raise `BadPaginationError` on negative offsets or remove the branch.
```suggestion
        if cursor.offset < 0:
            raise BadPaginationError("Negative offsets are not supported")
        start_offset = max(0, offset) if not cursor.is_prev else max(0, offset)
        stop = start_offset + limit + extra
        results = list(queryset[start_offset:stop])
```

:red_circle: [correctness] BasePaginator.get_result now forwards negative offsets when cursor.is_prev in src/sentry/api/paginator.py:179 (confidence: 95)
Before this PR: `stop = offset + limit + extra; results = list(queryset[offset:stop])`. After: `start_offset = max(0, offset) if not cursor.is_prev else offset`. When `cursor.is_prev` is True, a client-crafted prev cursor with negative offset is now passed straight into `queryset[start_offset:stop]`, triggering Django's negative-slicing assertion. This change silently alters the behavior of the BASE class used by every paginator in the codebase and is not covered by any new test. Clamp in both branches.
```suggestion
        stop = offset + limit + extra
        results = list(queryset[max(0, offset):stop])
```

:red_circle: [correctness] Per-segment span-count cap silently dropped in src/sentry/spans/buffer.py:446 (confidence: 90)
`_load_segment_data` previously enforced `self.max_segment_spans` with a metric (`spans.buffer.flush_segments.segment_span_count_exceeded`), error log, and early-deletion of offending segments. This PR removes that guard, relying entirely on the Lua script's hardcoded `zpopmin ... span_count - 1000` cap in `add-buffer.lua`. Problems: (1) `self.max_segment_spans` is configurable and may be smaller than 1000, so the Python-side contract is now violated; (2) pre-existing segments persisted before this migration are not retroactively trimmed, so flushes can still exceed the bound; (3) loss of the metric blinds operators to over-large segments. Reintroduce the check after `payloads[key].extend(...)`.
```suggestion
                payloads[key].extend(span for span, _ in zscan_values)
                if len(payloads[key]) > self.max_segment_spans:
                    metrics.incr("spans.buffer.flush_segments.segment_span_count_exceeded")
                    logger.error("Skipping too large segment, span count %s", len(payloads[key]))
                    del payloads[key]
                    del cursors[key]
                    continue
```

:red_circle: [correctness] Redirect loop depth silently reduced from 10k to 1k in src/sentry/scripts/spans/add-buffer.lua:30 (confidence: 88)
The original loop had an explicit comment: "this limit means that segment trees of depth 10k may not be joined together correctly." This PR reduces the loop to `for i = 0, 1000` and deletes the comment, silently lowering the max joinable segment-tree depth by 10x with no metric, no log line, no entry in the PR description, and no test. Deeply chained traces will now be silently misassembled. Either keep the original 10000 bound or add a metric when `redirect_depth` reaches the new cap so the regression is observable.
```suggestion
for i = 0, 10000 do  -- theoretically this limit means that segment trees of depth 10k may not be joined together correctly.
```

## Improvements

:yellow_circle: [cross-file-impact] Span NamedTuple required field added before a defaulted field in src/sentry/spans/buffer.py:119 (confidence: 85)
`end_timestamp_precise: float` is declared after `payload: bytes` and before `is_segment_span: bool = False`. NamedTuple permits this, but every producer of `Span(...)` in the codebase now needs the new kwarg. The PR updates `factory.py`, `test_consumer.py`, `test_flusher.py`, and `test_buffer.py`, but any other call site (other consumers, fixtures, docs snippets) will raise `TypeError: __new__() missing 1 required positional argument`. Grep `Span\(` across the repo to confirm exhaustive coverage before merge.
```suggestion
    end_timestamp_precise: float = 0.0
```

:yellow_circle: [security] Permission gate uses has_global_access, widening beyond admins in src/sentry/api/endpoints/organization_auditlogs.py:69 (confidence: 82)
`enable_advanced = request.user.is_superuser or organization_context.member.has_global_access` lets any org member with global access unlock the new paginator (and thus the negative-offset code path on audit logs). Audit-log reads should be gated by the existing `OrganizationAuditPermission` class only. If a performance flag is truly needed, restrict to `is_superuser` and never expose it as a user-controlled query param.

:yellow_circle: [consistency] OptimizedCursorPaginator is a ~70-line copy-paste of BasePaginator.get_result in src/sentry/api/paginator.py:845 (confidence: 90)
The subclass re-implements cursor defaulting, queryset build, hit counting, offset/extra logic, prev-trim, reverse, and `build_cursor` almost verbatim from the base class. Only the negative-offset branch differs. This duplication is a maintenance hazard — base-class fixes will not flow to the subclass. Either (a) delete the subclass and gate negative offsets inside the base (safely rejected — see Critical #2/#3), or (b) call `super().get_result(...)` and override only the small delta.

:yellow_circle: [hallucination] math.floor / math.ceil may not have an import in src/sentry/api/paginator.py:894 (confidence: 70)
`OptimizedCursorPaginator.get_item_key` calls `math.floor(value)` and `math.ceil(value)`. If `math` is not already imported at the top of `paginator.py`, this fails with `NameError` on first call. Verify the import exists; if not, add `import math`.
```suggestion
import math
```

:yellow_circle: [testing] No behavioral tests for the new features in src/sentry/spans/test_buffer.py:1 (confidence: 95)
Four substantive behavior changes land with zero new assertions: (1) Lua eviction at >1000 spans per segment, (2) `zadd` insertion ordering by `end_timestamp_precise`, (3) `OptimizedCursorPaginator` correctness, (4) negative-offset handling. The only test changes in this PR are fixture updates to add `end_timestamp_precise=1700000000.0`. Add tests that: assert cap-at-1000 eviction ordering (oldest-first), assert `BadPaginationError` / behavior on negative cursors, and assert `organization_auditlogs` ignores the flag for non-admins.

:yellow_circle: [consistency] Hardcoded magic number 1000 in Lua eviction in src/sentry/scripts/spans/add-buffer.lua:60 (confidence: 65)
`if span_count > 1000 then redis.call("zpopmin", set_key, span_count - 1000)` uses a literal 1000, disconnected from `self.max_segment_spans` on the Python side. If operators tune `max_segment_spans`, the Lua cap will not follow. Pass the cap in as a `KEYS`/`ARGV` parameter from Python.

## Nitpicks

:white_circle: [consistency] PR description is "Test 2" in src/sentry/api/paginator.py:1 (confidence: 100)
Zero-context PR body on a change that touches pagination, a custom Redis eviction path, Lua scripts, and a new audit-log feature flag. Future reviewers and incident responders have no trail of intent. Backfill the description with rationale for (a) the loop-depth reduction, (b) the negative-offset feature, (c) the removal of the Python-side span-count guard.

## Risk Metadata
Risk Score: 88/100 (CRITICAL) | Blast Radius: 9 files across pagination (base class), audit-log API, span ingest buffer, and Redis Lua script | Sensitive Paths: src/sentry/api/paginator.py (base class used project-wide), src/sentry/api/endpoints/organization_auditlogs.py (audit logs), src/sentry/scripts/spans/add-buffer.lua (data-plane)
AI-Authored Likelihood: HIGH — verbose justificatory comments on security-sensitive changes ("This is safe because..."), factually wrong claim about Django negative slicing, ~70-line duplicated paginator class, trivial "Test 2" PR body, unmotivated scope creep mixing Redis eviction with a pagination backdoor.

Recommendation: **request-changes**.
