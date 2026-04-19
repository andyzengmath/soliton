## Summary
4 files changed, 51 lines added, 11 lines deleted. 5 findings (3 critical, 2 improvements).
Fix adds a null-check in `GroupAdapter.getSubGroupsCount()` to avoid NPE on concurrent group deletion, and adds a regression test — but the test has race/thread-lifecycle defects that can silently hide the very regression it is meant to catch.

## Critical
:red_circle: [testing] Assertion races reader thread — in-flight exceptions lost in tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:145 (confidence: 95)
The sequence `deletedAll.set(true)` → `assertThat(caughtExceptions, Matchers.empty())` does not wait for the reader thread to finish. The reader checks `!deletedAll.get()` at the top of its loop, so it may be mid-request when the flag flips. Any exception thrown during that in-flight `groups()` call is appended to `caughtExceptions` after `assertThat` has already evaluated the list — those exceptions are permanently lost, and a real NPE regression reintroduced by a future commit can silently pass. The thread is also never joined, so if the assertion fails (or passes) while the thread is alive, it keeps issuing HTTP calls against whatever realm state exists during subsequent tests.
```suggestion
Thread reader = new Thread(() -> {
    while (!deletedAll.get()) {
        try {
            managedRealm.admin().groups().groups(null, 0, Integer.MAX_VALUE, true);
        } catch (Exception e) {
            caughtExceptions.add(e);
        }
    }
}, "group-reader");
reader.setDaemon(true);
reader.start();

groupUuids.forEach(uuid -> managedRealm.admin().groups().group(uuid).remove());
deletedAll.set(true);

reader.join(30_000);
assertFalse("Reader thread did not finish in time", reader.isAlive());
assertThat(caughtExceptions, Matchers.empty());
```

:red_circle: [testing] Thread leakage — bare Thread with no daemon flag, no name, no join in tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:130 (confidence: 92)
The reader thread is created with `new Thread(...)` and started, but never joined and never marked as a daemon thread. If the test method returns (pass or fail) while the thread is still alive — easily possible given the race described above — it continues issuing HTTP requests against the server, potentially polluting state observed by subsequent tests in the same class or Surefire fork. The absence of a name also makes heap/thread dumps unreadable during incident investigation. Pairing `setDaemon(true)` + `setName(...)` + a bounded `join(timeout)` is the minimum hygiene for test-owned worker threads.
```suggestion
Thread reader = new Thread(readerBody, "group-reader");
reader.setDaemon(true);
reader.start();
// ... work ...
reader.join(30_000);
```

:red_circle: [testing] No @Timeout — a hung test will stall CI indefinitely in tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:118 (confidence: 90)
The test intentionally exercises a concurrency edge case but has no `@Timeout` annotation. If the reader deadlocks or a delete hangs waiting on a server response, the method never returns and CI hangs until the global Surefire timeout fires (often 1h or unbounded). A per-test timeout is the appropriate safety net for any test that spawns a background thread.
```suggestion
@Test
@Timeout(value = 60, unit = TimeUnit.SECONDS)
public void createMultiDeleteMultiReadMulti() {
    // ...
}
```

## Improvements
:yellow_circle: [correctness] getSubGroupsCount() silently returns null where callers likely expect a count in model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java:274 (confidence: 88)
Before this change `getGroupModel().getSubGroupsCount()` would throw an NPE when the underlying group had been evicted. The fix replaces that with `return model == null ? null : model.getSubGroupsCount();`. Because the return type is the boxed `Long`, static analysis will not flag callers that auto-unbox into a primitive `long`, pass the value into arithmetic, or serialize it as a JSON number — those sites will now either throw NPE further up the stack (harder to diagnose than here) or produce silently incorrect pagination / display values. Returning `0L` is both null-safe and semantically accurate: a group that has been concurrently deleted has zero subgroups from the perspective of the caller that still holds a stale adapter. If the design instead wants to signal "no longer exists," a purpose-built `ModelIllegalStateException` would be clearer than a `null` Long.
```suggestion
@Override
public Long getSubGroupsCount() {
    if (isUpdated()) return updated.getSubGroupsCount();
    GroupModel model = modelSupplier.get();
    return model == null ? 0L : model.getSubGroupsCount();
}
```

:yellow_circle: [testing] Test can pass without exercising the regression path in tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:130 (confidence: 87)
The test relies on thread-scheduler luck: the reader must actually issue a `groups()` call while at least one delete is in flight, otherwise `caughtExceptions` is naturally empty and the assertion passes without having tested anything. There is no `CountDownLatch` / `CyclicBarrier` to guarantee overlap, and no counter asserting the reader ran at all. On a slow CI worker this test can silently green on the very commit that reintroduces the NPE. Two small changes fix this: (a) block the reader on a latch until the first delete starts; (b) track a counter of reads performed during the deletion window and assert it is positive.
```suggestion
CountDownLatch firstDeleteStarted = new CountDownLatch(1);
AtomicInteger concurrentReads = new AtomicInteger();

Thread reader = new Thread(() -> {
    try { firstDeleteStarted.await(); } catch (InterruptedException ie) { return; }
    while (!deletedAll.get()) {
        try {
            managedRealm.admin().groups().groups(null, 0, Integer.MAX_VALUE, true);
            concurrentReads.incrementAndGet();
        } catch (Exception e) {
            caughtExceptions.add(e);
        }
    }
}, "group-reader");
reader.setDaemon(true);
reader.start();

for (int i = 0; i < groupUuids.size(); i++) {
    if (i == 0) firstDeleteStarted.countDown();
    managedRealm.admin().groups().group(groupUuids.get(i)).remove();
}
deletedAll.set(true);
reader.join(30_000);

assertThat("reader never overlapped with deletion window", concurrentReads.get(), Matchers.greaterThan(0));
assertThat(caughtExceptions, Matchers.empty());
```

## Risk Metadata
Risk Score: 18/100 (LOW) | Blast Radius: small — 3 production files (2 infinispan cache classes, 1 services util) + 1 test | Sensitive Paths: none
AI-Authored Likelihood: LOW
