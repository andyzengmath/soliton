Warning: consistency timed out (6/7 agents completed)

## Summary
3 files changed, 128 lines added, 10 lines deleted. 8 findings (4 critical, 4 improvements, 0 nitpicks).
PR claims "performance optimization" but relies on Django QuerySet behavior (negative indexing) that Django explicitly forbids; the opt-in path and an inadvertently-global base-paginator change both crash with `AssertionError`. Authorization gate has an unguarded None-deref, and the datetime-keyed `OptimizedCursorPaginator` builds SQL that will not type-check on Postgres.

## Critical

:red_circle: [correctness] Django ORM does not support negative QuerySet slicing — `OptimizedCursorPaginator.get_result` crashes in the advanced-features branch in src/sentry/api/paginator.py:884 (confidence: 99)
When `self.enable_advanced_features and cursor.offset < 0`, the code does `start_offset = cursor.offset` (negative int) and calls `list(queryset[start_offset:stop])`. Django explicitly raises `AssertionError: Negative indexing is not supported` on negative QuerySet slices — the inline comment ("The underlying Django ORM properly handles negative slicing automatically") is factually wrong. Cursor values are decoded directly from the `?cursor=` query parameter with no lower bound, so any user who passes the `enable_advanced` gate (superuser OR `has_global_access`) can trigger a guaranteed 500 on the audit-logs endpoint by crafting a cursor with a negative offset.
```suggestion
# Remove the negative-offset branch entirely. Django ORM does not support
# negative slicing; reverse traversal is already handled by cursor.is_prev.
start_offset = max(0, offset) if not cursor.is_prev else max(0, offset)
stop = start_offset + limit + extra
results = list(queryset[start_offset:stop])
```
[References: https://docs.djangoproject.com/en/stable/topics/db/queries/#limiting-querysets]

:red_circle: [correctness] `BasePaginator.get_result` is_prev branch silently accepts negative offsets — cross-endpoint DoS in src/sentry/api/paginator.py:179 (confidence: 97)
The refactored line `start_offset = max(0, offset) if not cursor.is_prev else offset` only clamps forward cursors. For `cursor.is_prev=True`, `offset` flows through unclamped to `list(queryset[start_offset:stop])`. Combined with the comment change in `Cursor.__init__` that advertises negative offsets as legitimate, any authenticated user on ANY paginated Sentry endpoint whose paginator inherits `BasePaginator.get_result` (including `DateTimePaginator`, which is the default for audit logs, alerts, releases, and many others) can send `?cursor=value:-N:1` to produce an unhandled 500. This is a non-opt-in behavior change — no `optimized_pagination` flag is required — so it is a global DoS vector triggered by a single crafted query string.
```suggestion
# Clamp unconditionally; negative offsets are never a valid Django slice index.
start_offset = max(0, offset)
stop = start_offset + limit + extra
results = list(queryset[start_offset:stop])
```
[References: https://cwe.mitre.org/data/definitions/400.html, https://docs.djangoproject.com/en/stable/topics/db/queries/#limiting-querysets]

:red_circle: [correctness] `OptimizedCursorPaginator.get_item_key` raises TypeError on every datetime value in src/sentry/api/paginator.py:830 (confidence: 99)
`get_item_key` computes `int(math.floor(value) if self._is_asc(for_prev) else math.ceil(value))`. The only caller site in this PR (`organization_auditlogs.py`) wires the paginator with `order_by="-datetime"`, so `self.key == "datetime"` and `value = getattr(item, "datetime")` is a `datetime.datetime` instance. `math.floor`/`math.ceil` require a numeric arg that implements `__floor__`/`__ceil__`; `datetime` does not — Python raises `TypeError: must be real number, not datetime`. This fires on every cursor construction after a successful page fetch, so even if the negative-slice bug is avoided, the very first page's `next`/`prev` cursor emission will crash with a 500.
```suggestion
def get_item_key(self, item, for_prev=False):
    value = getattr(item, self.key)
    if hasattr(value, "timestamp"):
        value = value.timestamp()  # datetime -> float seconds
    return int(math.floor(value) if self._is_asc(for_prev) else math.ceil(value))
```

:red_circle: [correctness] Unguarded `organization_context.member.has_global_access` — `AttributeError` when member is None in src/sentry/api/endpoints/organization_auditlogs.py:69 (confidence: 94)
`organization_context` on a `ControlSiloOrganizationEndpoint` carries a `.member` that is `None` for requests where the authenticated user is not a member of the organization — this includes superusers viewing a foreign org, API-token requests without an associated membership, and staff impersonation paths. Since `request.user.is_superuser` is checked with `or`, Python short-circuits only when the left side is `True`; when a non-superuser without membership hits the endpoint, the expression evaluates `organization_context.member.has_global_access` against `None` and raises `AttributeError`, producing an unhandled 500. This path was not reachable before this PR because the old code did not touch `.member`.
```suggestion
use_optimized = request.GET.get("optimized_pagination") == "true"
member = getattr(organization_context, "member", None)
enable_advanced = request.user.is_superuser or (
    member is not None and member.has_global_access
)
```

## Improvements

:yellow_circle: [correctness] `OptimizedCursorPaginator` inherits `BasePaginator.build_queryset` — integer-vs-datetime SQL type mismatch in src/sentry/api/paginator.py:815 (confidence: 88)
The class inherits from `BasePaginator`, whose `build_queryset` emits `queryset.extra(where=[f"{col} {operator} %s"], params=col_params)` with `col_params` sourced from `get_item_key` — i.e., a raw integer. `DateTimePaginator` exists precisely because datetime columns need the cursor value converted back to a `datetime` before it goes to the DB. Using `BasePaginator` directly against the `datetime` column on `AuditLogEntry` produces SQL like `"datetime" > 1700000000` which Postgres rejects with `operator does not exist: timestamp with time zone > integer`. The opt-in path will fail at the DB layer even once the negative-slice and `math.floor` bugs are fixed.
```suggestion
class OptimizedCursorPaginator(DateTimePaginator):
    def __init__(self, *args, enable_advanced_features=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.enable_advanced_features = enable_advanced_features
    # get_item_key / value_from_cursor inherited from DateTimePaginator
```

:yellow_circle: [correctness] Boundary check uses `offset` but slice used `start_offset` — off-by-one when `is_prev` and offset clamps differ in src/sentry/api/paginator.py:896 (confidence: 85)
Inside `OptimizedCursorPaginator.get_result`, the trim logic reads `elif len(results) == offset + limit + extra` while the slice earlier used `start_offset`. When `start_offset != offset` (e.g., forward cursor with a corrupted negative offset clamped to 0 by the else-branch, or any future non-identity mapping), the expected result-set length no longer matches the actual slice size, so the lookahead sentinel is not trimmed and one extra row leaks into the returned page. Fix by comparing against `start_offset` consistently, or by computing `stop` first and comparing against `stop - start_offset`.
```suggestion
elif len(results) == start_offset + limit + extra:
    results = results[:-1]
```

:yellow_circle: [consistency] `BasePaginator.get_result` silently clamps negative offsets — masks cursor-corruption and attacker probes in src/sentry/api/paginator.py:179 (confidence: 88)
Before this PR, a cursor with a negative offset produced a loud `AssertionError` that Sentry's error layer surfaced as a 500 with a stack trace — this was useful: it detected cursor-generation bugs and attacker probes of tampered cursors during development. The new `max(0, offset)` silently swallows the bad input and returns page one as if the cursor were valid. Combined with the fact that `Cursor` now explicitly allows negative offsets by comment, this makes cursor tampering undetectable and hides cursor-math regressions in upstream callers. Either reject with a 400 at the `Cursor` boundary (preferred) or emit a `logger.warning` so the clamp is observable.
```suggestion
# src/sentry/utils/cursors.py — reject negative offsets at parse time:
offset_int = int(offset)
if offset_int < 0:
    raise ValueError("cursor offset must be non-negative")
self.offset = offset_int
```

:yellow_circle: [testing] Zero test coverage for `OptimizedCursorPaginator`, the authorization gate, and the `BasePaginator` offset-clamp change in src/sentry/api/paginator.py:815 (confidence: 95)
The PR adds ~90 lines of new paginator logic, a new `?optimized_pagination=true` query-parameter-driven feature flag, a new `is_superuser or has_global_access` authorization gate, and a semantically-significant refactor of `BasePaginator.get_result` that affects every downstream paginator — yet no test files are modified. The four `:red_circle:` findings above (negative-slice crash, `math.floor(datetime)` crash, unclamped is_prev offset, None-member AttributeError) would all have been caught by a first-pass unit test. At minimum, add: (a) a regression test that a negative-offset prev cursor on `DateTimePaginator` does NOT return an HTTP 500, (b) a test that `OptimizedCursorPaginator` with `order_by="-datetime"` successfully paginates audit-log entries end-to-end against a real DB, and (c) a test that a non-superuser without `has_global_access` sending `?optimized_pagination=true` silently falls through to `DateTimePaginator` with HTTP 200 (pinning current silent-fallback behavior).
```suggestion
# tests/sentry/api/test_paginator.py
def test_base_paginator_rejects_negative_prev_offset(self):
    qs = AuditLogEntry.objects.all()
    paginator = DateTimePaginator(qs, order_by="-datetime")
    cursor = Cursor(value=0, offset=-5, is_prev=True)
    with pytest.raises((ValueError, AssertionError)):
        paginator.get_result(limit=10, cursor=cursor)

def test_optimized_paginator_datetime_key_paginates(self, audit_log_factory):
    for _ in range(3): audit_log_factory()
    qs = AuditLogEntry.objects.all()
    p = OptimizedCursorPaginator(qs, order_by="-datetime")
    result = p.get_result(limit=2, cursor=Cursor(0, 0, 0))
    assert len(result.results) == 2  # must not raise TypeError on datetime
```

## Risk Metadata
Risk Score: 54/100 (MEDIUM) | Blast Radius: `paginator.py` + `cursors.py` are foundational — every paginated endpoint inherits the `BasePaginator.get_result` behavior change (estimated 27+ downstream importers, cap-hit at 100 on this factor) | Sensitive Paths: none directly matched, but audit-log data is security-relevant (login, permission change, key rotation records)
AI-Authored Likelihood: HIGH — marketing-style docstring ("sophisticated pagination patterns", "high-traffic endpoints") paired with factually-wrong justifications ("Django ORM properly handles negative slicing automatically", "This is safe because permissions are checked at the queryset level"), ~95% duplication between `OptimizedCursorPaginator.get_result` and `BasePaginator.get_result`, semicolon-joined `__init__` body, and PR body of "Test 1" against a verbose product-speak title.

(4 additional findings below confidence threshold: `has_global_access` gate breadth [conf 80], misleading "safe because permissions are checked" comment [conf 70], undocumented `?optimized_pagination` query param [conf 82], no-op `get_item_key`/`value_from_cursor` duplicate overrides [conf 70])
