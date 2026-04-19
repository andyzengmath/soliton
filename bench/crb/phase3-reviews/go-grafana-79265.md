## Summary
11 files changed, 105 lines added, 41 lines deleted. 9 findings (2 critical, 5 improvements, 2 nitpicks).
Adds a configurable `auth.anonymous.device_limit` that caps tracked anonymous devices, but the enforcement path has a TOCTOU race, conflates "no matching device" with "limit reached", and quietly converts a fire-and-forget goroutine in the auth hot path into a synchronous DB call whose errors can now fail authentication.

## Critical

:red_circle: [correctness] TOCTOU race in `CreateOrUpdateDevice` lets concurrent requests exceed the device limit in `pkg/services/anonymous/anonimpl/anonstore/database.go:118` (confidence: 92)

The limit check is a classic check-then-act with no transaction, row lock, or unique constraint holding the invariant:

```go
if s.deviceLimit > 0 {
    count, err := s.CountDevices(ctx, ...)
    if err != nil { return err }
    if count >= s.deviceLimit {
        return s.updateDevice(ctx, device)
    }
}
// ... unguarded INSERT/UPSERT proceeds here
```

Two anonymous requests that each see `count == deviceLimit - 1` will both fall through to the INSERT branch, producing `deviceLimit + 1` rows. Under bursts (the exact workload this limit exists to defend against — scraping, bots, load spikes) the cap is silently exceeded, which defeats the feature's purpose and leaves operators believing they have a hard ceiling.

```suggestion
// Enforce the limit inside a single transaction, or rely on a DB-side constraint
// (e.g. an atomic "UPSERT WHERE (SELECT count(*) FROM anon_device WHERE ...) < ?"
// or a SELECT ... FOR UPDATE around count + insert) so the invariant is held
// under concurrency.
```

References:
- https://cwe.mitre.org/data/definitions/367.html

:red_circle: [cross-file-impact] Removing the async `go func()` wrapper in `Authenticate` makes every anonymous auth request block on DB I/O and propagates DB errors to the auth decision in `pkg/services/anonymous/anonimpl/client.go:44` (confidence: 90)

Before this PR, `TagDevice` ran in a goroutine with a 2-minute timeout and a panic recover; its errors only produced a warning log, never an auth failure. After this PR:

```go
if err := a.anonDeviceService.TagDevice(ctx, httpReqCopy, anonymous.AnonDeviceUI); err != nil {
    if errors.Is(err, anonstore.ErrDeviceLimitReached) {
        return nil, err
    }
    a.log.Warn("Failed to tag anonymous session", "error", err)
}
```

Two regressions are bundled here:

1. **Latency / availability on the auth hot path.** Anonymous auth now waits for `CountDevices` + `INSERT`/`UPDATE` against `anon_device` on every request that missed the 29-minute local cache. Under DB pressure this synchronously stalls request handling; the prior design was fire-and-forget precisely to avoid this.
2. **`ErrDeviceLimitReached` fails authentication.** The comment-less `return nil, err` short-circuits the caller with an error the outer authn stack will treat as an auth failure, not a graceful "tag failed, still anonymous." Combined with finding #3 (the error is over-broadly raised), a stale device or mistimed clock can deny anonymous access entirely — a behavioral change that isn't discussed in the PR description and has no rollback toggle.

Additionally, the `Authenticate` method now takes `ctx` instead of creating a fresh `context.Background()` with a timeout, so a client-cancelled request aborts the DB write mid-flight. That's usually desirable, but it's another silent semantic change.

```suggestion
// Option A (preserves async behavior, still enforces limit):
//   - Keep TagDevice in a goroutine with timeout + recover.
//   - Check ErrDeviceLimitReached via a fast in-memory counter updated by the
//     goroutine, and reject *new* anon sessions synchronously only when the
//     counter indicates the cap is already full.
//
// Option B (accept the sync cost, but separate tagging failure from limit):
//   - Only return err when errors.Is(err, anonstore.ErrDeviceLimitReached).
//   - Wrap the call in a short timeout so DB stalls don't block auth forever.
//   - Add a feature toggle so the sync path can be disabled in prod if it
//     turns out to be too expensive.
```

References:
- https://grafana.com/docs/grafana/latest/setup-grafana/configure-security/configure-authentication/anonymous-auth/

## Improvements

:yellow_circle: [correctness] `updateDevice` returns `ErrDeviceLimitReached` when the device simply doesn't exist in `pkg/services/anonymous/anonimpl/anonstore/database.go:86` (confidence: 88)

The sentinel is raised purely from `rowsAffected == 0`:

```go
result, err := dbSession.Exec(args...)
// ...
if rowsAffected == 0 {
    return ErrDeviceLimitReached
}
```

But `rowsAffected == 0` occurs for at least three distinct reasons under the WHERE clause `device_id = ? AND updated_at BETWEEN ? AND ?`:

1. The device_id is genuinely new (not in the table) — this is the "limit reached, reject new device" case the PR intends.
2. The device_id exists but its `updated_at` is older than `anonymousDeviceExpiration` — i.e. a long-idle but legitimate returning user. This is conflated with "limit reached" and will now fail their auth (given finding #2).
3. The device_id exists but `updated_at` is in the future relative to the window (clock skew between app servers, or the caller passed a stale `UpdatedAt`).

Because the error leaks out through `Authenticate` → auth failure, operators will see limit-breach metrics / alerts for cases that aren't actually limit breaches, and idle users past the 30-day window will get rejected even when there's headroom under the cap.

```suggestion
// Disambiguate: only return ErrDeviceLimitReached when we've confirmed the
// device does not exist at all (or the caller is responsible for detecting
// new-vs-existing before falling through to updateDevice).
//
// e.g.:
//   exists, err := s.deviceExists(ctx, device.DeviceID)
//   if err != nil { return err }
//   if !exists {
//       return ErrDeviceLimitReached
//   }
//   // proceed with bounded UPDATE; rowsAffected == 0 here means stale window,
//   // not limit breach — return a different error or silently succeed.
```

:yellow_circle: [consistency] Duplicate `30 * 24 * time.Hour` constant under two different names in `pkg/services/anonymous/anonimpl/anonstore/database.go:15` (confidence: 95)

The PR introduces `anonymousDeviceExpiration = 30 * 24 * time.Hour` in `anonstore/database.go` while `api/api.go` already had (and still has, under a renamed symbol) the same literal:

```go
// anonstore/database.go
const anonymousDeviceExpiration = 30 * 24 * time.Hour

// api/api.go
const anonymousDeviceExpiration = 30 * 24 * time.Hour
```

The PR actually renames `thirtyDays` → `anonymousDeviceExpiration` in `api.go`, which is a drive-by refactor that keeps the value but doesn't deduplicate it. Two copies of a business-critical retention constant will drift; one should live in a shared package (e.g. exported from `anonstore`) and the other should import it.

```suggestion
// In anonstore/database.go:
const AnonymousDeviceExpiration = 30 * 24 * time.Hour // exported

// In api/api.go:
fromTime := time.Now().Add(-anonstore.AnonymousDeviceExpiration)
```

:yellow_circle: [correctness] `TagDevice` now returns `tagDeviceUI` errors; any transient DB hiccup fails anon auth in `pkg/services/anonymous/anonimpl/impl.go:144` (confidence: 82)

The diff changes:

```go
err = a.tagDeviceUI(ctx, httpReq, taggedDevice)
if err != nil {
    a.log.Debug("Failed to tag device for UI", "error", err)
+   return err
}
```

Combined with `client.go`'s new synchronous call, this means any DB error during tagging — a momentary connection hiccup, a lock contention timeout on `anon_device` — surfaces to `Authenticate` and, since only `ErrDeviceLimitReached` is specifically branched, falls into `log.Warn + continue`. That's actually fine for non-sentinel errors, but:

- A `sql: transaction has already been committed or rolled back`-class error would previously have been swallowed in the goroutine; now it burns retry budget on every hit.
- `tagDeviceUI` also covers the `CreateOrUpdateDevice` TOCTOU path; any duplicate-key error from concurrent inserts (see finding #1) will now bubble up here as a non-limit error and spam warnings.

```suggestion
err = a.tagDeviceUI(ctx, httpReq, taggedDevice)
if err != nil {
    a.log.Debug("Failed to tag device for UI", "error", err)
    if errors.Is(err, anonstore.ErrDeviceLimitReached) {
        return err
    }
    return nil // preserve prior best-effort semantics for transient errors
}
```

:yellow_circle: [security] `AnonymousDeviceLimit` has no input validation at config load in `pkg/setting/setting.go:1653` (confidence: 70)

```go
cfg.AnonymousDeviceLimit = anonSection.Key("device_limit").MustInt64(0)
```

Negative values silently disable the feature (because the guard is `if s.deviceLimit > 0`). An operator who sets `device_limit = -1` expecting "deny all" will get "no limit enforced". Extremely large values (`9223372036854775807`) likewise silently disable. A log warning (or rejection) when the value is non-positive-but-nonzero preserves operator intent.

```suggestion
cfg.AnonymousDeviceLimit = anonSection.Key("device_limit").MustInt64(0)
if cfg.AnonymousDeviceLimit < 0 {
    cfg.Logger.Warn("auth.anonymous.device_limit is negative; ignoring and treating as unlimited", "value", cfg.AnonymousDeviceLimit)
    cfg.AnonymousDeviceLimit = 0
}
```

:yellow_circle: [consistency] `ProvideAnonymousDeviceService` signature change from `anonstore.AnonStore` to `db.DB` leaks a concrete dependency in `pkg/services/anonymous/anonimpl/impl.go:36` (confidence: 72)

The constructor now takes `db.DB` and internally calls `anonstore.ProvideAnonDBStore(sqlStore, cfg.AnonymousDeviceLimit)` — which is why the tests also had to switch from passing a store to passing a raw DB handle. This moves the seam out of the constructor, which has two costs:

1. Tests can no longer inject a fake `AnonStore` directly — the existing fake-store tests in `impl_test.go` had to change shape to use a real DB, which is slower and less isolated.
2. A caller wanting to swap in an alternate storage backend (in-memory, pluggable) now has to reach around `ProvideAnonymousDeviceService`.

The simpler fix is to keep accepting `AnonStore` and route the limit into the store at the Wire site, not the service site.

```suggestion
// Keep the AnonStore interface injection; configure the limit at the
// anonstore.ProvideAnonDBStore call site in the Wire / DI setup.
```

## Nitpicks

:white_circle: [consistency] JSON DTO field lacks `omitempty`; unset limit serializes as `0` rather than absent in `pkg/api/dtos/frontend_settings.go:195` (confidence: 65)

```go
AnonymousDeviceLimit int64 `json:"anonymousDeviceLimit"`
```

The matching TypeScript type is `number | undefined`, but the Go struct will always serialize a zero. Frontend code checking `if (config.anonymousDeviceLimit)` works by coincidence; `if (config.anonymousDeviceLimit !== undefined)` breaks. Add `,omitempty` or change the TS type to `number` (non-optional).

:white_circle: [testing] `TestIntegrationBeyondDeviceLimit` only covers the "create a 2nd device with limit=1" path; no test for concurrency, for refreshing an existing device under the limit, or for the window-bound logic in `updateDevice` in `pkg/services/anonymous/anonimpl/anonstore/database_test.go:51` (confidence: 75)

The new test confirms `ErrDeviceLimitReached` fires when a second distinct device tries to register — but given finding #3, that error actually fires because the second device doesn't exist in the table, not because the count exceeds the limit. A stronger suite would include: (a) an existing device getting refreshed after the limit is hit (the intended "update only" path), (b) a stale device past `anonymousDeviceExpiration` whose refresh should or shouldn't succeed, and (c) a parallel `goroutine` burst test to pin the TOCTOU semantics (even if only documenting current non-atomic behavior).

## Risk Metadata
Risk Score: 62/100 (MEDIUM) | Blast Radius: auth hot path (`pkg/services/anonymous/**`, `pkg/api/frontendsettings.go`, frontend config types) | Sensitive Paths: `pkg/services/anonymous/`, `pkg/setting/setting.go` (auth/anonymous section), `pkg/api/dtos/frontend_settings.go`
AI-Authored Likelihood: LOW (idiomatic Grafana style, multi-round human review by `@IevaVasiljeva`, `@kalleep`, `@eleijonmarck` on 2023-12-08 through 2023-12-12, iterative refactor of constants and test wiring consistent with human back-and-forth)
