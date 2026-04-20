## Summary
4 files changed, 268 lines added, 3 lines deleted. 5 findings (1 critical, 4 improvements).
Adds a login-IDP cache with invalidation hooks; one cache-hit path skips the organization-aware wrapping that all other paths apply, creating cached-vs-uncached behavioral divergence, plus several test-coverage gaps around the new `BROKER_PUBLIC` predicate branch.

## Critical
:red_circle: [correctness] Happy-path cache-hit return in `getForLogin` omits `createOrganizationAwareIdentityProviderModel` wrapping in model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:248 (confidence: 95)
In the new `getForLogin` method, when all IDP ids are successfully resolved from the Infinispan cache (the primary happy path), the resolved `IdentityProviderModel` instances are added to the result set directly. Every other return branch within the same method applies `createOrganizationAwareIdentityProviderModel`: the `isInvalid(cacheKey)` bypass maps with `this::createOrganizationAwareIdentityProviderModel`, and the stale-entry fallback (where `getById` returns `null`) also wraps before returning. Only the cache-hit loop bypasses the wrapper. `createOrganizationAwareIdentityProviderModel` decorates the model with organization context (visibility / linked-org flags). Without it, callers served from cache receive a semantically different model than callers served from the delegate. The compensating `idp.isEnabled()` re-check added in `OrganizationAwareIdentityProviderBean` with the comment *"re-check isEnabled as idp might have been wrapped"* is itself a tell that wrapped and unwrapped models behave differently here — that workaround only helps that one consumer, not other callers of `getForLogin` that may now silently receive unwrapped models.
```suggestion
Set<IdentityProviderModel> identityProviders = new HashSet<>();
for (String id : cached) {
    IdentityProviderModel idp = session.identityProviders().getById(id);
    if (idp == null) {
        realmCache.registerInvalidation(cacheKey);
        return idpDelegate.getForLogin(mode, organizationId)
                .map(this::createOrganizationAwareIdentityProviderModel);
    }
    identityProviders.add(createOrganizationAwareIdentityProviderModel(idp));
}
return identityProviders.stream();
```

## Improvements
:yellow_circle: [testing] Test cleanup uses hardcoded literal `"alias"` instead of `"idp-alias-" + i`, leaving up to 21 IDPs unreleased after the test in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java:378 (confidence: 97)
Inside the setup loop (and again after the `idp-alias-20` creation later in the test) the cleanup registration calls `testRealm().identityProviders().get("alias")::remove`. The string `"alias"` is a literal, not `"idp-alias-" + i`, so none of the 21 cleanup entries targets a real alias — each one silently no-ops. Every IDP created by this test persists in the realm after the test finishes, polluting the realm state for any subsequent test in the suite that queries identity providers and making `testCacheIDPForLogin` non-repeatable if the test class is re-run against the same realm.
```suggestion
final String alias = "idp-alias-" + i;
testRealm().identityProviders().create(idpRep).close();
getCleanup().addCleanup(testRealm().identityProviders().get(alias)::remove);
```

:yellow_circle: [testing] New `BROKER_PUBLIC` predicate branch in `getLoginPredicate()` has no test coverage — the `organizationId != null && BROKER_PUBLIC=false` exclusion path is never exercised in server-spi/src/main/java/org/keycloak/models/IdentityProviderStorageProvider.java:254 (confidence: 92)
The added clause `.and(idp -> idp.getOrganizationId() == null || Boolean.parseBoolean(idp.getConfig().get(OrganizationModel.BROKER_PUBLIC)))` means an IDP that is linked to an organization but does *not* have `BROKER_PUBLIC=true` must be filtered out of the login results. In the new test, every org-linked IDP (indices 10–19, and later `idp-alias-20`) is created with `BROKER_PUBLIC=true`. No IDP in the test data satisfies `organizationId != null` AND `BROKER_PUBLIC != true`, so the new branch never evaluates to `false`. A regression that removed the `BROKER_PUBLIC` guard entirely would still pass this test suite.
```suggestion
IdentityProviderRepresentation privateOrgIdp = new IdentityProviderRepresentation();
privateOrgIdp.setAlias("idp-private-org");
privateOrgIdp.setEnabled(true);
privateOrgIdp.setProviderId("keycloak-oidc");
privateOrgIdp.getConfig().put(OrganizationModel.BROKER_PUBLIC, Boolean.FALSE.toString());
testRealm().identityProviders().create(privateOrgIdp).close();
testRealm().organizations().get(orgaId).identityProviders().addIdentityProvider("idp-private-org");

getTestingClient().server(TEST_REALM_NAME).run((RunOnServer) session -> {
    List<String> orgOnlyAliases = session.identityProviders()
            .getForLogin(FetchMode.ORG_ONLY, orgaId)
            .map(IdentityProviderModel::getAlias)
            .collect(Collectors.toList());
    assertFalse("private-org IDP must be excluded by BROKER_PUBLIC guard",
            orgOnlyAliases.contains("idp-private-org"));
});
```

:yellow_circle: [testing] Scenario 4 conflates a `BROKER_PUBLIC` config update and an org-link into a single assertion, so it cannot isolate which operation triggered invalidation in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java:508 (confidence: 85)
Scenario 4 performs two sequential state changes on `idp-alias-20` — first an `update()` setting `BROKER_PUBLIC=true` (which flows through `registerIDPLoginInvalidationOnUpdate`), then a separate `addIdentityProvider` org-link call — and only asserts the caches are clear once at the end. Because each change independently triggers cache invalidation, the assertion does not verify that `addIdentityProvider` alone invalidates the login cache when the IDP already has `BROKER_PUBLIC=true`. If the invalidation path inside `addIdentityProvider` were accidentally dropped, this test would still pass because the preceding update already cleared the cache.
```suggestion
// 4a: flip BROKER_PUBLIC only, then repopulate caches
idpRep = testRealm().identityProviders().get("idp-alias-20").toRepresentation();
idpRep.getConfig().put(OrganizationModel.BROKER_PUBLIC, Boolean.TRUE.toString());
testRealm().identityProviders().get("idp-alias-20").update(idpRep);
getTestingClient().server(TEST_REALM_NAME).run((RunOnServer) session -> {
    for (FetchMode mode : FetchMode.values()) {
        session.identityProviders().getForLogin(mode, orgaId);
    }
});

// 4b: org-link alone MUST invalidate every login-cache entry
testRealm().organizations().get(orgaId).identityProviders().addIdentityProvider("idp-alias-20");
getTestingClient().server(TEST_REALM_NAME).run((RunOnServer) session -> {
    RealmModel realm = session.getContext().getRealm();
    RealmCacheSession realmCache = (RealmCacheSession) session.getProvider(CacheRealmProvider.class);
    for (FetchMode mode : FetchMode.values()) {
        assertNull("org-link alone must invalidate cache for " + mode,
                realmCache.getCache().get(cacheKeyForLogin(realm, mode), IdentityProviderListQuery.class));
    }
});
```

:yellow_circle: [consistency] New public helper named `cacheKeyForLogin` breaks the `cacheKeyIdpXxx` convention used by neighboring helpers in model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:77 (confidence: 85)
Sibling cache-key builders in the same class are named `cacheKeyIdpAlias` and `cacheKeyOrgId` (both prefixed with `cacheKeyIdp`/`cacheKey<Concept>`). The new helper `cacheKeyForLogin` deviates from that pattern; the backing constant `IDP_LOGIN_SUFFIX` similarly omits the `_KEY_SUFFIX` tail used by `IDP_COUNT_KEY_SUFFIX`, `IDP_ALIAS_KEY_SUFFIX`, `IDP_ORG_ID_KEY_SUFFIX`. Renaming keeps the public API of this class uniform and avoids future confusion for maintainers scanning for cache-key producers.
```suggestion
private static final String IDP_LOGIN_KEY_SUFFIX = ".idp.login";

public static String cacheKeyIdpLogin(RealmModel realm, FetchMode fetchMode) {
    return realm.getId() + IDP_LOGIN_KEY_SUFFIX + "." + fetchMode;
}
```

## Risk Metadata
Risk Score: 35/100 (MEDIUM) | Blast Radius: server-spi interface change (`IdentityProviderStorageProvider`) — touches broadly-referenced SSO login path; Infinispan cache impl + freemarker bean downstream | Sensitive Paths: none matched literal globs, but semantic area is authentication/login-IDP surfacing
AI-Authored Likelihood: LOW

(5 additional findings below confidence threshold: `remove()` silently skipping login-cache invalidation when `idpDelegate.getByAlias` returns `null` (75), `IDP_LOGIN_SUFFIX` constant naming without `_KEY_SUFFIX` tail (82), inconsistent revision source in the two `addRevisioned` branches — `startupRevision` vs `cache.getCurrentCounter()` (78), missing direct test for the new `idp.isEnabled()` re-check in `OrganizationAwareIdentityProviderBean` (80), and the new org/`BROKER_PUBLIC` clause being added inline rather than as a `LoginFilter` enum value alongside the existing pluggable filters (68).)
