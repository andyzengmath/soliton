## Summary
10 files changed, 866 lines added, 138 lines deleted. 7 findings (0 critical, 5 improvements, 2 nitpicks).
Adds a `Clients` resource type and `ClientPermissionsV2` evaluator for the V2 admin permissions schema, with test refactoring and new integration tests; core logic is additive and well covered, but the new evaluator has dead code, duplicated scope-matching logic, and a feature-flag placement worth revisiting.

## Improvements

:yellow_circle: [consistency] Unused `Logger` field in `ClientPermissionsV2` in services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:52 (confidence: 95)
`private static final Logger logger = Logger.getLogger(ClientPermissionsV2.class);` is declared but never referenced anywhere in the new class. Drop it (and the `org.jboss.logging.Logger` import) or add actual logging at debug points in `hasPermission(...)` where permission resolution is non-trivial (e.g. the all-clients fallback path) — silent evaluation in authorization code makes production triage harder.
```suggestion
// remove the unused field and import; or, if keeping, add a debug log at the all-clients fallback:
// logger.debugf("No client-specific resource for %s; falling back to all-clients permission", client.getId());
```

:yellow_circle: [correctness] Dead private method `getEvaluationContext` in services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:230 (confidence: 90)
The `getEvaluationContext(ClientModel, AccessToken)` helper is private and has no callers inside `ClientPermissionsV2` — `canExchangeTo` throws `UnsupportedOperationException("Not supported in V2")` and nothing else uses it. Either remove it, or wire it into the V2 exchange path when that is implemented. Leaving it in risks a future caller assuming it is the canonical evaluation context for V2 when it was intended only for the (disabled) token-exchange case.
```suggestion
// Delete the `getEvaluationContext` method until V2 actually needs it;
// commit note should reference the follow-up issue for V2 token exchange.
```

:yellow_circle: [consistency] Duplicated permission-evaluation loop across the two `hasPermission` overloads in services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:186 (confidence: 85)
`hasPermission(ClientModel, String)` and `hasPermission(String)` both end with the same `evaluatePermission` → iterate `permissions` → iterate `permission.getScopes()` → compare against an `Arrays.asList(scope)` — only the resource lookup differs. Extract a private `evaluateResource(Resource, String)` helper that both overloads call. This also makes the single-scope comparison easier to simplify (see next finding) in one place.
```suggestion
private boolean evaluateResource(Resource resource, String scope) {
    ResourceServer server = root.realmResourceServer();
    Collection<Permission> permissions = root.evaluatePermission(new ResourcePermission(resource, resource.getScopes(), server), server);
    for (Permission p : permissions) {
        if (p.getScopes().contains(scope)) return true;
    }
    return false;
}
```

:yellow_circle: [correctness] Client-specific vs. all-clients fallback in `hasPermission(ClientModel, String)` evaluates the all-clients resource without context of the concrete client in services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:186 (confidence: 70)
When no per-client `Resource` exists, the method falls back to the all-clients resource and builds `new ResourcePermission(resource, resource.getScopes(), server)` with that resource. Any policy attached to the all-clients resource (including negative/deny policies that were authored with a different client in mind) is then evaluated without the concrete `client` being visible to the `EvaluationContext`. Confirm against `UserPermissionsV2` that this matches the established V2 pattern and that the test case in `PermissionClientTest#testManageOnlyOneClient` (permission for `myclient.getId()` only) exercises the branch where the target client has no per-resource entry *and* another client has an all-clients deny — today the tests cover positive cases well but I don't see a negative-policy-on-all-clients test for clients.
```suggestion
// Add a regression test analogous to UserResourceTypeEvaluationTest#testImpersonatePermission
// (negative permission that denies a specific client while an all-clients allow is active)
// to pin the fallback semantics.
```

:yellow_circle: [correctness] Feature-flag guard wraps listener **body** rather than listener **registration** in services/src/main/java/org/keycloak/services/resources/admin/permissions/AdminPermissions.java:74 (confidence: 75)
The entire `onEvent` body is now wrapped in `if (Profile.isFeatureEnabled(Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ)) { ... }`. Every role/client/group removal event now pays a profile lookup even when FGAP is permanently disabled. If the flag cannot change at runtime for a given server boot (the common case), guard registration in `registerListener` instead so the listener is never added. If it *can* change, leave as-is but document why. Either way, a one-line comment capturing the intent would prevent a future "optimisation" breaking this.
```suggestion
public static void registerListener(ProviderEventManager manager) {
    if (!Profile.isFeatureEnabled(Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ)) return;
    manager.register(new ProviderEventListener() { /* existing body, unwrapped */ });
}
```

## Nitpicks

:yellow_circle: [consistency] `Arrays.asList(scope)` used for single-element `contains` check in services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:200 (confidence: 95)
`List<String> expectedScopes = Arrays.asList(scope);` then `expectedScopes.contains(s)` — allocates a single-element list per call just to do a string compare. Replace with `scope.equals(s)` (or lean on `permission.getScopes().contains(scope)` directly). Low impact, but this path runs on every permission check.
```suggestion
if (permission.getScopes().contains(scope)) return true;
```

:yellow_circle: [consistency] Stray trailing whitespace / formatting noise in server-spi-private/src/main/java/org/keycloak/authorization/AdminPermissionsSchema.java:52 (confidence: 90)
The diff converts the blank line after `public class AdminPermissionsSchema extends AuthorizationSchema {` from `\n` to `    \n` (indented empty line) with no semantic change. Strip it before merging to keep the diff minimal and to avoid tripping whitespace-strict reviewers / `git blame` churn.
```suggestion
public class AdminPermissionsSchema extends AuthorizationSchema {

    public static final String USERS_RESOURCE_TYPE = "Users";
```

## Risk Metadata
Risk Score: 42/100 (MEDIUM) | Blast Radius: authorization SPI + admin permissions (2 production modules, 4 test classes); `AdminPermissionsSchema.SCHEMA` is a global singleton so `CLIENTS` is visible to all FGAP consumers | Sensitive Paths: `services/.../admin/permissions/`, `server-spi-private/.../authorization/`
AI-Authored Likelihood: LOW (idiomatic Keycloak style, consistent with existing V1/V2 split, human-authored Javadoc, real test scenarios mirroring user-resource evaluation patterns)

## Recommendation
approve (with optional cleanup of the dead `logger` / `getEvaluationContext` and a short comment on the feature-flag placement before merge)
