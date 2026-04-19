# PR Review — keycloak/keycloak#36880

**Title:** Add Client resource type and its scopes to authorization schema and evaluation implementation for ClientsPermissionsV2
**Base:** `main` ← **Head:** `35564-client-authz-schema`
**Closes:** #35564

## Summary

10 files changed, 866 lines added, 138 lines deleted. 9 findings (2 critical, 4 improvements, 3 nitpicks).

Implements V2 Fine-Grained Admin Permissions (FGAP) for the `Clients` resource type — introduces a new `ClientPermissionsV2` evaluator, registers `Clients` in the `AdminPermissionsSchema` with scopes (`configure`, `manage`, `map-roles`, `map-roles-client-scope`, `map-roles-composite`, `view`), and adds extensive integration tests. The core authorization-check logic touches security-sensitive evaluation paths; several methods are deliberately disabled via `UnsupportedOperationException`, and the event-listener cleanup path is now feature-flag–gated — both behaviors warrant attention.

## Critical

:red_circle: [security] `UnsupportedOperationException` on V2 silently disables token-exchange and permission CRUD paths in `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:103-147` (confidence: 85)

`ClientPermissionsV2` overrides a large number of methods inherited from `ClientPermissions` — `canExchangeTo`, `exchangeToPermission`, `mapRolesPermission`, `mapRolesClientScopePermission`, `mapRolesCompositePermission`, `managePermission`, `configurePermission`, `viewPermission`, `isPermissionsEnabled`, `setPermissionsEnabled`, `resource`, `getPermissions` — and throws `UnsupportedOperationException("Not supported in V2")` from every one of them. Any caller that still reaches these code paths when V2 is active (for example token-exchange flows via `AdminPermissionEvaluator`/`AdminPermissions`, or the admin-console's per-client permission management UI that probably calls `isPermissionsEnabled` / `getPermissions` / `setPermissionsEnabled`) will see a 500 Internal Server Error instead of a clean `ForbiddenException` or a documented "V2 does not support X" response. This is both a correctness issue (feature-regression when ADMIN_FINE_GRAINED_AUTHZ_V2 is on) and a security-surface issue (500s with stack traces on security-relevant endpoints).

Recommend: (a) audit every call site for the listed methods and either route them through a V1/V2 switch or surface a proper `ForbiddenException` / 404, (b) if the intent is explicitly "token exchange is not supported in V2 yet," return `false` from `canExchangeTo` and document it rather than throwing, and (c) add an integration test that asserts V2 handles these admin-console endpoints gracefully.

```suggestion
    @Override
    public boolean canExchangeTo(ClientModel authorizedClient, ClientModel to, AccessToken token) {
        // Token exchange permissions are not modeled in V2 FGAP (see #35564);
        // treat as denied rather than throwing so callers get a clean ForbiddenException.
        return false;
    }
```

:red_circle: [correctness] `hasPermission(String scope)` uses the resource-type literal as a resource *name* lookup in `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:239-260` (confidence: 85)

The "all clients" overload calls
`resourceStore.findByName(server, AdminPermissionsSchema.CLIENTS_RESOURCE_TYPE, server.getId())` — i.e. it looks up a resource whose *name* is the literal string `"Clients"`. The per-client overload above (line 207) resolves the same concept via `AdminPermissionsSchema.SCHEMA.getResourceTypeResource(session, server, CLIENTS_RESOURCE_TYPE)`, and `UserPermissionsV2` (the pattern this is modeled after) uses the schema's `getResourceTypeResource` helper. If the schema resource is ever auto-provisioned under a different name (or if the name is changed in the schema), this branch will silently return `false` and deny all type-level permissions — i.e. legitimate `MANAGE`/`VIEW` grants on *all* clients stop working. Even if the names currently coincide, coupling authorization to a magic-string name lookup instead of the schema helper is fragile.

```suggestion
    private boolean hasPermission(String scope) {
        if (!root.isAdminSameRealm()) {
            return false;
        }
        ResourceServer server = root.realmResourceServer();
        if (server == null) return false;

        Resource resource = AdminPermissionsSchema.SCHEMA.getResourceTypeResource(
                session, server, AdminPermissionsSchema.CLIENTS_RESOURCE_TYPE);
        if (resource == null) return false;

        Collection<Permission> permissions = root.evaluatePermission(
                new ResourcePermission(resource, resource.getScopes(), server), server);
        for (Permission permission : permissions) {
            if (permission.getScopes().contains(scope)) {
                return true;
            }
        }
        return false;
    }
```

## Improvements

:yellow_circle: [correctness] Feature-gated cleanup may leak permission records across feature toggles in `services/src/main/java/org/keycloak/services/resources/admin/permissions/AdminPermissions.java:76-97` (confidence: 70)

The entire body of `onEvent` is now wrapped in `if (Profile.isFeatureEnabled(Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ))`. If an operator temporarily disables the feature, role/client/group removals that happen while it is disabled will *not* call `setPermissionsEnabled(..., false)`, leaving stale permission records. When the feature is re-enabled those stale records are still present. Previously the cleanup ran unconditionally and would no-op if no permissions existed. Consider either keeping the cleanup unconditional (cheap if no records exist) or documenting the toggle semantics explicitly.

```suggestion
                // The cleanup is safe to run even when FGAP is disabled — it no-ops if no
                // permissions exist — and protects against stale records if the feature
                // is toggled off and on again.
                if (event instanceof RoleContainerModel.RoleRemovedEvent) {
                    ...
```

:yellow_circle: [correctness] `AdminPermissionsSchema.SCHEMA.getResourceTypeResource(...)` return value not null-checked in `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:217-223` (confidence: 75)

In the per-client `hasPermission(ClientModel, String)` fallback, if the per-client resource is not found, the code fetches the type-level resource and then calls `authz.getStoreFactory().getPolicyStore().findByResource(server, resource)`. If `getResourceTypeResource` returns `null` (e.g. in a realm where the schema resource hasn't been materialized yet), this NPEs instead of returning `false`. Defensive null-check keeps the evaluator robust across realm lifecycle edges.

```suggestion
        Resource resource = resourceStore.findByName(server, client.getId(), server.getId());
        if (resource == null) {
            resource = AdminPermissionsSchema.SCHEMA.getResourceTypeResource(
                    session, server, AdminPermissionsSchema.CLIENTS_RESOURCE_TYPE);
            if (resource == null) {
                return false;
            }
            if (authz.getStoreFactory().getPolicyStore().findByResource(server, resource).isEmpty()) {
                return false;
            }
        }
```

:yellow_circle: [consistency] `canView(ClientScopeModel)` diverges from the contract documented on the interface in `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:86-90` (confidence: 70)

The Javadoc added in `ClientPermissionEvaluator` says `canView(ClientScopeModel)` should return `true` "if the caller has at least one of the `VIEW_CLIENTS` or `MANAGE_CLIENTS` roles [… or V2] `VIEW` or `MANAGE`." The V2 implementation uses `hasPermission(VIEW) || hasPermission(MANAGE)` which looks OK — but `canView(ClientModel)` above it uses `canView() || canConfigure(client) || hasPermission(client, VIEW)`, a richer relationship. The two are inconsistent in how they honor `CONFIGURE`/client-object permissions. Confirm this matches the intended V1 semantics; if so, add a one-line comment explaining the asymmetry (client-scope permissions are realm-wide, per-client permissions aren't).

:yellow_circle: [correctness] Unused-but-allocated `EvaluationContext` helper signals incomplete refactor in `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:262-271` (confidence: 65)

`private EvaluationContext getEvaluationContext(ClientModel authorizedClient, AccessToken token)` is defined but never called anywhere in the class. If it's intended for `canExchangeTo` once that lands, leave a `// TODO(#35564 phase N)` reference; otherwise delete it. Dead helpers in security-sensitive code create confusion about whether a code path exists.

```suggestion
    // (remove the method entirely, or add a TODO tying it to the follow-up issue)
```

## Nitpicks

:white_circle: [consistency] Unused imports and fields in `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:17-40` (confidence: 80)

- `import org.keycloak.authorization.model.Scope;` — not referenced.
- `import static ...AdminPermissionManagement.TOKEN_EXCHANGE;` — not referenced.
- `private static final Logger logger = Logger.getLogger(ClientPermissionsV2.class);` — never used; either add debug logging on the V2 paths (helpful when diagnosing denies) or remove.

:white_circle: [consistency] `Arrays.asList(scope)` for a single-element list in `services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:230, 253` (confidence: 70)

Use `List.of(scope)` or drop the list entirely and inline `scope.equals(s)` — it's a hot code path invoked on every admin request touching a client.

```suggestion
        for (Permission permission : permissions) {
            if (permission.getScopes().contains(scope)) {
                return true;
            }
        }
```

:white_circle: [consistency] Trailing whitespace and missing spaces after commas in `server-spi-private/src/main/java/org/keycloak/authorization/AdminPermissionsSchema.java:53` and several test files (confidence: 90)

- `AdminPermissionsSchema.java` line 53: empty line with trailing spaces introduced.
- Multiple test files contain `createUserPolicy(realm, client,"Only My Admin User Policy", ...)` with no space after the comma. Not a correctness issue but the repo checkstyle typically rejects these.

## Risk Metadata

Risk Score: 72/100 (HIGH) | Blast Radius: authz evaluator on admin APIs — every admin request touching a client passes through this code; sensitive paths: `services/.../permissions/**`, `server-spi-private/.../authorization/**` | Sensitive Paths: 2/10 files (both in permissions/authz)
AI-Authored Likelihood: LOW (style, copyright year, and test structure are consistent with the surrounding Keycloak codebase)

---

**Recommendation:** `request-changes` — two critical items (`UnsupportedOperationException` surface area and the type-resource-by-name lookup) should be addressed before merge; the remaining items are nice-to-haves.

_Note: the PR has already been approved by contributor `@pedroigor` on 2025-02-11; this is an independent second-opinion review and does not reflect the maintainer decision._
