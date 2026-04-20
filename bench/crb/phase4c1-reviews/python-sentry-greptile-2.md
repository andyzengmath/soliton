## Summary
9 files changed, 183 lines added, 29 lines deleted. 10 findings (4 critical, 6 improvements, 0 nitpicks).
Critical: negative Django ORM slicing will raise `ValueError` at runtime in the new `OptimizedCursorPaginator` (backed by a factually-wrong inline comment about Django), and the audit-log endpoint gates this path on `has_global_access`, which in Sentry is not an admin scope.

## Critical

:red_circle: [correctness] Negative Django ORM queryset slicing raises ValueError at runtime — hallucinated framework behavior in src/sentry/api/paginator.py:840 (confidence: 98)
`OptimizedCursorPaginator.get_result` with `enable_advanced_features=True` and `cursor.offset < 0` assigns `start_offset = cursor.offset` (a negative integer) and then runs `list(queryset[start_offset:stop])`. Django QuerySets explicitly do NOT support negative indexing — the slice raises `ValueError: Negative indexing is not supported.` The inline comment "The underlying Django ORM properly handles negative slicing automatically" is false — a hallucinated framework claim that directly caused the bug. Any request reaching this branch 500s; a caller who can pass `?optimized_pagination=true` can supply `cursor=-N:0:0` to trigger the crash deterministically, yielding a cheap DoS/log-pollution vector. Very large negative magnitudes also risk `OverflowError`.
```suggestion
        # Remove the negative-offset branch entirely and the misleading comment.
        start_offset = max(0, cursor.offset)
        stop = start_offset + limit + extra
        results = list(queryset[start_offset:stop])
```
[References: https://owasp.org/Top10/A04_2021-Insecure_Design/, https://cwe.mitre.org/data/definitions/20.html]

:red_circle: [security] Broken access control — `has_global_access` misused as admin gate on audit-log endpoint in src/sentry/api/endpoints/organization_auditlogs.py:65 (confidence: 92)
The new branch is gated by `request.user.is_superuser or organization_context.member.has_global_access`, combined with the user-controlled query parameter `optimized_pagination=true`. In Sentry's permission model, `has_global_access` means "can access all teams" — in open-membership organizations this is True for ordinary members and it is NOT equivalent to admin scope. Treating it as an admin gate is a category-error. The path is further reachable by a client-controlled query-string toggle, creating a dual code path where only the default branch is well-tested. The inline assurance comment "This is safe because permissions are checked at the queryset level" is misleading — `build_queryset` does not re-verify authorization. Audit logs are security-sensitive (SSO changes, invites, secret rotations); an unprivileged member of an open-membership org can freely toggle into a less-tested retrieval path over sensitive data.
```suggestion
        # Remove the dual path. If a performance optimization is truly warranted,
        # apply it transparently to every caller. If gating is required, use an
        # admin-scoped check (is_active_superuser + staff mode) or a server-side
        # feature flag, never has_global_access or a client-controlled query param.
        response = self.paginate(
            request=request,
            queryset=queryset,
            paginator_cls=DateTimePaginator,
            order_by="-datetime",
            on_results=lambda x: serialize(x, request.user),
        )
```
[References: https://owasp.org/Top10/A01_2021-Broken_Access_Control/, https://cwe.mitre.org/data/definitions/285.html]

:red_circle: [correctness] `BasePaginator.get_result` `is_prev` branch still permits negative offsets in src/sentry/api/paginator.py:179 (confidence: 92)
The new guard reads `start_offset = max(0, offset) if not cursor.is_prev else offset`. The `else offset` arm applies no floor, so a negative offset carried by an `is_prev=True` cursor propagates directly into `queryset[start_offset:stop]`, triggering the same `ValueError: Negative indexing is not supported.` as the `OptimizedCursorPaginator` path. This affects every paginator inheriting from `BasePaginator` — not just the new subclass — and is reachable via any cursor-paginated endpoint, even without `optimized_pagination=true`.
```suggestion
        start_offset = max(0, offset)
        stop = start_offset + limit + extra
        results = list(queryset[start_offset:stop])
```

:red_circle: [security] Cursor parser silently accepts negative offsets codebase-wide in src/sentry/utils/cursors.py:25 (confidence: 90)
The change annotates that the `Cursor` constructor now accepts negative `offset` values ("Allow negative offsets for advanced pagination scenarios") with no rejection. Every paginator that consumes `Cursor.from_string(...)` — all of them — now happily parses `?cursor=-N:0:0` from client input. Each such endpoint becomes a pre-authz crash vector via the `ValueError` in Django slicing (amplified by log-pollution/alert fatigue). Centralizing this validation here is the correct defense-in-depth layer: rejecting negative offsets at parse time makes the paginator bugs unreachable from untrusted input.
```suggestion
        offset = int(offset)
        if offset < 0:
            raise ValueError("cursor offset must be non-negative")
        self.offset = offset
```

## Improvements

:yellow_circle: [correctness] `is_prev` post-trim length check uses stale `offset` instead of `start_offset` in src/sentry/api/paginator.py:851 (confidence: 95)
After the slice uses `start_offset`, the trim guard reads `len(results) == offset + limit + extra`, but `offset` is the raw `cursor.offset` (possibly clamped away or negative) while `start_offset` is what was actually used. The comparison silently disagrees with the slice boundary, causing the sentinel-element trim either not to fire when it should or to fire when the slice was already bounded, corrupting the page boundary.
```suggestion
        elif len(results) == start_offset + limit + extra:
            results = results[:-1]
```

:yellow_circle: [correctness] `get_item_key` calls `getattr(item, self.key)` where `self.key` carries Django order-by prefix in src/sentry/api/paginator.py:820 (confidence: 88)
`self.key` is set from the caller's `order_by="-datetime"`, so `getattr(item, "-datetime")` raises `AttributeError` because `"-datetime"` is not a valid Python attribute name. Even after fixing the prefix, `math.floor(value)` on a `datetime` instance raises `TypeError`. Both the prefix stripping and the datetime→timestamp conversion are absent from the override.
```suggestion
    def get_item_key(self, item, for_prev=False):
        raw_key = self.key.lstrip("-")
        value = getattr(item, raw_key)
        if isinstance(value, datetime):
            value = value.timestamp()
        return int(math.floor(value) if self._is_asc(for_prev) else math.ceil(value))
```

:yellow_circle: [correctness] `organization_context.member` dereferenced without null check in src/sentry/api/endpoints/organization_auditlogs.py:68 (confidence: 85)
`organization_context.member.has_global_access` will raise `AttributeError` when `.member` is `None` — this happens for superusers who are not actual organization members. The `is_superuser` short-circuit only saves the superuser case; any non-member who hits this endpoint via an alternate auth path will crash on the attribute access.
```suggestion
        member = organization_context.member
        enable_advanced = request.user.is_superuser or (
            member is not None and member.has_global_access
        )
```

:yellow_circle: [consistency] `OptimizedCursorPaginator` duplicates `BasePaginator` logic instead of calling `super()` in src/sentry/api/paginator.py:836 (confidence: 85)
The non-advanced branch of `OptimizedCursorPaginator.get_result` copy-pastes the offset/slice block verbatim from `BasePaginator.get_result`. Future bug fixes to the base class (including the negative-offset clamp above) will not propagate to the subclass, guaranteeing these two code paths drift. The duplication already contributed to the stale-`offset` trim-check bug in the base class.
```suggestion
    def get_result(self, limit=100, cursor=None, count_hits=False, known_hits=None, max_hits=None):
        if self.enable_advanced_features and cursor is not None and cursor.offset < 0:
            # Only reason to override: the advanced-feature branch.
            # Implement it safely (no negative slice) and return early.
            ...
            return advanced_result
        return super().get_result(limit=limit, cursor=cursor, count_hits=count_hits,
                                  known_hits=known_hits, max_hits=max_hits)
```

:yellow_circle: [cross-file-impact] `enable_advanced_features=True` may be silently dropped by base `Endpoint.paginate()` in src/sentry/api/endpoints/organization_auditlogs.py:37 (confidence: 75)
`self.paginate(..., enable_advanced_features=True)` must travel through the shared `Endpoint.paginate()` helper (not in this diff) to reach `OptimizedCursorPaginator.__init__`. If that helper only forwards a fixed, named set of arguments to `paginator_cls` and drops unknown kwargs, the flag defaults to `False`, the new subclass silently behaves identically to a plain paginator, and the feature is dead code with no warning. Confirm the kwargs-forwarding contract, or switch to a constructor parameter surfaced through the supported forwarding path (e.g., `paginator_options={"enable_advanced_features": True}`).
```suggestion
        # Verify sentry/api/base.py Endpoint.paginate forwards **kwargs to
        # paginator_cls(...). If it does not, pass the flag through whatever
        # supported mechanism paginate() exposes.
```

:yellow_circle: [consistency] Redirect-depth loop reduced 10000 → 1000 without rationale or safeguard in src/sentry/scripts/spans/add-buffer.lua:30 (confidence: 75)
The prior comment — "theoretically this limit means that segment trees of depth 10k may not be joined together correctly" — explicitly flagged 10,000 as the safety boundary. The 10x silent reduction drops both the cap and the comment, with no accompanying metric, test, or evidence that observed trace depths stay below the new cap. Deep trace trees that silently exceed 1,000 will be mis-attributed with no operator-visible signal.
```suggestion
-- Reduced from 10000 after measuring p99 observed redirect depth < N in prod.
-- Emit a metric when we actually reach the cap so regressions are detected.
for i = 0, 1000 do
    ...
    if i == 1000 then
        redis.call("incr", "span-buf:redirect-depth-cap-reached")
    end
```

## Risk Metadata
Risk Score: 53/100 (MEDIUM) | Blast Radius: `paginator.py`/`cursors.py` are foundational utilities imported across the API layer; the cursor parser change affects every cursor-paginated endpoint | Sensitive Paths: none matched literal patterns, but the audit-log endpoint is security-adjacent
AI-Authored Likelihood: HIGH

(3 additional findings below confidence threshold)
