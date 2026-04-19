## Summary
4 files changed, 268 lines added, 3 lines deleted. 13 findings (5 critical, 6 improvements, 2 nitpicks).
Adds an Infinispan login-IDP cache with invalidation hooks and a tightened `LoginFilter` predicate; correctness and consistency gaps in the cache-hit path and the new integration test warrant changes before merge-quality confidence is high.

## Critical
:red_circle: [correctness] NullPointerException when `idp.getConfig()` returns null in `getLoginPredicate` in server-spi/src/main/java/org/keycloak/models/IdentityProviderStorageProvider.java:254 (confidence: 88)
The new clause `idp.getConfig().get(OrganizationModel.BROKER_PUBLIC)` is evaluated by every `registerIDPLoginInvalidation` call (create/update/remove). `IdentityProviderModel.getConfig()` can legitimately return `null` for freshly-built models. The outer `Objects::nonNull` guard only protects against a null `idp`, not a null config map, so a single null-config IDP aborts any mutation with an NPE.
```suggestion
.and(idp -> idp.getOrganizationId() == null
        || (idp.getConfig() != null && Boolean.parseBoolean(idp.getConfig().get(OrganizationModel.BROKER_PUBLIC))))
```

:red_circle: [correctness] Cache-hit path omits `createOrganizationAwareIdentityProviderModel`, producing inconsistent IDP models vs. cache-miss path in model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:105-115 (confidence: 85)
Both delegate-bypass paths (lines 79 and 110) wrap results via `this::createOrganizationAwareIdentityProviderModel`. The cache-hit resolution loop resolves IDs through `session.identityProviders().getById(id)` and adds the raw result to the output set. Callers therefore receive differently-shaped objects depending on cache state; the new `isEnabled()` band-aid in `OrganizationAwareIdentityProviderBean` is a direct consequence of this inconsistency. In addition, `getLoginPredicate()` inside `registerIDPLoginInvalidation` may misclassify unwrapped org-linked IDPs and skip invalidation.
```suggestion
for (String id : cached) {
    IdentityProviderModel idp = session.identityProviders().getById(id);
    if (idp == null) {
        realmCache.registerInvalidation(cacheKey);
        return idpDelegate.getForLogin(mode, organizationId).map(this::createOrganizationAwareIdentityProviderModel);
    }
    identityProviders.add(createOrganizationAwareIdentityProviderModel(idp));
}
```

:red_circle: [cross-file-impact] `getForLogin()` callers outside `OrganizationAwareIdentityProviderBean` lack the `isEnabled()` re-check in services/src/main/java/org/keycloak/organization/forms/login/freemarker/model/OrganizationAwareIdentityProviderBean.java:75-82 (confidence: 88)
The added filter is accompanied by "re-check isEnabled as idp might have been wrapped" — an explicit acknowledgement that cache-hit re-hydration via `getById()` returns models whose `isEnabled()` may diverge from the stored flag. The same guard is not applied to other callers of `session.identityProviders().getForLogin(...)` such as `IdentityProviderBean` (standard login page) or IDP authenticators. On a warm cache those callers may render or auto-select IDPs the wrapping layer treats as disabled.
```suggestion
// Preferred: push the filter inside InfinispanIdentityProviderStorageProvider.getForLogin()
// on the cache-hit path so every caller is protected transparently:
Set<IdentityProviderModel> identityProviders = new LinkedHashSet<>();
for (String id : cached) {
    IdentityProviderModel idp = createOrganizationAwareIdentityProviderModel(session.identityProviders().getById(id));
    if (idp == null) { /* fallback */ }
    if (idp.isEnabled()) identityProviders.add(idp);
}
```

:red_circle: [correctness] Stream from `HashSet` in cache-hit path yields non-deterministic IDP ordering in model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:90,105-115 (confidence: 82)
Both `cached` (`Collectors.toSet()`) and `identityProviders` (`new HashSet<>()`) have arbitrary iteration order. The JPA delegate returns a deterministic order; `OrganizationAwareIdentityProviderBean` happens to `.sorted(IDP_COMPARATOR_INSTANCE)` afterward, but any other caller iterating the stream directly will see randomized login-button ordering on warm-cache hits — a UX regression and a source of flaky tests that snapshot IDP order.
```suggestion
cached = idpDelegate.getForLogin(mode, organizationId)
        .map(IdentityProviderModel::getInternalId)
        .collect(Collectors.toCollection(LinkedHashSet::new));
// ...
List<IdentityProviderModel> identityProviders = new ArrayList<>();
for (String id : cached) { ... identityProviders.add(idp); }
return identityProviders.stream();
```

:red_circle: [testing] Cleanup hook uses literal `"alias"` instead of the loop variable, leaking all 21 created IDPs in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java:234,278 (confidence: 99)
Inside the creation loop, `getCleanup().addCleanup(testRealm().identityProviders().get("alias")::remove)` passes the literal string `"alias"`, not `"idp-alias-" + i`. The mistake repeats at line 278 for `"idp-alias-20"`. Every cleanup call is a no-op against a nonexistent alias; all 21 created IDPs persist in the realm and pollute any subsequent test in the suite.
```suggestion
final String idpAlias = "idp-alias-" + i;
// ... create idp with idpAlias ...
getCleanup().addCleanup(testRealm().identityProviders().get(idpAlias)::remove);
// And at line 278:
getCleanup().addCleanup(testRealm().identityProviders().get("idp-alias-20")::remove);
```

## Improvements
:yellow_circle: [testing] Test assumes at least one organization exists without creating one in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java:237 (confidence: 95)
`testRealm().organizations().getAll().get(0).getId()` throws `IndexOutOfBoundsException` when the realm has no organization — which is the case when running this test in isolation. Make the test self-contained by creating an organization in setup and cleaning it up after.
```suggestion
OrganizationRepresentation orgRep = new OrganizationRepresentation();
orgRep.setName("test-org-for-login-cache");
orgRep.addDomain(new OrganizationDomainRepresentation("login-cache.org"));
try (Response r = testRealm().organizations().create(orgRep)) { assertEquals(201, r.getStatus()); }
String orgaId = testRealm().organizations().getAll().stream()
        .filter(o -> "test-org-for-login-cache".equals(o.getName()))
        .findFirst().orElseThrow().getId();
getCleanup().addCleanup(() -> testRealm().organizations().get(orgaId).delete());
```

:yellow_circle: [testing] "No invalidation" assertions only check cache presence, not cache content in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java:283-317 (confidence: 88)
Sections 1 and 2 assert `assertNotNull(identityProviderListQuery)` but never check the cached IDP set size. A defective implementation that cleared the inner set while keeping the outer object would pass. Add `assertEquals(expected, query.getIDPs(key).size())` after each `assertNotNull` in the "no invalidation" blocks.
```suggestion
assertNotNull(realmOnlyQuery); assertEquals(5, realmOnlyQuery.getIDPs("").size());
assertNotNull(orgOnlyQuery);   assertEquals(5, orgOnlyQuery.getIDPs(orgaId).size());
assertNotNull(allQuery);       assertEquals(10, allQuery.getIDPs(orgaId).size());
```

:yellow_circle: [correctness] Login cache not invalidated when `storedIdp` is null in `remove()` in model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:52-67 (confidence: 82)
If `idpDelegate.getByAlias(alias)` returns null (double-remove, concurrent delete), `registerIDPLoginInvalidation(null)` short-circuits via `Objects::nonNull` and registers no invalidation. The coarse-grained, long-lived login cache can then retain a stale internal ID until the next unrelated event triggers a refresh. Consider invalidating all FetchMode keys unconditionally on `remove()` since the cost is trivial and the stale window is long.
```suggestion
if (storedIdp == null) {
    for (FetchMode mode : FetchMode.values()) realmCache.registerInvalidation(cacheKeyForLogin(getRealm(), mode));
} else {
    registerIDPLoginInvalidation(storedIdp);
}
```

:yellow_circle: [testing] Stale-reference fallback (`idp == null`) in `getForLogin` is untested in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java:365+ (confidence: 85)
The production code has an explicit recovery path for cached IDs that no longer resolve (`session.identityProviders().getById(id) == null` → invalidate, re-query delegate). This is the most likely real-world failure mode (concurrent deletion) and is not exercised. Add a case that populates the cache, forces the underlying IDP to disappear, and asserts the fallback path is taken.

:yellow_circle: [cross-file-impact] Concurrent `getForLogin()` with different `organizationId` for the same `FetchMode` thrashes a shared cache key in model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:93-101 (confidence: 75)
The cache key is `realm + FetchMode` only; `organizationId` is stored as a sub-key inside `IdentityProviderListQuery`. When a second `organizationId` is not yet cached, the code `invalidateObject(cacheKey)` and reinserts a merged query at `getCurrentCounter()`. Under concurrent load each new org search repeatedly wipes and rebuilds the entry, defeating the performance goal. A composite key `(realm, FetchMode, orgId)` removes the need for the invalidate-then-merge pattern.

:yellow_circle: [consistency] `cacheKeyForLogin` naming and composition diverge from sibling cache-key methods in model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:32-34 (confidence: 82)
Existing helpers use `cacheKey{Entity}` naming (`cacheKeyIdpAlias`, `cacheKeyOrgId`, `cacheKeyIdpMapperAliasName`) and the body layout `realmId + "." + identifier + SUFFIX`. The new method uses a preposition-based name and reversed layout (`realmId + SUFFIX + "." + identifier`). Rename and reorder for parity.
```suggestion
public static String cacheKeyIdpLogin(RealmModel realm, FetchMode fetchMode) {
    return realm.getId() + "." + fetchMode + IDP_LOGIN_SUFFIX;
}
```

## Nitpicks
:white_circle: [testing] Magic number assertions (5, 5, 10, 6, 11) are undocumented in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/organization/cache/OrganizationCacheTest.java:257-267 (confidence: 82)
The derived counts (half enabled, half org-linked with BROKER_PUBLIC, plus the idp-alias-20 transition) are scattered across lambdas with no constants or comments. Any change to the loop bounds silently breaks assertions with no indication of what invariant is violated. Extract named constants (`REALM_ONLY_ENABLED`, `ORG_ONLY_ENABLED`, `ALL_ENABLED`) derived from the setup.

:white_circle: [consistency] Invalidation-method naming inserts "Login" mid-identifier in model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/idp/InfinispanIdentityProviderStorageProvider.java:125,146 (confidence: 75)
`registerIDPLoginInvalidation` / `registerIDPLoginInvalidationOnUpdate` deviate from the sibling pattern `registerIDP{Entity}Invalidation` (`registerIDPInvalidation`, `registerIDPMapperInvalidation`). `registerLoginInvalidation` or `registerIDPInvalidationForLogin` better matches the convention.

## Risk Metadata
Risk Score: 39/100 (MEDIUM) | Blast Radius: SPI-level change to `IdentityProviderStorageProvider`; core Infinispan cache layer; ~271 LOC | Sensitive Paths: none matched by glob, but touches IDP login brokering (security-adjacent)
AI-Authored Likelihood: LOW
