## Summary
3 files changed, 128 lines added, 10 lines deleted. 8 findings (4 critical, 4 improvements, 0 nitpicks).
Negative queryset slicing in `OptimizedCursorPaginator` crashes Django ORM at runtime; `import math` is missing; the user-toggleable `optimized_pagination` query parameter exposes the buggy path to any org Owner/Manager.

## Critical

:red_circle: [correctness] Negative queryset slicing raises AssertionError — Django ORM does not support it, enables DoS in src/sentry/api/paginator.py:848 (confidence: 98)
The new branch inside `OptimizedCursorPaginator.get_result` explicitly slices the queryset with a negative `start_offset` when `cursor.offset < 0` and `enable_advanced_features` is True. Django's ORM raises `AssertionError("Negative indexing is not supported.")` on any queryset slice with a negative index. The comment claiming "The underlying Django ORM properly handles negative slicing automatically" is factually incorrect — Django's `QuerySet.__getitem__` explicitly rejects negative indices and negative slice bounds.

This has two concrete consequences: (1) any request reaching this branch will crash with an unhandled AssertionError, producing a 500 for the caller; (2) since `cursor` is fully attacker-controlled (parsed from `?cursor=` via `Cursor.from_string`), any caller who passes the `enable_advanced` gate can craft `?cursor=0:-N:0` to repeatedly trigger 500s, generating Sentry self-error events and audit-log noise (DoS amplification).
```suggestion
if cursor.offset < 0:
    raise BadCursor("offset must be non-negative")

start_offset = max(0, offset)
stop = start_offset + limit + extra
results = list(queryset[start_offset:stop])
```
[References: https://docs.djangoproject.com/en/stable/topics/db/queries/#limiting-querysets, https://owasp.org/Top10/A04_2021-Insecure_Design/, https://cwe.mitre.org/data/definitions/20.html, https://cwe.mitre.org/data/definitions/770.html]

:red_circle: [correctness] Negative offset passed to queryset slice in BasePaginator when cursor.is_prev=True — runtime crash in src/sentry/api/paginator.py:176 (confidence: 98)
The patch to `BasePaginator.get_result` changes offset clamping to `start_offset = max(0, offset) if not cursor.is_prev else offset`. When `cursor.is_prev` is True, `start_offset` is set directly to the raw cursor offset value with no lower-bound guard. `Cursor` stores `int(offset)` without validation, so `cursor.offset` can be negative. This makes `queryset[start_offset:stop]` callable with a negative start index, which Django raises as `AssertionError`. This is a latent crash in the existing (non-optimized) pagination path triggered whenever a prev-direction cursor carries a negative offset value. The adjacent comment claims "allow negative offsets to enable efficient bidirectional pagination" but the code actually floors negative offsets to 0 in the forward path — the misrepresentation further obscures real behavior.
```suggestion
start_offset = max(0, offset)
stop = start_offset + limit + extra
results = list(queryset[start_offset:stop])
```

:red_circle: [correctness] Missing `import math` — NameError at runtime in OptimizedCursorPaginator.get_item_key in src/sentry/api/paginator.py:819 (confidence: 95)
`get_item_key` calls `math.floor(value)` and `math.ceil(value)`, but the diff adds no `import math` statement to `paginator.py`. If the existing file does not already import `math`, every call to `get_item_key` raises `NameError: name 'math' is not defined`, crashing the optimized pagination path completely.
```suggestion
# Add at top of src/sentry/api/paginator.py:
import math
```

:red_circle: [security] Weak authorization gate on advanced pagination — has_global_access grants access to all Owners and Managers in src/sentry/api/endpoints/organization_auditlogs.py:65 (confidence: 90)
The `enable_advanced` gate uses `request.user.is_superuser or organization_context.member.has_global_access`. `has_global_access` is granted to any organization Owner or Manager (and can extend further under Open Membership settings), not only to security or audit administrators. Combined with a user-controlled query parameter (`?optimized_pagination=true`), this exposes an under-tested code path containing the negative-cursor-offset crash above to a much broader population than intended. Audit logs are a sensitive surface (admin actions, member invites, SSO events, token activity); gating a new, buggy code path behind anything weaker than `is_superuser` or an explicit `org:admin` scope is an Insecure Design issue per OWASP A01/A04.
```suggestion
from sentry import features

if request.access.has_scope("org:admin") and features.has(
    "organizations:optimized-audit-pagination", organization, actor=request.user
):
    paginator_cls = OptimizedCursorPaginator
else:
    paginator_cls = DateTimePaginator
```
[References: https://owasp.org/Top10/A01_2021-Broken_Access_Control/, https://cwe.mitre.org/data/definitions/285.html, https://owasp.org/Top10/A04_2021-Insecure_Design/]

## Improvements

:yellow_circle: [correctness] Boundary sentinel check uses raw `offset` instead of clamped `start_offset` — off-by-one result in src/sentry/api/paginator.py:855 (confidence: 85)
Inside `OptimizedCursorPaginator.get_result`, after fetching results, the code checks `len(results) == offset + limit + extra`. The slice that produced `results` used `start_offset` (which differs from `offset` when offset was negative and clamped to 0 via `max(0, offset)`). The comparison produces an incorrect length when offset was negative and start_offset was 0, causing the trailing sentinel element to not be stripped, returning one extra result to the caller.
```suggestion
elif len(results) == start_offset + limit + extra:
    results = results[:-1]
```

:yellow_circle: [consistency] Comment falsely claims a "performance optimization" where code is unchanged in src/sentry/utils/cursors.py:23 (confidence: 90)
The added comment states "Performance optimization: Allow negative offsets for advanced pagination scenarios" but `self.offset = int(offset)` is identical to the pre-patch code. No optimization was introduced; cursors already accepted any int value. The comment is misleading and will confuse future maintainers into thinking a behavioral change was made here.
```suggestion
self.offset = int(offset)
```

:yellow_circle: [consistency] Comment claims negative offsets are "allowed" but forward path clamps them to zero in src/sentry/api/paginator.py:176 (confidence: 90)
The comment says "allow negative offsets to enable efficient bidirectional pagination" but the actual code `start_offset = max(0, offset) if not cursor.is_prev else offset` floors negative offsets to 0 in the forward path and passes them through raw (crashing) in the backward path. Neither behavior matches the comment's description of intentional allowance.
```suggestion
# Forward pagination clamps offset to non-negative; backward path retains
# the raw cursor offset. Negative values in the backward path will raise
# AssertionError from Django's ORM and must be rejected upstream.
start_offset = max(0, offset) if not cursor.is_prev else offset
```

:yellow_circle: [consistency] OptimizedCursorPaginator docstring advertises features that are broken or nonexistent in src/sentry/api/paginator.py:815 (confidence: 88)
The class docstring claims three capabilities that are inaccurate: (1) "Negative offset support for efficient reverse pagination" — broken, Django raises AssertionError at runtime; (2) "Streamlined boundary condition handling" — no such handling exists in the code; (3) "Optimized query path for large datasets" — the code duplicates BasePaginator logic with no measurable optimization. All three claims will mislead maintainers about the class's actual behavior.
```suggestion
"""Cursor-based paginator variant for the audit-log endpoint.

Currently a near-duplicate of BasePaginator with an opt-in flag.
Pending real optimization work; do not rely on the negative-offset path.
"""
```

## Risk Metadata
Risk Score: 40/100 (MEDIUM) | Blast Radius: paginator.py is foundational across Sentry's API layer (~10+ importers) | Sensitive Paths: none matched, but endpoint touches audit-log access control
AI-Authored Likelihood: HIGH

(4 additional findings below confidence threshold)
