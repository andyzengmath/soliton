Warning: consistency timed out (5/6 agents completed)

## Summary
8 files changed, 480 lines added, 6 lines deleted. 10 findings (7 critical, 3 improvements, 0 nitpicks).
Python hash randomization makes upsampling cache non-functional across workers; unrelated git submodule `sentry-repo` added; core SnQL function and public helper untested.

## Critical

:red_circle: [testing] invalidate_upsampling_cache is completely untested in src/sentry/api/helpers/error_upsampling.py:67 (confidence: 95)
The `invalidate_upsampling_cache` function has no test coverage at all. Cache invalidation logic is a common source of subtle bugs — if the cache key does not match the key used during population, invalidation silently does nothing and callers continue to receive stale data. Without a test verifying the key symmetry between write and invalidation, this bug class is invisible. The risk is compounded by the hash-randomization bug in the cache key (see separate finding), which means the invalidation path may already be broken.
```suggestion
def test_invalidate_upsampling_cache_clears_cached_result(self):
    # Populate cache via is_errors_query_for_error_upsampled_projects
    # Call invalidate_upsampling_cache with same params
    # Assert subsequent call re-fetches (e.g., mock side_effect call count increments)
```

:red_circle: [testing] Main public entry point is_errors_query_for_error_upsampled_projects has no direct unit test in src/sentry/api/helpers/error_upsampling.py:11 (confidence: 92)
The new test file covers internal helpers (`_are_all_projects_error_upsampled`, `_is_error_focused_query`, `_should_apply_sample_weight_transform`) but never exercises the public function end-to-end. Bugs at the integration seam between helpers — for example, incorrect short-circuit ordering, wrong boolean combination of results, or a cache-hit path that diverges from the cache-miss path — will go undetected. Testing only private helpers without testing the public function that composes them is an incomplete strategy for a function with observable caching side effects.
```suggestion
# Add a TestIsErrorsQueryForErrorUpsampledProjects class that exercises:
#   - cache miss + all projects allowlisted + errors dataset      -> True
#   - cache miss + partial allowlist + errors dataset             -> False
#   - cache hit (stored True)  + transactions dataset             -> False
#   - cache hit (stored False) + errors dataset                   -> False
```

:red_circle: [security] Unexplained git submodule `sentry-repo` pinned to foreign SHA — supply-chain risk in sentry-repo:1 (confidence: 90)
The PR introduces a new git submodule `sentry-repo` pinned at commit `a5d290951def84afdcc4c88d2f1f20023fc36e2a` in a feature PR that is otherwise about error upsampling and has no documented need for this submodule. The PR description is only "Test 3". Submodule additions in unrelated PRs are a classic vector for supply-chain tampering: the pinned SHA could point at a fork, a prank branch, or a commit containing malicious code that executes at build/test/CI time (via `setup.py`, `conftest.py`, or install hooks). Submodule SHAs are not protected by branch/tag rules on the upstream, so the SHA itself must be audited.
```suggestion
# Remove the submodule from this PR unless its purpose is documented and reviewed:
git submodule deinit -f sentry-repo
git rm -f sentry-repo
rm -rf .git/modules/sentry-repo
```
[References: https://owasp.org/Top10/A08_2021-Software_and_Data_Integrity_Failures/, https://cwe.mitre.org/data/definitions/829.html]

:red_circle: [testing] New upsampled_count() SnQL function has no unit test in src/sentry/search/events/datasets/discover.py:1041 (confidence: 90)
The `upsampled_count()` SnQL function has no dedicated unit test. This function performs the core aggregation that the entire error-upsampling feature depends on. If the formula is wrong (wrong column, missing null-handling, wrong cast), every upsampled metric will be silently incorrect. Higher-level endpoint tests that assert on count values depend on this function's correctness but do not isolate it — a bug here would not be pinpointed by test failures alone, it would only surface as a mysteriously off-by-a-factor number in the integration test.
```suggestion
# Add a unit test that builds the aggregate directly and asserts on the generated SQL:
def test_upsampled_count_aggregate_shape(self):
    fn = DiscoverDatasetConfig().function_converter["upsampled_count"]
    expr = fn.snql_aggregate([], "the_alias")
    rendered = str(expr)
    assert "sample_weight" in rendered
    assert "sum" in rendered
    assert "toInt64" in rendered
```

:red_circle: [correctness] Python hash randomization makes cross-process cache keys non-deterministic, rendering cache a no-op in multi-worker deployments in src/sentry/api/helpers/error_upsampling.py:17 (confidence: 90)
The cache key uses `hash(tuple(sorted(snuba_params.project_ids)))` where `hash()` is Python's built-in hash function. Python 3.3+ enables hash randomization by default (`PYTHONHASHSEED` is randomized per process start), so the same tuple of project IDs produces a different hash value in each worker process. In a multi-worker deployment with a shared cache backend (Redis, Memcached), a key written by worker A can never be read by worker B. The cache silently degrades to a no-op — every request recomputes `_are_all_projects_error_upsampled`. The intended performance optimization is entirely defeated without any visible error, and the branch name `error-upsampling-race-condition` suggests the author was already concerned about this area.
```suggestion
project_ids_key = "-".join(str(pid) for pid in sorted(snuba_params.project_ids))
cache_key = f"error_upsampling_eligible:{organization.id}:{project_ids_key}"
```

:red_circle: [correctness] upsampled_count() uses sum(sample_weight) with no NULL fallback, silently undercounts events lacking sample_weight in src/sentry/search/events/datasets/discover.py:1041 (confidence: 88)
The SnQL aggregate `sum(sample_weight)` treats NULL `sample_weight` values as 0 because ClickHouse's `sum` ignores NULLs. Events ingested before upsampling was enabled, or events from non-upsampled SDKs, will have NULL `sample_weight` and contribute nothing to the sum. A mixed dataset of weighted and unweighted events will produce a count lower than the true number of errors with no warning or error surfaced. The correct semantics are that an unweighted event counts as 1 (weight defaults to 1.0), so the aggregate must defend against NULL explicitly.
```suggestion
snql_aggregate=lambda args, alias: Function(
    "toInt64",
    [Function("sum", [Function("ifNull", [Column("sample_weight"), 1])])],
    alias,
),
```

:red_circle: [cross-file-impact] store_event unconditionally mutates normalized_data["sample_rate"] for any event with contexts.error_sampling — may corrupt unrelated tests across the test suite in src/sentry/testutils/factories.py:1045 (confidence: 85)
The new hook `_set_sample_rate_from_error_sampling(normalized_data)` fires inside `store_event` for every call, not just for tests that opted into the upsampling feature. Any test constructing an event with `contexts.error_sampling.client_sample_rate` set will have `normalized_data["sample_rate"]` silently overwritten before storage. `store_event` is called across hundreds of test files; tests that include an error-sampling context for unrelated reasons but assert on the stored event's `sample_rate` field will see mutated values and may fail loudly — or, worse, pass with wrong data. This is an implicit, invisible side effect in a high-use test utility.
```suggestion
# Gate the hook behind an explicit opt-in keyword so callers are not affected silently:
def store_event(self, data, ..., apply_error_sampling_context: bool = False):
    ...
    normalized_data = manager.get_data()
    if apply_error_sampling_context:
        _set_sample_rate_from_error_sampling(normalized_data)
```

## Improvements

:yellow_circle: [testing] top_events query path (topEvents > 0) not exercised by any new upsampling test in tests/snuba/api/endpoints/test_organization_events_stats.py:1 (confidence: 88)
None of the 4 new endpoint tests set `topEvents > 0`, so the top-events code path — which is one of the three branches modified in `organization_events_stats.py` to apply the upsampling transform — is never reached. Top-events queries construct the query differently from standard timeseries queries; the upsampling transform may be applied incorrectly, skipped, or cause a KeyError that only surfaces in that path. This branch has no regression protection for the upsampling feature.
```suggestion
# Add a test variant with topEvents=5 + event.type:error query, allowlisted projects,
# and assert that the response series reflect upsampled counts (1 event @ 0.1 rate -> 10).
```

:yellow_circle: [testing] Cache stale-value behavior after allowlist change is untested in tests/sentry/api/helpers/test_error_upsampling.py:1 (confidence: 88)
No test demonstrates what happens when the cache holds a previously-computed positive result after an organization is removed from the upsampling allowlist. A stale cached `True` would incorrectly continue upsampling queries after removal, potentially inflating metrics for projects that opted out. The test suite should verify that either the 60s TTL is acceptable for the use case or that `invalidate_upsampling_cache` is called on allowlist changes and that invalidation actually flushes the cached entry.
```suggestion
def test_stale_cache_not_served_after_invalidation(self):
    # 1) Allowlist includes org -> first call caches True
    # 2) Remove org from allowlist
    # 3) invalidate_upsampling_cache(org_id, project_ids)
    # 4) Next call must return False, not the cached True
```

:yellow_circle: [testing] RPC query path (use_rpc=True) not covered by any new upsampling test in tests/snuba/api/endpoints/test_organization_events_stats.py:1 (confidence: 85)
None of the 4 new tests force `use_rpc=True`. If the upsampling transform is applied before or after the RPC serialization step incorrectly, RPC-backed queries will return wrong results. RPC is the newer of the three modified query paths (top-events-rpc, standalone-rpc, standard) and more likely to have integration gaps with newly added query transforms. Without a test covering this path, silent regressions are possible when RPC becomes the default or when a feature flag flips the endpoint into RPC mode.
```suggestion
# Add a test variant that enables the RPC code path (via feature flag or dataset)
# and asserts upsampled_count() columns are serialized into the RPC payload.
```

## Risk Metadata
Risk Score: 37/100 (MEDIUM) | Blast Radius: HIGH — touches `testutils/factories.py` (used by hundreds of tests), `datasets/discover.py` (core query layer), and `organization_events_stats.py` (primary stats endpoint) | Sensitive Paths: none matched
AI-Authored Likelihood: LOW

(6 additional findings below confidence threshold)
