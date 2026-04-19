## Summary
11 files changed, 105 lines added, 46 lines deleted. 6 findings (2 critical, 4 improvements).
Adds a configurable anonymous-device limit, but introduces a TOCTOU race on the count-then-create path and silently removes the async/panic-recovered TagDevice goroutine in the auth hot path.

## Critical

:red_circle: [correctness] TOCTOU race between CountDevices and insert allows device limit to be exceeded in pkg/services/anonymous/anonimpl/anonstore/database.go:118 (confidence: 92)
`CreateOrUpdateDevice` checks the current count and, if under the limit, falls through to the insert path. Under concurrent anonymous requests, N goroutines can all observe `count < deviceLimit` before any of them inserts, producing `deviceLimit + N-1` rows. There is no transaction, `SELECT ... FOR UPDATE`, unique constraint on the count, or retry-after-insert guard. For a feature whose entire purpose is capping device rows, this makes the cap soft under load — exactly when it matters most.
```suggestion
// Perform count + insert inside a single transaction, and re-check after insert,
// OR move the enforcement to a DB-level constraint / advisory lock.
if s.deviceLimit > 0 {
	return s.sqlStore.WithTransactionalDbSession(ctx, func(sess *sqlstore.DBSession) error {
		count, err := s.countDevicesTx(sess, time.Now().UTC().Add(-anonymousDeviceExpiration), time.Now().UTC().Add(time.Minute))
		if err != nil {
			return err
		}
		if count >= s.deviceLimit {
			return s.updateDeviceTx(sess, device)
		}
		return s.insertDeviceTx(sess, device)
	})
}
```

:red_circle: [correctness] Synchronous TagDevice on the auth hot path removes goroutine + panic recovery and adds DB latency to every anonymous request in pkg/services/anonymous/anonimpl/client.go:41 (confidence: 90)
The previous implementation dispatched `TagDevice` in a goroutine with a 2-minute timeout and a `recover()` guard, so DB slowness or a panic in tagging could never block or crash anonymous auth. This PR replaces that with a synchronous in-line call that: (1) propagates DB latency (including the new `CountDevices` round trip) into every anonymous `Authenticate` call, (2) removes panic recovery so any panic in the tagging path now kills the request goroutine, and (3) returns `ErrDeviceLimitReached` directly from `Authenticate`, turning a capacity signal into an auth failure visible to end users. The description claims this is about adding a limit — the synchronous/recovery change is undocumented and materially changes the failure domain of anonymous auth.
```suggestion
// Keep tagging asynchronous for latency + panic isolation; surface the limit
// via a dedicated signal rather than failing Authenticate synchronously.
go func() {
	defer func() {
		if r := recover(); r != nil {
			a.log.Warn("Tag anon session panic", "err", r)
		}
	}()
	newCtx, cancel := context.WithTimeout(context.Background(), timeoutTag)
	defer cancel()
	if err := a.anonDeviceService.TagDevice(newCtx, httpReqCopy, anonymous.AnonDeviceUI); err != nil {
		if errors.Is(err, anonstore.ErrDeviceLimitReached) {
			// increment a metric / structured log instead of failing the request
			a.log.Debug("anon device limit reached", "error", err)
			return
		}
		a.log.Warn("Failed to tag anonymous session", "error", err)
	}
}()
```

## Improvements

:yellow_circle: [correctness] updateDevice conflates "row not updated" with "device limit reached" in pkg/services/anonymous/anonimpl/anonstore/database.go:86 (confidence: 88)
`updateDevice` returns `ErrDeviceLimitReached` whenever `rowsAffected == 0`, but that condition fires for at least three distinct cases: (a) the caller is actually over the limit and the device does not yet exist, (b) the device exists but its `updated_at` is outside the `[now-30d, now+1min]` window (stale), (c) the device was deleted between the count check and the update. Callers in `client.go` re-surface this as an auth error, so stale-but-legitimate devices will be told "limit reached" when the root cause is row expiry. Distinguish "no existing row to update" from the limit being hit.
```suggestion
if rowsAffected == 0 {
	// Could be: over-limit new device, stale row outside the window, or a race
	// with DeleteDevicesOlderThan. Return a more specific error so callers can
	// react accordingly instead of surfacing "limit reached" for stale rows.
	return ErrDeviceUpdateSkipped
}
```

:yellow_circle: [consistency] Duplicate anonymousDeviceExpiration constant across two packages in pkg/services/anonymous/anonimpl/api/api.go:18 (confidence: 90)
`const anonymousDeviceExpiration = 30 * 24 * time.Hour` is now defined both in `anonimpl/api/api.go` and in `anonimpl/anonstore/database.go`. The two values must stay in sync by convention only — a future change to one will silently desynchronize the list API window from the create/count window. Expose it from one package (likely `anonstore`) and import it in the other.
```suggestion
// In pkg/services/anonymous/anonimpl/api/api.go, delete the local const and use:
fromTime := time.Now().Add(-anonstore.AnonymousDeviceExpiration)
```

:yellow_circle: [cross-file-impact] TS type anonymousDeviceLimit: number | undefined will never actually be undefined in packages/grafana-data/src/types/config.ts:200 (confidence: 87)
On the Go side, `FrontendSettingsDTO.AnonymousDeviceLimit` is `int64` (not a pointer and no `omitempty`), so JSON always serializes it as a number — `0` when unset. The TS field is typed `number | undefined` and initialized to `undefined` in `GrafanaBootConfig`, which implies frontend code will check `if (anonymousDeviceLimit !== undefined)`. That check will never be false after boot: the server always sends `0`. Either make the Go field `*int64` with `omitempty`, or tighten the TS type to `number` and have callers check `> 0`.
```suggestion
// In pkg/api/dtos/frontend_settings.go:
AnonymousDeviceLimit int64 `json:"anonymousDeviceLimit"`
// TS (packages/grafana-data/src/types/config.ts):
anonymousDeviceLimit: number; // 0 means unlimited
```

:yellow_circle: [testing] New limit test does not cover the "update existing device while at limit" success path in pkg/services/anonymous/anonimpl/anonstore/database_test.go:153 (confidence: 86)
`TestIntegrationBeyondDeviceLimit` only asserts that a *second, new* device is rejected when `deviceLimit == 1`. It never exercises the intended positive case: an *existing* device whose `updated_at` is inside the window should still be updateable after the limit is reached (that is the whole point of `updateDevice`). Add a case that inserts a device, fills the limit with a different device, then re-taggs the original and asserts `NoError` plus that `client_ip` / `user_agent` were refreshed.
```suggestion
t.Run("existing device still updatable at limit", func(t *testing.T) {
	store := db.InitTestDB(t)
	s := ProvideAnonDBStore(store, 1)

	existing := &Device{DeviceID: "keep", ClientIP: "10.0.0.1", UserAgent: "ua1", UpdatedAt: time.Now()}
	require.NoError(t, s.CreateOrUpdateDevice(context.Background(), existing))

	// Refresh the same device — should succeed even though limit is reached.
	existing.ClientIP = "10.0.0.2"
	existing.UpdatedAt = time.Now()
	require.NoError(t, s.CreateOrUpdateDevice(context.Background(), existing))

	got, err := s.ListDevices(context.Background(), nil, nil)
	require.NoError(t, err)
	require.Len(t, got, 1)
	require.Equal(t, "10.0.0.2", got[0].ClientIP)
})
```

## Risk Metadata
Risk Score: 62/100 (MEDIUM) | Blast Radius: auth hot path + DI wiring change (ProvideAnonymousDeviceService signature) | Sensitive Paths: pkg/services/anonymous/**, pkg/api/frontendsettings.go
AI-Authored Likelihood: LOW
