## Summary
4 files changed, 240 lines added, 33 lines deleted. 10 findings (4 critical, 6 improvements).
Cache key collision in `cache.go:30` enables forged denials and cross-resource authorization confusion; `List()` cache-hit asymmetry silently breaks the PR's stated fix for newly-created resources; core denial-cache write path is untested.

## Critical

:red_circle: [testing] Denial cache write path is entirely untested — the core new behavior has no coverage in pkg/services/authz/rbac/service.go:137 (confidence: 97)
The production code writes to `permDenialCache` after a DB-path denial, but no test verifies this write occurs, and no test proves that a second identical `Check()` call returns from the denial cache without hitting the DB. The `fakeStore` has no call counter, making it impossible to assert DB bypass. This is the single most important new behavior introduced by the PR and is completely uncovered.
```suggestion
t.Run("Should cache denial after DB deny and skip DB on second call", func(t *testing.T) {
    s := setupService()
    callCount := 0
    st := &fakeStore{
        userID: userID,
        userPermissions: []accesscontrol.Permission{},
        getUserPermissionsFunc: func() { callCount++ },
    }
    s.store = st
    s.permissionStore = st
    s.idCache.Set(ctx, userIdentifierCacheKey("org-12", "test-uid"), *userID)

    req := &authzv1.CheckRequest{
        Namespace: "org-12", Subject: "user:test-uid",
        Group: "dashboard.grafana.app", Resource: "dashboards",
        Verb: "get", Name: "dash1",
    }

    resp1, err := s.Check(ctx, req)
    require.NoError(t, err)
    assert.False(t, resp1.Allowed)
    assert.Equal(t, 1, callCount)

    resp2, err := s.Check(ctx, req)
    require.NoError(t, err)
    assert.False(t, resp2.Allowed)
    assert.Equal(t, 1, callCount) // denial cache served it, DB not called again
})
```

:red_circle: [security] Cache key collision in `userPermDenialCacheKey` enables forged denials and cross-resource authorization confusion in pkg/services/authz/rbac/cache.go:30 (confidence: 95)
`userPermDenialCacheKey` concatenates `namespace + ".perm_" + userUID + "_" + action + "_" + name + "_" + parent` using `_` as the only delimiter. Because `_` is legal in every field — especially user-chosen `name`/`parent` values like dashboard/folder UIDs — different logical tuples serialize to the same key. Example: `(uid="a_b", action="c", name="d", parent="e")` collides with `(uid="a", action="b_c", name="d", parent="e")`. Security impact: (1) attacker-influenced names can provoke a denial on a tuple that collides with a different legitimate `(user, resource)` pair, causing targeted denial griefing for up to `shortCacheTTL`; (2) two legitimate different requests share a single cached decision. Aligned with CWE-694 and OWASP A01 (Broken Access Control).
```suggestion
func userPermDenialCacheKey(namespace, userUID, action, name, parent string) string {
    // NUL is not valid in k8s names, UIDs, or action strings.
    return namespace + ".perm_denial\x00" + userUID + "\x00" + action + "\x00" + name + "\x00" + parent
}
```
[References: https://cwe.mitre.org/data/definitions/694.html, https://owasp.org/Top10/A01_2021-Broken_Access_Control/]

:red_circle: [correctness] `List()` does not fall through to DB when cached permissions are stale, contradicting `Check()` and the PR's stated fix in pkg/services/authz/rbac/service.go:180 (confidence: 95)
`Check()` implements a two-level strategy: cached perms that don't grant access cause a fallthrough to a DB refetch. `List()` does NOT implement this strategy. When `getCachedIdentityPermissions` returns `err == nil` (cache hit), `List()` returns immediately with those cached permissions without any DB fallback. A user who just created a dashboard whose permission map is cached without the new resource will see `List()` return incomplete results until `shortCacheTTL` expires. `Check()` would correctly surface the new permission via DB fallback; `List()` will not. The PR's stated fix ("dashboard/folder creator can't access dashboard/folder they just created") is therefore only partially applied — list/search views remain broken.
```suggestion
permissions, err := s.getIdentityPermissions(ctx, listReq.Namespace, listReq.IdentityType, listReq.UserUID, listReq.Action)
if err != nil {
    ctxLogger.Error("could not get user permissions", "subject", req.GetSubject(), "error", err)
    s.metrics.requestCount.WithLabelValues("true", "true", req.GetVerb(), req.GetGroup(), req.GetResource()).Inc()
    return nil, err
}
```

:red_circle: [testing] "Should deny on explicit cache deny entry" test passes vacuously — denial cache is not proven effective in pkg/services/authz/rbac/service_test.go:976 (confidence: 95)
The test sets the positive `permCache` entry to `map[string]bool{"dashboards:uid:dash1": false}`. A map value of `false` does not grant access — it is semantically identical to key absence. If the denial-cache short-circuit were deleted entirely, the positive-cache path would also return `Allowed=false` and this test would still pass. The test comment "Allow access to the dashboard to prove this is not checked" is incorrect and gives false confidence in the denial-cache code path.
```suggestion
// Positive cache GRANTS access — so Allowed=false can only come from the
// denial-cache short-circuit. If that short-circuit is removed, this test
// will fail (returns Allowed=true), proving the denial cache is effective.
s.permCache.Set(ctx,
    userPermCacheKey("org-12", "test-uid", "dashboards:read"),
    map[string]bool{"dashboards:uid:dash1": true},
)
```

## Improvements

:yellow_circle: [correctness] Denial cached after DB refetch that races with concurrent permission grant in pkg/services/authz/rbac/service.go:137 (confidence: 92)
In `Check()`, after cached permissions do not grant access, the code falls through to a DB refetch. If a permission grant completes between the `getCachedIdentityPermissions` miss and `getIdentityPermissions` returning, the DB query may still return `!allowed`. The code then caches this fresh denial for `shortCacheTTL` (~30s). This TOCTOU pattern means the user cannot access the resource for up to ~30s despite having been granted access — precisely the scenario the PR aims to fix.
```suggestion
hadCachedPerms := false
cachedPerms, err := s.getCachedIdentityPermissions(ctx, checkReq.Namespace, checkReq.IdentityType, checkReq.UserUID, checkReq.Action)
if err == nil {
    hadCachedPerms = true
    // ... existing allowed check ...
}

// ... DB refetch ...
if !allowed && hadCachedPerms {
    // Only cache the denial when we had a stale perm map (not a cold miss),
    // shrinking the window in which a racing grant gets cached as denied.
    s.permDenialCache.Set(ctx, permDenialKey, true)
}
```

:yellow_circle: [correctness] `checkPermission` error on cached perms returns deny without attempting DB fallback in pkg/services/authz/rbac/service.go:116 (confidence: 88)
The two-level cache strategy intends to fall through to the DB whenever the cache cannot positively confirm access. However, if `checkPermission` errors when called with cached perms, the code returns `(deny, err)` immediately, bypassing the DB refetch. A transient or structural error during permission evaluation (e.g., a folder tree traversal issue) will deny access without attempting the live DB path, whereas a cache miss on the same request would have triggered DB fallback.
```suggestion
cachedPerms, cacheErr := s.getCachedIdentityPermissions(ctx, checkReq.Namespace, checkReq.IdentityType, checkReq.UserUID, checkReq.Action)
if cacheErr == nil {
    allowed, checkErr := s.checkPermission(ctx, cachedPerms, checkReq)
    if checkErr != nil {
        ctxLogger.Warn("could not check permission against cache, falling back to DB", "error", checkErr)
        // fall through to DB refetch
    } else if allowed {
        s.metrics.permissionCacheUsage.WithLabelValues("true", checkReq.Action).Inc()
        s.metrics.requestCount.WithLabelValues("false", "true", req.GetVerb(), req.GetGroup(), req.GetResource()).Inc()
        return &authzv1.CheckResponse{Allowed: allowed}, nil
    }
}
s.metrics.permissionCacheUsage.WithLabelValues("false", checkReq.Action).Inc()
```

:yellow_circle: [testing] `List()` cache-hit asymmetry with `Check()` is untested — silent regression risk in pkg/services/authz/rbac/service_test.go:1288 (confidence: 88)
`Check()` falls through to DB when cached perms don't cover the requested resource; `List()` does not. This intentional asymmetry has no test. If a developer later aligns `List()` with `Check()`'s fallthrough behavior, no test will catch the regression or validate that the change was correct.
```suggestion
t.Run("List uses cached permissions even if cache is a subset of DB permissions", func(t *testing.T) {
    s := setupService()
    ctx := types.WithAuthInfo(context.Background(), callingService)
    userID := &store.UserIdentifiers{UID: "test-uid", ID: 1}
    st := &fakeStore{
        userID: userID,
        userPermissions: []accesscontrol.Permission{
            {Action: "dashboards:read", Scope: "dashboards:uid:dash1"},
            {Action: "dashboards:read", Scope: "dashboards:uid:dash2"},
        },
    }
    s.store = st
    s.permissionStore = st
    s.idCache.Set(ctx, userIdentifierCacheKey("org-12", "test-uid"), *userID)
    s.permCache.Set(ctx,
        userPermCacheKey("org-12", "test-uid", "dashboards:read"),
        map[string]bool{"dashboards:uid:dash1": true}, // cache subset — no dash2
    )

    resp, err := s.List(ctx, &authzv1.ListRequest{
        Namespace: "org-12", Subject: "user:test-uid",
        Group: "dashboard.grafana.app", Resource: "dashboards", Verb: "list",
    })
    require.NoError(t, err)
    require.ElementsMatch(t, resp.Items, []string{"dash1"}) // dash2 not returned
})
```

:yellow_circle: [correctness] `NoopCache.Get` may return wrong `ErrNotFound` sentinel if `cache` import is local package in pkg/services/authz/rbac.go:242 (confidence: 85)
`NoopCache.Get` returns `cache.ErrNotFound`. For `authzlib` to treat this as a cache miss, the sentinel must be the `ErrNotFound` value from `github.com/grafana/authlib/cache`, not a local package with the same name. If the `cache` import in `rbac.go` resolves to `github.com/grafana/grafana/pkg/services/authz/rbac/cache`, the returned error is a different value and `authzlib` will interpret every `NoopCache.Get` as a backend failure rather than a miss, silently disabling the caching fast-path.
```suggestion
import (
    authlibcache "github.com/grafana/authlib/cache"
)

// Compile-time assertion that NoopCache satisfies the authlib cache contract.
var _ authlibcache.Cache = (*NoopCache)(nil)

func (c *NoopCache) Get(ctx context.Context, key string) ([]byte, error) {
    return nil, authlibcache.ErrNotFound
}
```

:yellow_circle: [consistency] Denial cache key prefix collides with positive `permCache` key prefix in pkg/services/authz/rbac/cache.go:30 (confidence: 85)
The denial cache key shares the `.perm_` prefix with `userPermCacheKey`'s positive permission cache prefix. Though stored in different `cacheWrap` instances today, if the underlying `cache.LocalCache` is ever shared or consolidated, keys could collide or shadow each other. Operational tooling that inspects or invalidates cache keys by prefix would incorrectly match both families.
```suggestion
func userPermDenialCacheKey(namespace, userUID, action, name, parent string) string {
    return namespace + ".perm_denial_" + userUID + "_" + action + "_" + name + "_" + parent
}
```

:yellow_circle: [consistency] `NoopCache` receiver name `lc` is a copy-paste artifact from `LocalCache` in pkg/services/authz/rbac.go:239 (confidence: 85)
`NoopCache` methods use receiver name `lc` across `Get`, `Set`, and `Delete`. This is copied verbatim from `LocalCache` (`lc` = LocalCache). For `NoopCache` the name is misleading and violates Go receiver-naming convention.
```suggestion
func (c *NoopCache) Get(ctx context.Context, key string) ([]byte, error) {
    return nil, cache.ErrNotFound
}

func (c *NoopCache) Set(ctx context.Context, key string, data []byte, exp time.Duration) error {
    return nil
}

func (c *NoopCache) Delete(ctx context.Context, key string) error {
    return nil
}
```

## Risk Metadata
Risk Score: 65/100 (HIGH) | Blast Radius: 100 (RBAC provider + Check/List handlers invoked across all RBAC-gated paths) | Sensitive Paths: 100 (all 4 files under `pkg/services/authz/`) | Test Coverage Gap: 67 (2 of 3 production files have no corresponding test changes) | File Size: 60 (273 total changed lines)
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold 85: denial cache has no invalidation on permission grant (70), unbounded Prometheus label cardinality on `Action` label (75), `TypeAnonymous`/`TypeRenderService` paths have no test coverage (82).)
