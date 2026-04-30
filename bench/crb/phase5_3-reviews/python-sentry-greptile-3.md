## Summary
8 files changed, 480 lines added, 6 lines deleted. 6 findings (2 critical, 4 improvements, 0 nitpicks).
Error-upsampling feature has two correctness bugs that will defeat its caching layer and likely break query execution; remaining issues are silent-failure patterns and a sample-rate edge case in shared test infrastructure.

## Critical
:red_circle: [correctness] Cache key uses Python `hash()` — PYTHONHASHSEED randomization makes the cache per-process and effectively dead in src/sentry/api/helpers/error_upsampling.py:16 (confidence: 97)
The cache key is built as `f"error_upsampling_eligible:{organization.id}:{hash(tuple(sorted(snuba_params.project_ids)))}"`. Python's built-in `hash()` is randomized per-process via PYTHONHASHSEED (default since 3.3), so each web worker computes a different integer for the same project-ID tuple. Cache hits never occur across workers, the 60-second TTL provides no shared benefit, and `invalidate_upsampling_cache` cannot delete entries written by other processes — it computes a key with its own hash seed and silently no-ops. The function appears to work but defeats both its stated optimization and its stated invalidation guarantee.
```suggestion
def _upsampling_cache_key(organization_id: int, project_ids: Sequence[int]) -> str:
    sorted_ids = ",".join(str(p) for p in sorted(project_ids))
    return f"error_upsampling_eligible:{organization_id}:{sorted_ids}"

# in is_errors_query_for_error_upsampled_projects:
cache_key = _upsampling_cache_key(organization.id, snuba_params.project_ids)

# in invalidate_upsampling_cache:
cache_key = _upsampling_cache_key(organization_id, project_ids)
```

:red_circle: [correctness] `transform_query_columns_for_error_upsampling` injects inline `as count` alias into `y_axes` / `selected_columns` — non-standard, likely fails query parsing in src/sentry/api/helpers/error_upsampling.py:90 (confidence: 92)
The function rewrites `count()` to the string `"upsampled_count() as count"`, which is then passed unchanged into `top_events_timeseries`, `run_timeseries_query`, `run_top_events_timeseries_query`, and `timeseries_query` as part of `y_axes` / `timeseries_columns` / `selected_columns`. Sentry's column parser (`parse_function` in `sentry.search.events.fields`) expects bare function-call expressions like `count()` and derives the alias internally via `get_function_alias`; the SQL-style `<expr> as <alias>` syntax is reserved for equation/select-list contexts and will typically be rejected with `InvalidSearchQuery`. The same `as count` aliasing also produces duplicate column names if `query_columns` ever contains `count()` more than once.
```suggestion
def transform_query_columns_for_error_upsampling(
    query_columns: Sequence[str],
) -> list[str]:
    transformed_columns = []
    for column in query_columns:
        if column.lower().strip() == "count()":
            transformed_columns.append("upsampled_count()")
        else:
            transformed_columns.append(column)
    return transformed_columns
```

## Improvements
:yellow_circle: [correctness] Bare `except Exception: pass` on dict access in `_set_sample_rate_from_error_sampling` swallows real bugs in src/sentry/testutils/factories.py:346 (confidence: 95)
The first try/except wraps a chain of `.get()` calls on a plain dict. `dict.get()` never raises — the only exceptions reachable here are genuine bugs (e.g., `normalized_data` is not a dict, an unexpected non-dict value is stored under `contexts`). Silencing them with `pass` turns a loud, immediately-debuggable bug into a fixture that silently produces `client_sample_rate = None` and a vacuous test. Test factories should fail loudly so authors learn about malformed fixtures at the source.
```suggestion
def _set_sample_rate_from_error_sampling(normalized_data: MutableMapping[str, Any]) -> None:
    """Set 'sample_rate' on normalized_data if contexts.error_sampling.client_sample_rate is present and valid."""
    client_sample_rate = (
        normalized_data.get("contexts", {})
        .get("error_sampling", {})
        .get("client_sample_rate")
    )
    if client_sample_rate is not None:
        normalized_data["sample_rate"] = float(client_sample_rate)
```

:yellow_circle: [correctness] Bare `except Exception: pass` on `float()` conversion silently drops malformed sample rates in src/sentry/testutils/factories.py:354 (confidence: 95)
If `client_sample_rate` is a non-numeric value (string `"high"`, nested dict, etc.), `float()` raises `ValueError`/`TypeError`. The bare swallow leaves `normalized_data["sample_rate"]` unset and the test proceeds with partially-mutated data — failures will surface elsewhere with no link to the malformed fixture. Either let the exception propagate, or raise with a message naming the offending value.
```suggestion
try:
    normalized_data["sample_rate"] = float(client_sample_rate)
except (ValueError, TypeError) as exc:
    raise ValueError(
        f"error_sampling.client_sample_rate has non-numeric value {client_sample_rate!r}"
    ) from exc
```

:yellow_circle: [correctness] `if client_sample_rate:` drops valid sample rate of 0.0 in src/sentry/testutils/factories.py:353 (confidence: 95)
A `client_sample_rate` of 0.0 is semantically valid (the client dropped all events before sending the residual). The truthy guard treats 0.0 as falsy and silently skips assigning `normalized_data["sample_rate"]`, so downstream upsampling math sees no sample rate at all for the legitimate "drop everything" case. Use an explicit `None` check instead.
```suggestion
if client_sample_rate is not None:
    normalized_data["sample_rate"] = float(client_sample_rate)
```

:yellow_circle: [correctness] `invalidate_upsampling_cache` duplicates cache-key construction — drift risk in src/sentry/api/helpers/error_upsampling.py:64 (confidence: 88)
The cache key is built independently in `is_errors_query_for_error_upsampled_projects` and again in `invalidate_upsampling_cache`. If the two are ever updated out of sync (e.g., the writer's key gains a new field while the invalidator's does not), invalidation silently becomes a no-op with no error. Extract a single helper used by both sites — this also makes the PYTHONHASHSEED fix above a single edit instead of two.
```suggestion
def _upsampling_cache_key(organization_id: int, project_ids: Sequence[int]) -> str:
    sorted_ids = ",".join(str(p) for p in sorted(project_ids))
    return f"error_upsampling_eligible:{organization_id}:{sorted_ids}"
```

## Risk Metadata
Risk Score: 41/100 (MEDIUM) | Blast Radius: 70/100 (factories.py is shared test infrastructure used across the test suite; `error_upsampling.py` and `discover.py` add new public surface) | Sensitive Paths: none directly hit (testutils/factories.py is shared infra but does not match configured patterns)
AI-Authored Likelihood: MEDIUM

(11 additional findings below confidence threshold)
