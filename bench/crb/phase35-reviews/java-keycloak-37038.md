## Summary
19 files changed, 831 lines added, 126 lines deleted. 10 findings (8 critical, 2 improvements).
PR #37038 introduces the `Groups` resource type plus `view-members` / `manage-members` / `manage-membership` scopes for FGAP V2 and a new `GroupPermissionsV2`. The refactor drops several defense-in-depth filters and changes V1 short-circuit semantics, creating authorization-bypass and privilege-regression paths that tests do not cover.

## Critical

:red_circle: [correctness] `GroupPermissionsV2.canManage()` grants manage on the VIEW scope in services/src/main/java/org/keycloak/services/resources/admin/permissions/GroupPermissionsV2.java:592 (confidence: 97)
The global `canManage()` calls `hasPermission(null, AdminPermissionsSchema.VIEW, AdminPermissionsSchema.MANAGE)` — meaning a principal that only holds VIEW on the all-groups resource is granted manage capability (used to gate create/update/delete and role-mapping on groups). The per-group `canManage(GroupModel)` on line 601 correctly passes only `MANAGE`, so the global variant is inconsistent with both the per-group variant and the Javadoc contract ("permission to MANAGE groups"). This is a privilege-escalation path: any policy that was intended to only allow group listing/viewing ends up allowing full group management.
```suggestion
@Override
public boolean canManage() {
    if (root.hasOneAdminRole(AdminRoles.MANAGE_USERS)) {
        return true;
    }
    return hasPermission(null, AdminPermissionsSchema.MANAGE);
}
```

:red_circle: [correctness] `getGroupIdsWithViewPermission()` passes resource entity UUID where the resource name is expected, making the per-group scan a no-op in services/src/main/java/org/keycloak/services/resources/admin/permissions/GroupPermissionsV2.java:648 (confidence: 96)
Inside the `findByType` callback, `groupResource.getId()` is the authorization resource entity UUID. It is forwarded to `hasPermission(groupId, ...)` which does `resourceStore.findByName(server, groupId)`. Resources for groups are created with the group's UUID as their *name* (via `resolveGroup()` → `group.getId()` in `AdminPermissionsSchema.getOrCreateResource`), so `findByName(<resource-entity-UUID>, ...)` cannot match and always returns null. The code then falls through to the all-groups fallback, so per-group VIEW_MEMBERS/MANAGE_MEMBERS permissions are never counted and `getGroupIdsWithViewPermission()` effectively always returns an empty set unless an all-groups policy exists. This breaks the core FGAP V2 "admin sees only users in groups they have VIEW_MEMBERS on" story — users with per-group view-members permission will never appear in user search results.
```suggestion
resourceStore.findByType(server, AdminPermissionsSchema.GROUPS_RESOURCE_TYPE, groupResource -> {
    if (hasPermission(groupResource.getName(), AdminPermissionsSchema.VIEW_MEMBERS, AdminPermissionsSchema.MANAGE_MEMBERS)) {
        granted.add(groupResource.getName());
    }
});
```

:red_circle: [security] Removed `.filter(usersEvaluator::canView)` in `UsersResource.searchForUser` combined with ambiguous empty-set semantics may leak all users in services/src/main/java/org/keycloak/services/resources/admin/UsersResource.java:445 (confidence: 90)
The old code applied `.filter(usersEvaluator::canView)` on the returned user stream as defense-in-depth and only set `UserModel.GROUPS` when `!auth.users().canView()`. The new code always queries `getGroupIdsWithViewPermission()` and only sets the attribute when the set is non-empty, trusting the storage layer to enforce the group filter. `getGroupIdsWithViewPermission()` returns an empty set in three semantically distinct cases: (a) caller has global view (OK), (b) caller is cross-realm admin (`!root.isAdminSameRealm()`), (c) no resource server / no granted group resources. In cases (b) and (c) the store receives no `UserModel.GROUPS` filter and the stream-level safety net is gone, so a caller that satisfies `requireQuery()` — e.g. an admin holding only `QUERY_USERS` — can now receive the full realm user list. The same bug applies to `BruteForceUsersResource.searchForUser`, and compounds with finding #2 above which makes (c) the default state for every FGAP V2 user-search call.
```suggestion
Set<String> groupIds = auth.groups().getGroupIdsWithViewPermission();
Stream<UserModel> userModels = session.users().searchForUserStream(realm, attributes, firstResult, maxResults);

if (!auth.users().canView()) {
    if (groupIds.isEmpty()) {
        return toRepresentation(realm, usersEvaluator, briefRepresentation, Stream.empty());
    }
    session.setAttribute(UserModel.GROUPS, groupIds);
    userModels = userModels.filter(usersEvaluator::canView);
}

return toRepresentation(realm, usersEvaluator, briefRepresentation, userModels);
```

:red_circle: [security] Same authorization-bypass pattern in `BruteForceUsersResource.searchForUser` leaks brute-force status of every realm user in rest/admin-ui-ext/src/main/java/org/keycloak/admin/ui/rest/BruteForceUsersResource.java:144 (confidence: 88)
Identical change to `UsersResource.searchForUser`: removed the `!auth.users().canView()` guard around the GROUPS attribute and no per-user `canView` filter is applied. A caller that only has whatever role the upstream REST layer requires to reach this helper (and no user-view / group-view-members permission) now receives the full brute-force user list across the realm. Brute-force data is especially sensitive — it identifies accounts currently under attack and aids target selection for further attacks.
```suggestion
private Stream<BruteUser> searchForUser(Map<String, String> attributes, RealmModel realm, UserPermissionEvaluator usersEvaluator, Boolean briefRepresentation, Integer firstResult, Integer maxResults, Boolean includeServiceAccounts) {
    attributes.put(UserModel.INCLUDE_SERVICE_ACCOUNT, includeServiceAccounts.toString());

    if (!auth.users().canView()) {
        Set<String> groupIds = auth.groups().getGroupIdsWithViewPermission();
        if (groupIds.isEmpty()) {
            return Stream.empty();
        }
        session.setAttribute(UserModel.GROUPS, groupIds);
    }

    return toRepresentation(realm, usersEvaluator, briefRepresentation,
        session.users().searchForUserStream(realm, attributes, firstResult, maxResults)
            .filter(usersEvaluator::canView));
}
```

:red_circle: [correctness] V1 `GroupPermissions.getGroupIdsWithViewPermission()` dropped the `canManage()` early-return, restricting MANAGE_USERS admins to group-scoped visibility in services/src/main/java/org/keycloak/services/resources/admin/permissions/GroupPermissions.java:499 (confidence: 92)
Old:
```java
if (root.users().canView() || root.users().canManage()) return Collections.emptySet();
```
New:
```java
if (root.users().canView()) return Collections.emptySet();
```
A principal holding only the `MANAGE_USERS` role (so `root.users().canManage()` is true but `root.users().canView()` is false for this check's purposes if the role check diverges) now proceeds into the full FGAP resource scan. If any group-level fine-grained permissions exist in the realm, the scan may return a non-empty set, and `UsersResource` / `BruteForceUsersResource` will restrict the admin to only users in those groups — a silent privilege regression for what is supposed to be a realm-wide admin role.
```suggestion
@Override
public Set<String> getGroupIdsWithViewPermission() {
    if (root.users().canView() || root.users().canManage()) return Collections.emptySet();
    // ... rest unchanged
}
```

:red_circle: [correctness] `requireViewMembers` now delegates to `canViewMembers()` which lost the `canManage()` short-circuit, breaking MANAGE_USERS admins in services/src/main/java/org/keycloak/services/resources/admin/permissions/GroupPermissions.java:508 (confidence: 91)
Old `requireViewMembers(group)` called `getGroupsWithViewPermission(group)` which short-circuited to true when `root.users().canView() || root.users().canManage()`. The new implementation calls `canViewMembers(group)`, which in V1 only short-circuits on `root.users().canView()`. An admin holding MANAGE_USERS (so `root.users().canManage()` is true but not necessarily `canView()`) now falls through into the resource-server permission evaluation; if no explicit view-members policy exists for that group they get `ForbiddenException`, even though MANAGE_USERS is a global admin role that previously implied member visibility.
```suggestion
@Override
public boolean canViewMembers(GroupModel group) {
    if (root.users().canView() || root.users().canManage()) return true;

    if (!root.isAdminSameRealm()) return false;
    ResourceServer server = root.realmResourceServer();
    if (server == null) return false;

    return hasPermission(group, VIEW_MEMBERS_SCOPE, MANAGE_MEMBERS_SCOPE);
}
```

:red_circle: [testing] `canManage()` VIEW-grants-manage bug has no denial test in services/src/main/java/org/keycloak/services/resources/admin/permissions/GroupPermissionsV2.java:592 (confidence: 92)
The positive path (VIEW grants canView) is covered by `testViewGroups`, but there is no negative test asserting that a caller with *only* VIEW scope is denied manage operations (create/update/subgroup/role-mapping). Because V1 had `canManage() ⇔ MANAGE_USERS role only`, the V2 asymmetry introduced in finding #1 is a behavioral divergence that slipped through precisely because no test exercises the denial path.
```suggestion
@Test
public void testViewScopeDoesNotGrantManage() {
    UserPolicyRepresentation policy = createUserPolicy(realm, client, "Only My Admin Policy",
        realm.admin().users().search("myadmin").get(0).getId());
    createAllGroupsPermission(policy, Set.of(VIEW));

    // VIEW should allow listing
    assertThat(realmAdminClient.realm(realm.getName()).groups().groups(), hasSize(1));

    // VIEW must NOT grant create/update
    try (Response response = realmAdminClient.realm(realm.getName()).groups().add(new GroupRepresentation())) {
        assertEquals(Response.Status.FORBIDDEN.getStatusCode(), response.getStatus());
    }
    try {
        realmAdminClient.realm(realm.getName()).groups().group(topGroup.getId()).update(topGroup);
        fail("Expected ForbiddenException");
    } catch (ForbiddenException expected) { }
}
```

:red_circle: [testing] `UserPermissionsV2.canMapRoles` `canManageByGroup` fallback is untested in services/src/main/java/org/keycloak/services/resources/admin/permissions/UserPermissionsV2.java:84 (confidence: 91)
The new `canMapRoles(user)` adds a fallback: `hasPermission(user, null, MANAGE, MAP_ROLES) || canManageByGroup(user)`. `canManageByGroup` walks the user's group hierarchy calling `root.groups()::canManageMembers`, which in V2 requires `MANAGE_MEMBERS` on the group. No test grants only `MANAGE_MEMBERS` on a containing group and then exercises `mapRoles` on a member — so whether `MANAGE_MEMBERS` implicitly grants `map-roles` is neither confirmed nor denied. Given this is security-sensitive (role-mapping grants privileges), the intended semantics must be pinned down by a test.
```suggestion
@Test
public void testManageMembersAndMapRolesSemantics() {
    UserRepresentation myadmin = realm.admin().users().search("myadmin").get(0);
    UserPolicyRepresentation policy = createUserPolicy(realm, client, "Only My Admin Policy", myadmin.getId());
    createGroupPermission(topGroup, Set.of(MANAGE_MEMBERS), policy);

    // Pin intended behavior — expect denial unless MAP_ROLES is also granted:
    try {
        realmAdminClient.realm(realm.getName()).users().get(userAlice.getId())
            .roles().realmLevel().add(List.of(new RoleRepresentation("role-name", null, false)));
        fail("Expected ForbiddenException");
    } catch (ForbiddenException expected) { }
}
```

## Improvements

:yellow_circle: [testing] `QUERY_GROUPS` added to shared `myadmin` may silently widen assertions in `UserResourceTypeEvaluationTest` in tests/base/src/test/java/org/keycloak/tests/admin/authz/fgap/RealmAdminPermissionsConfig.java:34 (confidence: 90)
`RealmAdminPermissionsConfig.myadmin` now carries both `QUERY_USERS` and `QUERY_GROUPS`. The same `myadmin` is reused by `UserResourceTypeEvaluationTest`. Since `GroupPermissions.canList()` now returns true when `QUERY_GROUPS` is held, any pre-existing test assumption that `myadmin` cannot list groups is silently violated. Add an explicit regression test that the new role does not widen *user* visibility beyond what each test explicitly grants.
```suggestion
@Test
public void testQueryGroupsRoleDoesNotWidenUserVisibility() {
    // myadmin has QUERY_GROUPS but no VIEW_MEMBERS / MANAGE_MEMBERS on any group
    assertTrue(realmAdminClient.realm(realm.getName()).users().search(null, -1, -1).isEmpty(),
        "QUERY_GROUPS must not widen user visibility");
}
```

:yellow_circle: [correctness] Potential double-close of `Response` in `GroupResourceTypeEvaluationTest.onBefore()` after `ApiUtil.handleCreatedResponse` refactor in tests/base/src/test/java/org/keycloak/tests/admin/authz/fgap/GroupResourceTypeEvaluationTest.java:1159 (confidence: 88)
`onBefore()` uses `try (Response response = realm.admin().groups().add(topGroup))` and inside the block calls `ApiUtil.handleCreatedResponse(response)`. The updated `ApiUtil.handleCreatedResponse` now wraps the response in its own `try (response) { ... }`, so the response is closed once on method return and again when the outer try-with-resources block exits. JAX-RS implementations typically tolerate double close but may warn — worse, some RESTEasy versions throw on double close. Either drop the outer try-with-resources or keep the old close-on-exit contract in `ApiUtil`.
```suggestion
@BeforeEach
public void onBefore() {
    topGroup.setName(groupName);
    topGroup.setId(ApiUtil.handleCreatedResponse(realm.admin().groups().add(topGroup)));
    realm.cleanup().add(r -> r.groups().group(topGroup.getId()).remove());
    realm.admin().users().get(userAlice.getId()).joinGroup(topGroup.getId());
}
```

## Risk Metadata
Risk Score: 78/100 (HIGH) | Blast Radius: fine-grained admin permissions SPI + REST endpoints (Users/Groups/BruteForce) — touches security-critical authorization paths; SPI contract change in `GroupPermissionEvaluator` (removed `getGroupsWithViewPermission(GroupModel)`, renamed `getGroupsWithViewPermission()` → `getGroupIdsWithViewPermission()`) | Sensitive Paths: services/.../permissions/**, rest/admin-ui-ext/** — 11 files under authz directory, 1 new security-relevant class (`GroupPermissionsV2`)
AI-Authored Likelihood: LOW
