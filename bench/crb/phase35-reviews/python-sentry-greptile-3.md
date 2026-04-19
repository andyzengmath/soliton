Warning: hallucination, cross-file-impact, consistency, historical-context could not verify against source (repo is a shim/empty checkout — 4/8 agents returned no findings).

## Summary
8 files changed, 480 lines added, 6 lines deleted. 7 findings (1 critical, 6 improvements, 0 nitpicks).
New error-upsampling feature wires `upsampled_count()` into four query-execution branches of `organization_events_stats`, gated by an in-memory cache whose key is non-portable across workers; plus a stray submodule and several test-coverage gaps.

## Critical
:red_circle: [correctness] Cache key uses `hash()` which is non-deterministic across processes (PYTHONHASHSEED) in src/sentry/api/helpers/error_upsampling.py:16 (confidence: 97)
The cache key is built with `hash(tuple(sorted(snuba_params.project_ids)))`. Since Python 3.3, hash randomization is enabled by default via `PYTHONHASHSEED`. Each worker process (gunicorn/uWSGI) gets a different random seed, so the same set of project IDs produces a different hash value in every worker. In any multi-worker deployment every worker independently misses the cache on every request and re-executes `_are_all_projects_error_upsampled`, so the cache — described as the primary mechanism for avoiding repeated allowlist lookups — provides zero cross-request benefit. The TTL is also moot because nothing survives a reload. Given the comment block explicitly justifies the cache as a perf optimization for "high-traffic periods", this silently defeats the stated goal.
```suggestion
    cache_key = (
        f"error_upsampling_eligible:{organization.id}:"
        f"{','.join(str(pid) for pid in sorted(snuba_params.project_ids))}"
    )
```

## Improvements
:yellow_circle: [correctness] `transform_query_columns_for_error_upsampling` only rewrites the literal string `"count()"` in src/sentry/api/helpers/error_upsampling.py:85 (confidence: 88)
The match is `column.lower().strip() == "count()"` — strict equality. Common Discover column expressions are left untransformed: `count_unique(user)`, `count_if(...)`, `count(id)`, and equations like `equation|count() / count_unique(user)`. When a y_axis list mixes `count()` with any of these, the response row contains an upsampled count alongside a non-upsampled count, producing ratios and comparisons that are internally inconsistent (e.g. an upsampled numerator over a raw denominator in a per-user error rate). This is a silent data-correctness bug, not a UX one — the caller has no signal that the mix happened.
```suggestion
for column in query_columns:
    stripped = column.lower().strip()
    if stripped == "count()":
        transformed_columns.append("upsampled_count() as count")
    elif stripped.startswith(("count_unique(", "count_if(", "count(")) or "count()" in stripped:
        # Either extend to cover these variants or explicitly bail out of upsampling
        # for this query so mixed/inconsistent aggregates are never produced.
        return list(query_columns)
    else:
        transformed_columns.append(column)
```

:yellow_circle: [correctness] Stale cached `True` keeps applying `upsampled_count()` for up to 60s after a project is removed from the allowlist in src/sentry/api/helpers/error_upsampling.py:14 (confidence: 82)
The result of `_are_all_projects_error_upsampled` is cached for 60s with no invalidation hook wired into the option writer. If a project is delisted during the window, the cached `True` silently keeps rewriting `count()` to `upsampled_count()`, inflating the reported event count by the `sample_weight` multiplier. The inverse (stale `False` after listing) just delays upsampling and is benign; the stale `True` actively corrupts metrics. The branch name itself (`error-upsampling-race-condition`) and the inline comment ("eventual consistency is acceptable") suggest the author is aware — but nothing calls `invalidate_upsampling_cache` from the option-change path.
```suggestion
# Either lower the TTL significantly or call invalidate_upsampling_cache(...)
# from the code path that updates `issues.client_error_sampling.project_allowlist`.
cache.set(cache_key, is_eligible, 10)  # down from 60
```

:yellow_circle: [supply-chain] Submodule `sentry-repo` added with no matching `.gitmodules` entry in sentry-repo:1 (confidence: 75)
The PR records a gitlink at `sentry-repo` pointing at commit `a5d290951def84afdcc4c88d2f1f20023fc36e2a`, but no `.gitmodules` is added or modified. Consequences: (a) reviewers cannot audit which upstream URL this resolves to; (b) `git submodule update --init` is a no-op because no URL is declared, so the pinned SHA is never actually fetched by normal clones, which almost certainly means this was an accidental add rather than a deliberate vendoring; (c) any downstream consumer that does configure a URL via `git config submodule.sentry-repo.url` (local, CI, `insteadOf` rewrite) could point the submodule at arbitrary code and have it pulled in — an OWASP A08 supply-chain gap. Greptile flagged the same issue as "empty submodule".
```suggestion
# Most likely this is a stray add — drop it:
#   git rm --cached sentry-repo && git commit -m "Remove accidental sentry-repo submodule"
# If the submodule is intentional, commit a .gitmodules declaring the pinned URL:
# [submodule "sentry-repo"]
#     path = sentry-repo
#     url = https://github.com/getsentry/sentry.git
```

:yellow_circle: [testing] `is_errors_query_for_error_upsampled_projects` caching path is entirely untested in tests/sentry/api/helpers/test_error_upsampling.py:1 (confidence: 90)
The unit tests exercise `_are_all_projects_error_upsampled` directly and bypass the caching wrapper that all production callers go through. Not tested: cache hit returning the cached value, cache miss populating the cache, cache-key org scoping, interaction with `_should_apply_sample_weight_transform` on a cached hit. Given that the cache-key bug above (PYTHONHASHSEED) would be caught instantly by a "same inputs → same key" assertion, this gap directly enabled the critical bug to ship.
```suggestion
@patch("sentry.api.helpers.error_upsampling.cache")
def test_cache_key_is_stable_across_calls(self, mock_cache):
    mock_cache.get.return_value = None
    is_errors_query_for_error_upsampled_projects(self.snuba_params, self.organization, errors, self.request)
    is_errors_query_for_error_upsampled_projects(self.snuba_params, self.organization, errors, self.request)
    keys = [call.args[0] for call in mock_cache.get.call_args_list]
    assert keys[0] == keys[1]  # fails today under PYTHONHASHSEED across processes
```

:yellow_circle: [testing] `mock_options.get.return_value = ...` intercepts every option key, not just the allowlist in tests/sentry/api/helpers/test_error_upsampling.py:36 (confidence: 88)
`return_value` on a `MagicMock` answers any argument tuple with the same value, so `options.get("completely.wrong.key")` would also return the project list. If the production code ever typos the option key, or a refactor renames it, every existing test continues to pass. Use `side_effect` keyed on the real option name so a key mismatch fails loudly.
```suggestion
def _option_side_effect(key, default=None):
    if key == "issues.client_error_sampling.project_allowlist":
        return allowlisted
    return default
mock_options.get.side_effect = _option_side_effect
```

:yellow_circle: [testing] Column-transform tests miss the variants that actually matter in tests/sentry/api/helpers/test_error_upsampling.py:56 (confidence: 85)
The existing cases cover `count()`, `COUNT()`, `" count() "` — every one of which already passes. Missing: `count(id)`, `count_unique(user)`, `count_if(...)`, equations that embed `count()`, and a column already in upsampled form. These are the cases where the transform is actually ambiguous and where the correctness finding above reproduces.
```suggestion
@pytest.mark.parametrize("column,should_transform", [
    ("count()", True),
    ("count(id)", False),
    ("count_unique(user)", False),
    ("count_if(transaction.status, equals, ok)", False),
    ("equation|count() / count_unique(user)", False),  # or explicitly handled
])
def test_transform_count_variants(self, column, should_transform):
    ...
```

## Risk Metadata
Risk Score: 36/100 (MEDIUM) | Blast Radius: ~7 importers est. (high-traffic endpoint + core Discover dataset + test factory used across test suite); local grep unavailable (shim repo) | Sensitive Paths: none matched
AI-Authored Likelihood: LOW — branch name "error-upsampling-race-condition" suggests active human debugging; comment style is unusually marketing-flavored but no explicit AI attribution visible.

(4 findings suppressed below confidence threshold of 85: narrow `_is_error_focused_query` substring match, hash-collision security framing, spans/metrics dataset test gap, missing test for events without `client_sample_rate`. The `factories.py` "unbound `client_sample_rate`" finding was a false positive — the PR does initialize `client_sample_rate = None` before the `try` block and was dropped.)
