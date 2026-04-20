## Summary
19 files changed, 740 lines added, 126 lines deleted. 7 findings (1 critical, 6 improvements, 0 nitpicks).
Adds Groups resource type and scopes to FGAP v2; mostly solid, but GroupPermissionsV2.canManage() appears to accept VIEW-only scope, and removal of a public ModelToRepresentation helper risks SPI callers.

## Critical

:red_circle: [security] canManage() grants manage access to callers with only VIEW scope in services/src/main/java/org/keycloak/services/resources/admin/permissions/GroupPermissionsV2.java:54 (confidence: 90)
GroupPermissionsV2.canManage() (no-arg, "all groups" check) calls hasPermission(null, AdminPermissionsSchema.VIEW, AdminPermissionsSchema.MANAGE). Because hasPermission returns true if the caller has ANY of the listed scopes, a principal granted only the VIEW scope on the Groups resource type will pass canManage() and be treated as able to manage groups globally. This is an authorization bypass: VIEW is read-only and must not satisfy a manage check.

Compare to the per-group variant canManage(GroupModel), which correctly passes only AdminPermissionsSchema.MANAGE. The global variant should do the same. The symmetric UserPermissionsV2.canManage/canManage(user) also use only MANAGE, reinforcing that this is a bug rather than an intentional design.

Impact: any FGAP v2 policy that grants VIEW across all groups (a common "read-only auditor" pattern demonstrated in the new test testViewGroups) silently gains global manage capability on groups (create/update/delete/add-child/map-roles), as exercised in the sibling test testManageAllGroups.
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

:yellow_circle: [cross-file-impact] Removal of public static ModelToRepresentation.searchGroupModelsByAttributes breaks SPI consumers in server-spi-private/src/main/java/org/keycloak/models/utils/ModelToRepresentation.java:173 (confidence: 90)
ModelToRepresentation lives in server-spi-private, which is consumed by providers/extensions outside this repository. The PR deletes the public static method searchGroupModelsByAttributes(KeycloakSession, RealmModel, Map<String,String>, Integer, Integer) and only updates the single in-tree caller (GroupsResource) to call session.groups().searchGroupsByAttributes(...) directly. There is no deprecation cycle, no @Deprecated stub, and no release-note entry in this diff.

Any downstream SPI-private consumer (custom admin REST extensions, downstream forks, Red Hat SSO plugins) that referenced this helper will fail to compile against the new Keycloak version. Because the helper was a one-line delegator added precisely to hide the GroupProvider call site, third-party usage is plausible.
```suggestion
Restore the method as a thin @Deprecated(forRemoval = true) delegator for one release cycle, or add an upgrade/migration note flagging the removal for SPI consumers.
```

:yellow_circle: [correctness] getGroupIdsWithViewPermission javadoc/naming does not match actual scopes checked in services/src/main/java/org/keycloak/services/resources/admin/permissions/GroupPermissionsV2.java:97 (confidence: 88)
The GroupPermissionEvaluator javadoc for getGroupIdsWithViewPermission says "Stream of IDs of groups with view permission" and the short-circuit is `if (root.users().canView()) return emptySet()`. However, the V2 implementation iterates groups and includes an ID when hasPermission returns true for AdminPermissionsSchema.VIEW_MEMBERS or AdminPermissionsSchema.MANAGE_MEMBERS — i.e., it is returning groups whose MEMBERS the caller can view, not groups the caller itself can VIEW.

This method feeds UsersResource/BruteForceUsersResource to scope user search by group membership, so the semantics (view-members) are what callers actually want. But the name, javadoc, and the short-circuit on users().canView() (rather than users().canView()-equivalent for members) form a naming/doc inconsistency that will mislead future maintainers. The V1 implementation had the same confusion but the renamed interface method now codifies it. Recommend either renaming to getGroupIdsWithViewMembersPermission or tightening the javadoc to state explicitly that it checks VIEW_MEMBERS/MANAGE_MEMBERS.
```suggestion
Rename to getGroupIdsWithViewMembersPermission, or update the javadoc on GroupPermissionEvaluator to: "Returns group IDs where the caller has VIEW_MEMBERS or MANAGE_MEMBERS permission (used for scoping user search). Returns empty set when the caller already has global user view permission."
```

:yellow_circle: [correctness] Session attribute UserModel.GROUPS is now set even when caller has global user view in rest/admin-ui-ext/src/main/java/org/keycloak/admin/ui/rest/BruteForceUsersResource.java:144 (confidence: 85)
The prior code gated setting session.setAttribute(UserModel.GROUPS, groupModels) on `!auth.users().canView()`. The new code unconditionally sets the attribute whenever getGroupIdsWithViewPermission() returns a non-empty set. Functionally this is safe today because getGroupIdsWithViewPermission() is defined to return an empty set when root.users().canView() is true, so the outer `if (!groupIds.isEmpty())` effectively preserves the gate.

However, this correctness now depends on an invariant of a different class (GroupPermissionsV2.getGroupIdsWithViewPermission short-circuit) rather than being locally obvious. If any future implementation of GroupPermissionEvaluator ever returns a non-empty set while users().canView() is true, the UserModel.GROUPS attribute will leak into session.users().searchForUserStream() and incorrectly narrow results for a privileged admin — a silent visibility regression that is easy to miss.
```suggestion
if (!auth.users().canView()) {
    Set<String> groupIds = auth.groups().getGroupIdsWithViewPermission();
    if (!groupIds.isEmpty()) {
        session.setAttribute(UserModel.GROUPS, groupIds);
    }
}
```

:yellow_circle: [security] searchForUser drops post-filter usersEvaluator::canView from stream in services/src/main/java/org/keycloak/services/resources/admin/UsersResource.java:445 (confidence: 85)
Prior implementation applied a final `.filter(usersEvaluator::canView)` on the user stream after `session.users().searchForUserStream(...)`. The new implementation removes that post-filter and relies solely on the session attribute UserModel.GROUPS being honored by the storage layer to restrict results.

This moves the authorization decision from an explicit Java-layer filter into an implicit contract with every user-storage provider (JPA, LDAP, custom SPI). If any provider ignores the UserModel.GROUPS attribute (e.g., a federated provider that did not integrate the FGAP v2 filter), search results will include users the caller is not permitted to view. The belt-and-suspenders canView filter used to catch such cases; its removal makes correctness depend on uniform provider behavior.

Recommend retaining the post-filter as a defense-in-depth guard, or documenting clearly that all UserStorageProvider implementations MUST honor UserModel.GROUPS for FGAP to be sound.
```suggestion
return toRepresentation(realm, usersEvaluator, briefRepresentation,
    session.users().searchForUserStream(realm, attributes, firstResult, maxResults)
        .filter(usersEvaluator::canView));
```

:yellow_circle: [testing] No tests for subgroup permission inheritance or negative VIEW-only vs MANAGE separation in tests/base/src/test/java/org/keycloak/tests/admin/authz/fgap/GroupResourceTypeEvaluationTest.java:1 (confidence: 88)
The new GroupResourceTypeEvaluationTest covers the happy paths for VIEW/MANAGE/VIEW_MEMBERS/MANAGE_MEMBERS/MANAGE_MEMBERSHIP at a single group level, but does not exercise:

1. Subgroup inheritance: a permission granted on topGroup should (or should not, per design) apply to its children. UserPermissionsV2.evaluateHierarchy walks the parent chain via canManageByGroup/canViewByGroup, but no test validates that a child group inherits topGroup's MANAGE_MEMBERS or is correctly denied when only topGroup has VIEW_MEMBERS.
2. Scope non-escalation: there is no negative test that a caller granted only VIEW (group scope) is denied MANAGE operations at the global canManage() path. Given the canManage() bug flagged in this review (treats VIEW as sufficient), adding such a test would have caught the regression.

Adding these two cases significantly strengthens confidence that the new scope matrix behaves as documented.
```suggestion
Add test testViewScopeDoesNotGrantManageOnAllGroups: grant only VIEW on GROUPS resource type, assert groups().add(...) returns 403 and groups().group(existing).update(...) returns 403.
Add test testSubgroupInheritsParentManagePermission (or its negation) to pin subgroup behavior.
```

:yellow_circle: [consistency] hasPermission differs from UserPermissionsV2.hasPermission in fallback semantics in services/src/main/java/org/keycloak/services/resources/admin/permissions/GroupPermissionsV2.java:121 (confidence: 86)
GroupPermissionsV2.hasPermission, when groupId is null OR the per-resource lookup misses, falls back to the resource-type resource and only proceeds if policyStore.findByResource(server, resource) is non-empty. That check gates the evaluation, so if no all-groups permission exists, all scope checks return false — including per-group checks whose resource was found by name (since the null branch is only entered when resource==null after findByName).

This differs structurally from UserPermissionsV2.hasPermission (not shown in full but referenced in V1/V2 diff), which evaluates per-user resources without a similar policy-existence short-circuit. The asymmetry is subtle: a user can be denied group VIEW globally purely because no all-groups permission is configured, even though their per-group permission would otherwise grant access — but only when the per-group resource has never been persisted via getOrCreateResource. Worth a comment or a unified helper to make the intent explicit and keep Groups/Users evaluators aligned.
```suggestion
Add an inline comment explaining why the empty-policy short-circuit is specific to the resource-type fallback, and consider extracting a shared helper in an abstract base (e.g., FGAPPermissions) so Users/Groups/Clients V2 evaluators follow identical fallback rules.
```

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: Admin REST endpoints for Groups/Users/BruteForce; FGAP v2 schema and evaluators; consumers of ModelToRepresentation in server-spi-private | Sensitive Paths: services/.../permissions/GroupPermissionsV2.java, services/.../permissions/UserPermissionsV2.java, services/.../permissions/GroupPermissions.java, server-spi-private/.../AdminPermissionsSchema.java, services/.../admin/UsersResource.java
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold)
