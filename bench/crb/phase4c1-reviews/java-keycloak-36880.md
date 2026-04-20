## Summary
10 files changed, 866 lines added, 138 lines deleted. 5 findings (2 critical, 3 improvements).
Introduces a V2 fine-grained-authorization evaluator for the Clients resource type; core concerns are inconsistent delegation to legacy V1 scope fallbacks and a feature-gate that may silently disable permission cleanup in V2-only deployments.

## Critical
:red_circle: [correctness] Inconsistent super-delegation across V2 overrides drops legacy V1 scope checks in `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java`:61 (confidence: 88)
`canManage()` and `canView()` call `super.canManage()` / `canViewClientDefault()` to preserve the V1 role/scope fallback, but `canConfigure(ClientModel)`, `canMapRoles(ClientModel)`, `canMapCompositeRoles(ClientModel)`, and `canMapClientScopeRoles(ClientModel)` do not invoke `super`. The Javadoc on `ClientPermissionEvaluator` states each of these methods should return `true` when the caller has the legacy `ClientPermissionManagement#*_SCOPE` permission "or" the new V2 `AdminPermissionsSchema#*` permission. The V2 overrides replace the parent entirely, so any caller that today is authorized through the V1 resource-level `MAP_ROLES_SCOPE` / `CONFIGURE_SCOPE` policies will be denied under V2 even though the documented contract still promises acceptance. Either the override should OR-in `super.canMapRoles(client)` / `super.canConfigure(client)` / etc., or the interface Javadoc should be updated to explicitly say V2 no longer honours the legacy per-client scope policies.
```suggestion
@Override
public boolean canConfigure(ClientModel client) {
    if (canManage(client)) return true;
    if (super.canConfigure(client)) return true;
    return hasPermission(client, AdminPermissionsSchema.CONFIGURE);
}

@Override
public boolean canMapRoles(ClientModel client) {
    return super.canMapRoles(client) || hasPermission(client, AdminPermissionsSchema.MAP_ROLES);
}

@Override
public boolean canMapCompositeRoles(ClientModel client) {
    return super.canMapCompositeRoles(client) || hasPermission(client, AdminPermissionsSchema.MAP_ROLES_COMPOSITE);
}

@Override
public boolean canMapClientScopeRoles(ClientModel client) {
    return super.canMapClientScopeRoles(client) || hasPermission(client, AdminPermissionsSchema.MAP_ROLES_CLIENT_SCOPE);
}
```

:red_circle: [security] Feature-gate on the removal listener may orphan permissions in V2-only deployments in `services/src/main/java/org/keycloak/services/resources/admin/permissions/AdminPermissions.java`:76 (confidence: 86)
The role/client/group-removed handlers, which previously always called `setPermissionsEnabled(..., false)` to clean up authorization resources, are now wrapped in `Profile.isFeatureEnabled(Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ)`. The V2 code paths introduced in this PR (`MgmtPermissionsV2`, `ClientPermissionsV2`) are selected by the V2 feature flag (`ADMIN_FINE_GRAINED_AUTHZ_V2`), which can be enabled independently of V1. If a realm runs with V2 enabled and V1 disabled, deleting a role/client/group will leave behind V1 permission resources and policies with no cleanup path, and stale V2 resources never get pruned either. Either widen the predicate to also include the V2 feature, or lift the gate and let the underlying `management(...).<type>().setPermissionsEnabled(...)` call be a no-op when neither feature is enabled (which was the pre-PR behaviour).
```suggestion
if (Profile.isFeatureEnabled(Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ)
        || Profile.isFeatureEnabled(Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ_V2)) {
    // existing event-dispatch body
}
```

## Improvements
:yellow_circle: [consistency] `void require*()` Javadocs say "Returns true" but the methods are void and throw in `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionEvaluator.java`:86 (confidence: 96)
Several of the newly added Javadoc blocks on `void` `require*()` methods describe them as returning `true` when the corresponding `can*()` predicate returns true — e.g. `requireView()` ("Returns `true` if `canView()` returns `true`."), `requireViewClientScopes()`, and the `requireList()` block. These methods are declared `void` and, per the surrounding convention, throw `ForbiddenException` when the predicate is false. The Javadoc should state the throws contract so generated docs and IDE tooltips don't mislead callers into expecting a boolean result.
```suggestion
/**
 * Throws ForbiddenException if {@link #canView()} returns {@code false}.
 */
void requireView();

/**
 * Throws ForbiddenException if {@link #canViewClientScopes()} returns {@code false}.
 */
void requireViewClientScopes();
```

:yellow_circle: [correctness] `hasPermission(String)` silently returns false when the all-clients resource is not yet materialised in `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java`:233 (confidence: 84)
The per-client variant `hasPermission(ClientModel, String)` falls back to `AdminPermissionsSchema.SCHEMA.getResourceTypeResource(session, server, CLIENTS_RESOURCE_TYPE)` when no client-specific resource exists, so an administrator who created an "all clients" permission before any per-client resource existed is still evaluated correctly. The no-arg `hasPermission(String)` variant (used by `canManage()`, `canView()`, `canManageClientScopes()`, `canView(ClientScopeModel)`) only does `resourceStore.findByName(server, CLIENTS_RESOURCE_TYPE, server.getId())` and returns `false` if null — it never asks the schema to create / resolve the type resource. In a realm where permissions exist but the type-resource row has not yet been persisted (e.g. immediately after feature enablement, or after a reset), this will under-authorise every caller that only has an all-clients policy. Mirror the fallback used in the per-client overload.
```suggestion
private boolean hasPermission(String scope) {
    if (!root.isAdminSameRealm()) return false;
    ResourceServer server = root.realmResourceServer();
    if (server == null) return false;

    Resource resource = resourceStore.findByName(server, AdminPermissionsSchema.CLIENTS_RESOURCE_TYPE, server.getId());
    if (resource == null) {
        resource = AdminPermissionsSchema.SCHEMA.getResourceTypeResource(session, server, AdminPermissionsSchema.CLIENTS_RESOURCE_TYPE);
        if (resource == null || authz.getStoreFactory().getPolicyStore().findByResource(server, resource).isEmpty()) {
            return false;
        }
    }
    // remaining evaluation unchanged
}
```

:yellow_circle: [testing] `onAfter()` cleanup can NPE when no permissions were created in `tests/base/src/test/java/org/keycloak/tests/admin/authz/fgap/PermissionClientTest.java`:69 (confidence: 87)
The new `@AfterEach` calls `permissions.findAll(null, null, null, -1, -1).forEach(...)`. Elsewhere in this PR (`UserResourceTypeEvaluationTest.testDelete`) `findAll` is asserted to return `null` after all permissions are deleted (`assertThat(existing, nullValue())`), so the same `findAll` contract can return `null` here. If a test that doesn't reach any `createPermission` call fails early — which is common during development of the new negative-path tests like `testManageOnlyOneClient` — `onAfter()` will NPE and mask the real failure. Guard with a null/empty check, or collect into a list.
```suggestion
@AfterEach
public void onAfter() {
    ScopePermissionsResource permissions = getScopePermissionsResource(client);
    List<ScopePermissionRepresentation> all = permissions.findAll(null, null, null, -1, -1);
    if (all == null) return;
    all.forEach(p -> permissions.findById(p.getId()).remove());
}
```

## Risk Metadata
Risk Score: 82/100 (HIGH) | Blast Radius: core admin-authz path, 10 files, touches `server-spi-private` + `services` + integration tests, effective for every admin-console caller in realms with fine-grained-authz-v2 enabled | Sensitive Paths: `services/.../permissions/*`, `server-spi-private/.../authorization/AdminPermissionsSchema.java`
AI-Authored Likelihood: LOW
