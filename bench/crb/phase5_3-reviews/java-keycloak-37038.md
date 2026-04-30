## Summary
14 files changed, ~700 lines added, ~130 lines deleted. 4 findings (1 critical, 3 improvements).
Adds a `Groups` resource type to the V2 fine-grained admin-permissions schema, introduces `GroupPermissionsV2`, and refactors `getGroupsWithViewPermission` → `getGroupIdsWithViewPermission`. The new `GroupPermissionsV2.canManage()` (no-arg) accepts the `VIEW` scope as granting management rights, which is a privilege-escalation bug; a few smaller behavioural and javadoc issues are flagged below.

## Critical
:red_circle: [security] `GroupPermissionsV2.canManage()` accepts `VIEW` scope as granting manage in `services/src/main/java/org/keycloak/services/resources/admin/permissions/GroupPermissionsV2.java:38` (confidence: 90)
The no-arg `canManage()` calls `hasPermission(null, AdminPermissionsSchema.VIEW, AdminPermissionsSchema.MANAGE)`. Because `hasPermission` returns `true` when *any* granted scope intersects the expected list, a principal who only holds the `VIEW` scope on the all-groups resource will pass `canManage()` — and therefore `requireManage()` — and can create top-level groups. Compare with `canManage(GroupModel)` directly above, which correctly checks only `MANAGE`. The `canView()` no-arg method legitimately accepts `[VIEW, MANAGE]` (manage implies view), so the symmetric pattern in `canManage()` looks like a copy-paste defect rather than intentional. The new `GroupResourceTypeEvaluationTest.testManageAllGroups` only exercises the `MANAGE` happy path, so the regression is not caught by the test suite. The class-level javadoc on `GroupPermissionEvaluator.canManage()` documents the intended behaviour ("permission to MANAGE groups") — the implementation is broader than the contract.
```suggestion
    @Override
    public boolean canManage() {
        if (root.hasOneAdminRole(AdminRoles.MANAGE_USERS)) {
            return true;
        }

        return hasPermission(null, AdminPermissionsSchema.MANAGE);
    }
```

## Improvements
:yellow_circle: [correctness] Removed `.filter(usersEvaluator::canView)` from user search may exclude users granted via per-user FGAP permissions in `services/src/main/java/org/keycloak/services/resources/admin/UsersResource.java:445` (confidence: 85)
The old flow ran the SQL search and then applied a Java-level `.filter(usersEvaluator::canView)`, which honoured per-user FGAP permissions (e.g. a permission set on a single `UserModel` rather than on a group). The new flow relies entirely on the `UserModel.GROUPS` SQL filter populated from `getGroupIdsWithViewPermission()`. A caller who has FGAP `VIEW`/`MANAGE` directly on individual users (no group-membership grant) will now: (a) be filtered out of the result set when no groups are viewable, or (b) receive the *unfiltered* result set when no group-view perms exist (since `groupIds.isEmpty()` skips setting the SQL filter and the Java post-filter is gone). Either branch is wrong. The same pattern was applied in `BruteForceUsersResource.searchForUser`. If this is intentional (V2 permissions are group-scoped only), please add a regression test covering "principal has VIEW on user X by user-policy, no group access" to lock the contract.
```suggestion
        Set<String> groupIds = auth.groups().getGroupIdsWithViewPermission();
        if (!groupIds.isEmpty()) {
            session.setAttribute(UserModel.GROUPS, groupIds);
        }

        Stream<UserModel> userModels = session.users().searchForUserStream(realm, attributes, firstResult, maxResults).filter(usersEvaluator::canView);
        return toRepresentation(realm, usersEvaluator, briefRepresentation, userModels);
```

:yellow_circle: [correctness] `AdminPermissionsSchema.getOrCreateResource` now throws `IllegalStateException` for unknown resource types in `server-spi-private/src/main/java/org/keycloak/authorization/AdminPermissionsSchema.java:100` (confidence: 85)
The previous implementation initialised `name = null` and let the unknown-type branch fall through to the existing `if (name == null)` handler (which produces a `ModelException` / `NotFound` style outcome that callers already handle). The new switch's `default -> throw new IllegalStateException(...)` changes both the exception type and the resulting HTTP status (500-style instead of the previous 404/Model error). Any caller — including SPI extensions outside this PR's diff — that previously routed unknown types into a known error path will now surface an internal-server-error to clients. If this is intentional, prefer a checked / model-level exception (e.g. `ModelException`) so the REST layer can map it to a 400/404 rather than 500.
```suggestion
        switch (resourceType) {
            case CLIENTS_RESOURCE_TYPE -> name = resolveClient(session, id);
            case GROUPS_RESOURCE_TYPE -> name = resolveGroup(session, id);
            case USERS_RESOURCE_TYPE -> name = resolveUser(session, id);
            default -> throw new ModelException("Resource type [" + resourceType + "] not found.");
        }
```

:yellow_circle: [comment-accuracy] Stale / wrong javadoc on `GroupPermissionEvaluator` in `services/src/main/java/org/keycloak/services/resources/admin/permissions/GroupPermissionEvaluator.java:115` (confidence: 95)
Two contract-doc bugs introduced by the new javadoc block:
1. `getGroupIdsWithViewPermission` is documented as "@return Stream of IDs of groups with view permission" but the signature returns `Set<String>`. The first sentence — "If `UserPermissionEvaluator#canView()` evaluates to `true`, returns empty set." — also under-specifies; it should say *empty set, meaning "no group filter, the caller can already see all users"*, otherwise readers may misread "empty" as "no access".
2. `requireManageMembers(GroupModel)` javadoc reads "Throws ForbiddenException if `canManageMembership(GroupModel)` returns `false`." That is incorrect — the method should require `canManageMembers`, not `canManageMembership`; the implementation in `GroupPermissions` confirms this. Same docstring is duplicated verbatim on `requireManageMembership`, leaving readers unable to tell the two `require*` methods apart from the doc alone.
```suggestion
    /**
     * Throws ForbiddenException if {@link #canManageMembers(GroupModel)} returns {@code false}.
     */
    void requireManageMembers(GroupModel group);

    /**
     * Returns Map with information what access the caller for the provided group has.
     */
    Map<String, Boolean> getAccess(GroupModel group);

    /**
     * If {@link UserPermissionEvaluator#canView()} evaluates to {@code true}, returns an empty
     * set indicating no group filter is required (caller can view all users). Otherwise returns
     * the IDs of groups for which the caller holds VIEW_MEMBERS or MANAGE_MEMBERS.
     *
     * @return Set of group IDs the caller may use to view members, or empty if no filter applies.
     */
    Set<String> getGroupIdsWithViewPermission();
```

## Risk Metadata
Risk Score: 62/100 (HIGH) | Blast Radius: high — touches admin authorization SPI, REST users/groups endpoints, and FGAP V2 evaluator chain (≈14 files, both production and a new resource type) | Sensitive Paths: `services/.../permissions/`, `server-spi-private/.../authorization/AdminPermissionsSchema.java`, `rest/admin-ui-ext/.../BruteForceUsersResource.java`
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold)
