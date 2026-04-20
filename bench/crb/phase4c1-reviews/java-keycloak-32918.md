## Summary
4 files changed, 268 lines added, 3 lines deleted. 6 findings (2 critical, 4 improvements).
Cache for `getForLogin` is functional but the cache-hit path can return disabled IDPs that the pre-cache delegate would have filtered, and the new integration test has a cleanup bug and gaps in scenario coverage that hide real regressions.

## Critical
:red_circle: [testing] Cleanup registers literal `"alias"` instead of the actual IDP alias — created IDPs are never cleaned up in `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java`:234 (confidence: 97)
The cleanup registration inside the 20-iteration creation loop calls `testRealm().identityProviders().get("alias")::remove`, where `"alias"` is a hardcoded string literal rather than the loop alias `"idp-alias-" + i`. The method reference is captured immediately, so all 20 iterations (and the extra IDP at line 278) register a no-op delete against a nonexistent resource. If the test fails partway through, none of the created IDPs are cleaned up, poisoning subsequent test runs in the same realm with leftover IDPs and organization links.
```suggestion
// inside the loop at line 233:
String alias = "idp-alias-" + i;
idpRep.setAlias(alias);
// ...
testRealm().identityProviders().create(idpRep).close();
getCleanup().addCleanup(testRealm().identityProviders().get(alias)::remove);

// and at line 278 for the extra IDP:
getCleanup().addCleanup(testRealm().identityProviders().get("idp-alias-20")::remove);
```

:red_circle: [testing] `OrganizationAwareIdentityProviderBean.isEnabled()` re-check has no corresponding test in `services/src/main/java/org/keycloak/organization/forms/login/freemarker/model/OrganizationAwareIdentityProviderBean.java`:187 (confidence: 85)
The PR adds `idp.isEnabled() &&` to both filter calls in `searchForIdentityProviders` with the comment "re-check isEnabled as idp might have been wrapped". This is a user-visible defensive filter on the login page, but no test in the diff (or the existing organization login testsuite referenced by it) exercises a wrapped-but-disabled IDP actually being suppressed. Without a test, there is no regression guard that a wrapped disabled IDP will continue to be hidden from the login page as the wrapping stack evolves.
```suggestion
@Test
public void testDisabledWrappedIdpNotShownOnLoginPage() {
    IdentityProviderRepresentation idpRep = createOrgBroker(orgId, /* enabled */ false, /* brokerPublic */ true);
    testRealm().identityProviders().create(idpRep).close();
    testRealm().organizations().get(orgId).identityProviders().addIdentityProvider(idpRep.getAlias());

    loginPage.open();
    assertFalse(loginPage.isSocialButtonPresent(idpRep.getAlias()));
}
```

## Improvements
:yellow_circle: [correctness] `getForLogin` cache-hit path can return IDPs whose wrapped `isEnabled()` is false in `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java`:105 (confidence: 88)
Cached IDs are resolved via `session.identityProviders().getById(id)` (line 107 of the diff). If the session provider stack includes an organization-aware layer whose wrapper overrides `isEnabled()` to reflect the linked organization's disabled state, the cache-hit return at line 115 (`return identityProviders.stream();`) yields models where `isEnabled()` is false — even though the delegate had enforced `LoginFilter` at population time. The `isInvalid` fallback paths go through `idpDelegate.getForLogin(...)` and wrap explicitly, producing the same effect, so the contract divergence is only on the cache-hit path. `OrganizationAwareIdentityProviderBean` defensively re-checks (see separate finding), but any other caller of `getForLogin` that trusts the documented contract ("IDPs available for login") will silently receive disabled IDPs from the cache.
```suggestion
Set<IdentityProviderModel> identityProviders = new HashSet<>();
for (String id : cached) {
    IdentityProviderModel idp = session.identityProviders().getById(id);
    if (idp == null) {
        realmCache.registerInvalidation(cacheKey);
        return idpDelegate.getForLogin(mode, organizationId).map(this::createOrganizationAwareIdentityProviderModel);
    }
    identityProviders.add(idp);
}

return identityProviders.stream().filter(IdentityProviderModel::isEnabled);
```

:yellow_circle: [testing] Scenario 1 never removes a login-eligible IDP — the `registerIDPLoginInvalidation` branch of `remove()` is untested in `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java`:281 (confidence: 90)
The test removes `"idp-alias-1"`, which has `enabled=false` (index 1: `(1 % 2) == 0` is false), so it does not pass `getLoginPredicate()` and the production `registerIDPLoginInvalidation(storedIdp)` call from `remove()` short-circuits. The branch where a login-eligible IDP is removed and login caches must actually be invalidated is never exercised, so a regression that failed to invalidate on removal of a live login IDP would not be caught by this suite.
```suggestion
// append after the existing Scenario 1:
testRealm().identityProviders().get("idp-alias-0").remove();

getTestingClient().server(TEST_REALM_NAME).run((RunOnServer) session -> {
    RealmModel realm = session.getContext().getRealm();
    RealmCacheSession realmCache = (RealmCacheSession) session.getProvider(CacheRealmProvider.class);
    for (FetchMode fetchMode : FetchMode.values()) {
        assertNull(realmCache.getCache().get(cacheKeyForLogin(realm, fetchMode), IdentityProviderListQuery.class));
    }
    session.identityProviders().getForLogin(FetchMode.REALM_ONLY, null);
    IdentityProviderListQuery q = realmCache.getCache().get(cacheKeyForLogin(realm, FetchMode.REALM_ONLY), IdentityProviderListQuery.class);
    assertEquals(4, q.getIDPs("").size());
});
```

:yellow_circle: [testing] All assertions inspect cache-internal state only — actual `getForLogin()` return values are never verified end-to-end in `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java`:254 (confidence: 88)
Every `session.identityProviders().getForLogin(...)` call discards its return value; assertions target only `IdentityProviderListQuery` objects pulled directly from the Infinispan cache. The test therefore verifies which IDs are stored but never that callers receive the correct `IdentityProviderModel` objects back — and the stale-cache fallback path at lines 107–111 of the production code (when `session.identityProviders().getById(id)` returns null and the method falls back to the delegate) is completely unexercised.
```suggestion
List<IdentityProviderModel> realmIdps = session.identityProviders()
    .getForLogin(FetchMode.REALM_ONLY, null)
    .collect(Collectors.toList());
assertEquals(5, realmIdps.size());
assertTrue(realmIdps.stream().allMatch(IdentityProviderModel::isEnabled));
assertTrue(realmIdps.stream().noneMatch(idp -> idp.getOrganizationId() != null));
```

:yellow_circle: [testing] Unlink scenario (org IDP demoted back to realm-level) is not covered — only the link direction is tested in `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java`:354 (confidence: 85)
Scenario 4 covers promoting a realm IDP to an org-linked IDP (setting `BROKER_PUBLIC=true` and calling `addIdentityProvider`). The symmetric operation — removing an IDP from an org, or flipping `BROKER_PUBLIC` off — is absent. The `registerIDPLoginInvalidationOnUpdate` production method handles both directions of the org-linkage change, but only one direction is exercised, leaving the reverse-direction invalidation logic untested.
```suggestion
// 5- unlink an org IDP back to realm-level — should also invalidate caches
idpRep = testRealm().identityProviders().get("idp-alias-20").toRepresentation();
idpRep.getConfig().remove(OrganizationModel.BROKER_PUBLIC);
testRealm().identityProviders().get("idp-alias-20").update(idpRep);
testRealm().organizations().get(orgaId).identityProviders().delete("idp-alias-20");

getTestingClient().server(TEST_REALM_NAME).run((RunOnServer) session -> {
    RealmModel realm = session.getContext().getRealm();
    RealmCacheSession realmCache = (RealmCacheSession) session.getProvider(CacheRealmProvider.class);
    for (FetchMode fetchMode : FetchMode.values()) {
        assertNull(realmCache.getCache().get(cacheKeyForLogin(realm, fetchMode), IdentityProviderListQuery.class));
    }
});
```

## Risk Metadata
Risk Score: 40/100 (MEDIUM) | Blast Radius: 70 (SPI interface + Infinispan cache layer + Freemarker login UI bean — authentication hot path, ~15–25 importers expected in full tree) | Sensitive Paths: 0 literal pattern matches, but all production files are semantically auth-adjacent (login IDP filtering)
AI-Authored Likelihood: N/A (shim repo has no git history; no AI co-author markers visible in diff)

(12 additional findings below confidence threshold — includes a cross-file-impact finding at 82% that the new `getLoginPredicate()` org/BROKER_PUBLIC clause must be mirrored by the JPA `getForLogin` filtering for the invalidation skip to be sound, a correctness finding at 82% that `remove()` now issues an unconditional `getByAlias` delegate call even on cache hits, a security finding at 65% that the cache-population path captures `loaded = cache.getCurrentRevision(cacheKey)` but then passes `startupRevision` / `getCurrentCounter()` to `addRevisioned` instead, a security finding at 70% that `addIdentityProvider`/`removeIdentityProvider` invalidation lives outside this diff and is an implicit contract, and a correctness finding at 72% that `idp.getConfig().get(BROKER_PUBLIC)` is not null-guarded against `getConfig() == null`. Consider lowering `--threshold` to surface these for deeper review.)
