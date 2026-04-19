## Summary
4 files changed, 268 lines added, 3 lines deleted. 4 findings (1 critical, 3 improvements).
Adds a per-realm login-IDP cache with invalidation hooks. Logic is sound, but one test-cleanup typo leaks state, `remove()` now does an unconditional DB lookup, and the new `getForLogin` cache key collides across organization searches, causing frequent rebuilds.

## Critical
:red_circle: [testing] Test cleanup targets nonexistent alias `"alias"` in OrganizationCacheTest.java:234 (confidence: 95)
The cleanup lambda captures the literal string `"alias"` instead of the loop-scoped `"idp-alias-" + i`. Every iteration registers the same cleanup, so after the test none of the 20 `idp-alias-*` providers are removed, and 20 calls to `identityProviders().get("alias").remove()` will fire against an IDP that never existed. This both leaks realm state into subsequent tests and can mask failures by NPE / 404 during teardown. The same typo is repeated at line 278 for `idp-alias-20`.
```suggestion
            String alias = "idp-alias-" + i;
            testRealm().identityProviders().create(idpRep).close();
            getCleanup().addCleanup(() -> testRealm().identityProviders().get(alias).remove());
```

## Improvements
:yellow_circle: [correctness] `remove()` now performs an unconditional `getByAlias` DB round-trip in InfinispanIdentityProviderStorageProvider.java:94 (confidence: 88)
Before the change, `idpDelegate.getByAlias(alias)` was only invoked inside the `isInvalid(cacheKey)` branch. The new code hoists the lookup above the `isInvalid` check so that `storedIdp` is always available for `registerIDPLoginInvalidation`. For realms with a warm IDP-by-alias cache this doubles the DB load on every IDP removal. Prefer reading from the existing `realmCache.getCache().get(cacheKey, CachedIdentityProvider.class)` when the key is valid, and only fall back to `idpDelegate.getByAlias` when cache is cold. Alternatively, reuse the already-fetched `cached` entry inside the `else` branch for the login-invalidation decision.
```suggestion
    @Override
    public boolean remove(String alias) {
        String cacheKey = cacheKeyIdpAlias(getRealm(), alias);
        IdentityProviderModel storedIdp = null;
        if (isInvalid(cacheKey)) {
            storedIdp = idpDelegate.getByAlias(alias);
            registerIDPInvalidation(storedIdp);
        } else {
            CachedIdentityProvider cached = realmCache.getCache().get(cacheKey, CachedIdentityProvider.class);
            if (cached != null) {
                storedIdp = cached.getIdentityProvider();
                realmCache.registerInvalidation(cached.getId());
            } else {
                storedIdp = idpDelegate.getByAlias(alias);
            }
        }
        registerCountInvalidation();
        registerIDPLoginInvalidation(storedIdp);
        return idpDelegate.remove(alias);
    }
```

:yellow_circle: [cross-file-impact] Tightened `getLoginPredicate()` silently filters org-linked non-public IDPs in IdentityProviderStorageProvider.java:253 (confidence: 88)
The one-line addition `idp -> idp.getOrganizationId() == null || Boolean.parseBoolean(idp.getConfig().get(OrganizationModel.BROKER_PUBLIC))` changes the SPI contract of `LoginFilter.getLoginPredicate()` for *every* caller, not only the new `InfinispanIdentityProviderStorageProvider.getForLogin`. Any provider implementation, mapper, or test that applies `getLoginPredicate()` directly to a stream of IDPs will now drop org-linked IDPs that were previously returned when `BROKER_PUBLIC` wasn't set. If `OrganizationModel` is feature-gated and `getOrganizationId()` can be null for every IDP when the Organizations feature is disabled, the change is a no-op there — but it should be validated and called out in the PR description. Consider moving the org-scope filter next to its caller (e.g. a helper invoked only from the Infinispan provider's `getForLogin`) rather than redefining the public predicate, or at minimum add a `@since` javadoc noting the behavior change so downstream reimplementers (JPA/LDAP provider variants) apply the same filter.
```suggestion
        public static Predicate<IdentityProviderModel> getLoginPredicate() {
            // Exclude org-linked IDPs unless they are explicitly marked BROKER_PUBLIC.
            // NOTE: contract change — prior to this revision org-linked IDPs were not filtered here.
            return ((Predicate<IdentityProviderModel>) Objects::nonNull)
                    .and(idp -> idp.getOrganizationId() == null
                            || Boolean.parseBoolean(idp.getConfig().get(OrganizationModel.BROKER_PUBLIC)))
                    .and(Stream.of(values()).map(LoginFilter::getFilter).reduce(Predicate::and).get());
        }
```

:yellow_circle: [correctness] `cacheKeyForLogin` collides across distinct `organizationId` searches, causing repeated cache drops in InfinispanIdentityProviderStorageProvider.java:213 (confidence: 85)
The cache key is `realmId + ".idp.login." + fetchMode` — it does **not** include `organizationId`. The code stores per-org results inside a single `IdentityProviderListQuery` via `searchKey`, but when a new `searchKey` is requested for an already-cached query, the `else`-branch calls `cache.invalidateObject(cacheKey)` and rebuilds the query from scratch, discarding every previously-cached org. For a realm with N organizations, the first login request for each org invalidates all prior cached orgs, so the cache behaves almost like no-cache under fan-out load. Either (a) fold `organizationId` into the cache key so each org gets its own `IdentityProviderListQuery`, or (b) extend the existing query in place with the new search key instead of invalidating and rebuilding.
```suggestion
        } else {
            cached = query.getIDPs(searchKey);
            if (cached == null) {
                // extend the existing query rather than invalidate-and-rebuild so prior org
                // search keys remain cached.
                cached = idpDelegate.getForLogin(mode, organizationId)
                        .map(IdentityProviderModel::getInternalId)
                        .collect(Collectors.toSet());
                IdentityProviderListQuery extended = new IdentityProviderListQuery(
                        query.getRevision(), cacheKey, getRealm(), searchKey, cached, query);
                cache.addRevisioned(extended, cache.getCurrentCounter());
                query = extended;
            }
        }
```

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: SPI change in `IdentityProviderStorageProvider.LoginFilter.getLoginPredicate()` reachable by all IDP storage implementations; `InfinispanIdentityProviderStorageProvider` is the default server impl | Sensitive Paths: none matched (no auth/, secrets, credentials); IDP login filtering is security-adjacent
AI-Authored Likelihood: LOW
