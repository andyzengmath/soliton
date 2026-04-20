## Summary
8 files changed, 480 lines added, 6 lines deleted. 11 findings (5 critical, 6 improvements, 0 nitpicks).
8 files changed. 11 findings (5 critical, 6 improvements). No tests for `is_errors_query_for_error_upsampled_projects` — the main cached entry point for the entire feature.

## Critical

:red_circle: [testing] No tests for `is_errors_query_for_error_upsampled_projects` — the main cached entry point in src/sentry/api/helpers/error_upsampling.py:12 (confidence: 95)
The function that composes eligibility + 60s caching for the entire feature has zero unit tests. No test covers cache hit, cache miss, TTL expiry, or `invalidate_upsampling_cache`. A bug here silently disables upsampling for all users or applies it to ineligible queries.
```suggestion
Add tests covering: (1) cache miss calls underlying check once; (2) second call within TTL does NOT re-call check; (3) after TTL expiry or invalidate, check is called again; (4) cached False is still returned correctly.
```

:red_circle: [correctness] `hash()` of tuple is PYTHONHASHSEED-randomized, making cache keys non-deterministic across worker processes in src/sentry/api/helpers/error_upsampling.py:1 (confidence: 92)
Both `is_errors_query_for_error_upsampled_projects` and `invalidate_upsampling_cache` build their cache key using `hash(tuple(sorted(project_ids)))`. Python randomizes hash seeds per process by default (PYTHONHASHSEED). In a multi-worker WSGI deployment (gunicorn, uWSGI — standard for Sentry), the same list of project IDs produces a different integer from `hash()` in each worker process. Worker A caches under one key; worker B computes a different key, gets a miss, and re-computes. `invalidate_upsampling_cache` called from worker A deletes a key worker B never wrote, leaving stale entries. The result is either wasted work (cache always misses) or stale data (invalidation doesn't clear all entries).
```suggestion
import hashlib

def _project_ids_hash(project_ids):
    key_material = ",".join(str(pid) for pid in sorted(project_ids))
    return hashlib.md5(key_material.encode()).hexdigest()[:16]

cache_key = f"error_upsampling_eligible:{organization.id}:{_project_ids_hash(snuba_params.project_ids)}"
```

:red_circle: [testing] No unit test for the new `upsampled_count` SnQL function in src/sentry/search/events/datasets/discover.py:1041 (confidence: 92)
The new `upsampled_count` SnQL function is exercised only indirectly via the HTTP integration test. No unit test validates the generated expression (`toInt64(sum(sample_weight))`), alias stability, or result type. Expression-level regressions are caught late or not at all.
```suggestion
Add a unit test that resolves `upsampled_count()` via the discover QueryBuilder and asserts the produced SnQL contains `sum(sample_weight)` and `toInt64`, and that the alias matches downstream consumers.
```

:red_circle: [correctness] `sum(sample_weight)` silently undercounts events where `sample_weight` is NULL in src/sentry/search/events/datasets/discover.py:1041 (confidence: 88)
The `upsampled_count` SnQL function computes `toInt64(sum(Column("sample_weight")))`. In ClickHouse, NULL values are ignored by `sum()`. Any event row with a NULL `sample_weight` (legacy events ingested before upsampling, or events from mixed queries) contributes 0 to the sum instead of 1. This causes `upsampled_count()` to silently undercount with no warning — a data-correctness bug.
```suggestion
snql_aggregate=lambda args, alias: Function(
    "toInt64",
    [Function("sum", [Function("ifNull", [Column("sample_weight"), 1])])],
    alias,
),
```

:red_circle: [security] Unjustified new `sentry-repo` git submodule pinned to unverified commit (supply-chain risk) in sentry-repo:1 (confidence: 85)
The PR introduces a new git submodule `sentry-repo` pinned to commit `a5d290951def84afdcc4c88d2f1f20023fc36e2a` with no justification in the PR description ("Test 3") and no `.gitmodules` URL visible in the diff. If the submodule URL points to an untrusted repository (fork/attacker-controlled mirror), this is a supply-chain attack vector (OWASP A08). Submodule commits can be force-pushed/rewritten upstream; even a currently-safe pin may become malicious.
```suggestion
Before merging: (1) verify `.gitmodules` points to a trusted internal/official repo; (2) confirm the commit exists upstream and is signed/tagged; (3) require the author to justify why the submodule is needed (the PR is about error upsampling, not vendoring); (4) if unrelated, remove from this PR and land separately.
```
[References: https://owasp.org/Top10/A08_2021-Software_and_Data_Integrity_Failures/]

## Improvements

:yellow_circle: [consistency] Repeated conditional upsampling transformation logic appears 3 times in src/sentry/api/endpoints/organization_events_stats.py:218 (confidence: 95)
The identical block `if upsampling_enabled: final_columns = transform_query_columns_for_error_upsampling(query_columns)` appears in three separate code paths (top_events, RPC, standard). This violates DRY; if the transformation logic changes, three places must be updated. The input and operation are identical across all three occurrences.
```suggestion
should_upsample = is_errors_query_for_error_upsampled_projects(
    snuba_params, organization, dataset, request
)
final_columns = (
    transform_query_columns_for_error_upsampling(query_columns)
    if should_upsample
    else query_columns
)
# Then use final_columns in all 3 paths without further conditionals.
```

:yellow_circle: [consistency] Redundant intermediate variable `upsampling_enabled = should_upsample` in src/sentry/api/endpoints/organization_events_stats.py:218 (confidence: 90)
`upsampling_enabled` is assigned directly from `should_upsample` without any processing. The alias obscures intent and adds no value.
```suggestion
Remove the intermediate assignment; use `should_upsample` directly (or rename at the original assignment).
```

:yellow_circle: [security] `_is_error_focused_query` naive substring check matches negations and unrelated tokens in src/sentry/api/helpers/error_upsampling.py:130 (confidence: 90)
`"event.type:error" in query.lower()` is a substring match that matches negated queries (`!event.type:error`, `NOT event.type:error`), unrelated tokens (`event.type:errors-group`, `event.type:error_boundary`), and any free-text field containing the literal substring. This lets upsampling apply to queries that explicitly exclude errors — leading to incorrect billing/quota/displayed counts.
```suggestion
from sentry.api.event_search import parse_search_query

def _is_error_focused_query(request):
    try:
        tokens = parse_search_query(request.GET.get("query", ""))
    except Exception:
        return False
    return any(
        getattr(t, "key", None) and t.key.name == "event.type"
        and t.operator == "=" and t.value.raw_value == "error"
        for t in tokens
    )
```
[References: https://owasp.org/Top10/A04_2021-Insecure_Design/]

:yellow_circle: [testing] `transform_query_columns_for_error_upsampling` not tested with count_unique / count_if alongside count() in src/sentry/api/helpers/error_upsampling.py:88 (confidence: 90)
Tests cover `count()`, `COUNT()`, and whitespace but NOT a mixed list like `["count()", "count_unique(user)"]`. A naive string-replace regression could corrupt `count_unique` or `count_if`. Analysts routinely combine these in queries.
```suggestion
Add: test_transform_does_not_modify_count_unique; test_transform_rewrites_count_but_not_count_unique_in_same_list; test_transform_does_not_modify_count_if.
```

:yellow_circle: [testing] No test for null/missing `sample_weight` in upsampled count aggregation in src/sentry/api/helpers/error_upsampling.py:1 (confidence: 88)
No test asserts the behavior of `sum(sample_weight)` when `sample_weight` is absent (legacy events, mixed datasets). A silent NULL/0 bucket is not caught. This is the test counterpart to the correctness finding about `ifNull`.
```suggestion
Add an integration test that stores an event without `sample_weight` in an allowlisted project and asserts the bucket count is 1 (not 0/NULL).
```

:yellow_circle: [testing] No integration test for the top_events or RPC (use_rpc) query paths in tests/snuba/api/endpoints/test_organization_events_stats.py:3552 (confidence: 85)
The PR wires upsampling into three query paths — top_events, RPC, standard — but integration tests only exercise the standard path. top_events is user-facing (Events chart) and RPC is a separate backend route. A bug in either wiring would go undetected.
```suggestion
Add tests using `topEvents=5` and `useRpc=1` request parameters that assert upsampled counts reach the response.
```

## Risk Metadata
Risk Score: 38/100 (MEDIUM) | Blast Radius: discover.py / factories.py / events endpoint are high-fanout (score 70) | Sensitive Paths: none hit
AI-Authored Likelihood: MEDIUM

(3 additional findings below confidence threshold)
