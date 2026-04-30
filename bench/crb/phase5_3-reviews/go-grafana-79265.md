## Summary
11 files changed, 105 lines added, 37 lines deleted. 10 findings (4 critical, 6 improvements, 0 nitpicks).
Non-atomic count-then-insert allows device limit bypass under concurrent load in pkg/services/anonymous/anonimpl/anonstore/database.go:118.

## Critical
:red_circle: [correctness] Non-atomic count-then-insert allows device limit bypass under concurrent load (TOCTOU) in pkg/services/anonymous/anonimpl/anonstore/database.go:118 (confidence: 97)
`CreateOrUpdateDevice` calls `CountDevices` and then conditionally proceeds to INSERT in two separate, non-transactional DB operations. Under concurrent anonymous traffic, N goroutines can all observe `count < deviceLimit`, all pass the guard, and all execute the INSERT path — causing the actual number of stored devices to exceed `deviceLimit` by up to N-1. There is no DB-level lock, SELECT FOR UPDATE, unique constraint enforcement, or retry loop. The device limit guarantee is not upheld under any meaningful concurrency. From a security perspective this is CWE-367 (TOCTOU): an attacker scripting parallel requests with distinct device fingerprints can defeat the operator's intended cap, especially given the cap value is also disclosed via the unauthenticated frontend settings endpoint.
```suggestion
err := s.sqlStore.WithTransactionalDbSession(ctx, func(sess *sqlstore.DBSession) error {
    if s.deviceLimit > 0 {
        count, err := countDevicesTx(sess, time.Now().UTC().Add(-anonymousDeviceExpiration), time.Now().UTC().Add(time.Minute))
        if err != nil { return err }
        if count >= s.deviceLimit {
            return updateDeviceTx(sess, device)
        }
    }
    return insertDeviceTx(sess, device)
})
```
[References: https://cwe.mitre.org/data/definitions/367.html]

:red_circle: [correctness] updateDevice conflates stale-device with device-limit-reached, causing incorrect lockout in pkg/services/anonymous/anonimpl/anonstore/database.go:86 (confidence: 95)
The UPDATE query filters `updated_at BETWEEN (now-30d) AND (now+1min)`. If a known device's row has `updated_at` older than 30 days, the BETWEEN clause excludes it and `rowsAffected` is 0, which causes the function to return `ErrDeviceLimitReached`. A legitimate returning anonymous user last seen more than 30 days ago gets locked out with the wrong error — their device exists in the DB but its timestamp is outside the window. New-device-when-full and stale-device-exists are indistinguishable to the caller, so the caller cannot take the correct remediation action.
```suggestion
// Either introduce a distinct sentinel:
var ErrDeviceNotFound = fmt.Errorf("device not found")
// ...
if rowsAffected == 0 { return ErrDeviceNotFound }

// Or remove the BETWEEN condition entirely (DeleteDevicesOlderThan handles 30-day expiry):
const query = `UPDATE anon_device SET client_ip = ?, user_agent = ?, updated_at = ? WHERE device_id = ?`
```

:red_circle: [correctness] ErrDeviceLimitReached propagated out of Authenticate blocks all anonymous access at limit in pkg/services/anonymous/anonimpl/client.go:248 (confidence: 93)
When `updateDevice` returns `ErrDeviceLimitReached`, `TagDevice` returns it, and `Authenticate` returns `(nil, ErrDeviceLimitReached)` to the authn broker. The PR description implies the intent is to silently limit new device registration while still permitting existing devices to browse. Returning a hard error from `Authenticate` fails that goal: a device limit configuration detail becomes a gate on whether any anonymous session can proceed. The prior goroutine approach made tagging best-effort so the session always succeeded; the new synchronous path gives a tagging implementation detail veto power over login.
```suggestion
if err := a.anonDeviceService.TagDevice(ctx, httpReqCopy, anonymous.AnonDeviceUI); err != nil {
    if errors.Is(err, anonstore.ErrDeviceLimitReached) {
        a.log.Warn("Anonymous device limit reached, session not tagged", "error", err)
        // fall through — still return a valid anonymous identity
    } else {
        a.log.Warn("Failed to tag anonymous session", "error", err)
    }
}
```

:red_circle: [correctness] Panic recovery removed — TagDevice panics now escape to HTTP handler in pkg/services/anonymous/anonimpl/client.go:41 (confidence: 88)
The previous implementation ran `TagDevice` inside a goroutine with `defer recover()`. The goroutine is gone and there is no panic recovery in the new synchronous code path. Any panic inside `TagDevice` (nil-pointer in DB session, SQL driver bug, fingerprint hashing) propagates upward through `Authenticate`. The old contract was: panic equals WARN log, auth succeeds. The new contract is: panic equals unrecovered propagation through the HTTP stack. Whether the outermost HTTP middleware catches it is not guaranteed.
```suggestion
func (a *Anonymous) tagSafely(ctx context.Context, httpReqCopy *http.Request) (err error) {
    defer func() {
        if r := recover(); r != nil {
            a.log.Warn("Tag anon session panic", "err", r)
            err = nil
        }
    }()
    return a.anonDeviceService.TagDevice(ctx, httpReqCopy, anonymous.AnonDeviceUI)
}
```

## Improvements
:yellow_circle: [correctness] Non-limit DB errors swallowed at WARN with no metric — device accounting silently fails under DB outage in pkg/services/anonymous/anonimpl/client.go:248 (confidence: 90)
When `TagDevice` returns any error other than `ErrDeviceLimitReached`, it is logged at WARN and `Authenticate` returns a successful identity. There is no prometheus counter increment, no structured error field, and no alertable signal beyond the WARN log entry. Under a sustained DB outage the device limit is never enforced and there is no way for operators to detect this state programmatically.
```suggestion
if err := a.anonDeviceService.TagDevice(ctx, httpReqCopy, anonymous.AnonDeviceUI); err != nil {
    if errors.Is(err, anonstore.ErrDeviceLimitReached) { return nil, err }
    metrics.MAnonDeviceTagErrors.Inc()
    a.log.Warn("Failed to tag anonymous session", "error", err, "limitEnforced", false)
}
```

:yellow_circle: [correctness] updateDevice BETWEEN window incorrectly excludes devices unseen for 30+ days during re-tagging in pkg/services/anonymous/anonimpl/anonstore/database.go:93 (confidence: 88)
The caller sets `device.UpdatedAt = time.Now()` before calling `updateDevice`. The BETWEEN window is `[now-30d, now+1min]`. Any existing row whose stored `updated_at` is older than 30 days is excluded by the predicate — the row exists but does not match, `rowsAffected` is 0, and the call incorrectly returns `ErrDeviceLimitReached`. There is no grace period or separate path for re-admitting lapsed devices.
```suggestion
const query = `UPDATE anon_device SET
client_ip = ?,
user_agent = ?,
updated_at = ?
WHERE device_id = ?`
```

:yellow_circle: [correctness] tagDeviceUI errors logged at Debug — failures invisible in production deployments in pkg/services/anonymous/anonimpl/impl.go:144 (confidence: 87)
When `tagDeviceUI` returns an error, `impl.go` logs at `a.log.Debug(...)` before returning. Debug logging is suppressed in standard Grafana production deployments. For a feature enforcing an operator-configured device limit, failures in the core write path are silently dropped in production, making the limit unreliable without any signal to the operator.
```suggestion
err = a.tagDeviceUI(ctx, httpReq, taggedDevice)
if err != nil {
    a.log.Warn("Failed to tag device for UI", "error", err)
    return err
}
```

:yellow_circle: [correctness] Cache not populated on ErrDeviceLimitReached — repeated DB queries on every request at limit in pkg/services/anonymous/anonimpl/impl.go:144 (confidence: 85)
When `CreateOrUpdateDevice` returns `ErrDeviceLimitReached`, the local cache entry is never written. Every subsequent request from any anonymous device misses the cache and executes `CountDevices` plus `updateDevice` — two DB round-trips per request. Under high anonymous traffic with the limit active, this generates unbounded unnecessary DB load proportional to request rate.
```suggestion
err = a.anonStore.CreateOrUpdateDevice(ctx, taggedDevice)
if err != nil {
    if errors.Is(err, anonstore.ErrDeviceLimitReached) {
        a.localCache.Set(cacheKey, struct{}{}, cache.DefaultExpiration) // sentinel
    }
    return err
}
a.localCache.Set(cacheKey, taggedDevice, cache.DefaultExpiration)
```

:yellow_circle: [security] Configured device limit disclosed to unauthenticated clients in frontend settings in pkg/api/frontendsettings.go:195 (confidence: 80)
`AnonymousDeviceLimit` is included in the unauthenticated frontend settings JSON response. Combined with the non-atomic limit check, this gives an attacker precise knowledge of how many distinct fake-fingerprint devices are needed to fill the table and trigger lockout behavior for legitimate users. CWE-200 (Information Exposure).
```suggestion
// Remove from unauthenticated response:
// AnonymousDeviceLimit: hs.Cfg.AnonymousDeviceLimit,

// If UX needs the value, expose a boolean computed server-side instead:
AnonymousDeviceLimitReached: hs.anonService.LimitReached(c.Req.Context()),
```
[References: https://cwe.mitre.org/data/definitions/200.html]

:yellow_circle: [correctness] Request context passed to TagDevice — client disconnect mid-write can silently skip device accounting in pkg/services/anonymous/anonimpl/client.go:248 (confidence: 80)
The previous implementation used `context.WithTimeout(context.Background(), 2*time.Minute)` to detach the write from the request lifecycle. The new code passes the request `ctx` directly to `TagDevice`. If a client disconnects after `CountDevices` but before `CreateOrUpdateDevice` commits, `ctx` is cancelled, the write is abandoned, and the device is never recorded. Subsequent requests from that device pass the count check again. The limit can fail to enforce correctly under load with short client timeouts.
```suggestion
tagCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Minute)
defer cancel()
if err := a.anonDeviceService.TagDevice(tagCtx, httpReqCopy, anonymous.AnonDeviceUI); err != nil {
    // ...
}
```

## Risk Metadata
Risk Score: 34/100 (MEDIUM) | Blast Radius: HIGH (~15 directly impacted referencing files — wire-injection breakage from `ProvideAnonDBStore` and `ProvideAnonymousDeviceService` signature changes; `GrafanaConfig` interface addition propagates to many frontend consumers) | Sensitive Paths: none matched the configured patterns literally, but the diff touches the anonymous-authentication subsystem (`pkg/services/anonymous/`, `auth.anonymous` ini parsing in `pkg/setting/setting.go`).
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold)
