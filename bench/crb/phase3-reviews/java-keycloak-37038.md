# PR Review: keycloak/keycloak#37038 — Add Groups resource type and scopes to authorization schema and evaluation

**Target:** `keycloak/keycloak#37038`
**Base:** `main` ← **Head:** `35562-Groups-FGAP`
**Stats:** 19 files changed, +831 / −126, closes #35562
**Scope:** Fine-Grained Admin Permissions (FGAP) v2 — adds `Groups` resource type plus new scopes (`manage-membership`, `manage-members`, `view-members`) and a new `GroupPermissionsV2` evaluator that extends `GroupPermissions`. Touches `AdminPermissionsSchema`, the Users and Groups resource endpoints, the `UserPermissions(V2)` evaluator, listener registration, and a 320-line integration test.

## Summary

19 files changed, 831 lines added, 126 lines deleted. 8 findings (1 critical, 4 improvements, 3 nitpicks).
Security-sensitive authorization rewrite: extends the FGAP v2 model to Groups. Logic is mostly sound, but `GroupPermissionsV2.canManage()` (no-arg) treats `VIEW` scope as sufficient for manage, a removed post-filter on user search widens reliance on DB-level authorization, and a listener-registration refactor changes runtime-toggle semantics for the `ADMIN_FINE_GRAINED_AUTHZ` feature flag.

## Critical

:red_circle: [security] `GroupPermissionsV2.canManage()` grants manage from VIEW scope in `services/src/main/java/org/keycloak/services/resources/admin/permissions/GroupPermissionsV2.java:69` (confidence: 92)

The no-argument `canManage()` returns `true` when the caller has either `VIEW` **or** `MANAGE` scope on the `Groups` resource type:

```java
@Override
public boolean canManage() {
    if (root.hasOneAdminRole(AdminRoles.MANAGE_USERS)) {
        return true;
    }
    return hasPermission(null, AdminPermissionsSchema.VIEW, AdminPermissionsSchema.MANAGE);
}
```

Contrast with the per-group overload on line 76 (`canManage(GroupModel group)`), which correctly requires only `MANAGE`. Allowing `VIEW` to answer `canManage()` yes is either a copy-paste bug from `canView()` (lines 51–57) or a deliberate but undocumented weakening. `canManage()` is the classic gate for realm-level actions like creating a new top-level group; a user whose only grant is `Groups` / `VIEW` should not pass that gate.

Compare V1 semantics in `GroupPermissions.canManage()` (line 275): it delegated to `root.hasOneAdminRole(AdminRoles.MANAGE_USERS)` — no permission-scope fallback at all. V2 broadens this; VIEW-as-MANAGE is very likely unintended.

```suggestion
    @Override
    public boolean canManage() {
        if (root.hasOneAdminRole(AdminRoles.MANAGE_USERS)) {
            return true;
        }
        return hasPermission(null, AdminPermissionsSchema.MANAGE);
    }
```

If the author *intends* the broader semantics, the javadoc on `GroupPermissionEvaluator.canManage()` ("Returns `true` if the caller has `MANAGE_USERS` role. For V2 only: Also if it has permission to `VIEW` or `MANAGE` groups.") should explicitly call out that `VIEW` is deliberately sufficient for the no-arg overload but not the per-group overload, and there should be an integration test covering the `VIEW`-only-to-`canManage()` path in `GroupResourceTypeEvaluationTest`.

[References: CWE-285 Improper Authorization; OWASP A01:2021 Broken Access Control]

## Improvements

:yellow_circle: [security] Removal of `.filter(usersEvaluator::canView)` post-filter relies entirely on DB-level filtering in `services/src/main/java/org/keycloak/services/resources/admin/UsersResource.java:449` (confidence: 78)

Both `UsersResource.searchForUser` and `rest/admin-ui-ext/.../BruteForceUsersResource.searchForUser` dropped their post-query `.filter(usersEvaluator::canView)`:

```java
// before
if (!auth.users().canView()) {
    Set<String> groupModels = auth.groups().getGroupsWithViewPermission();
    if (!groupModels.isEmpty()) {
        session.setAttribute(UserModel.GROUPS, groupModels);
    }
}
Stream<UserModel> userModels = session.users().searchForUserStream(realm, attributes, firstResult, maxResults)
    .filter(usersEvaluator::canView);

// after
Set<String> groupIds = auth.groups().getGroupIdsWithViewPermission();
if (!groupIds.isEmpty()) {
    session.setAttribute(UserModel.GROUPS, groupIds);
}
return toRepresentation(realm, usersEvaluator, briefRepresentation,
    session.users().searchForUserStream(realm, attributes, firstResult, maxResults));
```

The caller now relies exclusively on `UserModel.GROUPS` being honored by every `UserProvider` implementation as a hard filter. Consider the corner case where the caller has `auth.users().canView() == false` and `getGroupIdsWithViewPermission()` returns an empty set (no group-view permissions, no per-user permissions). In the old code, the `.filter(canView)` would yield an empty stream. In the new code, the `UserModel.GROUPS` attribute is **not set** (the `if (!groupIds.isEmpty())` guard), and `searchForUserStream` will enumerate *all* users.

For this to be safe, the public entry points that call `searchForUser` must already have rejected the request via a `requireQuery()` / `requireView()` gate that fails closed when the caller has neither global view nor any group-view nor any per-user-view permission. `UserPermissionsV2.canView(user)` does handle per-user permissions, but those are a per-user evaluation that the DB-layer `UserModel.GROUPS` filter cannot express. Removing the Java-side post-filter eliminates the defence-in-depth for users a caller can see only via per-user resource permissions (not via group membership).

Recommend either:
1. Restoring the `.filter(usersEvaluator::canView)` as a defence-in-depth cross-check (measurable cost, but the permission-evaluation cache should absorb it), **or**
2. Adding an explicit `requireQuery()` gate at every public `search`/`getUsers` entry and documenting the invariant that `UserModel.GROUPS` is the single source of truth for per-row filtering, and adding a test where a caller with only a per-user `VIEW` permission (not via group membership) searches for users.

:yellow_circle: [correctness] Listener registration now captures the feature flag at registration time in `services/src/main/java/org/keycloak/services/resources/admin/permissions/AdminPermissions.java:73` (confidence: 85)

The refactor moved `Profile.isFeatureEnabled(Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ)` from inside `onEvent` to outside the `manager.register(...)` call:

```java
// before: checked on every event dispatch
manager.register(new ProviderEventListener() {
    public void onEvent(ProviderEvent event) {
        if (Profile.isFeatureEnabled(Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ)) {
            // handle event
        }
    }
});

// after: checked once at registration
if (Profile.isFeatureEnabled(Profile.Feature.ADMIN_FINE_GRAINED_AUTHZ)) {
    manager.register(new ProviderEventListener() { ... });
}
```

This is a genuine semantic change. If `ADMIN_FINE_GRAINED_AUTHZ` is ever toggled *after* `registerListener` runs (dynamic feature management, test suites that enable/disable profiles mid-run, `kc.sh start --features=...` reload), the listener will no longer kick in retroactively, and role/client/group removal events will silently stop cascading permission cleanup.

If feature flags in Keycloak are guaranteed immutable for the lifetime of a process (which is the common case), this change is a reasonable micro-optimization — please confirm in the commit message or a comment. Otherwise, move the check back into `onEvent`.

:yellow_circle: [consistency] `UserPermissionsV2` drops `AdminRoles.ADMIN` from role checks in `services/src/main/java/org/keycloak/services/resources/admin/permissions/UserPermissionsV2.java:47` (confidence: 70)

```java
// before
if (root.hasOneAdminRole(AdminRoles.ADMIN, AdminRoles.MANAGE_USERS, AdminRoles.VIEW_USERS)) {
    return true;
}
// after
if (root.hasOneAdminRole(AdminRoles.MANAGE_USERS, AdminRoles.VIEW_USERS)) {
    return true;
}
```

Same pattern on `canManage(user)`. In practice the `admin` composite role includes `manage-users`/`view-users`, so a genuine admin still passes. But if a deployment has a custom composite that grants `AdminRoles.ADMIN` without the underlying per-resource admin roles (unusual but not impossible — consider master-realm custom setups), this narrows behavior silently. Worth an explicit note in the PR description or upgrading guide, and a check that `AbstractPermissionTest` / existing tests cover the "admin role only" caller path.

:yellow_circle: [correctness] `canMapRoles(user)` and `canManageGroupMembership(user)` in `UserPermissionsV2` now require MANAGE or the specific sub-scope plus `canManageByGroup` in `services/src/main/java/org/keycloak/services/resources/admin/permissions/UserPermissionsV2.java:77` (confidence: 72)

```java
// before
public boolean canMapRoles(UserModel user) {
    if (canManage(user)) return true;
    return hasPermission(user, null, AdminPermissionsSchema.MAP_ROLES);
}
// after
public boolean canMapRoles(UserModel user) {
    if (root.hasOneAdminRole(AdminRoles.MANAGE_USERS)) return true;
    return hasPermission(user, null, AdminPermissionsSchema.MANAGE, AdminPermissionsSchema.MAP_ROLES)
           || canManageByGroup(user);
}
```

Two behaviour changes bundled in one rewrite:
1. `canManage(user)` is expanded inline but the old "user can manage → user can map roles" chain now short-circuits on role only, skipping the per-user `hasPermission(user, ..., VIEW, MANAGE)` path for map-roles (the scope `VIEW` is not in the new scope list, so that's actually intentional narrowing — VIEW no longer implies map-roles — good).
2. Adds `canManageByGroup(user)` — a group-members-based fallback that didn't exist for map-roles before. This means a user with `MANAGE_MEMBERS` on group G can now **map roles** on any user in G. Is this the intended semantic? The javadoc on `UserPermissionEvaluator.canMapRoles` states so explicitly, but it is a genuine broadening relative to V1 and should have a test (`testCanMapRolesByGroupManageMembers`).

Same reasoning applies to `canManageGroupMembership(user)` — `MANAGE_MEMBERS` on a group now implies ability to change that user's group memberships.

## Nitpicks

:white_circle: [correctness] `ApiUtil.handleCreatedResponse` uses `try (response)` but several call sites already own the `Response` in an outer try-with-resources in `test-framework/core/src/main/java/org/keycloak/testframework/util/ApiUtil.java:9` (confidence: 85)

```java
public static String handleCreatedResponse(Response response) {
    try (response) {
        Assertions.assertEquals(201, response.getStatus());
        String path = response.getLocation().getPath();
        return path.substring(path.lastIndexOf('/') + 1);
    }
}
```

`GroupResourceTypeEvaluationTest` calls this inside its own `try (Response response = realm.admin().groups().add(topGroup)) { ... ApiUtil.handleCreatedResponse(response); ... }` — the inner try-with-resources closes the response, then the outer one closes it again. JAX-RS `Response.close()` is idempotent on standard implementations, so this is safe today, but it's an easy footgun for future callers. Consider either:
1. Dropping the try-with-resources in `handleCreatedResponse` (leave lifecycle to the caller) — preserves the original `response.close()` behaviour, or
2. Adding a doc comment declaring that `handleCreatedResponse` takes ownership of the response.

:white_circle: [consistency] `getGroupsWithViewPermission` renamed to `getGroupIdsWithViewPermission` in `services/src/main/java/org/keycloak/services/resources/admin/permissions/GroupPermissionEvaluator.java:69` (confidence: 95)

Good rename — the return type was `Set<String>` of IDs despite the `...Groups...` name. No public API? `GroupPermissionEvaluator` is in `org.keycloak.services.resources.admin.permissions` which is internal, so renaming is safe. Confirm no SPI consumers.

:white_circle: [test-quality] `GroupResourceTypeEvaluationTest` has several narrow-coverage gaps in `tests/base/src/test/java/org/keycloak/tests/admin/authz/fgap/GroupResourceTypeEvaluationTest.java:1` (confidence: 65)

The new 320-line test suite is solid for the happy paths but does not exercise:
- `canManage()` (no-arg) with only `VIEW` scope granted (the critical finding above) — would catch that regression.
- A user with only per-user `VIEW` permission (no group membership, no global role) searching users — would catch the second improvement finding.
- Revocation: create a permission, exercise it, revoke, confirm access is denied. Existing tests only grant.
- Permissions inheritance through sub-group hierarchy (`evaluateHierarchy` path in `UserPermissions.canViewByGroup` / `canManageByGroup`) with a user in a sub-group and the view permission on the top group.

Also the double-semicolon typo `new GroupRepresentation();;` on line 64 of the new test file.

## Risk Metadata

Risk Score: 72/100 (HIGH) | Blast Radius: authorization-core (`permissions/*`) + two REST resources + test framework utility | Sensitive Paths: `permissions/`, new evaluator class, schema registration | AI-Authored Likelihood: LOW (consistent with existing Keycloak code style, javadoc voice matches author's prior commits, no telltale AI patterns)

Recommendation: **request-changes** — the critical `canManage()` finding should be resolved before merge; the `UsersResource` filter-removal warrants a targeted test before the defence-in-depth change lands; the other items are discussion-worthy but not blocking.
