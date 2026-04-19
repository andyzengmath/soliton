## Summary
8 files changed, 480 lines added, 6 lines deleted. 16 findings (8 critical, 8 improvements, 0 nitpicks).
Error-upsampling feature has pervasive correctness hazards: process-local `hash()` in cache keys, an unparseable inline alias string, references to an unregistered option and a non-existent `sample_weight` column, and a stray unused git submodule — all under a branch literally named `error-upsampling-race-condition`.

## Critical

:red_circle: [supply-chain] Git submodule added with no `.gitmodules` entry in `sentry-repo:1` (confidence: 95)
The diff adds gitlink mode 160000 at path `sentry-repo` pinned to `a5d290951def84afdcc4c88d2f1f20023fc36e2a`, but no `.gitmodules` stanza is added anywhere. `git submodule update --init` will fail because no URL is recorded; recursive clones end up with an uninitialized, unusable submodule. There is also no rationale in the PR for vendoring an entire copy of sentry here. This is either an accidental commit or a supply-chain placeholder; either way it must not ship.
```suggestion
# Remove the gitlink entirely:
#   git rm sentry-repo
# Or, if intentional, add a real .gitmodules entry with a pinned, reviewed URL:
# [submodule "sentry-repo"]
#     path = sentry-repo
#     url = https://github.com/getsentry/sentry.git
```
[References: https://owasp.org/Top10/A08_2021-Software_and_Data_Integrity_Failures/, https://cwe.mitre.org/data/definitions/1357.html]

:red_circle: [correctness] `hash()` on tuple produces process-local cache keys in `src/sentry/api/helpers/error_upsampling.py:153` (confidence: 97)
`cache_key = f"error_upsampling_eligible:{organization.id}:{hash(tuple(sorted(snuba_params.project_ids)))}"` relies on Python's built-in `hash()`, which is salted per-process via `PYTHONHASHSEED` (random by default since CPython 3.3). Every gunicorn/celery worker computes a different key for the same org + project-set, so the shared Redis cache is never hit across workers — defeating the "performance optimization for high-traffic periods" described in the docstring. Worse, if two workers' hashes collide for different project-id tuples, worker A can read worker B's cached eligibility for a *different* project set, silently applying (or suppressing) upsampling for the wrong tenant. `invalidate_upsampling_cache` at line 199 has the same bug and will therefore fail to invalidate the entry in most workers.
```suggestion
project_ids_key = ",".join(str(pid) for pid in sorted(snuba_params.project_ids))
cache_key = f"error_upsampling_eligible:{organization.id}:{project_ids_key}"
# Apply the same change in invalidate_upsampling_cache.
```
[References: https://docs.python.org/3/reference/datamodel.html#object.__hash__, https://docs.python.org/3/using/cmdline.html#envvar-PYTHONHASHSEED]

:red_circle: [correctness] Inline `" as count"` alias in column list is not Sentry/Snuba syntax in `src/sentry/api/helpers/error_upsampling.py:221` (confidence: 88)
`transform_query_columns_for_error_upsampling` rewrites `"count()"` to the single-string entry `"upsampled_count() as count"`, and this string is passed as an element of `y_axes` / `timeseries_columns` / `selected_columns` in four downstream callsites. Sentry's `event_search` / query-builder parses each entry as a single function or field and does not accept SQL-style inline `" as <alias>"` aliasing inside one string. The most likely runtime outcomes are a parse error, or the string being treated as an unknown column whose key in the response is `"upsampled_count() as count"` rather than `"count"` — which breaks every test assertion of the form `data[0][1][0]["count"]`. The unit test only asserts the *output list* of the transform function, so end-to-end breakage is not caught.
```suggestion
# Emit the bare function and remap the key at response time, or register
# upsampled_count as an alias of "count" in the SnQL function table:
transformed_columns.append("upsampled_count()")
```

:red_circle: [hallucination] Reads unregistered option `issues.client_error_sampling.project_allowlist` in `src/sentry/api/helpers/error_upsampling.py:184` (confidence: 88)
`options.get("issues.client_error_sampling.project_allowlist", [])` is written in `dict.get`-style with a default, but Sentry's `sentry.options.get(key, silent=False)` does not take a positional default — it raises `UnknownOption` for unregistered keys. This PR adds no `register(...)` call in `sentry/options/defaults.py` or equivalent. The tests pass only because they mock the entire `options` module; the first non-mocked production request will raise.
```suggestion
# In sentry/options/defaults.py (or similar):
register(
    "issues.client_error_sampling.project_allowlist",
    type=Sequence,
    default=[],
    flags=FLAG_AUTOMATOR_MODIFIABLE,
)
```

:red_circle: [hallucination] `upsampled_count` references a `sample_weight` column that isn't in the errors schema in `src/sentry/search/events/datasets/discover.py:282` (confidence: 80)
`Function("sum", [Column("sample_weight")])` assumes a top-level `sample_weight` column on the errors/events Clickhouse table. That column is not part of Sentry's standard events schema, and nothing in this PR adds it (no Snuba schema migration, no ingest-processor change). The companion factory mutation writes `normalized_data["sample_rate"] = float(client_sample_rate)` — a different key, and the value is the raw rate rather than the inverse (weight = 1/rate). Running this against the real errors dataset will fail with an unknown-column error from Snuba; the integration tests pass only because they exercise mocked options and Snuba fixtures that don't enforce the real schema.
```suggestion
# 1. Add sample_weight to the Snuba errors schema + consumer pipeline that
#    writes weight = 1 / contexts.error_sampling.client_sample_rate.
# 2. In factories, mirror the production pipeline:
#    normalized_data["sample_weight"] = 1.0 / float(client_sample_rate)
```

:red_circle: [testing] Public caching function `is_errors_query_for_error_upsampled_projects` has zero unit coverage in `src/sentry/api/helpers/error_upsampling.py:139` (confidence: 97)
`tests/sentry/api/helpers/test_error_upsampling.py` imports only the four private helpers and never calls the public function. The entire `cache.get` / `cache.set` path — cache miss, cache hit, cache-hit-with-dataset-mismatch, cached-False short-circuit — is untested. This is the riskiest addition in the PR and has no unit coverage at all.
```suggestion
# Add tests that call the public function directly, patching options and
# asserting options.get call_count across repeated invocations to prove
# cache hits vs. misses. Use django.core.cache.cache.clear() in setUp.
```

:red_circle: [testing] No `cache.clear()` in setUp/tearDown leaks state across tests in `tests/sentry/api/helpers/test_error_upsampling.py:360` (confidence: 95)
The new tests write to `sentry.utils.cache.cache` (60s TTL) and never clear it. Django's test runner does not flush the cache between methods. A prior test in the suite that populates `error_upsampling_eligible:<org_id>:<hash>` will be read as a stale hit by later tests that share the same org/project combination, producing false passes (or false failures). Applies to both the unit and integration test classes.
```suggestion
from django.core.cache import cache

def setUp(self):
    cache.clear()
    super().setUp()
    ...

def tearDown(self):
    cache.clear()
    super().tearDown()
```

:red_circle: [testing] `invalidate_upsampling_cache` is untested in `src/sentry/api/helpers/error_upsampling.py:193` (confidence: 92)
The invalidation function constructs its cache key independently and calls `cache.delete`. There is no test that the delete-key matches the set-key (a single character drift silently turns invalidation into a no-op). Given the `hash()` randomization bug, this round-trip is *known* to be broken across processes and should be pinned down by a test.
```suggestion
def test_invalidate_round_trips(self):
    # cache.clear(), populate via is_errors_query_for_error_upsampled_projects,
    # call invalidate_upsampling_cache, then assert the next call re-runs
    # the allowlist lookup (options.get.call_count increments).
    ...
```

## Improvements

:yellow_circle: [correctness] `default_result_type="number"` for an integer aggregate in `src/sentry/search/events/datasets/discover.py:285` (confidence: 85)
The new SnQLFunction wraps `toInt64(sum(sample_weight))`, which always returns an integer, but declares `default_result_type="number"` (float in Sentry's type system). The neighboring function in the same `function_converter` block uses `"integer"`. Downstream formatters/dashboards can serialize the count as a float (`10.0`) or pick the wrong axis type.
```suggestion
default_result_type="integer",
```

:yellow_circle: [security] Non-deterministic cache key undermines allowlist consistency window in `src/sentry/api/helpers/error_upsampling.py:153` (confidence: 85)
Beyond the correctness angle, the same `hash()` randomization means that when an allowlist entry is *removed*, the 60-second eventual-consistency contract is not actually bounded — each worker carries its own stale `True` until its own local cache expires, and `invalidate_upsampling_cache()` targets the wrong key in every worker except the one that wrote it. This extends the window during which a deprecated project is still treated as upsampling-eligible.
```suggestion
# Use a deterministic key (see critical finding above) and also store a
# structured payload so a read can verify org_id/project_ids on retrieval,
# defending against any residual collisions.
```

:yellow_circle: [testing] `count == 10` asserted against an invisible factory pipeline in `tests/snuba/api/endpoints/test_organization_events_stats.py:504` (confidence: 91)
The test stores one event per bucket with `client_sample_rate: 0.1` and asserts `count == 10`, while the nearby comment says "First bucket has 1 event". The value 10 only holds if the factory inverts rate to weight (1/0.1) and Snuba sums a `sample_weight` column — neither of which is visible here, and at least one of which is broken (see `sample_weight` finding above). A future change to the factory or schema will produce a cryptic off-by-10x failure.
```suggestion
EXPECTED = int(1 / 0.1)  # weight = 1 / client_sample_rate
assert data[0][1][0]["count"] == EXPECTED, (
    f"Expected upsampled count {EXPECTED} from 1 event × weight 1/0.1, "
    f"got {data[0][1][0]['count']}"
)
```

:yellow_circle: [testing] `self.user` reassigned mid-setUp shadows parent-class auth user in `tests/snuba/api/endpoints/test_organization_events_stats.py:459` (confidence: 90)
`setUp` calls `self.login_as(user=self.user)` and then reassigns `self.user = self.create_user()`. After setUp the attribute points to a brand-new, unauthenticated user; the authenticated user is only preserved as `self.authed_user`. Parent-class helpers that reference `self.user` (permission checks, `tags.sentry:user` filters) will silently use the wrong user object.
```suggestion
self.login_as(user=self.user)
self.extra_user = self.create_user()
self.extra_user2 = self.create_user()
# Update event tags to use self.extra_user.email / self.extra_user2.email.
```

:yellow_circle: [testing] `_is_error_focused_query` substring match has false positives and no coverage in `tests/sentry/api/helpers/test_error_upsampling.py:418` (confidence: 88)
`"event.type:error" in query.lower()` also matches `event.type:errors` and variants where the token is embedded mid-word. The tests cover only three happy-path cases. A test asserting `event.type:errors` should currently fail and would surface an actual substring-matching bug; compound queries (`level:fatal event.type:error`), missing `query` param, and negated queries are also untested.
```suggestion
self.request.GET = QueryDict("query=event.type:errors")
assert _is_error_focused_query(self.request) is False  # likely a real bug today
self.request.GET = QueryDict("query=level:fatal event.type:error")
assert _is_error_focused_query(self.request) is True
```

:yellow_circle: [testing] Transaction-path test has weak negative assertion in `tests/snuba/api/endpoints/test_organization_events_stats.py:553` (confidence: 85)
The test only asserts count values on a transaction query; it never asserts that `transform_query_columns_for_error_upsampling` was *not* invoked. If upsampling regresses into the transactions path and `sample_weight` happens to be 1 for transactions, the test still passes.
```suggestion
@mock.patch("sentry.api.endpoints.organization_events_stats.transform_query_columns_for_error_upsampling")
def test_error_upsampling_with_transaction_events(self, mock_transform, mock_options):
    ...
    mock_transform.assert_not_called()
```

:yellow_circle: [testing] No test for the race condition announced by the branch name in `src/sentry/api/helpers/error_upsampling.py:193` (confidence: 82)
The branch is literally `error-upsampling-race-condition`, and the code acknowledges "can return different results between calls if the configuration changes during request processing." Yet nothing tests cache stampede (two concurrent misses writing the same key) or stale-cache behavior after allowlist shrink. Without a pinned specification of the accepted staleness window, any future refactor of the caching layer is flying blind.
```suggestion
# Add a test that populates the cache with allowlist=[p], then sets
# allowlist=[] and asserts the cached True is still returned until
# invalidate_upsampling_cache is called.
```

:yellow_circle: [consistency] Three duplicated transform blocks in `src/sentry/api/endpoints/organization_events_stats.py:215-294` (confidence: 80)
`if upsampling_enabled: final_columns = transform_query_columns_for_error_upsampling(query_columns)` is copy-pasted into each of the three execution branches even though `upsampling_enabled` and `query_columns` don't change. The surrounding comments ("Apply upsampling transformation just before query execution", "This late transformation ensures we use the most current schema assumptions") are vacuous — the transform is pure and the "schema assumptions" never change mid-function. Hoist the call once before branching so a future fourth path cannot silently forget it.
```suggestion
final_columns = (
    transform_query_columns_for_error_upsampling(query_columns)
    if should_upsample else query_columns
)
# then use final_columns in all three branches with no per-branch re-check.
```

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: 2 hub files (organization_events_stats.py, discover.py) + widely-imported factories.py | Sensitive Paths: `sentry-repo` submodule (supply chain), `**/api/**`, `pyproject.toml`
AI-Authored Likelihood: HIGH — vacuous scaffolding comments ("This separation allows for better query optimization and caching", "This late transformation ensures we use the most current schema assumptions"), triplicated blocks instead of hoisted computation, a defined-but-never-called public `invalidate_upsampling_cache`, contradiction between a docstring ("reads the allowlist configuration fresh each time") and the caching wrapper around it, and a stray unused git submodule all point to synthesized code rather than authored code.

(11 additional findings below confidence threshold 80: bare `except Exception: pass` in factory helper; allowlist `in list` membership O(n) with no type validation; `invalidate_upsampling_cache` dead-code in production; string-rewrite injection surface for future callers; `sample_rate` vs `sample_weight` key mismatch in factory; fragmented `from sentry.snuba import …` splits; literal `" as count"` alias string style; `Organization.objects.create` bypassing factory helpers; 4-assertions-per-test packing; docstring/caching contradiction; overly broad `ModuleType` dataset annotation.)
