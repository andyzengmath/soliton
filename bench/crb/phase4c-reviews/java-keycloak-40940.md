## Summary
4 files changed, 51 lines added, 11 lines deleted. 5 findings (3 critical, 2 improvements, 0 nitpicks).
PR fixes an NPE in `GroupAdapter.getSubGroupsCount()` during concurrent group deletion, but the null-return contract change leaks to callers and the new concurrency test has structural flaws that let it pass even when the bug is not fixed.

## Critical

:red_circle: [correctness] Reader thread is never joined — assertion runs before thread finishes, test can give false-green result in tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:157 (confidence: 95)
The new `createMultiDeleteMultiReadMulti` test starts a reader thread, deletes all groups on the main thread, sets `deletedAll.set(true)`, and then immediately calls `assertThat(caughtExceptions, Matchers.empty())`. There is no `Thread.join()` between flipping the flag and evaluating the assertion. The reader thread can still be inside `groups().groups(...)` or be about to append to `caughtExceptions` at the exact moment the assertion runs. `CopyOnWriteArrayList` guarantees memory visibility but not ordering relative to the assertion, so an exception thrown on the last in-flight call can be appended *after* the check. The test is structurally unable to reliably catch the NPE it is intended to regression-guard.
```suggestion
Thread readerThread = new Thread(() -> {
    while (!deletedAll.get()) {
        try {
            managedRealm.admin().groups().groups(null, 0, Integer.MAX_VALUE, true);
        } catch (Exception e) {
            caughtExceptions.add(e);
        }
    }
});
readerThread.setName("group-concurrent-reader");
readerThread.setDaemon(true);
readerThread.start();

groupUuids.forEach(groupUuid -> managedRealm.admin().groups().group(groupUuid).remove());
deletedAll.set(true);

readerThread.join(30_000);
assertFalse("Reader thread did not finish in time", readerThread.isAlive());
assertThat(caughtExceptions, Matchers.empty());
```

:red_circle: [testing] Test may not exercise the fixed code path — `getSubGroupsCount` invocation unverified in tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:157 (confidence: 88)
The production fix lives in `GroupAdapter.getSubGroupsCount()`, but the test only calls `groups().groups(null, 0, Integer.MAX_VALUE, true)` and trusts that the briefRepresentation listing path transitively invokes `getSubGroupsCount()`. There is no direct assertion or logging that proves the fixed method sits on this call path. If the admin REST listing short-circuits before reaching `getSubGroupsCount` (or only hits it for specific briefRepresentation values), the test passes identically with and without the fix, providing no regression protection. A direct unit test against `GroupAdapter` with a supplier returning `null` is needed alongside the integration test.
```suggestion
@Test
public void getSubGroupsCount_returnsNull_whenModelSupplierReturnsNull() {
    GroupAdapter adapter = new GroupAdapter(session, realm, /* cached */ null, () -> null);
    assertNull(adapter.getSubGroupsCount());
}

@Test
public void getSubGroupsCount_delegatesToModel_whenPresent() {
    GroupModel mockModel = mock(GroupModel.class);
    when(mockModel.getSubGroupsCount()).thenReturn(3L);
    GroupAdapter adapter = new GroupAdapter(session, realm, /* cached */ null, () -> mockModel);
    assertEquals(Long.valueOf(3L), adapter.getSubGroupsCount());
}
```

:red_circle: [cross-file-impact] `getSubGroupsCount()` can now return null — unboxing callers (e.g. `GroupUtils.populateSubGroupCount`) will NPE in model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java:274 (confidence: 85)
The method signature is `Long getSubGroupsCount()` but prior to this PR the Infinispan `GroupAdapter` never returned `null` — it either produced a value or threw NPE inside `getGroupModel()`. The fix now explicitly returns `null` when `modelSupplier.get()` returns null (a stale / evicted cache entry — exactly the scenario this PR is written to handle, so it will occur in production). All other `GroupModel` implementations (JPA etc.) still return non-null, so callers that obtained a `GroupModel` polymorphically — most importantly `GroupUtils.populateSubGroupCount`, which is invoked from `GroupResource`/`GroupsResource` — will surface-level auto-unbox or pass the value into arithmetic, producing a fresh NPE at the HTTP layer (500 response) instead of the old one inside the adapter. The NPE has been moved, not removed. Safer alternatives: return `0L` as a sentinel (semantically a deleted group has no subgroups), or make the null contract explicit at the interface level and null-guard every caller. Additionally, sibling methods in `GroupAdapter` still call `getGroupModel()` directly — they carry the same latent NPE and should share the same null-tolerant helper.
```suggestion
    @Override
    public Long getSubGroupsCount() {
        if (isUpdated()) return updated.getSubGroupsCount();
        GroupModel model = modelSupplier.get();
        return model == null ? 0L : model.getSubGroupsCount();
    }
```

## Improvements

:yellow_circle: [testing] Reader thread has no timeout — a hung HTTP call will hang the test suite indefinitely in tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:145 (confidence: 90)
The reader thread loops calling `managedRealm.admin().groups().groups(...)` until `deletedAll` flips. If the server stalls (slow CI node, or a deadlock triggered by the very race being tested), the call blocks indefinitely, the thread never terminates, and the whole test run hangs — there is no `join(timeout)`, no interrupt, no daemon flag, and no per-call read timeout. Combining `setDaemon(true)` with a bounded `join(timeout)` and an explicit `isAlive()` assertion turns a silent hang into a fast, actionable failure.
```suggestion
readerThread.setDaemon(true);
readerThread.start();
// ... deletions ...
deletedAll.set(true);
readerThread.join(15_000);
assertFalse("Reader thread did not finish in time", readerThread.isAlive());
```

:yellow_circle: [testing] No cleanup on test failure — 100 groups leak into subsequent tests if an assertion or deletion throws in tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:118 (confidence: 90)
100 groups are created inline and deleted in the same method body. If `fail(...)` is hit during creation, or any mid-loop deletion throws, the remaining groups are never removed — there is no `@AfterEach` and no `try/finally`. Leaked groups will corrupt group-count assertions in later tests in the same class and can bleed across the suite when tests share realm state.
```suggestion
List<String> groupUuids = new ArrayList<>();
try {
    IntStream.range(0, CONCURRENT_GROUP_COUNT).forEach(groupIndex -> { /* create */ });
    // ... start reader thread, delete groups, assert ...
} finally {
    groupUuids.forEach(id -> {
        try { managedRealm.admin().groups().group(id).remove(); } catch (Exception ignored) {}
    });
}
```

## Risk Metadata
Risk Score: 27/100 (LOW) | Blast Radius: ~5 estimated importers across infinispan cache + services layer | Sensitive Paths: none
AI-Authored Likelihood: LOW

(7 additional findings below confidence threshold 85: null-return contract violation vs `GroupModel` interface, `GroupResource.getSubGroups` missing null guard, bypass of `getGroupModel()` invariants, single-reader flakiness window, `groupUuuids` typo, magic number 100, redundant removal of unused private helper.)
