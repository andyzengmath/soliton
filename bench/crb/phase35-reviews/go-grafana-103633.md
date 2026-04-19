## Summary
4 files changed, 240 lines added, 33 lines deleted. 6 findings (2 critical, 4 improvements).
AuthZ caching overhaul: adds a short-TTL permission-denial cache, re-queries the DB when cached perms don't grant access, and swaps the in-proc client cache for a `NoopCache` to avoid double-caching. The fix-forward direction is correct, but denial-cache key construction, ordering vs the positive cache, and metric labels introduce a cross-resource DoS primitive and silently block freshly-granted access for up to `shortCacheTTL`.

## Critical
:red_circle: [security] `userPermDenialCacheKey` uses `_` as the sole separator, so distinct resource tuples alias to the same key in pkg/services/authz/rbac/cache.go:30 (confidence: 92)
`userPermDenialCacheKey` concatenates namespace, userUID, action, name, and parent with `_`:
```go
return namespace + ".perm_" + userUID + "_" + action + "_" + name + "_" + parent
```
Grafana resource UIDs and folder UIDs are free-form identifiers that may legally contain `_`. That means `(action="a", name="x_y", parent="z")` and `(action="a", name="x", parent="y_z")` both collapse to `..._a_x_y_z`. Because the denial cache is consulted *before* any DB lookup and returns an early `Allowed:false`, an actor able to cause a deny entry against a crafted `(name, parent)` pair can poison the cache so that legitimate `Check()` calls for a different resource that collapses to the same key are short-circuited as denied for up to `shortCacheTTL` (~30s). This is a cross-resource availability regression (CWE-694 / CWE-706). Only denials are cached so it's not a privilege-escalation primitive, but it is a real correctness defect that survived review. Use an unambiguous separator (or structured key), and add a unit test asserting `("x_y","z")` and `("x","y_z")` produce distinct keys.
```suggestion
func userPermDenialCacheKey(namespace, userUID, action, name, parent string) string {
    // '|' is not a legal character in Grafana UIDs or action strings,
    // so it unambiguously separates the components.
    return fmt.Sprintf("%s.permdeny|%s|%s|%s|%s", namespace, userUID, action, name, parent)
}
```
[References: https://cwe.mitre.org/data/definitions/694.html]

:red_circle: [correctness] Denial cache is checked before the positive permCache and has no invalidation hook, silently blocking freshly-granted access in pkg/services/authz/rbac/service.go:113 (confidence: 90)
`Check()` now evaluates the denial cache first:
```go
if _, ok := s.permDenialCache.Get(ctx, permDenialKey); ok {
    return &authzv1.CheckResponse{Allowed: false}, nil
}
cachedPerms, err := s.getCachedIdentityPermissions(...)
```
Nothing in this diff deletes denial entries when a role/team/permission assignment changes, and this change adds no `Delete` call on any permission-mutation path. Combined with the ordering above, a user who is denied at T=0 and granted at T=1 continues to receive `Allowed:false` even though the positive `permCache` has a fresh entry for the same `(user, action)` — the denial early-return runs before any positive lookup. The PR's stated motivation is *freshness* ("users can fetch newly created dashboards and folders"), but the denial cache introduces a new staleness axis on the opposite side that is undocumented and untested. Two-part fix: (1) consult the positive cache first and only fall through to the denial cache when the positive cache did not grant; (2) invalidate denial entries for a `(user, action)` when its `permCache` entry is overwritten. Add a regression test `denial_is_invalidated_on_permission_change`.
```suggestion
// Check the positive permCache first so a freshly-written grant overrides
// any stale denial entry for the same (user, action, resource).
cachedPerms, err := s.getCachedIdentityPermissions(ctx, checkReq.Namespace, checkReq.IdentityType, checkReq.UserUID, checkReq.Action)
if err == nil {
    s.metrics.permissionCacheUsage.WithLabelValues("true", checkReq.Action).Inc()
    allowed, err := s.checkPermission(ctx, cachedPerms, checkReq)
    if err != nil {
        ctxLogger.Error("could not check permission", "error", err)
        s.metrics.requestCount.WithLabelValues("true", "true", req.GetVerb(), req.GetGroup(), req.GetResource()).Inc()
        return deny, err
    }
    if allowed {
        s.metrics.requestCount.WithLabelValues("false", "true", req.GetVerb(), req.GetGroup(), req.GetResource()).Inc()
        return &authzv1.CheckResponse{Allowed: true}, nil
    }
}
if _, ok := s.permDenialCache.Get(ctx, permDenialKey); ok {
    s.metrics.requestCount.WithLabelValues("false", "true", req.GetVerb(), req.GetGroup(), req.GetResource()).Inc()
    return &authzv1.CheckResponse{Allowed: false}, nil
}
```

## Improvements
:yellow_circle: [correctness] Positive cache hit with `allowed=false` is mis-recorded as a cache miss in `permissionCacheUsage` in pkg/services/authz/rbac/service.go:114 (confidence: 90)
When `getCachedIdentityPermissions` returns `err == nil` and `checkPermission` returns `allowed == false`, execution falls past the `if allowed { ... return }` block and reaches `s.metrics.permissionCacheUsage.WithLabelValues("false", checkReq.Action).Inc()`. That records a **miss** for what was actually a **hit** — the permissions *were* cached; they just did not grant this specific resource. This silently inflates the miss rate in the cache-effectiveness dashboards the team relies on for tuning TTL. Separately, a non-`ErrNotFound` error from `getCachedIdentityPermissions` (e.g., identity-resolution failure) is also recorded as a miss and retried via `getIdentityPermissions`, conflating hard errors with cache misses.
```suggestion
cachedPerms, err := s.getCachedIdentityPermissions(ctx, checkReq.Namespace, checkReq.IdentityType, checkReq.UserUID, checkReq.Action)
if err == nil {
    s.metrics.permissionCacheUsage.WithLabelValues("true", checkReq.Action).Inc()
    // ...checkPermission / return allowed...
} else if errors.Is(err, cache.ErrNotFound) {
    s.metrics.permissionCacheUsage.WithLabelValues("false", checkReq.Action).Inc()
} else {
    ctxLogger.Error("cache lookup failed", "error", err)
    return deny, err
}
```

:yellow_circle: [consistency] Denial-cache hits share the `permissionCacheUsage{cache_hit="true"}` label with allow-cache hits in pkg/services/authz/rbac/service.go:109 (confidence: 86)
Both the denial-cache early return and the allow-cache early return increment `permissionCacheUsage` with the same `"true"` label, so dashboards cannot distinguish "allow-cache warming nicely" from "denial cache absorbing a flood of unauthorized requests." `@eleijonmarck` raised exactly this concern in the PR thread and the team deferred to a later metrics refactor — fine, but until then there is no way to alert on denial-cache pressure (the more interesting signal for the incident this PR fixes). Cheap fix now: introduce a distinct label value (e.g., `"deny"`) at the denial-cache hit site so the three paths (allow-hit, deny-hit, miss) are separately observable.
```suggestion
// At the denial-cache hit site:
s.metrics.permissionCacheUsage.WithLabelValues("deny", checkReq.Action).Inc()
```

:yellow_circle: [testing] `"Should deny on explicit cache deny entry"` does not actually discriminate the denial path from the positive-cache path in pkg/services/authz/rbac/service_test.go:329 (confidence: 92)
The subtest seeds `permCache` with `{"dashboards:uid:dash1": false}` and `permDenialCache` with a deny entry, then asserts `resp.Allowed == false`. If the `permDenialCache` check were deleted entirely from `Check()`, execution would fall through to `getCachedIdentityPermissions` → `checkPermission`, evaluate the `false` map entry, and also return deny — the test passes either way. The inline comment claims the `permCache` entry would grant access ("Allow access to the dashboard to prove this is not checked"), but the stored value is `false`, so the test is self-contradictory and does not prove the denial-cache code path is being exercised.
```suggestion
// Seed permCache to GRANT dash1. If permDenialCache is not consulted first,
// the permCache path would return Allowed:true and the assertion below would fail.
s.permCache.Set(ctx, userPermCacheKey("org-12", "test-uid", "dashboards:read"), map[string]bool{"dashboards:uid:dash1": true})
```

:yellow_circle: [testing] `TestService_CacheList` covers only the cache-hit path; the DB-fallback branch and its miss-counter are untested in pkg/services/authz/rbac/service_test.go:1291 (confidence: 90)
`TestService_CacheList` has one subtest ("List based on cached permissions"). The rewritten `List()` has two paths — cache hit vs `getIdentityPermissions` fallback on miss — and only the first is exercised. Path 2, the `permissionCacheUsage{"false", ...}` counter, and the propagation of DB errors out of the List path are all unasserted. Add a subtest that leaves `permCache` empty, pre-populates the store, and asserts the returned items match the store's contents.
```suggestion
t.Run("List falls back to the database on cache miss", func(t *testing.T) {
    s := setupService()
    ctx := types.WithAuthInfo(context.Background(), callingService)
    userID := &store.UserIdentifiers{UID: "test-uid", ID: 1}

    dbStore := &fakeStore{
        userID: userID,
        userPermissions: []accesscontrol.Permission{
            {Action: "dashboards:read", Scope: "dashboards:uid:dash3"},
        },
    }
    s.store = dbStore
    s.permissionStore = dbStore
    s.idCache.Set(ctx, userIdentifierCacheKey("org-12", "test-uid"), *userID)
    s.identityStore = &fakeIdentityStore{}

    resp, err := s.List(ctx, &authzv1.ListRequest{
        Namespace: "org-12", Subject: "user:test-uid",
        Group: "dashboard.grafana.app", Resource: "dashboards", Verb: "list",
    })
    require.NoError(t, err)
    require.ElementsMatch(t, resp.Items, []string{"dash3"})
})
```

## Risk Metadata
Risk Score: 68/100 (HIGH) | Blast Radius: `pkg/services/authz/**` is on the authorization hot path for effectively every Grafana API handler; 4 files, ~273 total lines changed | Sensitive Paths: all 4 files match the `auth/` sensitive pattern
AI-Authored Likelihood: LOW (domain-specific identifiers, varied test setups, active human design discussion in the PR thread)

Recommendation: **request-changes** — the denial-cache key collision and the denial-checked-before-permCache ordering combined with no-invalidation-on-grant are correctness/availability defects that undercut the PR's stated freshness goal. The metrics mis-labelling and the test that does not actually prove the denial-cache path should be tightened in the same revision.
