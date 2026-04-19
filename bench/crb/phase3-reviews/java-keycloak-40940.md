# PR #40940 — Fix NPE when accessing group concurrently

**Repo:** keycloak/keycloak
**Base:** main ← **Head:** 40368-NPE-concurrent-groups-deletion
**Closes:** #40368
**Scope:** 4 files, +51 / −11

## Summary

4 files changed, 51 lines added, 11 lines deleted. 7 findings (0 critical, 4 improvements, 3 nitpicks).
Narrow, correct fix for a concurrent-delete NPE in the Infinispan group cache adapter, plus a regression test. The production change is sound; the test and the scope of the fix are the main review surfaces.

## Improvements

:yellow_circle: [correctness] Null-safety fix is inconsistent with the rest of `GroupAdapter` in `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java`:274 (confidence: 85)

The patch changes **only** `getSubGroupsCount()` to call `modelSupplier.get()` directly and null-check the result, while every other accessor on `GroupAdapter` continues to go through `getGroupModel()` (which presumably throws / NPEs when the underlying cache entry has been evicted by a concurrent delete). That means any concurrent caller of `getName()`, `getId()`, `getParentId()`, `getSubGroupsStream(...)`, `getRoleMappings*`, `getAttributes()`, etc. will still hit the same class of NPE that #40368 describes — the fix only addresses the one call path that happened to produce the reported stack trace.

Either (a) push the null-check into `getGroupModel()` and decide a single policy (return `null`, throw a typed `ModelIllegalStateException`, or return a tombstone) so every accessor benefits, or (b) explicitly document in the PR why this one method is special. As written, the bug class is not closed — only one instance of it is.

```suggestion
    @Override
    public Long getSubGroupsCount() {
        if (isUpdated()) return updated.getSubGroupsCount();
        GroupModel model = modelSupplier.get();
        return model == null ? null : model.getSubGroupsCount();
    }
    // TODO(#40368): same guard is needed on every other delegating accessor;
    // consider centralizing in getGroupModel().
```

:yellow_circle: [correctness] `getSubGroupsCount()` now silently returns `null` on a deleted group in `model/infinispan/src/main/java/org/keycloak/models/cache/infinispan/GroupAdapter.java`:275 (confidence: 80)

Returning `null` from a `Long`-returning count is a real contract change. Many callers will auto-unbox (`long count = group.getSubGroupsCount();` → NPE), and most `GroupModel` implementations return a non-null primitive-like value today. You are trading a `NullPointerException` inside the cache layer for a `NullPointerException` at the call site, just further from the cause.

Two reasonable alternatives:
1. Return `0L` (the entity is gone; it has no subgroups) — matches what most callers will reasonably do anyway.
2. Throw a dedicated `ModelIllegalStateException("group was deleted")` so callers can distinguish "no subgroups" from "this handle is stale".

Whichever is chosen, please add a Javadoc on the method documenting the new post-delete semantics, since this is now a public contract of the adapter.

:yellow_circle: [testing] Regression test leaks a background thread on failure in `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java`:117 (confidence: 90)

The new `createMultiDeleteMultiReadMulti` starts an unnamed `Thread` that spins on `while (!deletedAll.get())`. `deletedAll.set(true)` is only reached if the `groupUuuids.forEach(... remove())` loop completes successfully. If **any** `remove()` throws (or the surrounding `managedRealm` is torn down early, or the test is interrupted), the background thread will loop forever in the test JVM and hold references to the Keycloak admin client for the rest of the suite.

```suggestion
        Thread reader = new Thread(() -> {
            while (!deletedAll.get()) {
                try {
                    managedRealm.admin().groups().groups(null, 0, Integer.MAX_VALUE, true);
                } catch (Exception e) {
                    caughtExceptions.add(e);
                }
            }
        }, "group-concurrent-reader");
        reader.setDaemon(true);
        reader.start();

        try {
            groupUuuids.forEach(groupUuid -> managedRealm.admin().groups().group(groupUuid).remove());
        } finally {
            deletedAll.set(true);
            reader.join(TimeUnit.SECONDS.toMillis(30));
        }

        assertThat(caughtExceptions, Matchers.empty());
```

Key changes: daemon thread so it cannot block JVM shutdown, named thread for debuggability, `try/finally` around delete loop so `deletedAll` is always flipped, and an explicit bounded `join` before the assertion so the assertion sees the final state deterministically.

:yellow_circle: [testing] Test does not guarantee the race it claims to reproduce in `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java`:135 (confidence: 75)

On a warm JVM / fast CI box, the main thread can finish all 100 `remove()` calls before the background thread completes its first `groups(...)` round-trip, in which case the test passes trivially whether or not the fix is present. There is nothing in the test that proves the reader actually observed a partially-deleted state during the run.

Consider one of:
- Assert `reader ran at least N iterations` by incrementing a counter in the reader loop and requiring `counter.get() > 0` (ideally `> some small number`).
- Use a `CyclicBarrier` so the reader thread and the deleter thread both wait at a rendezvous, then fire together.
- Increase group count to a level that empirically overlaps on CI (e.g. 500–1000) and wrap each `remove()` in a brief `Thread.yield()` to widen the window.

Without one of these, the test is only a *probabilistic* regression guard and can silently regress to useless.

## Nitpicks

:white_circle: [consistency] Variable name `groupUuuids` has three u's in `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java`:119

Typo — should be `groupUuids` (or `groupIds`, to match the rest of the file's convention).

:white_circle: [consistency] Background `Thread` has no name in `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java`:131

Unnamed threads in integration tests make thread-dumps during CI flakes unreadable. Name it (e.g. `"group-concurrent-reader"`) — see suggested fix above.

:white_circle: [consistency] Stray blank line inside `catch` block in `tests/base/src/test/java/org/keycloak/tests/admin/group/GroupTest.java`:138

Cosmetic: the blank line between `} catch (Exception e) {` and `caughtExceptions.add(e);` doesn't match the surrounding style. Also, `catch (Exception e)` is wider than necessary — narrow to the admin-client exception types actually thrown, or at minimum re-interrupt on `InterruptedException` so the reader can be shut down by `Thread.interrupt()` if future cleanup wants to.

## Observations (not findings)

- **`CachedGroup.java`**: the `@Override` annotation addition on `getRealm()` is a pure static-analysis improvement. Non-functional, safe, good hygiene.
- **`GroupUtils.java`**: removing `groupMatchesSearchOrIsPathElement` is a dead-code cleanup — verified by diff that the method was `private` and had no remaining call sites after earlier refactors. Safe.
- **Existing PR state**: the PR is already merged / approved by a Keycloak contributor (`pedroigor`), and a maintainer has asked that automated AI review comments not be posted to this (closed) PR. This review is produced for local CRB benchmark evaluation only and must not be posted upstream.

## Risk Metadata

Risk Score: 35/100 (LOW–MEDIUM) | Blast Radius: model/infinispan cache layer (hot path, but change is minimal and additive) + one new integration test | Sensitive Paths: none (no auth/, security/, payment/, credentials, migrations)
AI-Authored Likelihood: LOW (small, targeted, idiomatic Keycloak patch; test structure has human-typical rough edges like the `Uuuids` typo)

## Recommendation

**needs-discussion** — the production change is correct and minimal, but the fix pattern should be generalized across `GroupAdapter` (or explicitly scoped), the `null` return contract should be documented or replaced with `0L`, and the regression test should be hardened (thread lifecycle + actual race guarantee) before it's relied on as a regression signal.

---

_Generated by soliton pr-review skill — local CRB benchmark run, not posted to upstream._
