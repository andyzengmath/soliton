## Summary
4 files changed, 51 lines added, 11 lines deleted. 3 findings (1 critical, 2 improvements, 0 nitpicks).
NPE fix for `GroupAdapter.getSubGroupsCount()` is narrowly scoped — the same concurrent-deletion race affects other methods on the adapter, and the new concurrency test has a join race that can mask failures.

## Critical
:red_circle: [correctness] Partial fix — other `GroupAdapter` methods have the same concurrent-deletion NPE in `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java`:274 (confidence: 88)
`getSubGroupsCount()` is the only method in `GroupAdapter` hardened against `modelSupplier.get()` returning `null` after a concurrent group deletion. The issue being fixed (#40368) is a race between reads and deletes in the Infinispan cache; that race is not specific to `getSubGroupsCount`. Peer methods on the same adapter (for example `getName`, `getDescription`, `getParentId`, `getType`, `getSubGroupsStream`, `getRealm`, `getAttributes`, `getRoleMappingsStream`) all dereference the result of `getGroupModel()` / `modelSupplier.get()` without a null check and will throw the same NPE under the exact workload exercised by the new test. The concurrency test only asserts on `groups()` listing calls, which happens to route through `getSubGroupsCount`, so it will pass even though siblings remain broken. Either centralize the null handling (e.g. make `getGroupModel()` return a sentinel/optional and handle it once) or apply the same pattern to every method that touches `modelSupplier.get()`.
```suggestion
    // Apply consistently to every method that reads through modelSupplier.get().
    // Example for getName():
    @Override
    public String getName() {
        if (isUpdated()) return updated.getName();
        GroupModel model = modelSupplier.get();
        return model == null ? null : model.getName();
    }
```

## Improvements
:yellow_circle: [testing] Reader thread is not joined before assertion — test is racy in `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java`:137 (confidence: 90)
The test sets `deletedAll.set(true)` and immediately runs `assertThat(caughtExceptions, Matchers.empty())`, but the reader thread is never joined. The reader may still be mid-request (or iterating back through its `while` loop after the volatile flag flip) when the assertion fires, so an NPE raised in the reader *after* the assertion point will silently land in `caughtExceptions` without failing the build — the test can regress without anyone noticing. The thread is also non-daemon and unnamed, which leaks it into the JUnit test runner if the test aborts. Capture the thread reference and `join(timeout)` it before the assertion, and mark it as daemon / give it a descriptive name.
```suggestion
        Thread reader = new Thread(() -> {
            while (!deletedAll.get()) {
                try {
                    managedRealm.admin().groups().groups(null, 0, Integer.MAX_VALUE, true);
                } catch (Exception e) {
                    caughtExceptions.add(e);
                }
            }
        }, "group-concurrent-read");
        reader.setDaemon(true);
        reader.start();

        groupUuids.forEach(groupUuid -> managedRealm.admin().groups().group(groupUuid).remove());
        deletedAll.set(true);
        reader.join(TimeUnit.SECONDS.toMillis(30));
        assertThat(caughtExceptions, Matchers.empty());
```

:yellow_circle: [correctness] `getSubGroupsCount()` now returns `null`, which is a contract change callers may not handle in `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java`:275 (confidence: 85)
`GroupModel#getSubGroupsCount()` returns `Long`. Before this change the cached adapter always returned a non-null value (or threw NPE). After the change, a caller that receives a concurrently-deleted group back via `groups(...)` listing will get `null` from `getSubGroupsCount()`. Any caller that auto-unboxes the result (`long count = group.getSubGroupsCount();`) will now throw a different NPE on the unbox instead of the original `getGroupModel().getSubGroupsCount()` NPE — we've moved the failure, not removed it. Either document on the `GroupModel` interface that `null` means "group was concurrently deleted" and audit callers, or return `0L` / throw a dedicated `GroupDeletedException` that listing code can skip. Grep the tree for `.getSubGroupsCount()` usages before merging.
```suggestion
    @Override
    public Long getSubGroupsCount() {
        if (isUpdated()) return updated.getSubGroupsCount();
        GroupModel model = modelSupplier.get();
        // Group was concurrently removed from the cache — treat as empty rather
        // than propagating null, which would NPE on auto-unbox in callers.
        return model == null ? 0L : model.getSubGroupsCount();
    }
```

## Risk Metadata
Risk Score: 32/100 (LOW) | Blast Radius: `GroupAdapter` is on the admin-group read path; `GroupModel` interface contract affects every backing provider | Sensitive Paths: none hit
AI-Authored Likelihood: LOW (typo `groupUuuids`, inline thread usage, and PR description style are consistent with human authorship)
