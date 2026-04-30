## Summary
10 files changed, 879 lines added, 138 lines deleted. 7 findings (5 critical, 2 improvements, 0 nitpicks).
ClientPermissionsV2 introduces several `UnsupportedOperationException` overrides that crash existing admin REST endpoints and the entity-removal event listener under V2; the type-level fallback in `hasPermission(client, scope)` and the feature-flag-gated cleanup listener also need attention.

## Critical

:red_circle: [correctness] `hasPermission(client, scope)` evaluates type-level resource with side-effecting `getOrCreate`, leaking all type scopes into evaluation in services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:493 (confidence: 92)
When `findByName(server, client.getId(), server.getId())` returns null (no per-client resource exists), the code falls back to `AdminPermissionsSchema.SCHEMA.getResourceTypeResource(...)` — a `getOrCreate` call that mutates the authorization store as a side effect of a read-only permission check. It then constructs `new ResourcePermission(resource, resource.getScopes(), server)` with the type-level resource and ALL declared scopes (CONFIGURE, MANAGE, MAP_ROLES, MAP_ROLES_CLIENT_SCOPE, MAP_ROLES_COMPOSITE, VIEW), not just the requested `scope`. The current scope-matching loop happens to filter correctly for simple cases, but the over-broad `ResourcePermission` is fragile: any future evaluation layer (deny policies, post-filters, policy combiner changes) operating on the constructed permission will see all type scopes as in-scope and may produce false positives. The `getOrCreate` side effect can also produce spurious resource rows during ordinary access checks.
```suggestion
private boolean hasPermission(ClientModel client, String scope) {
    if (!root.isAdminSameRealm()) return false;
    ResourceServer server = root.realmResourceServer();
    if (server == null) return false;

    Resource resource = resourceStore.findByName(server, client.getId(), server.getId());
    if (resource == null) {
        // Read-only lookup of the type-level "all clients" resource (no getOrCreate side effect)
        resource = resourceStore.findByName(server, AdminPermissionsSchema.CLIENTS_RESOURCE_TYPE, server.getId());
        if (resource == null) return false;
        if (authz.getStoreFactory().getPolicyStore().findByResource(server, resource).isEmpty()) {
            return false;
        }
    }

    Scope requestedScope = resource.getScopes().stream()
            .filter(s -> scope.equals(s.getName()))
            .findFirst().orElse(null);
    if (requestedScope == null) return false;

    Collection<Permission> permissions = root.evaluatePermission(
            new ResourcePermission(resource, List.of(requestedScope), server), server);
    for (Permission permission : permissions) {
        if (permission.getScopes().contains(scope)) return true;
    }
    return false;
}
```

:red_circle: [correctness] `ClientRemovedEvent` listener calls `setPermissionsEnabled` on V2 instance, throws `UnsupportedOperationException` on every client deletion in services/src/main/java/org/keycloak/services/resources/admin/permissions/AdminPermissions.java:92 (confidence: 92)
The updated listener body fires when `Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ` is enabled. Under that flag, `management(...)` returns `MgmtPermissionsV2`, and its `clients()` returns `ClientPermissionsV2`. `ClientPermissionsV2.setPermissionsEnabled(ClientModel, boolean)` is overridden to `throw new UnsupportedOperationException("Not supported in V2")`. The listener handler `else if (event instanceof ClientModel.ClientRemovedEvent) { ... management(...).clients().setPermissionsEnabled(cast.getClient(), false); }` therefore throws on every client deletion under V2 — corrupting the event-dispatch transaction or aborting downstream listeners depending on how the event manager handles the exception. The same crash applies when the V2 path is reached for `RoleRemovedEvent` (calls `roles().setPermissionsEnabled(role, false)`) and `GroupRemovedEvent`, if those V2 evaluators follow the same pattern.
```suggestion
@Override
public void setPermissionsEnabled(ClientModel client, boolean enable) {
    if (enable) {
        // V2 manages access via the authorization schema; nothing to enable per-client.
        return;
    }
    // Cleanup on removal: delete the per-client resource (if any) so policies referencing it can be GC'd.
    ResourceServer server = root.realmResourceServer();
    if (server == null) return;
    Resource resource = resourceStore.findByName(server, client.getId(), server.getId());
    if (resource != null) {
        authz.getStoreFactory().getResourceStore().delete(realm, resource.getId());
    }
}
```

:red_circle: [cross-file-impact] Admin client REST endpoints (`getPermissions`, `managePermission`, `viewPermission`, `configurePermission`) crash with 500 under V2 in services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:459 (confidence: 88)
`ClientResource` (the JAX-RS handler for `/admin/realms/{realm}/clients/{id}`) reads `auth.clients().getPermissions(client)` to build the response body and calls `managePermission(client)`, `viewPermission(client)`, `configurePermission(client)` to set up per-client policies. All four are overridden in `ClientPermissionsV2` to `throw new UnsupportedOperationException("Not supported in V2")`. Under the V2 feature flag, every `GET` on a client and every legacy permission-management call surfaces a 500 Internal Server Error to the admin console rather than a graceful empty/unsupported response — breaking client administration entirely on V2 deployments.
```suggestion
@Override
public Map<String, String> getPermissions(ClientModel client) {
    // V2 does not expose legacy per-client permission policy IDs; return an empty map
    // so the admin REST representation still serializes cleanly.
    return Collections.emptyMap();
}

@Override
public Policy managePermission(ClientModel client) { return null; }
@Override
public Policy viewPermission(ClientModel client) { return null; }
@Override
public Policy configurePermission(ClientModel client) { return null; }
```

:red_circle: [cross-file-impact] Token-exchange permission flow (`canExchangeTo`, `exchangeToPermission`) throws under V2 instead of failing closed in services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:434 (confidence: 87)
`canExchangeTo(authorizedClient, to, token)` and `exchangeToPermission(client)` both `throw new UnsupportedOperationException("Not supported in V2")`. Token-exchange is a security-critical flow; the safe behaviour when a permission evaluator does not support it is to deny (`return false`) and let the REST/management endpoint return a clean 4xx, not to throw a `RuntimeException` that produces a 500, leaks implementation details into logs, and could be caught at an outer layer with fail-open semantics depending on caller. Either implement the V2 semantics or fail closed deliberately.
```suggestion
@Override
public boolean canExchangeTo(ClientModel authorizedClient, ClientModel to, AccessToken token) {
    // Token exchange is not evaluated by V2; deny by default.
    logger.debug("canExchangeTo invoked under V2 — denying (token-exchange policies must be migrated to V2)");
    return false;
}

@Override
public Policy exchangeToPermission(ClientModel client) {
    // V2 does not expose legacy token-exchange policy objects.
    return null;
}
```

:red_circle: [cross-file-impact] Per-client `isPermissionsEnabled` toggle endpoint crashes under V2 in services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:474 (confidence: 85)
`ClientResource` exposes a REST endpoint that reads `auth.clients().isPermissionsEnabled(client)` (and corresponding `setPermissionsEnabled` for `PUT`). Both throw `UnsupportedOperationException` in V2. The admin console queries this endpoint when rendering the per-client "Permissions" tab, so opening that tab in a V2 realm produces a 500. V2 conceptually has fine-grained authz always-on per the schema, so the read should return `true` and the setter should be a no-op (or return a structured 4xx) rather than throw.
```suggestion
@Override
public boolean isPermissionsEnabled(ClientModel client) {
    // In V2, fine-grained authorization is always-on via the schema — no per-client toggle.
    return true;
}
```

## Improvements

:yellow_circle: [correctness] `getClientsWithPermission` returns client UUIDs (resource names) — verify caller filter expects UUID, not `clientId` in services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:411 (confidence: 88)
The method iterates `findByType(server, CLIENTS_RESOURCE_TYPE, ...)` and adds `resource.getName()` to the returned set. Per `AdminPermissionsSchema.resolveClient()` and `getOrCreateResource()`, per-client resource names are stored as `client.getId()` (internal UUID), not `client.getClientId()` (human-readable). The `UserPermissions` analog returns usernames (and the user resource's name IS the username), so the pattern doesn't transfer cleanly. If any caller of `getClientsWithPermission` filters a `ClientModel` stream by comparing against `client.getClientId()`, the returned UUID set will not match and the filter silently denies access to all clients. Verify the caller contract before relying on this return value; if callers expect `clientId`, resolve UUIDs back through `session.clients().getClientById(realm, ...)` before adding to the set.
```suggestion
resourceStore.findByType(server, AdminPermissionsSchema.CLIENTS_RESOURCE_TYPE, resource -> {
    if (hasGrantedPermission(resource, scope)) {
        // resource.getName() is the client UUID — return it as-is so callers can resolve via getClientById.
        granted.add(resource.getName());
    }
});
// Alternatively, if callers compare against ClientModel.getClientId():
// ClientModel c = session.clients().getClientById(root.getRealm(), resource.getName());
// if (c != null) granted.add(c.getClientId());
```

:yellow_circle: [correctness] V1 fine-grained-authz cleanup listener now skipped when `ADMIN_FINE_GRAINED_AUTHZ` (V2) feature is disabled in services/src/main/java/org/keycloak/services/resources/admin/permissions/AdminPermissions.java:76 (confidence: 85)
Before this PR the `RoleRemovedEvent`, `ClientRemovedEvent`, and `GroupRemovedEvent` cleanup ran unconditionally inside `registerListener`. After this PR the entire body is wrapped in `if (Profile.isFeatureEnabled(Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ))`. If `ADMIN_FINE_GRAINED_AUTHZ` is the V2 feature flag (separate from the V1 `admin_fine_grained_authz` flag), then any deployment running V1 fine-grained authz will stop cleaning up authorization resources/policies on entity deletion — leaking orphaned policy rows that may match later or cause evaluation noise. Even if both V1 and V2 share the same flag constant, toggling the feature off temporarily and then deleting a client/role/group leaves the original V1 permission entries un-GC'd. Confirm which feature flag this constant maps to and either gate per V1 / V2 explicitly, or run cleanup unconditionally with the V2-aware logic factored out.
```suggestion
public void onEvent(ProviderEvent event) {
    boolean v2 = Profile.isFeatureEnabled(Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ);
    boolean v1 = Profile.isFeatureEnabled(Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ_V1);
    if (!v1 && !v2) return;
    // ... existing handlers, with V2-specific paths guarded by `if (v2)` where they would otherwise
    // hit ClientPermissionsV2 methods that throw UnsupportedOperationException ...
}
```

## Risk Metadata
Risk Score: 70/100 (HIGH) | Blast Radius: admin permission evaluation path; affects every admin REST request to `/admin/realms/{realm}/clients/*` under V2 plus the entity-removal event listener | Sensitive Paths: services/.../permissions/, server-spi-private/.../authorization/
AI-Authored Likelihood: LOW

(8 additional findings below confidence threshold)
