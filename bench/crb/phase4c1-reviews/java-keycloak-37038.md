## Summary
19 files changed, ~831 lines added, ~126 lines deleted. 6 findings (2 critical, 4 improvements).
Introduces `Groups` resource type in FGAP v2 and refactors V1/V2 evaluators; a couple of subtle but serious permission-check bugs slipped in during the refactor.

## Critical

:red_circle: [security] `GroupPermissionsV2.canManage()` grants manage via `view` scope in services/src/main/java/org/keycloak/services/resources/admin/permissions/GroupPermissionsV2.java:592 (confidence: 95)
The realm-level `canManage()` falls back to `hasPermission(null, AdminPermissionsSchema.VIEW, AdminPermissionsSchema.MANAGE)`. `hasPermission` returns `true` when **any** of the expected scopes is granted, so a principal that has only the `view` scope on all-groups is silently promoted to realm-wide group management. The per-group overload on line 601 correctly checks `MANAGE` only, confirming this is a scope-list typo rather than an intentional elevation. The impact is privilege escalation on the Groups resource-type root in FGAP v2 (create/modify/delete groups via any endpoint that gates on `canManage()`).
```suggestion
    @Override
    public boolean canManage() {
        if (root.hasOneAdminRole(AdminRoles.MANAGE_USERS)) {
            return true;
        }

        return hasPermission(null, AdminPermissionsSchema.MANAGE);
    }
```

:red_circle: [security] User search leaks full user list when caller has only `query-users` in services/src/main/java/org/keycloak/services/resources/admin/UsersResource.java:445 (confidence: 80)
The refactor removes both the `!auth.users().canView()` guard and the post-stream `.filter(usersEvaluator::canView)`. In the previous implementation, if a caller had `canQuery()` but neither `canView()` nor any group-scoped view permission, the `UserModel.GROUPS` attribute was intentionally never set (empty set short-circuited the `if`), but the stream filter rejected every user so the response was empty. The new code still skips setting `GROUPS` when the id set is empty and no longer filters, so the underlying `searchForUserStream` returns every user in the realm. A principal with only the `query-users` admin role (and no fine-grained permissions) can now enumerate the full user directory by calling `GET /admin/realms/{realm}/users` — the exact endpoint `canQuery()` was designed to guard without granting `view`. The identical pattern was ported into `BruteForceUsersResource.searchForUser` (rest/admin-ui-ext/.../BruteForceUsersResource.java:144), so both endpoints regress together. Either restore the stream-level `filter(usersEvaluator::canView)`, or require callers to pass `canView()` before reaching the search path.
```suggestion
    private Stream<UserRepresentation> searchForUser(Map<String, String> attributes, RealmModel realm, UserPermissionEvaluator usersEvaluator, Boolean briefRepresentation, Integer firstResult, Integer maxResults, Boolean includeServiceAccounts) {
        attributes.put(UserModel.INCLUDE_SERVICE_ACCOUNT, includeServiceAccounts.toString());

        Set<String> groupIds = auth.groups().getGroupIdsWithViewPermission();
        if (!groupIds.isEmpty()) {
            session.setAttribute(UserModel.GROUPS, groupIds);
        }

        Stream<UserModel> userModels = session.users().searchForUserStream(realm, attributes, firstResult, maxResults).filter(usersEvaluator::canView);
        return toRepresentation(realm, usersEvaluator, briefRepresentation, userModels);
    }
```

## Improvements

:yellow_circle: [consistency] Javadoc for `requireManageMembers` references the wrong predicate in services/src/main/java/org/keycloak/services/resources/admin/permissions/GroupPermissionEvaluator.java:404 (confidence: 98)
The Javadoc says `Throws ForbiddenException if {@link #canManageMembership(GroupModel)} returns {@code false}`, but this method is `requireManageMembers`, not `requireManageMembership`. Readers using IDE link-navigation will jump to the wrong predicate, and the comment is a copy-paste from the block above.
```suggestion
    /**
     * Throws ForbiddenException if {@link #canManageMembers(GroupModel)} returns {@code false}.
     */
    void requireManageMembers(GroupModel group);
```

:yellow_circle: [correctness] `canViewByGroup` silently narrows semantics for manage-only admins in services/src/main/java/org/keycloak/services/resources/admin/permissions/UserPermissions.java:990 (confidence: 70)
The predicate changed from `group -> root.groups().getGroupsWithViewPermission(group)` (which explicitly returned true when `root.users().canView() || root.users().canManage()`) to `root.groups()::canViewMembers`, which short-circuits only on `root.users().canView()`. An admin holding `MANAGE_USERS` but not `VIEW_USERS` — an unusual but representable configuration — used to gain view-by-group access via the manage branch; after this change, the same admin is denied view-by-group unless they have explicit `VIEW_MEMBERS`/`MANAGE_MEMBERS` scopes. If this narrowing is intentional, please note it in the PR description and add a V1 regression test; otherwise, extend `canViewMembers` (or the caller here) to accept `root.users().canManage()` as a view grant to match prior behavior.

:yellow_circle: [performance] Removal of `canViewGlobal` short-circuit forces per-group policy evaluation in services/src/main/java/org/keycloak/services/resources/admin/GroupsResource.java:191 (confidence: 75)
The old filter precomputed `canViewGlobal = groupsEvaluator.canView()` once and used `canViewGlobal || groupsEvaluator.canView(g)` so global admins paid the per-group cost zero times. The new filter `groupsEvaluator::canView` always invokes the per-group overload, which in `GroupPermissionsV2` calls `hasPermission(group.getId(), VIEW, MANAGE)` — an authorization-policy evaluation per group. Although the method short-circuits on the `MANAGE_USERS`/`VIEW_USERS` role check at the top, adopting the eta-reduced form removes a deliberate optimization for the common case of realms with many groups. Same pattern is mirrored in `GroupResource.getSubGroups` (line 149) and is worth benchmarking before merging.
```suggestion
        boolean canViewGlobal = groupsEvaluator.canView();
        return stream
            .filter(g -> canViewGlobal || groupsEvaluator.canView(g))
            .map(g -> GroupUtils.populateSubGroupCount(g, GroupUtils.toRepresentation(groupsEvaluator, g, !briefRepresentation)));
```

:yellow_circle: [consistency] `@Deprecated` added to `isImpersonatable` without a replacement pointer in services/src/main/java/org/keycloak/services/resources/admin/permissions/UserPermissionEvaluator.java:929 (confidence: 65)
`isImpersonatable(UserModel, ClientModel)` is newly annotated `@Deprecated`, but the Javadoc block immediately above (which would normally contain `@deprecated` with a migration note) is missing. Callers updating to the new API have no signal about what to call instead (presumably `canImpersonate(UserModel, ClientModel)`). Add a Javadoc `@deprecated` tag naming the replacement and, if appropriate, use `@Deprecated(forRemoval = true, since = "...")` so static analysis can flag lingering usages.

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: admin-access-control path touched in 8+ files; V1 and V2 evaluators both modified; 1 new public resource type in the authorization schema | Sensitive Paths: services/src/main/java/org/keycloak/services/resources/admin/permissions/* (auth/security-sensitive)
AI-Authored Likelihood: LOW
