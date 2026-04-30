## Summary
4 files changed, 268 lines added, 3 lines deleted. 5 findings (1 critical, 3 improvements, 1 nitpick).
Caching layer for `IdentityProviderStorageProvider.getForLogin` is functionally well-structured and follows existing patterns in `InfinispanIdentityProviderStorageProvider`, but the new integration test has a copy-paste cleanup bug that leaks 21 IDPs per run, and the global `getLoginPredicate()` change quietly extends semantics in a way that needs cross-call-site verification.

## Critical
:red_circle: [testing] Test cleanup references literal alias `"alias"` instead of dynamic alias in `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java`:234 (confidence: 95)
The new `testCacheIDPForLogin` test creates 20 IDPs in a loop with aliases `"idp-alias-0"` through `"idp-alias-19"`, plus `"idp-alias-20"` later. Each `getCleanup().addCleanup(...)` call passes a method reference resolved against the literal alias `"alias"`, not the actual loop-bound alias. At cleanup time `testRealm().identityProviders().get("alias")::remove` will look up an IDP whose alias is the string `"alias"` (does not exist) — the cleanup either fails silently or 404s, leaving 21 stale `IdentityProviderRepresentation` entries plus their org links in the test realm. Subsequent test runs in the same suite will see drift (cached IDPs from previous runs influencing counts), and the assertions `assertEquals(5, …)`, `assertEquals(10, …)`, `assertEquals(11, …)` will eventually fail in long-running pipelines. Same bug appears for `idp-alias-20` at line 278.
```suggestion
            String alias = "idp-alias-" + i;
            idpRep.setAlias(alias);
            idpRep.setEnabled((i % 2) == 0); // half of the IDPs will be disabled and won't qualify for login.
            idpRep.setDisplayName("Broker " + i);
            idpRep.setProviderId("keycloak-oidc");
            if (i >= 10)
                idpRep.getConfig().put(OrganizationModel.BROKER_PUBLIC, Boolean.TRUE.toString());
            testRealm().identityProviders().create(idpRep).close();
            getCleanup().addCleanup(testRealm().identityProviders().get(alias)::remove);
```

## Improvements
:yellow_circle: [correctness] Global `getLoginPredicate()` change broadens semantics beyond the cache callsite in `server-spi/src/main/java/org/keycloak/models/IdentityProviderStorageProvider.java`:254 (confidence: 80)
The added clause `.and(idp -> idp.getOrganizationId() == null || Boolean.parseBoolean(idp.getConfig().get(OrganizationModel.BROKER_PUBLIC)))` is appended to the shared `getLoginPredicate()` returned to all callers, not just to the new cache path. Any consumer that previously used `getLoginPredicate()` to evaluate an org-linked private IDP will now get `false` for it. The PR description and tests both focus on the caching feature, so the predicate widening is an undocumented side effect. Verify the JPA storage provider implementation, any places that call `LoginFilter.getLoginPredicate()` directly, and any external SPI consumers — they will silently change behavior the moment this lands. At minimum, add a Javadoc on `getLoginPredicate()` documenting the new "org-linked IDPs must be `BROKER_PUBLIC` to qualify for login" rule and confirm in tests that org-private IDPs are still reachable via the org-aware lookup paths that don't go through this predicate.

:yellow_circle: [performance] `remove()` now performs an eager `idpDelegate.getByAlias(alias)` on every call in `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java`:99 (confidence: 75)
Previously `idpDelegate.getByAlias(alias)` was only invoked on the `isInvalid(cacheKey)` path. After this PR it runs unconditionally so the result can be passed to `registerIDPLoginInvalidation(storedIdp)` at the end. For workloads that bulk-remove IDPs (e.g. realm tear-down, org cleanup) this doubles the DB roundtrips on the hot path, even when the alias cache entry is fresh. Consider conditionally fetching only when the login-cache invalidation actually needs the IDP, or reusing the cached `CachedIdentityProvider` when `isInvalid(cacheKey)` is false: `IdentityProviderModel storedIdp = isInvalid(cacheKey) ? idpDelegate.getByAlias(alias) : cached.getIdentityProvider(getRealm(), this);` (or equivalent constructor on the cache wrapper).

:yellow_circle: [correctness] Cache write uses pre-query revision token, leaving a small invalidation race window in `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java`:225 (confidence: 65)
`Long loaded = cache.getCurrentRevision(cacheKey)` is captured before `idpDelegate.getForLogin(...)` runs. If a concurrent `update()`/`create()`/`remove()` triggers `registerIDPLoginInvalidation` between the revision capture and `cache.addRevisioned(query, startupRevision)`, the new entry can be installed with a revision predating the invalidation and the change will be masked until the next mutation. The neighbouring `getByOrganization` shows the same shape, so this is consistent with existing code, but it's worth a brief comment near the new method explaining the eventual-consistency guarantee callers should expect (especially since this code is exercised on the login hot path).

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: 4 files, login/auth flow, infinispan cache layer | Sensitive Paths: `auth`-adjacent (login predicate), no secrets/credentials touched
AI-Authored Likelihood: LOW
