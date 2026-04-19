## Summary
10 files changed, 866 lines added, 138 lines deleted. 2 findings (0 critical, 2 improvements).
Adds Client resource type + scopes to V2 admin authorization schema and introduces `ClientPermissionsV2`; tests are extended and shared helpers refactored. Approved upstream. Two dead-code improvements in the new V2 evaluator.

## Improvements

:yellow_circle: [correctness] Dead private method `getEvaluationContext` in `ClientPermissionsV2` in services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:254 (confidence: 95)
The `getEvaluationContext(ClientModel authorizedClient, AccessToken token)` helper is defined on the new `ClientPermissionsV2` class but is never referenced anywhere in the class or the rest of the diff. The two call-sites that would normally consume such a context — `canExchangeTo(...)` and `exchangeToPermission(...)` — both immediately throw `UnsupportedOperationException("Not supported in V2")`, so the helper is orphaned. It looks like a copy-paste artifact from `ClientPermissions` (V1). Drop it together with the `ClientModelIdentity` / `DefaultEvaluationContext` / `AccessToken` imports that only exist to support it — this reduces the surface area of the new V2 class and prevents future readers from inferring that token-exchange evaluation is partially wired up.
```suggestion
// remove the entire `private EvaluationContext getEvaluationContext(ClientModel authorizedClient, AccessToken token) { ... }` block
// and the corresponding imports: ClientModelIdentity, DefaultEvaluationContext, EvaluationContext, AccessToken
```

:yellow_circle: [consistency] Unused static import `AdminPermissionManagement.TOKEN_EXCHANGE` in services/src/main/java/org/keycloak/services/resources/admin/permissions/ClientPermissionsV2.java:38 (confidence: 95)
The file declares `import static org.keycloak.services.resources.admin.permissions.AdminPermissionManagement.TOKEN_EXCHANGE;` but `TOKEN_EXCHANGE` is never referenced in the class body. The only token-exchange entry points (`canExchangeTo`, `exchangeToPermission`) just throw `UnsupportedOperationException`. Remove the import; checkstyle/IDE warnings will otherwise accumulate and it signals (misleadingly) that token-exchange is handled here.
```suggestion
// delete line:
// import static org.keycloak.services.resources.admin.permissions.AdminPermissionManagement.TOKEN_EXCHANGE;
```

## Risk Metadata
Risk Score: 48/100 (MEDIUM) | Blast Radius: scoped to admin-authz V2 (feature-flagged under `ADMIN_FINE_GRAINED_AUTHZ`), new `ClientPermissionsV2` type + 1-line `MgmtPermissionsV2.clients()` wiring, shared `AdminPermissionsSchema` registers new `CLIENTS` resource type | Sensitive Paths: `services/src/main/java/org/keycloak/services/resources/admin/permissions/**` (authz), `server-spi-private/.../AdminPermissionsSchema.java` (auth schema)
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold: unused-import/style nit on `AdminPermissionsSchema.java:51` trailing whitespace; potential V2 client-removal cleanup gap — the `Profile.isFeatureEnabled(ADMIN_FINE_GRAINED_AUTHZ)` guard added in `AdminPermissions.java` means the cleanup listener no longer fires when only the V2 feature is active, so deleted clients may leave orphan authz resources — confidence 70, needs confirmation against the V2 feature flag's own cleanup path; `testMapRolesAndCompositesOnlyOneClient` asserts only the positive path after granting permissions, no negative-case coverage for a different client — confidence 72)
