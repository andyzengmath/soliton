## Summary
4 files changed, 240 lines added, 33 lines deleted. 7 findings (2 critical, 5 improvements, 0 nitpicks).
Most urgent: `getCachedIdentityPermissions` errors other than `ErrNotFound` silently fall through to the DB path in `Check()`.

## Critical

:red_circle: [correctness] getCachedIdentityPermissions errors other than ErrNotFound silently fall through to DB path in Check() in pkg/services/authz/rbac/service.go:114 (confidence: 90)
In `Check()`, `getCachedIdentityPermissions` returns two classes of non-nil error: (a) `cache.ErrNotFound` (expected miss) and (b) real errors such as a `GetUserIdentifiers` DB failure or the unsupported identity-type sentinel (`fmt.Errorf("unsupported identity type: %s", idType)`). The outer guard is `if err == nil { ... }`, so both classes silently skip the block and fall through to the DB path. For real errors, the original error is lost, no log line is emitted, and the code makes a redundant `getIdentityPermissions` call that will likely also fail. For unsupported identity types, the explicit error is swallowed and the code proceeds to attempt a full DB lookup, potentially returning a result for a type that should have been explicitly rejected.
```suggestion
if err != nil && !errors.Is(err, cache.ErrNotFound) {
    ctxLogger.Error("getCachedIdentityPermissions failed", "error", err)
    s.metrics.requestCount.WithLabelValues("true", "true", req.GetVerb(), req.GetGroup(), req.GetResource()).Inc()
    return deny, err
}
if err == nil {
    allowed, err := s.checkPermission(ctx, cachedPerms, checkReq)
    // ...
}
```

:red_circle: [security] permDenialCache key omits IdentityType, allowing cross-identity-type denial reuse in pkg/services/authz/rbac/cache.go:30 (confidence: 90)
`userPermDenialCacheKey(namespace, userUID, action, name, parent)` does not include the `IdentityType` dimension. The positive cache in `getCachedIdentityPermissions` correctly dispatches on `idType` (`TypeAnonymous` uses a UID-less key, `TypeRenderService` bypasses cache entirely, `TypeUser`/`TypeServiceAccount` look up by resolved user identifier). The denial-cache key collapses those identity types together. In Grafana, `TypeUser` and `TypeServiceAccount` are separate identity namespaces that can legitimately share numeric UIDs; a denial cached for one is served to the other without evaluating that identity's own permissions. The failure direction is false-deny (broken access-control correctness), but the inconsistency with the positive cache makes this a clear bug.
```suggestion
func userPermDenialCacheKey(namespace, identityType, userUID, action, name, parent string) string {
    return namespace + ".perm_" + identityType + "_" + userUID + "_" + action + "_" + name + "_" + parent
}
// Update call sites to pass string(checkReq.IdentityType).
```
[References: https://owasp.org/Top10/A01_2021-Broken_Access_Control/, https://cwe.mitre.org/data/definitions/863.html]

## Improvements

:yellow_circle: [security] Denial cache key uses unescaped '_' delimiter — collisions possible across (name, parentFolder) tuples in pkg/services/authz/rbac/cache.go:30 (confidence: 85)
`userPermDenialCacheKey` concatenates five fields with unescaped `_` as separator. Resource UIDs, folder UIDs, and some identity-provider user UIDs can legitimately contain underscores. Distinct tuples can therefore produce identical keys — for example `(name="b", parent="c_d")` and `(name="b_c", parent="d")` both serialize to the suffix `..._b_c_d`. A denial cached for one `(name, parent)` tuple is served for a different colliding tuple, producing stale or incorrect denials. An attacker who can choose a dashboard or folder UID could deliberately induce collisions to deny access to a legitimate peer resource.
```suggestion
const sep = "\x1f" // ASCII unit separator — cannot appear in any identifier
raw := strings.Join([]string{namespace, string(identityType), userUID, action, name, parent}, sep)
sum := sha256.Sum256([]byte(raw))
return namespace + ".permdeny_" + hex.EncodeToString(sum[:])
```

:yellow_circle: [security] TypeRenderService bypasses positive cache but NOT the denial cache — asymmetric staleness in pkg/services/authz/rbac/service.go:115 (confidence: 85)
`getCachedIdentityPermissions` returns `cache.ErrNotFound` for `TypeRenderService` unconditionally, so the positive cache is intentionally disabled for this identity type. However the `permDenialCache.Get` check fires before identity-type dispatch and is not gated on `idType`. A denial recorded for a `TypeRenderService` request is served from cache for up to `shortCacheTTL` even though the positive side is deliberately non-cacheable. If the render service's effective permissions change within that window, previously-denied actions remain denied while previously-granted ones correctly re-evaluate — contradicting the design intent that motivated disabling the positive cache for this identity type.
```suggestion
if checkReq.IdentityType != types.TypeRenderService {
    if _, ok := s.permDenialCache.Get(ctx, permDenialKey); ok {
        return &authzv1.CheckResponse{Allowed: false}, nil
    }
}
// and on the write side:
if !allowed && checkReq.IdentityType != types.TypeRenderService {
    s.permDenialCache.Set(ctx, permDenialKey, true)
}
```

:yellow_circle: [testing] getCachedIdentityPermissions TypeAnonymous and TypeRenderService branches have no test coverage in pkg/services/authz/rbac/service.go:172 (confidence: 92)
The new `getCachedIdentityPermissions` has three identity-type branches but every new subtest uses `TypeUser`. The `TypeAnonymous` branch (cache hit and miss via `anonymousPermCacheKey`) and the `TypeRenderService` branch (always returns `ErrNotFound`, intentionally bypasses cache) are never exercised. The `default` unsupported-type branch is likewise untested. Regressions in either — anonymous permissions cached under the wrong key, or a render service being served stale data — would ship undetected.
```suggestion
t.Run("Anonymous: allow from anonymousPermCacheKey", func(t *testing.T) {
    s := setupService()
    s.permCache.Set(ctx, anonymousPermCacheKey("org-12", "dashboards:read"),
        map[string]bool{"dashboards:uid:dash1": true})
    resp, err := s.Check(ctx, &authzv1.CheckRequest{/* ... */ Subject: "anonymous:anonymous", Name: "dash1"})
    require.NoError(t, err); assert.True(t, resp.Allowed)
})
t.Run("RenderService: cache is bypassed, always hits DB", func(t *testing.T) {
    s := setupService()
    s.store = &fakeStore{userPermissions: []accesscontrol.Permission{{Action: "dashboards:read", Scope: "dashboards:uid:dash1"}}}
    s.permCache.Set(ctx, userPermCacheKey("org-12", "test-uid", "dashboards:read"), map[string]bool{}) // poison positive cache
    resp, err := s.Check(ctx, &authzv1.CheckRequest{/* ... */ Subject: "render:render", Name: "dash1"})
    require.NoError(t, err); assert.True(t, resp.Allowed) // served from DB despite empty cache
})
```

:yellow_circle: [testing] List() cache-miss and DB-fallback paths are not tested in pkg/services/authz/rbac/service.go:177 (confidence: 90)
`TestService_CacheList` covers only the cache-hit path. The refactored `List()` now calls `getCachedIdentityPermissions` first and falls back to `getIdentityPermissions` on miss — the same shape as `Check()`, which gained two fallback subtests in this PR. The equivalent fallback subtests are absent for `List()`. A regression that skips the DB fallback (e.g., an accidental early return on `ErrNotFound`) would ship undetected for `List()` callers.
```suggestion
t.Run("Fallback to DB on cache miss", func(t *testing.T) {
    s := setupService()
    s.store = &fakeStore{userID: userID, userPermissions: []accesscontrol.Permission{{Action: "dashboards:read", Scope: "dashboards:uid:dash1"}}}
    s.idCache.Set(ctx, userIdentifierCacheKey("org-12", "test-uid"), *userID)
    // no permCache seeded — force DB fallback
    resp, err := s.List(ctx, &authzv1.ListRequest{/* ... */})
    require.NoError(t, err)
    require.ElementsMatch(t, resp.Items, []string{"dash1"})
})
```

:yellow_circle: [testing] Denial cache write path is untested — only the read path is covered in pkg/services/authz/rbac/service.go:137 (confidence: 88)
The existing "Should deny on explicit cache deny entry" subtest pre-seeds `permDenialCache` and verifies the read path. No test verifies the write side: after a DB-backed `Check()` returns `!allowed`, the entry is actually written to `permDenialCache`, and a subsequent identical request is served from cache without hitting the DB. A regression that breaks the `Set()` call (wrong key, wrong condition, or unreachable line) would be invisible in CI.
```suggestion
t.Run("Denial is cached after DB deny so second request skips DB", func(t *testing.T) {
    callCount := 0
    store := &fakeStore{userID: userID, userPermissions: nil, onGetPermissions: func() { callCount++ }}
    s := setupService(); s.store = store; s.permissionStore = store
    s.idCache.Set(ctx, userIdentifierCacheKey("org-12", "test-uid"), *userID)
    req := &authzv1.CheckRequest{/* ... */ Name: "dash1"}
    // first: DB hit, caches denial
    r1, err := s.Check(ctx, req); require.NoError(t, err); assert.False(t, r1.Allowed); assert.Equal(t, 1, callCount)
    // second: must be served from denial cache
    r2, err := s.Check(ctx, req); require.NoError(t, err); assert.False(t, r2.Allowed); assert.Equal(t, 1, callCount)
})
```

## Risk Metadata
Risk Score: 40/100 (MEDIUM) | Blast Radius: local (4 files under pkg/services/authz/) — actual Grafana consumption is broad but not measurable from this shim | Sensitive Paths: all four changed files match `auth/`
AI-Authored Likelihood: LOW

(6 additional findings below confidence threshold)
