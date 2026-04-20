## Summary
11 files changed, 105 lines added, 37 lines deleted. 11 findings (7 critical, 4 improvements).
PR introduces an anonymous-device limit but ships several correctness and availability regressions: TagDevice is now synchronous on the auth path, `updateDevice` conflates "device not found" with "limit reached", and the count/insert pair is not transactional. The core "existing devices can still update when limit is reached" claim is also untested.

## Critical

:red_circle: [correctness] `updateDevice` returns `ErrDeviceLimitReached` whenever `rowsAffected == 0`, conflating "device not found / expired" with "limit reached" in pkg/services/anonymous/anonimpl/anonstore/database.go:70 (confidence: 95)
When `count >= s.deviceLimit`, `CreateOrUpdateDevice` delegates to `updateDevice`, whose WHERE clause requires `device_id = ? AND updated_at BETWEEN (device.UpdatedAt - 30d) AND (device.UpdatedAt + 1min)`. A new device hitting the limit, a returning device whose stored row has aged past 30 days, and a row whose `updated_at` falls outside the window all produce `rowsAffected == 0` and surface as `ErrDeviceLimitReached`. The caller cannot distinguish these cases, so a legitimate returning user whose row has not yet been GC'd is permanently blocked from anonymous access instead of being re-registered. This undermines the "existing devices can still update" premise of the feature.
```suggestion
if rowsAffected == 0 {
    // Row does not exist (or is outside the active window).
    // Caller decides policy: a genuinely new device at the cap should be
    // rejected, but a returning-but-stale device should be re-inserted.
    return ErrDeviceNotFound
}
return nil
```
And in `CreateOrUpdateDevice`, fall through to the insert path on `ErrDeviceNotFound` (evicting an LRU row if strict cap is required) rather than re-surfacing it as a limit-reached error.

:red_circle: [correctness] TOCTOU race between `CountDevices` and insert lets the limit be exceeded under concurrent load and enables a lockout-DoS in pkg/services/anonymous/anonimpl/anonstore/database.go:73 (confidence: 92)
`CreateOrUpdateDevice` reads `CountDevices`, decides whether to insert or call `updateDevice`, and performs the write outside of any enclosing transaction or serializable isolation boundary. Under concurrent anonymous traffic, N goroutines can all observe `count < limit` and all insert, blowing past the cap. Worse, because device creation is unauthenticated and the count window is 30 days, an attacker can script `deviceLimit` fresh high-entropy `device_id`s to saturate the table; from then on every legitimate new user hits the `rowsAffected == 0` branch of `updateDevice` and is returned `ErrDeviceLimitReached` for up to 30 days. A capacity control becomes a complete anonymous-auth denial-of-service primitive.
```suggestion
err := s.sqlStore.InTransaction(ctx, func(ctx context.Context) error {
    count, err := s.CountDevices(ctx, now.Add(-anonymousDeviceExpiration), now.Add(time.Minute))
    if err != nil {
        return err
    }
    if s.deviceLimit > 0 && count >= s.deviceLimit {
        return s.updateDevice(ctx, device)
    }
    return s.insertDevice(ctx, device)
})
```
Combine with per-IP rate limiting on anon device creation and/or LRU eviction when the cap is hit so saturation cannot lock out legitimate users.

:red_circle: [correctness] `TagDevice` now propagates every `tagDeviceUI` error, creating an inconsistent contract across the two call sites in pkg/services/anonymous/anonimpl/impl.go:146 (confidence: 90)
Before the PR, `TagDevice` swallowed `tagDeviceUI` errors and always returned `nil`. The new `return err` means any transient DB error (lock wait, conn timeout, context cancellation on client disconnect) now propagates. `client.go` special-cases only `ErrDeviceLimitReached`; any other `TagDevice` caller (or a future one) that treats non-nil as a hard failure will start breaking anonymous auth on flaky DB conditions. Additionally, during client disconnects the in-flight UPDATE is cancelled, and a warning is logged for every dropped connection, producing log spam on high-traffic anonymous dashboards.
```suggestion
func (a *AnonDeviceService) tagDeviceUI(ctx context.Context, httpReq *http.Request, d *Device) error {
    if err := a.anonStore.CreateOrUpdateDevice(ctx, d); err != nil {
        if errors.Is(err, anonstore.ErrDeviceLimitReached) {
            return err // hard propagate only the cap signal
        }
        a.log.Warn("failed to create/update anonymous device", "error", err)
        return nil // preserve pre-PR behavior for transient errors
    }
    return nil
}
```

:red_circle: [security] `updateDevice` overwrites `client_ip`/`user_agent` keyed solely on attacker-observable `device_id`, enabling device-fingerprint poisoning in pkg/services/anonymous/anonimpl/anonstore/database.go:70 (confidence: 88)
The new UPDATE matches on `device_id` plus a 30-day freshness window — no binding to any server-verifiable identity. The `device_id` comes from the HTTP request (cookie/header) and must be treated as attacker-chosen. Any actor who learns or guesses another anonymous device's `device_id` (shared-browser enumeration, referrer leaks, log exposure) can overwrite that row's `ClientIP` and `UserAgent` with arbitrary values on every request. Previously this overwrite was fire-and-forget in a goroutine; the PR makes it a synchronous, guaranteed write on the auth path. Impact: poisoned audit records, spoofing a victim IP as the source of anonymous Grafana traffic, and corruption of the counting that drives the new `ErrDeviceLimitReached` gate (CWE-639, CWE-345).
```suggestion
const query = `UPDATE anon_device SET user_agent = ?, updated_at = ?
WHERE device_id = ? AND client_ip = ? AND updated_at BETWEEN ? AND ?`
args := []interface{}{device.UserAgent, now.UTC(),
    device.DeviceID, device.ClientIP,
    now.UTC().Add(-anonymousDeviceExpiration), now.UTC().Add(time.Minute)}
```
Longer-term: HMAC-sign `device_id` at issuance with a server secret and reject unsigned or mismatched IDs at the edge.
[References: https://cwe.mitre.org/data/definitions/639.html, https://cwe.mitre.org/data/definitions/345.html]

:red_circle: [cross-file-impact] `ErrDeviceLimitReached` is returned verbatim from `Authenticate` with no HTTP status mapping — rejected anonymous users see a 500 in pkg/services/anonymous/anonimpl/client.go:44 (confidence: 87)
Before the PR, `Authenticate` always returned a non-nil `Identity`; `TagDevice` errors were discarded inside a goroutine. The new code returns `nil, err` when `errors.Is(err, anonstore.ErrDeviceLimitReached)`. `ErrDeviceLimitReached` is a bare `fmt.Errorf` sentinel with no `authn.Error` wrapping and no status binding; Grafana's authn middleware will render any unrecognized error as HTTP 500. The user-visible outcome of the feature is therefore "anonymous dashboards intermittently 500" rather than a meaningful 429/403 with a `Retry-After` hint.
```suggestion
if errors.Is(err, anonstore.ErrDeviceLimitReached) {
    return nil, errutil.TooManyRequests("anonymous.deviceLimitReached",
        errutil.WithPublicMessage("Anonymous access is temporarily unavailable.")).Errorf("%w", err)
}
```

:red_circle: [testing] The success path of `updateDevice` (`rowsAffected > 0`) — the PR's core "existing devices can still update" claim — has no test coverage in pkg/services/anonymous/anonimpl/anonstore/database_test.go:51 (confidence: 95)
`TestIntegrationBeyondDeviceLimit` only exercises the failure branch: it inserts a device, then calls `CreateOrUpdateDevice` with a *different* `DeviceID` ("keep"), so the UPDATE's WHERE clause never matches any row and the test only asserts that the rejection path returns `ErrDeviceLimitReached`. The positive case — same `DeviceID` resubmits at the cap, UPDATE succeeds, row is refreshed — is never executed. This is the primary behavioural claim of the PR and it is untested.
```suggestion
func TestIntegrationUpdateExistingDeviceAtLimit(t *testing.T) {
    store := db.InitTestDB(t)
    anonDBStore := ProvideAnonDBStore(store, 1)

    d := &Device{DeviceID: "existing", ClientIP: "10.0.0.1",
        UserAgent: "ua-1", CreatedAt: time.Now().Add(-time.Hour),
        UpdatedAt: time.Now().Add(-time.Hour)}
    require.NoError(t, anonDBStore.CreateOrUpdateDevice(context.Background(), d))

    d.ClientIP = "10.0.0.2"
    d.UpdatedAt = time.Now()
    require.NoError(t, anonDBStore.CreateOrUpdateDevice(context.Background(), d),
        "existing device must still update when limit is reached")

    devs, err := anonDBStore.ListDevices(context.Background(), nil, nil)
    require.NoError(t, err)
    require.Len(t, devs, 1)
    assert.Equal(t, "10.0.0.2", devs[0].ClientIP)
}
```

:red_circle: [testing] `Authenticate` returning `nil, ErrDeviceLimitReached` has no test — the behaviour-breaking change for anonymous users is unverified in pkg/services/anonymous/anonimpl/client.go:44 (confidence: 92)
The PR switches `TagDevice` from fire-and-forget to synchronous and adds the `errors.Is(err, anonstore.ErrDeviceLimitReached)` short-circuit. Neither existing `impl_test.go` nor any new test drives a scenario where the limit is exceeded and then asserts `Authenticate` returns `nil, err` with `errors.Is(err, ErrDeviceLimitReached)`. A typo in the `errors.Is` check or in the error propagation chain would silently re-admit blocked users — no test would fail.
```suggestion
func TestAnonymousAuthenticate_rejectsAtDeviceLimit(t *testing.T) {
    sqlStore := db.InitTestDB(t)
    cfg := setting.NewCfg()
    cfg.AnonymousEnabled = true
    cfg.AnonymousDeviceLimit = 1

    anonService := ProvideAnonymousDeviceService(
        &usagestats.UsageStatsMock{}, &authntest.FakeService{},
        sqlStore, cfg, orgtest.NewOrgServiceFake(), nil,
        actest.FakeAccessControl{}, &routing.RouteRegisterImpl{})

    fill := &http.Request{Header: http.Header{"User-Agent": []string{"filler"}}, RemoteAddr: "1.2.3.4:0"}
    require.NoError(t, anonService.TagDevice(context.Background(), fill, anonymous.AnonDeviceUI))

    anon := &Anonymous{cfg: cfg, log: log.New("t"), anonDeviceService: anonService}
    newReq := &authn.Request{HTTPRequest: &http.Request{
        Header: http.Header{"User-Agent": []string{"new"}}, RemoteAddr: "5.6.7.8:0"}}
    id, err := anon.Authenticate(context.Background(), newReq)
    require.ErrorIs(t, err, anonstore.ErrDeviceLimitReached)
    require.Nil(t, id)
}
```

## Improvements

:yellow_circle: [correctness] `updateDevice`'s BETWEEN window is anchored on caller-supplied `device.UpdatedAt`, making the expiry window caller-controlled and inconsistent with `CountDevices` in pkg/services/anonymous/anonimpl/anonstore/database.go:75 (confidence: 85)
`CountDevices` uses `time.Now()` as its anchor but `updateDevice` uses `device.UpdatedAt - 30d` / `+ 1min`. If the caller passes a clock-skewed or far-past/far-future `UpdatedAt`, the acceptance window slides accordingly and a row that is "active" by the count check may fail the update check (or vice versa). Capture `now` once in `CreateOrUpdateDevice` and thread it through both calls.
```suggestion
func (s *AnonDBStore) CreateOrUpdateDevice(ctx context.Context, device *Device) error {
    now := time.Now().UTC()
    if s.deviceLimit > 0 {
        count, err := s.CountDevices(ctx, now.Add(-anonymousDeviceExpiration), now.Add(time.Minute))
        if err != nil {
            return err
        }
        if count >= s.deviceLimit {
            return s.updateDevice(ctx, device, now)
        }
    }
    // ...insert path uses `now` as well
}

func (s *AnonDBStore) updateDevice(ctx context.Context, device *Device, now time.Time) error {
    args := []interface{}{device.ClientIP, device.UserAgent, now, device.DeviceID,
        now.Add(-anonymousDeviceExpiration), now.Add(time.Minute)}
    // ...
}
```

:yellow_circle: [correctness] `CountDevices` is called with `time.Now()` evaluated twice, so the lower and upper bounds of the window drift by nanoseconds and do not correspond to a single instant in pkg/services/anonymous/anonimpl/anonstore/database.go:73 (confidence: 88)
The call is `s.CountDevices(ctx, time.Now().UTC().Add(-anonymousDeviceExpiration), time.Now().UTC().Add(time.Minute))`. Two separate `time.Now()` readings means the active-window boundaries are not anchored to the same moment; combined with the caller-anchored anchor in `updateDevice`, the PR has three independent time references that should be one. This is the same fix as above — capture a single `now` value at entry and reuse it.
```suggestion
now := time.Now().UTC()
count, err := s.CountDevices(ctx, now.Add(-anonymousDeviceExpiration), now.Add(time.Minute))
```

:yellow_circle: [testing] The changed `TagDevice` error-propagation contract is untested — no case drives a non-nil error out of `TagDevice` in pkg/services/anonymous/anonimpl/impl_test.go:116 (confidence: 88)
`impl.go` now does `return err` after the debug log on `tagDeviceUI` failure, but `TestIntegrationDeviceService_tag` only asserts `require.NoError(t, err)` on the success path. Drive the failure path so that the new error-propagation contract is pinned by a test: configure the store with `AnonymousDeviceLimit = 1`, tag twice with different device IDs, and assert the second `TagDevice` call returns `ErrDeviceLimitReached`.
```suggestion
func TestIntegrationDeviceService_tagReturnsLimitError(t *testing.T) {
    store := db.InitTestDB(t)
    cfg := setting.NewCfg()
    cfg.AnonymousDeviceLimit = 1
    svc := ProvideAnonymousDeviceService(&usagestats.UsageStatsMock{},
        &authntest.FakeService{}, store, cfg, orgtest.NewOrgServiceFake(), nil,
        actest.FakeAccessControl{}, &routing.RouteRegisterImpl{})

    r1 := &http.Request{Header: http.Header{"User-Agent": []string{"a"}}, RemoteAddr: "1.1.1.1:0"}
    r2 := &http.Request{Header: http.Header{"User-Agent": []string{"b"}}, RemoteAddr: "2.2.2.2:0"}
    require.NoError(t, svc.TagDevice(context.Background(), r1, anonymous.AnonDeviceUI))
    require.ErrorIs(t,
        svc.TagDevice(context.Background(), r2, anonymous.AnonDeviceUI),
        anonstore.ErrDeviceLimitReached)
}
```

:yellow_circle: [testing] No test covers a stale-but-known device at the limit — the policy for a returning device whose row has aged past 30 days is unspecified in pkg/services/anonymous/anonimpl/anonstore/database_test.go:51 (confidence: 85)
Because `updateDevice`'s WHERE clause demands `updated_at` within the last 30 days, a device row that has not been cleaned up yet but is older than the window cannot be updated when the cap is hit — it receives `ErrDeviceLimitReached` even though it is a known device. Whether this is intended (stale = treat as new) or a bug (stale-but-known = should refresh) needs to be decided and pinned by a test so the behaviour is not accidentally changed later.
```suggestion
func TestIntegrationUpdateStaleDeviceAtLimit(t *testing.T) {
    store := db.InitTestDB(t)
    anonDBStore := ProvideAnonDBStore(store, 1)
    // Seed a stale row directly via the store so the 30-day window check fires
    stale := &Device{DeviceID: "stale", ClientIP: "10.0.0.1", UserAgent: "old",
        CreatedAt: time.Now().Add(-31 * 24 * time.Hour),
        UpdatedAt: time.Now().Add(-31 * 24 * time.Hour)}
    require.NoError(t, anonDBStore.CreateOrUpdateDevice(context.Background(), stale))
    // Returning user, same DeviceID, fresh timestamp
    stale.UpdatedAt = time.Now()
    err := anonDBStore.CreateOrUpdateDevice(context.Background(), stale)
    // Document the intended policy; adjust assertion to match.
    require.NoError(t, err, "returning-but-stale device must be re-admissible")
}
```

## Risk Metadata
Risk Score: 59/100 (MEDIUM) | Blast Radius: wide — `pkg/setting/setting.go` + `grafana-data/config.ts` are central config types with 100+ importers each; auth hot-path touched | Sensitive Paths: `pkg/setting/setting.go` parses `auth.anonymous`, `pkg/services/anonymous/anonimpl/client.go` is an `authn.ContextAwareClient`
AI-Authored Likelihood: LOW (human-authored: branch `jguer/add-anon-device-limit`, multiple named human reviewers, idiomatic Go)

(9 additional findings below confidence threshold 85)
