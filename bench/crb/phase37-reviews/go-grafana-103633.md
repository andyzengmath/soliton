## Summary
4 files changed, 240 lines added, 33 lines deleted. 8 findings (1 critical, 7 improvements).
Adds a permission denial cache and rewires `Check()` / `List()` to a cache-first-then-DB pattern. The core design is sound and addresses the stated bug (creators seeing their own newly created dashboards/folders), but the denial-cache write path is untested, TTL-renewal on repeated denials extends the stale-permission window, metrics labels conflate several distinct cache outcomes, and a few error branches are silently swallowed.

## Critical
:red_circle: [testing] No test verifies that the denial cache is written after a DB-deny result in `pkg/services/authz/rbac/service.go`:153 (confidence: 95)
The production code writes to `permDenialCache` when `getIdentityPermissions` returns `allowed == false` — this is the primary correctness invariant of the new caching scheme: a DB-confirmed denial must be recorded so repeat requests short-circuit. None of the new tests exercise this write. "Should deny on explicit cache deny entry" pre-populates the denial cache manually (testing only the read path). The three DB-fallback subtests all have `Allowed: true` outcomes, so the write-on-deny branch is never reached. A future refactor that accidentally removes or guards this `Set` call would not be caught by the test suite — and the denial cache is the entire rationale for the PR's performance story.
```suggestion
t.Run("Writes denial cache after DB deny", func(t *testing.T) {
    s := setupService()
    ctx := types.WithAuthInfo(context.Background(), callingService)
    userID := &store.UserIdentifiers{UID: "test-uid", ID: 1}

    fakeS := &fakeStore{userID: userID, userPermissions: []accesscontrol.Permission{}}
    s.store = fakeS
    s.permissionStore = fakeS
    s.idCache.Set(ctx, userIdentifierCacheKey("org-12", "test-uid"), *userID)

    resp, err := s.Check(ctx, &authzv1.CheckRequest{
        Namespace: "org-12", Subject: "user:test-uid",
        Group: "dashboard.grafana.app", Resource: "dashboards",
        Verb: "get", Name: "dash1", Folder: "fold1",
    })
    require.NoError(t, err)
    assert.False(t, resp.Allowed)

    denialKey := userPermDenialCacheKey("org-12", "test-uid", "dashboards:read", "dash1", "fold1")
    _, ok := s.permDenialCache.Get(ctx, denialKey)
    assert.True(t, ok, "expected denial cache to be populated after DB-deny")
})
```

## Improvements
:yellow_circle: [correctness] Denial cache TTL is refreshed on every repeated denial, extending the stale-permission window indefinitely in `pkg/services/authz/rbac/service.go`:153 (confidence: 92)
The PR author's stated trade-off ("at worst they wait 30 seconds after a grant") assumes the denial entry expires `shortCacheTTL` after the first denial. But `s.permDenialCache.Set(...)` is called on every DB-denied check, so a polling client — common in dashboards/viewers — keeps refreshing the TTL window. A user denied at T=0 and whose request is retried at T=29 will have the denial cached until T=59; retried at T=58, until T=88. If permissions are granted in that window, the grant is not visible until the polling stops long enough for the TTL to run out naturally. This turns a bounded 30-second delay into an effectively unbounded one.
```suggestion
if !allowed {
    // Do not refresh the TTL on repeated denials — bound the stale window
    // to exactly one shortCacheTTL from the first denial.
    if _, exists := s.permDenialCache.Get(ctx, permDenialKey); !exists {
        s.permDenialCache.Set(ctx, permDenialKey, true)
    }
}
```

:yellow_circle: [correctness] `List()` metrics label `false` conflates cache-miss with `GetUserIdentifiers` infrastructure errors in `pkg/services/authz/rbac/service.go`:157 (confidence: 91)
`getCachedIdentityPermissions` returns a non-`ErrNotFound` error when `GetUserIdentifiers` fails (e.g. DB unavailable, context cancelled). The `List()` branch records `permissionCacheUsage{"false"}` for any non-nil error before falling back to `getIdentityPermissions`. An operator looking at the `permissionCacheUsage` dashboard during a storage outage will see an apparent cache-miss-rate spike — the actual cause is an upstream error, not cache behaviour. This will actively mislead on-call diagnosis of authz latency.
```suggestion
permissions, err := s.getCachedIdentityPermissions(ctx, listReq.Namespace, listReq.IdentityType, listReq.UserUID, listReq.Action)
if err == nil {
    s.metrics.permissionCacheUsage.WithLabelValues("true", listReq.Action).Inc()
} else {
    if errors.Is(err, cache.ErrNotFound) {
        s.metrics.permissionCacheUsage.WithLabelValues("false", listReq.Action).Inc()
    }
    permissions, err = s.getIdentityPermissions(ctx, listReq.Namespace, listReq.IdentityType, listReq.UserUID, listReq.Action)
    if err != nil {
        ctxLogger.Error("could not get user permissions", "subject", req.GetSubject(), "error", err)
        s.metrics.requestCount.WithLabelValues("true", "true", req.GetVerb(), req.GetGroup(), req.GetResource()).Inc()
        return nil, err
    }
}
```

:yellow_circle: [correctness] `Check()` `permissionCacheUsage{"false"}` fires on cache-hit-but-denied, inflating the apparent cache-miss rate in `pkg/services/authz/rbac/service.go`:128 (confidence: 88)
When `getCachedIdentityPermissions` succeeds (`err == nil`) but `checkPermission` returns `allowed=false`, execution falls through to line 128 which records `permissionCacheUsage{"false"}`. That same `"false"` label is also used for genuine cache misses. In a system where many users lack permission to specific resources (the normal steady state for fine-grained RBAC), the "false" counter will be dominated by successful cache lookups that simply returned denied — making the cache appear far less effective than it is, and making the metric unusable as a cache-tuning signal.
```suggestion
cachedPerms, err := s.getCachedIdentityPermissions(ctx, checkReq.Namespace, checkReq.IdentityType, checkReq.UserUID, checkReq.Action)
if err == nil {
    // Cache was consulted — record as hit regardless of allow/deny outcome
    s.metrics.permissionCacheUsage.WithLabelValues("true", checkReq.Action).Inc()
    allowed, err := s.checkPermission(ctx, cachedPerms, checkReq)
    if err != nil { /* ... */ return deny, err }
    if allowed {
        s.metrics.requestCount.WithLabelValues("false", "true", req.GetVerb(), req.GetGroup(), req.GetResource()).Inc()
        return &authzv1.CheckResponse{Allowed: true}, nil
    }
    // fall through to DB re-check (cache was used but gave deny)
} else {
    s.metrics.permissionCacheUsage.WithLabelValues("false", checkReq.Action).Inc()
}
```

:yellow_circle: [correctness] `getCachedIdentityPermissions` hard errors are silently swallowed in `Check()`, causing a redundant DB round-trip with no log trail in `pkg/services/authz/rbac/service.go`:114 (confidence: 87)
For `TypeUser`/`TypeServiceAccount`, `getCachedIdentityPermissions` calls `GetUserIdentifiers` first and returns its error verbatim. In `Check()`, the guard `if err == nil` treats every error identically to `cache.ErrNotFound`: execution falls through to `getIdentityPermissions`, which internally calls `GetUserIdentifiers` again. On a persistent error this doubles latency and DB load; on a transient error the first failure is invisible in traces because no log line is emitted for the swallowed error. Either distinguish the error kinds or at minimum log at warn level before falling through.
```suggestion
cachedPerms, cacheErr := s.getCachedIdentityPermissions(ctx, checkReq.Namespace, checkReq.IdentityType, checkReq.UserUID, checkReq.Action)
if cacheErr == nil {
    // ... existing cache-hit logic
} else if !errors.Is(cacheErr, cache.ErrNotFound) {
    ctxLogger.Warn("getCachedIdentityPermissions error, falling back to DB", "error", cacheErr)
}
s.metrics.permissionCacheUsage.WithLabelValues("false", checkReq.Action).Inc()
```

:yellow_circle: [correctness] `userPermDenialCacheKey` has a key-collision risk when inputs contain underscores in `pkg/services/authz/rbac/cache.go`:30 (confidence: 87)
The key format is `namespace + ".perm_" + userUID + "_" + action + "_" + name + "_" + parent` with `_` used as a plain separator and no escaping. Inputs that contain `_` collide: for example `(ns="o", uid="u_a", action="a", name="b", parent="c")` produces the same key as `(ns="o", uid="u", action="a_a", name="b", parent="c")`. Grafana historically allowed `[a-zA-Z0-9\-_]` in UIDs so legacy identifiers may contain underscores; action names from extensions can also. A collision fails closed (produces a false deny, not a false allow) so it is not a privilege-escalation vector, but it can cause the wrong user/resource to be silently denied until the TTL expires — exactly the class of "why can't I access my dashboard" bug this PR is trying to fix.
```suggestion
func userPermDenialCacheKey(namespace, userUID, action, name, parent string) string {
    // Use a non-identifier delimiter to avoid collisions when any component contains '_'.
    const sep = "\x1f" // ASCII unit separator
    return namespace + ".perm_deny" + sep + userUID + sep + action + sep + name + sep + parent
}
```

:yellow_circle: [testing] `List()` DB-fallback path has no test coverage in `pkg/services/authz/rbac/service_test.go`:1288 (confidence: 90)
`TestService_CacheList` contains only one subtest and it pre-populates `permCache` to exercise the cache-hit path. The else-branch — where `getCachedIdentityPermissions` returns `ErrNotFound` and `getIdentityPermissions` fetches from the DB — is entirely untested. This path is the actual fix path for the "creator can't see their own resource" bug for list/search, and is the primary behavioural change to `List()`; a regression would be invisible.
```suggestion
t.Run("Fallback to database on cache miss for List", func(t *testing.T) {
    s := setupService()
    ctx := types.WithAuthInfo(context.Background(), callingService)
    userID := &store.UserIdentifiers{UID: "test-uid", ID: 1}

    fakeS := &fakeStore{
        userID:          userID,
        userPermissions: []accesscontrol.Permission{{Action: "dashboards:read", Scope: "dashboards:uid:dash3"}},
    }
    s.store = fakeS
    s.permissionStore = fakeS
    s.idCache.Set(ctx, userIdentifierCacheKey("org-12", "test-uid"), *userID)

    resp, err := s.List(ctx, &authzv1.ListRequest{
        Namespace: "org-12", Subject: "user:test-uid",
        Group: "dashboard.grafana.app", Resource: "dashboards", Verb: "list",
    })
    require.NoError(t, err)
    require.ElementsMatch(t, resp.Items, []string{"dash3"})
})
```

:yellow_circle: [testing] `getCachedIdentityPermissions` hard-error path (non-`ErrNotFound` from `GetUserIdentifiers`) is untested in `pkg/services/authz/rbac/service_test.go`:890 (confidence: 88)
`getCachedIdentityPermissions` surfaces any error from `GetUserIdentifiers` — not just `ErrNotFound`. The silent-swallow behaviour in `Check()` depends on this contract. Without an explicit test, a future change that wraps the error (causing `errors.Is(err, ErrNotFound)` to no longer match in any refactored caller) or that returns a panic would go uncaught. Adding a subtest that injects a hard error and asserts `Check()` still succeeds via the DB fallback documents the intended contract.
```suggestion
t.Run("Hard error from GetUserIdentifiers falls through to DB", func(t *testing.T) {
    s := setupService()
    ctx := types.WithAuthInfo(context.Background(), callingService)
    userID := &store.UserIdentifiers{UID: "test-uid", ID: 1}

    fakeS := &fakeStore{
        userID:          userID,
        userPermissions: []accesscontrol.Permission{{Action: "dashboards:read", Scope: "dashboards:uid:dash1"}},
        idErr:           errors.New("db connection refused"),
    }
    s.store = fakeS
    s.permissionStore = fakeS
    // Intentionally do NOT set idCache, forcing getCachedIdentityPermissions to call GetUserIdentifiers.

    resp, err := s.Check(ctx, &authzv1.CheckRequest{
        Namespace: "org-12", Subject: "user:test-uid",
        Group: "dashboard.grafana.app", Resource: "dashboards",
        Verb: "get", Name: "dash1",
    })
    require.NoError(t, err)
    assert.True(t, resp.Allowed)
})
```

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: authz core (pkg/services/authz/* — Check/List are called on every resource access) | Sensitive Paths: pkg/services/authz/ matches `auth/` pattern
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold)
