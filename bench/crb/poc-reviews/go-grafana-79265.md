# PR Review — grafana/grafana #79265

**Title:** Anonymous: Add configurable device limit
**Head → Base:** `jguer/add-anon-device-limit` → `main`
**Files changed:** 11 (+105 / −43)

## Summary

11 files changed, 105 lines added, 43 lines deleted. 7 findings (2 critical, 3 improvements, 2 nitpicks).

Adds `auth.anonymous.device_limit` config and enforces it in the anonstore. Refactors `Anonymous.Authenticate` from an async fire-and-forget goroutine to a synchronous call so limit-exceeded can reject the request. Core logic is coherent, but the enforcement path has a TOCTOU race and the sync refactor silently regresses request-path performance and removes panic protection.

**Recommendation:** request-changes.

## Critical

:red_circle: [correctness] TOCTOU race allows exceeding the configured device limit under concurrent load in `pkg/services/anonymous/anonimpl/anonstore/database.go:122` (confidence: 92)

`CreateOrUpdateDevice` checks `CountDevices(...)` and then (when under the limit) falls through to an `INSERT` in a separate DB round-trip. There is no surrounding transaction, no `SELECT ... FOR UPDATE`, no unique constraint check against the count, and no advisory lock. Two concurrent anonymous requests can both read `count == limit-1`, both pass the gate, and both insert — exceeding `device_limit` by the number of concurrent inserters. This is precisely the scenario (high anonymous traffic) where the limit is supposed to matter. The bug is also silent: the user sees success; only operators notice when the table grows past `device_limit`.

```suggestion
// Option A: push the gate into the INSERT itself so it's atomic, e.g. a conditional INSERT
// (INSERT ... SELECT ... WHERE (SELECT count(*) FROM anon_device WHERE updated_at BETWEEN ...) < ?)
// with the same UTC window used by CountDevices, and rely on the rowsAffected result to
// distinguish "inserted" vs "limit reached".
//
// Option B: wrap the count + insert in s.sqlStore.WithTransactionalDbSession with an
// appropriate isolation level (SERIALIZABLE on postgres/mysql) and retry on serialization
// failure.
```

References: OWASP "Race Conditions", CWE-362.

---

:red_circle: [correctness] Removing the goroutine turns anonymous auth into a blocking DB call on every request and drops panic protection in `pkg/services/anonymous/anonimpl/client.go:44-54` (confidence: 88)

The previous implementation tagged the device in a background goroutine with a detached `context.Background()`, a 2-minute timeout, and a `defer recover()` panic handler. The new code calls `a.anonDeviceService.TagDevice(ctx, ...)` directly on the request goroutine, using the inbound `ctx`. Two behavioral regressions hide inside what looks like a simple refactor:

1. **Request-path latency / availability.** Every anonymous HTTP request now waits for at least one DB round-trip (two when the limit is configured: `CountDevices` + `INSERT`/`UPDATE`). Under DB slowness, this is now user-visible latency on the auth path; under DB outage, anonymous browsing fails instead of degrading gracefully as it did before. The local cache in `impl.go` masks this for repeat devices, but first-hit-per-TTL still pays the cost.
2. **Panic safety.** The `defer recover()` that previously swallowed panics inside `TagDevice` is gone. A panic in anonstore code now escapes into the HTTP handler. Go's `net/http` recovers at the server level, but the request will be terminated rather than silently logged, which is a net behavioral change for anonymous users.

The sync refactor is necessary to surface `ErrDeviceLimitReached`, but the other two concerns were load-bearing. Consider: call `TagDevice` synchronously only on the limit-check path, keep the goroutine for the happy path; or keep the goroutine and use a side-channel (e.g., a result written to a shared map) to short-circuit auth when the last observation was "limit reached".

```suggestion
// Minimal fix: preserve the recover() and a decoupled context, but wait for the result
// so ErrDeviceLimitReached can propagate:
done := make(chan error, 1)
go func() {
    defer func() {
        if p := recover(); p != nil {
            a.log.Warn("Tag anon session panic", "err", p)
            done <- nil
        }
    }()
    newCtx, cancel := context.WithTimeout(context.Background(), timeoutTag)
    defer cancel()
    done <- a.anonDeviceService.TagDevice(newCtx, httpReqCopy, anonymous.AnonDeviceUI)
}()
if err := <-done; err != nil {
    if errors.Is(err, anonstore.ErrDeviceLimitReached) {
        return nil, err
    }
    a.log.Warn("Failed to tag anonymous session", "error", err)
}
```

## Improvements

:yellow_circle: [consistency] Duplicate `anonymousDeviceExpiration` constant in two packages in `pkg/services/anonymous/anonimpl/anonstore/database.go:57` and `pkg/services/anonymous/anonimpl/api/api.go:192` (confidence: 95)

`const anonymousDeviceExpiration = 30 * 24 * time.Hour` is now declared in both `anonstore` and `api`. A future change to the window will silently go out of sync (`api` uses it for `ListDevices` time filter; `anonstore` uses it for `updateDevice`'s `BETWEEN` clause and for `CreateOrUpdateDevice`'s count window). Promote to a single exported constant in `anonstore` and import it from `api`, or move to `pkg/services/anonymous`.

```suggestion
// In anonstore/database.go:
const AnonymousDeviceExpiration = 30 * 24 * time.Hour

// In api/api.go:
fromTime := time.Now().Add(-anonstore.AnonymousDeviceExpiration)
```

---

:yellow_circle: [correctness] `updateDevice` returns `ErrDeviceLimitReached` for any `rowsAffected == 0`, conflating "limit reached + unknown device" with "known device whose `updated_at` fell outside the window" in `pkg/services/anonymous/anonimpl/anonstore/database.go:108` (confidence: 78)

Inside the limit-reached branch, `updateDevice` fires an `UPDATE ... WHERE device_id = ? AND updated_at BETWEEN ? AND ?` and treats `rowsAffected == 0` as `ErrDeviceLimitReached`. This is correct for a brand-new device (the common case, matching the test), but it also fires when the device *does* exist but was last seen >30 days ago (updated_at outside the `BETWEEN` window). In that second case we return "limit reached" even though the device was previously known — meaning a returning user who was inactive for a month gets rejected by an error whose name implies "capacity", not "your record expired". Consider either (a) issuing a first `SELECT ... WHERE device_id = ?` and returning a distinct `ErrDeviceExpired` vs `ErrDeviceLimitReached`, or (b) widening the `BETWEEN` window so expired rows still match.

---

:yellow_circle: [testing] `TestIntegrationBeyondDeviceLimit` doesn't cover the "update existing device still works when limit reached" path in `pkg/services/anonymous/anonimpl/anonstore/database_test.go:153` (confidence: 85)

The new test only asserts that a *new* device beyond the limit returns `ErrDeviceLimitReached`. The core promise of this PR — that existing devices continue to refresh their `updated_at` once the limit is hit — is untested. Add a sub-case that seeds `limit` devices, then re-tags one of those existing device IDs and asserts `NoError` plus an updated `updated_at`. Without this, a regression in `updateDevice`'s `WHERE` clause would not be caught.

```suggestion
t.Run("existing device continues to refresh at limit", func(t *testing.T) {
    // seed one device under a limit of 1
    first := &Device{DeviceID: "d1", ClientIP: "1.1.1.1", UserAgent: "ua", UpdatedAt: time.Now().Add(-time.Hour)}
    require.NoError(t, anonDBStore.CreateOrUpdateDevice(context.Background(), first))

    // re-tagging d1 should succeed, not hit ErrDeviceLimitReached
    first.UpdatedAt = time.Now()
    require.NoError(t, anonDBStore.CreateOrUpdateDevice(context.Background(), first))
})
```

## Nitpicks

:white_circle: [cross-file-impact] Frontend `anonymousDeviceLimit` typed `number | undefined` but server always sends `0` when unset in `pkg/api/dtos/frontend_settings.go:195` and `packages/grafana-data/src/types/config.ts:200` (confidence: 72)

Go `int64` with no `omitempty` tag marshals the zero value as `0`, so the frontend will see `anonymousDeviceLimit: 0` when the admin did not configure a limit — yet the TS field is declared `number | undefined` and the runtime default is `undefined`. Frontend code cannot distinguish "0 = no limit set" from "0 = explicitly disabled" without extra logic, and the type lies about what the server actually sends. Either (a) add `,omitempty` on the Go tag and keep the `| undefined` TS type, or (b) drop `| undefined` and document that 0 means "no limit".

---

:white_circle: [consistency] `anonymousDeviceLimit = undefined` in the TS runtime bootstrap has no explicit type annotation in `packages/grafana-runtime/src/config.ts:97` (confidence: 60)

The sibling field `anonymousEnabled = false` infers `boolean`, but `anonymousDeviceLimit = undefined` infers `undefined` and will fight the `GrafanaConfig` interface signature. Annotate explicitly: `anonymousDeviceLimit: number | undefined = undefined;`.

## Risk Metadata

Risk Score: 62/100 (MEDIUM-HIGH) | Blast Radius: auth.anonymous flow (11 files, request-path) | Sensitive Paths: `pkg/services/anonymous/**`, `pkg/api/frontendsettings.go`, `pkg/setting/setting.go`
AI-Authored Likelihood: LOW

Factors:
- sensitive_path (auth) — 80/100: changes run on every anonymous request and can reject auth
- request_path_latency — 70/100: sync DB round-trip added to hot path
- concurrency — 75/100: new check-then-act without a transaction
- test_coverage — 55/100: limit path has a single happy-path test, no concurrency or update-at-limit test
- blast_radius — 60/100: 11 files, but behavior concentrated in one service
- change_size — 30/100: +105/−43 is small

## Metadata

- Review duration: ~81 s
- Mode: local analysis (no upstream comment posted)
- Existing upstream state: PR already approved by `@eleijonmarck` on 2023-12-12; a prior automated review by `@mfeuerstein` on 2026-04-10 marked it "approved — 0 high-severity issues". This review disagrees with that assessment on the race condition and the sync-refactor regression.
