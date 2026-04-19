## Summary
4 files changed, 240 lines added, 33 lines deleted. 7 findings (1 critical, 6 improvements, 0 nitpicks).
Denial-cache key ambiguity risks false denies on same-user sibling resources; metric mislabel and several new code paths lack tests.

## Critical

:red_circle: [security] Denial-cache key ambiguous under underscore-separated concatenation in `pkg/services/authz/rbac/cache.go`:66 (confidence: 92)
`userPermDenialCacheKey` joins `namespace`, `userUID`, `action`, `name`, and `parent` with literal `_` separators, but Grafana dashboard/folder UIDs (the `name` and `parent` fields) are user-controllable and commonly contain `_`, so distinct `(name, parent)` tuples can collapse to the same key. A denial cached for one resource will silently deny access to a sibling resource for the full `shortCacheTTL` window.
```suggestion
func userPermDenialCacheKey(namespace, userUID, action, name, parent string) string {
	// '|' is not valid in Grafana UIDs or action strings, so it cannot appear in components.
	return namespace + ".perm_denial|" + userUID + "|" + action + "|" + name + "|" + parent
}
```
<details><summary>More context</summary>

Collision example (same user + `dashboards:read`):

- `name="foo",     parent="bar_baz"` → `ns.perm_uid_dashboards:read_foo_bar_baz`
- `name="foo_bar", parent="baz"`     → `ns.perm_uid_dashboards:read_foo_bar_baz`

The first Check's denial entry (valid for ~30s) will be served for the second Check, returning `Allowed:false` without consulting the DB — even if the user is permitted on the second resource. Because the `userUID` segment is still part of the key this is **not** cross-user leakage and the denial cache can only produce false *denies* (fail-closed), so it is a correctness / availability defect rather than a privilege-escalation bug, but it will manifest as intermittent, hard-to-repro 403s that operators will struggle to debug.

Length-prefixing or hashing the tuple achieves the same guarantee if `|` is not acceptable in your key grammar. The same treatment should probably be applied to `userPermCacheKey` for consistency, though that key has only three components so the collision surface is narrower.

References: [CWE-694 Use of Multiple Resources with Duplicate Identifier](https://cwe.mitre.org/data/definitions/694.html), [CWE-436 Interpretation Conflict](https://cwe.mitre.org/data/definitions/436.html)
</details>

## Improvements

:yellow_circle: [correctness] `permissionCacheUsage` metric labeled as a miss on cache-hit-but-denied path in `pkg/services/authz/rbac/service.go`:128 (confidence: 92)
When `getCachedIdentityPermissions` returns `err==nil` (the positive cache *was* hit) but `checkPermission` returns `allowed==false`, execution falls through to the unconditional `s.metrics.permissionCacheUsage.WithLabelValues("false", ...)` call, so a cache hit that happens not to cover the requested resource is reported as a cache miss. Dashboards and alerts keyed off this metric will overstate the miss rate and mask real cold-cache regressions.
```suggestion
cachedPerms, err := s.getCachedIdentityPermissions(ctx, checkReq.Namespace, checkReq.IdentityType, checkReq.UserUID, checkReq.Action)
if err == nil {
	allowed, cerr := s.checkPermission(ctx, cachedPerms, checkReq)
	if cerr != nil {
		s.metrics.requestCount.WithLabelValues("true", "true", req.GetVerb(), req.GetGroup(), req.GetResource()).Inc()
		return deny, cerr
	}
	s.metrics.permissionCacheUsage.WithLabelValues("true", checkReq.Action).Inc()
	if allowed {
		s.metrics.requestCount.WithLabelValues("false", "true", req.GetVerb(), req.GetGroup(), req.GetResource()).Inc()
		return &authzv1.CheckResponse{Allowed: true}, nil
	}
} else {
	s.metrics.permissionCacheUsage.WithLabelValues("false", checkReq.Action).Inc()
}
```

:yellow_circle: [testing] Denial-cache write path (stale-cache-hit → DB deny → write denial) is untested in `pkg/services/authz/rbac/service_test.go`:993 (confidence: 92)
The new `Check()` writes to `permDenialCache` only when `checkPermission` on DB-fetched perms returns `!allowed` *after* falling through either a denial-cache miss or a positive-cache hit that didn't cover the resource. `TestService_CacheCheck` never asserts that this write actually happens, so a regression flipping the condition polarity or dropping the `Set` call would silently disable the feature the PR is meant to introduce.
```suggestion
t.Run("Denial cache written when cache-hit but DB denies", func(t *testing.T) {
	s := setupService()

	store := &fakeStore{
		userID:          userID,
		userPermissions: []accesscontrol.Permission{{Action: "dashboards:read", Scope: "dashboards:uid:dash1"}},
	}
	s.store = store
	s.permissionStore = store

	s.idCache.Set(ctx, userIdentifierCacheKey("org-12", "test-uid"), *userID)
	s.permCache.Set(ctx, userPermCacheKey("org-12", "test-uid", "dashboards:read"),
		map[string]bool{"dashboards:uid:dash1": true})

	resp, err := s.Check(ctx, &authzv1.CheckRequest{
		Namespace: "org-12", Subject: "user:test-uid",
		Group: "dashboard.grafana.app", Resource: "dashboards",
		Verb: "get", Name: "dash2",
	})
	require.NoError(t, err)
	assert.False(t, resp.Allowed)

	_, ok := s.permDenialCache.Get(ctx, userPermDenialCacheKey("org-12", "test-uid", "dashboards:read", "dash2", ""))
	assert.True(t, ok, "denial cache entry should have been written")
})
```

:yellow_circle: [testing] `List()` cache-miss → DB fallback branch is not exercised in `pkg/services/authz/rbac/service_test.go`:1288 (confidence: 91)
`TestService_CacheList` has exactly one sub-case, which seeds `permCache` and exercises only the cache-hit branch. The `else` branch — where `getCachedIdentityPermissions` returns an error and `List()` falls back to `getIdentityPermissions` — is new code with no coverage, so a regression that swallows the cache error or skips the DB call would silently return empty results for users whose permissions aren't cached.
```suggestion
t.Run("List falls back to database on cache miss", func(t *testing.T) {
	s := setupService()
	ctx := types.WithAuthInfo(context.Background(), callingService)
	userID := &store.UserIdentifiers{UID: "test-uid", ID: 1}
	s.idCache.Set(ctx, userIdentifierCacheKey("org-12", "test-uid"), *userID)

	dbStore := &fakeStore{
		userID:          userID,
		userPermissions: []accesscontrol.Permission{{Action: "dashboards:read", Scope: "dashboards:uid:dash3"}},
	}
	s.store = dbStore
	s.permissionStore = dbStore
	s.identityStore = &fakeIdentityStore{}

	resp, err := s.List(ctx, &authzv1.ListRequest{
		Namespace: "org-12", Subject: "user:test-uid",
		Group: "dashboard.grafana.app", Resource: "dashboards", Verb: "list",
	})
	require.NoError(t, err)
	require.ElementsMatch(t, resp.Items, []string{"dash3"})
})
```

:yellow_circle: [testing] `userPermDenialCacheKey` has no key-format test in `pkg/services/authz/rbac/cache.go`:66 (confidence: 90)
The denial cache is the linchpin of this PR and both the write (in `Check()`) and the read (at the top of `Check()`) depend on this key function producing a stable, injective mapping. A silent typo — swapping `name` and `parent`, dropping a separator — would leave the write and read paths misaligned, so the denial cache would never fire and nothing in the current test suite would fail.
```suggestion
func TestUserPermDenialCacheKey(t *testing.T) {
	key := userPermDenialCacheKey("org-12", "uid-1", "dashboards:read", "dash1", "fold1")
	require.Equal(t, "org-12.perm_uid-1_dashboards:read_dash1_fold1", key)

	keyParent := userPermDenialCacheKey("org-12", "uid-1", "dashboards:read", "dash1", "fold2")
	require.NotEqual(t, key, keyParent)

	keyEmptyParent := userPermDenialCacheKey("org-12", "uid-1", "dashboards:read", "dash1", "")
	require.NotEqual(t, key, keyEmptyParent)
}
```

:yellow_circle: [testing] `getCachedIdentityPermissions` `default` (unsupported identity type) branch has no test in `pkg/services/authz/rbac/service.go`:195 (confidence: 87)
The `default` case in the new `getCachedIdentityPermissions` returns `fmt.Errorf("unsupported identity type: %s", idType)`. Both `Check()` and `List()` treat the returned `err` as a benign cache miss. If a future change ever promoted that error to fatal (or if the function accidentally returned `nil, nil`), every Check from that identity type would either panic or silently deny. A one-line test locks in the error contract.
```suggestion
t.Run("getCachedIdentityPermissions returns error for unsupported identity type", func(t *testing.T) {
	s := setupService()
	ns := types.NamespaceInfo{Value: "org-12"}
	_, err := s.getCachedIdentityPermissions(ctx, ns, types.IdentityType("robot"), "some-uid", "dashboards:read")
	require.Error(t, err)
	assert.Contains(t, err.Error(), "unsupported identity type")
})
```

:yellow_circle: [consistency] `NoopCache` receiver `lc` does not match the type name in `pkg/services/authz/rbac.go`:241 (confidence: 87)
Go convention is that the receiver is a short, consistent abbreviation of the type name; `lc` reads as "local cache", which is specifically what this type is *not*. Rename to `nc` (or drop the pointer receiver — none of the methods need it — and use `NoopCache{}`) for clarity.
```suggestion
type NoopCache struct{}

func (NoopCache) Get(ctx context.Context, key string) ([]byte, error) {
	return nil, cache.ErrNotFound
}

func (NoopCache) Set(ctx context.Context, key string, data []byte, exp time.Duration) error {
	return nil
}

func (NoopCache) Delete(ctx context.Context, key string) error {
	return nil
}
```

## Risk Metadata
Risk Score: 52/100 (MEDIUM) | Blast Radius: foundational authz package; ~5+ downstream callers via `ProvideAuthZClient` DI wiring | Sensitive Paths: all 4 files under `pkg/services/authz/` (auth/)
AI-Authored Likelihood: LOW

(1 additional finding below confidence threshold suppressed)
