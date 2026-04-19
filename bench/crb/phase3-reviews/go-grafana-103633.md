## Summary
4 files changed, 240 lines added, 33 lines deleted. 10 findings (1 critical, 7 improvements, 2 nitpicks).
Denial-cache key uses an ambiguous `_` separator that aliases across (name, parent) tuples; cache metric is mis-labelled on hit-with-deny; denial cache has no grant-time invalidation.

## Critical
:red_circle: [correctness] Denial cache key collision — underscore separator allows aliasing between (name, parent) pairs in pkg/services/authz/rbac/cache.go:30 (confidence: 95)
`userPermDenialCacheKey` concatenates all fields with `_` as the only separator:
```go
return namespace + ".perm_" + userUID + "_" + action + "_" + name + "_" + parent
```
`name` (resource UID) and `parent` (folder UID) are free-form identifiers that routinely contain underscores in Grafana. Two distinct tuples alias:
- `name="a_b", parent="c"` → `..._a_b_c`
- `name="a", parent="b_c"` → `..._a_b_c`

Because only denials are cached, an attacker cannot turn a cached deny into an allow (this is not a privilege-escalation primitive). However, any user/actor that can create resources with crafted UIDs can poison the denial cache so that legitimate `Check()` calls for an unrelated resource return `Allowed: false` for up to `shortCacheTTL` (≈30 s). This is a cross-resource availability / DoS bug and a cache-key-hygiene violation (CWE-694). The denial cache is consulted *before* any permission evaluation, so there is no fallback that masks the collision.
```suggestion
func userPermDenialCacheKey(namespace, userUID, action, name, parent string) string {
    // '|' is not a valid character in Grafana UIDs / action strings,
    // so it unambiguously separates the components.
    return fmt.Sprintf("%s.permdeny|%s|%s|%s|%s", namespace, userUID, action, name, parent)
}
```
Also add a unit test asserting `("a","b_c")` and `("a_b","c")` produce distinct keys.
[References: https://cwe.mitre.org/data/definitions/694.html, https://owasp.org/Top10/A04_2021-Insecure_Design/]

## Improvements
:yellow_circle: [correctness] Cache HIT with `allowed=false` is counted as a cache MISS in `permissionCacheUsage` in pkg/services/authz/rbac/service.go:121 (confidence: 92)
In `Check()`:
```go
cachedPerms, err := s.getCachedIdentityPermissions(...)
if err == nil {
    allowed, err := s.checkPermission(ctx, cachedPerms, checkReq)
    if err != nil { ... return deny, err }
    if allowed {
        s.metrics.permissionCacheUsage.WithLabelValues("true", ...).Inc()
        return &authzv1.CheckResponse{Allowed: allowed}, nil
    }
}
s.metrics.permissionCacheUsage.WithLabelValues("false", checkReq.Action).Inc()
```
When the permCache has an entry but the resource is not in it, `err == nil` but `allowed == false`; execution falls through and the post-block `Inc("false", ...)` records a **miss** for what was actually a **hit**. This silently inflates the miss rate in dashboards and deflates the hit rate, misleading cache-effectiveness tuning.
```suggestion
cachedPerms, err := s.getCachedIdentityPermissions(ctx, checkReq.Namespace, checkReq.IdentityType, checkReq.UserUID, checkReq.Action)
if err == nil {
    allowed, err := s.checkPermission(ctx, cachedPerms, checkReq)
    if err != nil {
        ctxLogger.Error("could not check permission", "error", err)
        s.metrics.requestCount.WithLabelValues("true", "true", req.GetVerb(), req.GetGroup(), req.GetResource()).Inc()
        return deny, err
    }
    // Record cache hit regardless of the allow/deny outcome.
    s.metrics.permissionCacheUsage.WithLabelValues("true", checkReq.Action).Inc()
    if allowed {
        s.metrics.requestCount.WithLabelValues("false", "true", req.GetVerb(), req.GetGroup(), req.GetResource()).Inc()
        return &authzv1.CheckResponse{Allowed: allowed}, nil
    }
    // Cache hit, but user not allowed by cached perms — fall through to DB for authoritative check.
} else {
    s.metrics.permissionCacheUsage.WithLabelValues("false", checkReq.Action).Inc()
}
```

:yellow_circle: [correctness] Denial cache has no invalidation path — permission grants silently blocked for up to `shortCacheTTL` in pkg/services/authz/rbac/service.go:136 (confidence: 90)
After a DB-confirmed deny, `s.permDenialCache.Set(ctx, permDenialKey, true)` is written with `shortCacheTTL` (≈30 s). Nothing in this diff invalidates denial entries when a role/permission assignment changes. If an admin grants access to a resource within the TTL window, the user will keep receiving `Allowed: false` until the entry expires. The fail-closed direction makes this an availability/UX regression rather than a confidentiality break, but the PR description ("users can fetch newly created dashboards and folders") frames freshness as a primary goal — and the denial cache introduces a *new* staleness axis on the opposite side (newly-granted access). This trade-off is not documented, and there is no `Delete` hook wired into the permission-mutation code path.
```suggestion
if !allowed {
    // NOTE: denials are cached for shortCacheTTL. If a permission is granted
    // for this (user, action, name, parent) within the TTL window, Check()
    // will keep returning Allowed:false until the entry expires — there is
    // no active invalidation. Keep TTL short and/or wire Delete(permDenialKey)
    // into the role/permission update path if stricter freshness is required.
    s.permDenialCache.Set(ctx, permDenialKey, true)
}
```
Also add a regression test `denial_is_invalidated_on_permission_change` asserting grant-after-deny semantics (today untested).

:yellow_circle: [correctness] `TestService_CacheCheck/"Should deny on explicit cache deny entry"` does not actually prove the denial cache is consulted first in pkg/services/authz/rbac/service_test.go:329 (confidence: 88)
The test seeds:
- `permDenialCache`: `(… "dash1", "fold1") = true`
- `permCache`: `{"dashboards:uid:dash1": false}`  ← value is `false`

The comment says "Allow access to the dashboard to prove this is not checked", implying the permCache entry should *allow* `dash1` so that a skipped denial-cache check would flip the result to `Allowed: true`. But the stored value is `false`, not `true`: on the permCache path, `checkPermission` also returns `allowed=false`. The test therefore passes even if `permDenialCache` were removed entirely — the permCache branch would return `false`, fall through to `getIdentityPermissions` against an unconfigured store, and still deny. The test does not discriminate between the two paths.
```suggestion
// Set permCache to ALLOW dash1. If permDenialCache is not consulted first,
// the permCache path would return Allowed:true and the assertion below would fail.
s.permCache.Set(ctx, userPermCacheKey("org-12", "test-uid", "dashboards:read"), map[string]bool{"dashboards:uid:dash1": true})
```

:yellow_circle: [cross-file-impact] In-proc authzlib client loses its 30 s client-level cache; server-side cache is now the only protection in pkg/services/authz/rbac.go:98 (confidence: 72)
Pre-PR, `ProvideAuthZClient`'s in-proc branch called `newRBACClient(channel, tracer)`, which configured `authzlib.NewClient` with `cache.NewLocalCache(30s)`. Post-PR, the in-proc branch passes `&NoopCache{}`, so every call traverses the in-memory gRPC channel and reaches `Service.Check`/`Service.List`; the server-side `permCache`/`permDenialCache` now carry the full load. Architecturally sound (avoids double-caching and the stale-read bug the PR is fixing), but: (a) any consumer that was implicitly relying on client-level burst absorption will now fan out to `singleflight` + DB on cold caches; (b) any integration test in `pkg/services/authz/**` or `zanzana/**` that asserts on client-cache hit counts in the in-proc mode will break.
```suggestion
// Add a brief comment at the in-proc branch documenting intent:
//
// In-proc mode talks directly to the local Service (see rbac/service.go),
// which maintains its own permCache and permDenialCache. A client-level
// cache on top would double-cache and reintroduce the stale-read bug that
// motivated this change, so we wire a NoopCache explicitly.
rbacClient := authzlib.NewClient(
    channel,
    authzlib.WithCacheClientOption(&NoopCache{}),
    authzlib.WithTracerClientOption(tracer),
)
```
Before merging, verify no `ProvideAuthZClient`/zanzana integration test asserts on client-level cache hit counts in the in-proc configuration.

:yellow_circle: [cross-file-impact] Dead `cacheHit` field in `TestService_getUserPermissions` test harness may mislead future contributors in pkg/services/authz/rbac/service_test.go:339 (confidence: 68)
The diff removes the only test case that set `cacheHit: true` in `TestService_getUserPermissions`, because `getUserPermissions` no longer checks `permCache`. However, the `cacheHit` field on the local `testCase` struct and the associated cache-seeding block inside the test loop are not visible as removed in this diff. If that harness code remains, a future contributor who adds a `cacheHit: true` case expecting short-circuit behavior will silently observe the DB being hit anyway — a confusing test failure (or false positive) for the wrong reason.
```suggestion
// Remove the cacheHit field from the testCase struct in
// TestService_getUserPermissions and remove the corresponding
// `if tt.cacheHit { s.permCache.Set(...) }` block inside the subtest loop.
// The caching contract is now exercised by TestService_CacheCheck / TestService_CacheList.
```

:yellow_circle: [consistency] `NoopCache` uses pointer receivers on a zero-size struct in pkg/services/authz/rbac.go:47 (confidence: 75)
```go
func (lc *NoopCache) Get(ctx context.Context, key string) ([]byte, error) { ... }
func (lc *NoopCache) Set(ctx context.Context, key string, data []byte, exp time.Duration) error { ... }
func (lc *NoopCache) Delete(ctx context.Context, key string) error { ... }
```
`NoopCache` is a zero-size, stateless type. Idiomatic Go uses value receivers for such types — they avoid an unnecessary indirection, allow use through both value and pointer in call sites, and signal the methods are non-mutating. The only reason to pick a pointer receiver would be interface-satisfaction constraints from the `authzlib` cache-option contract; if the interface accepts a value receiver (as it almost certainly does for `Get/Set/Delete` returning errors), prefer value receivers for consistency with Go style and common static-analysis rules.
```suggestion
func (NoopCache) Get(ctx context.Context, key string) ([]byte, error)           { return nil, cache.ErrNotFound }
func (NoopCache) Set(ctx context.Context, key string, data []byte, exp time.Duration) error { return nil }
func (NoopCache) Delete(ctx context.Context, key string) error                   { return nil }
```

:yellow_circle: [consistency] `permDenialCache` is not mentioned in the `Service` cache-fields comment in pkg/services/authz/rbac/service.go:55 (confidence: 60)
The cluster of cache fields in `Service` is introduced by the comment `// Cache for user permissions, user team memberships and user basic roles`. The PR adds `permDenialCache *cacheWrap[bool]` without updating the comment, leaving a documentation gap that asks the next reader to infer semantics from the field name alone.
```suggestion
// Caches for user permissions (allow results), explicit permission denials,
// user team memberships, user basic roles, and the folder tree.
idCache         *cacheWrap[store.UserIdentifiers]
permCache       *cacheWrap[map[string]bool]
permDenialCache *cacheWrap[bool]
teamCache       *cacheWrap[[]int64]
basicRoleCache  *cacheWrap[store.BasicRole]
folderCache     *cacheWrap[folderTree]
```

## Nitpicks
:white_circle: [testing] Missing security-relevant test coverage for the denial cache in pkg/services/authz/rbac/service_test.go:890 (confidence: 65)
`TestService_CacheCheck` covers happy paths but omits: (1) a collision test that asserts a deny cached for `(name="foo", parent="bar_baz")` does *not* affect a Check for `(name="foo_bar", parent="baz")`; (2) a grant-after-deny invalidation/TTL test; (3) a negative assertion that denials are *not* cached on `checkPermission` error paths; (4) a `NoopCache` integration test confirming `authzlib` callers handle `ErrNotFound` without spurious allows. Recommended to add at least the collision test alongside the key-separator fix above.

:white_circle: [consistency] `NoopCache` capitalization may not match Go acronym convention in pkg/services/authz/rbac.go:45 (confidence: 65)
`NoopCache` sits between `NoOpCache` (treating "NoOp" as an acronym) and `NopCache` (common shorthand). Not an error — `NoopCache` appears in the wild — but if the Grafana codebase has other `NoOp*` types or has `revive`/`stylecheck` enabled, consider renaming for alignment.

## Conflicts
None. Correctness and security agents both identified the `_`-separator collision; correctness framed it as a deny-legitimate-access bug, security framed it as a cross-resource DoS. The two framings are complementary, not contradictory — both agree on the root cause and fix.

## Risk Metadata
Risk Score: 61/100 (HIGH) | Blast Radius: `pkg/services/authz/**` is on the auth enforcement path for virtually every Grafana API handler; 4 files, 273 total lines changed | Sensitive Paths: all 4 files under `pkg/services/authz/` match the `auth/` sensitive pattern (score 100)
AI-Authored Likelihood: LOW (domain-specific identifiers, varied test setups, no generator signatures)

Recommendation: **request-changes** — the denial-cache key collision is a genuine correctness/availability bug that should be fixed before merge (one-line change), and the metrics mis-labelling + test that doesn't prove the denial path should be tightened in the same revision.
