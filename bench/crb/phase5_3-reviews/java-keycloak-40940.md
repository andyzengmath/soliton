## Summary
4 files changed, 53 lines added, 12 lines deleted. 5 findings (1 critical, 4 improvements, 0 nitpicks).
Fix prevents NPE on a concurrently-deleted group in the Infinispan cache layer; the new regression test contains a race that can hide the very failure it is meant to catch.

## Critical
:red_circle: [correctness] Reader thread not joined before assertion; race can mask the regression in `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:140` (confidence: 92)
The test spawns a `new Thread(...)` that loops `while (!deletedAll.get())` calling `groups().groups(...)` and stuffs any exception into `caughtExceptions`. The main thread sets `deletedAll = true` immediately after the last `remove()`, then runs `assertThat(caughtExceptions, Matchers.empty())`. There is no `thread.join()` between the flag flip and the assertion, so:
1. An HTTP request that started just before `deletedAll.set(true)` may still be in flight while the assertion executes — its exception lands in `caughtExceptions` after the test has already passed, silently dropping the failure.
2. The reader thread can also exit *before* the deletes finish if scheduling delays it, narrowing the contention window the test exists to exercise.
Both modes turn this into a flaky/false-negative test for the very NPE that `GroupAdapter.getSubGroupsCount()` is being patched to fix. Join the reader thread (with a bounded timeout) before asserting, and assert that the thread actually performed at least one read while deletes were running.
```suggestion
        Thread reader = new Thread(() -> {
            while (!deletedAll.get()) {
                try {
                    managedRealm.admin().groups().groups(null, 0, Integer.MAX_VALUE, true);
                } catch (Exception e) {
                    caughtExceptions.add(e);
                }
            }
        });
        reader.start();

        // delete groups
        groupUuuids.forEach(groupUuid -> managedRealm.admin().groups().group(groupUuid).remove());
        deletedAll.set(true);
        reader.join(30_000);
        if (reader.isAlive()) {
            reader.interrupt();
            fail("Reader thread did not terminate within 30s");
        }

        assertThat(caughtExceptions, Matchers.empty());
```

## Improvements
:yellow_circle: [correctness] `GroupAdapter.getSubGroupsCount()` silently returns `null` when the group disappears, leaking through API in `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java:274` (confidence: 88)
The fix changes the failure mode from `NullPointerException` to a `null` return value when `modelSupplier.get()` resolves to `null` (e.g. group was just deleted). `GroupModel#getSubGroupsCount()` is declared as boxed `Long`, so callers in `GroupResource`, `org.keycloak.admin.ui.rest.GroupsResource` and the JSON serializer for `GroupRepresentation` may auto-unbox the result and trigger an NPE further from the root cause, or serialize `"subGroupCount": null` into the admin REST payload. Either:
- Treat a vanished group as zero subgroups (`return model == null ? 0L : model.getSubGroupsCount();`) for parity with `getSubGroupsStream()` which would yield an empty stream, OR
- Throw a typed `ModelException` here and let callers translate it, rather than letting `null` propagate through the cache adapter contract.
The current behavior also diverges from the sibling `getSubGroupsStream(...)` overrides on the same adapter, which still call `getGroupModel()` (NPE-prone) — leaving the cache adapter in a half-fixed state.
```suggestion
        if (isUpdated()) return updated.getSubGroupsCount();
        GroupModel model = modelSupplier.get();
        return model == null ? 0L : model.getSubGroupsCount();
```

:yellow_circle: [silent-failure] Reader thread swallows exceptions with no logging or context in `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:135` (confidence: 86)
```java
} catch (Exception e) {

    caughtExceptions.add(e);
}
```
The blank line inside the `catch` block (and absence of any `LOG.warn` / `e.printStackTrace`) makes CI failures opaque: when the assertion fails, a developer sees `Expected: an empty collection but: <[org.jboss.resteasy.client.exception.ResteasyHttpException, ...]>` with no stack traces, no request URLs, and no timing. Add `Logger.getLogger(GroupTest.class).warn("read failed", e)` (or include `e.toString()` plus the iteration index in the failure message via a custom matcher) so flakes are diagnosable from the build log alone.
```suggestion
                } catch (Exception e) {
                    LOG.warnf(e, "Concurrent group read failed during delete");
                    caughtExceptions.add(e);
                }
```

:yellow_circle: [correctness] Busy-loop reader has no sleep, no iteration cap, and no overall test timeout in `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:128` (confidence: 80)
`while (!deletedAll.get())` hammers `groups().groups(null, 0, Integer.MAX_VALUE, true)` with no back-off. Two problems:
1. Under CI load this can DoS the test Keycloak instance, starving the delete loop and hiding the contention this test is supposed to exercise.
2. If the delete loop hangs (e.g. Infinispan replication stall), neither the test nor the reader has a timeout — the entire build hangs until the surefire wall-clock kills it, and the JUnit `@Test` declaration carries no `@Timeout`.
Add `@Timeout(value = 60, unit = TimeUnit.SECONDS)` on the test, and either limit the read loop to N iterations or insert a small `Thread.sleep(5)` between requests.

:yellow_circle: [consistency] Raw `new Thread(...).start()` violates the rest of the suite's `ExecutorService`-based concurrency style in `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java:120` (confidence: 78)
Other concurrent tests in `tests/base` (e.g. `LoginConcurrencyTest`, `ClientRegistrationConcurrencyTest`) use `Executors.newFixedThreadPool(...)` + `CompletableFuture` so that exceptions surface via `Future.get()` and the executor is `shutdownNow()` in a `finally`. The current pattern leaks the reader thread on test failure (it remains alive until the JVM exits, since the assertion throws before `deletedAll.set(true)` would otherwise stop it on a delete-loop exception path). Switch to an `ExecutorService` with try/finally cleanup, or at minimum mark the thread daemon (`reader.setDaemon(true)`) so a leak doesn't block JVM shutdown.

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: cache adapter (`GroupAdapter`) is on the read path of every group lookup in Infinispan-backed deployments; test-only changes do not ship | Sensitive Paths: none
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold)
