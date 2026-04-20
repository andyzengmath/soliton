## Summary
11 files changed, 105 lines added, 40 lines deleted. 6 findings (2 critical, 4 improvements, 0 nitpicks).
Device-limit feature is sensibly scoped, but the client-side refactor silently converted anonymous tagging from fire-and-forget to a blocking, error-propagating step in the auth path — any transient DB fault now breaks anonymous login.

## Critical

:red_circle: [correctness] Generic tagDeviceUI errors now abort anonymous authentication in `pkg/services/anonymous/anonimpl/impl.go`:146 (confidence: 92)
Prior behavior logged `tagDeviceUI` failures at Debug level and returned nil — tagging was best-effort. This PR adds `return err` unconditionally, so ANY error from `tagDeviceUI` (transient SQL failures, cache errors, context timeouts) now bubbles up to `client.go`, which converts it into an `Authenticate` failure. Combined with the client-side change that removed the fire-and-forget goroutine, every anonymous page view now gates on a successful device upsert. A flaky DB connection that used to cause a log line will now block anonymous users entirely. The intent is to surface `ErrDeviceLimitReached`, but the implementation propagates every error indistinguishably.
```suggestion
	err = a.tagDeviceUI(ctx, httpReq, taggedDevice)
	if err != nil {
		if errors.Is(err, anonstore.ErrDeviceLimitReached) {
			return err
		}
		a.log.Debug("Failed to tag device for UI", "error", err)
	}

	return nil
```

:red_circle: [correctness] TOCTOU race in device-limit enforcement in `pkg/services/anonymous/anonimpl/anonstore/database.go`:108 (confidence: 88)
`CreateOrUpdateDevice` calls `CountDevices` and then performs a separate INSERT/UPSERT without a transaction or unique constraint to serialize the decision. Under concurrent anonymous traffic (which is exactly the workload this feature is designed to meter), N requests can each read `count == deviceLimit - 1` simultaneously, each decide to insert, and collectively push the table several rows over the limit. Conversely, when `count == deviceLimit` exactly, a burst of requests will all be routed to `updateDevice` — if none of them matches an existing `device_id`, every one of them returns `ErrDeviceLimitReached` and every one of those anonymous sessions fails to authenticate (see finding above). Wrap the count-plus-insert in `WithTransactionalDbSession` with serializable/repeatable-read isolation, or replace the count with a conditional INSERT (e.g. `INSERT ... WHERE (SELECT COUNT(*) FROM anon_device WHERE updated_at BETWEEN ? AND ?) < ?`) so the check and the write are atomic.
```suggestion
	// Atomically check-and-insert under transaction to avoid TOCTOU race
	return s.sqlStore.WithTransactionalDbSession(ctx, func(sess *sqlstore.DBSession) error {
		if s.deviceLimit > 0 {
			var count int64
			_, err := sess.SQL(`SELECT COUNT(*) FROM anon_device WHERE updated_at BETWEEN ? AND ?`,
				time.Now().UTC().Add(-anonymousDeviceExpiration), time.Now().UTC().Add(time.Minute)).Get(&count)
			if err != nil {
				return err
			}
			if count >= s.deviceLimit {
				return s.updateDeviceInSession(sess, device)
			}
		}
		return s.insertDeviceInSession(sess, device)
	})
```

## Improvements

:yellow_circle: [correctness] Synchronous device tagging in the auth hot path in `pkg/services/anonymous/anonimpl/client.go`:44 (confidence: 86)
The pre-existing `go func() { ... }()` with a 2-minute timeout was removed so `TagDevice` now runs inline in `Authenticate`. Every anonymous request now pays the latency of a `COUNT(*)` plus an INSERT/UPDATE on `anon_device` before the user sees a response. Under load this adds P50/P99 latency to anonymous landing pages and, if the DB is degraded, causes visible blocking rather than a delayed side-effect. The local-cache check in `impl.go` amortizes this for repeat callers, but first-hit callers (which are exactly the new-device case the limit is designed to gate) always hit the DB. The semantic requirement is "reject request when at limit"; that only requires the limit check to be synchronous, not the successful path. Consider: (a) do the count-check synchronously and return early on limit-reached, but keep the write off-path via a channel/goroutine with the old 2-minute timeout; or (b) at minimum document the new latency characteristic in the commit message and/or docs.
```suggestion
	// Keep the limit check synchronous (fail-fast), but move the write off the auth path.
	if err := a.anonDeviceService.CheckDeviceLimit(ctx, httpReqCopy, anonymous.AnonDeviceUI); err != nil {
		return nil, err
	}
	go func() {
		defer func() {
			if err := recover(); err != nil {
				a.log.Warn("Tag anon session panic", "err", err)
			}
		}()
		newCtx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
		defer cancel()
		if err := a.anonDeviceService.TagDevice(newCtx, httpReqCopy, anonymous.AnonDeviceUI); err != nil {
			a.log.Warn("Failed to tag anonymous session", "error", err)
		}
	}()
```

:yellow_circle: [consistency] Duplicate `anonymousDeviceExpiration` constant declarations in `pkg/services/anonymous/anonimpl/anonstore/database.go`:16 (confidence: 90)
This PR defines `const anonymousDeviceExpiration = 30 * 24 * time.Hour` in `anonstore/database.go` and also renames `thirtyDays` to `anonymousDeviceExpiration` in `anonimpl/api/api.go` — two identical constants with the same name in two packages. If the retention window is ever changed, one call-site will drift. Since `anonstore` is imported by the `api` package, declare the constant once in `anonstore` and reference it as `anonstore.AnonymousDeviceExpiration` (exported) from the API layer.
```suggestion
// in pkg/services/anonymous/anonimpl/anonstore/database.go
const AnonymousDeviceExpiration = 30 * 24 * time.Hour

// in pkg/services/anonymous/anonimpl/api/api.go — delete the local const and use:
fromTime := time.Now().Add(-anonstore.AnonymousDeviceExpiration)
```

:yellow_circle: [correctness] Mutation of captured `args` slice inside `WithDbSession` closure in `pkg/services/anonymous/anonimpl/anonstore/database.go`:80 (confidence: 78)
```go
args := []interface{}{device.ClientIP, ...}
err := s.sqlStore.WithDbSession(ctx, func(dbSession *sqlstore.DBSession) error {
    args = append([]interface{}{query}, args...)   // <-- mutates outer slice
    result, err := dbSession.Exec(args...)
    ...
})
```
The closure reassigns `args` to include `query` as the first element. If `WithDbSession` ever invokes the callback more than once (retry on deadlock/serialization failure, which is common in Grafana's xorm session code), the second invocation will prepend `query` *again*, producing `[query, query, ClientIP, UserAgent, ...]` and either corrupting the UPDATE or panicking on the mismatched arg count. Build the exec-args slice as a local inside the closure.
```suggestion
	err := s.sqlStore.WithDbSession(ctx, func(dbSession *sqlstore.DBSession) error {
		execArgs := append([]interface{}{query}, args...)
		result, err := dbSession.Exec(execArgs...)
		if err != nil {
			return err
		}
		rowsAffected, err := result.RowsAffected()
		if err != nil {
			return err
		}
		if rowsAffected == 0 {
			return ErrDeviceLimitReached
		}
		return nil
	})
```

:yellow_circle: [cross-file-impact] TS type `anonymousDeviceLimit: number | undefined` mismatches wire format in `packages/grafana-data/src/types/config.ts`:200 (confidence: 80)
The Go DTO declares `AnonymousDeviceLimit int64` with no `omitempty`, so the JSON frontend-settings payload always contains `anonymousDeviceLimit: <number>` (zero when unset). The TS type `number | undefined` and the runtime initializer `anonymousDeviceLimit = undefined` in `grafana-runtime/src/config.ts` will never observe `undefined` at runtime — the server always sends a numeric `0`. This asymmetry is easy to misuse in the frontend (`if (config.anonymousDeviceLimit) ...` will silently treat unset and 0 identically, which is fine, but `if (config.anonymousDeviceLimit === undefined)` will always be false). Either (a) type it as `number` with a default of `0`, or (b) add `omitempty` on the Go side and keep the optional typing — but not both conventions.
```suggestion
// packages/grafana-data/src/types/config.ts
  anonymousDeviceLimit: number;
// packages/grafana-runtime/src/config.ts
  anonymousDeviceLimit = 0;
```

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: auth path for anonymous users (anonymous auth affects every unauthenticated request when `auth.anonymous.enabled=true`); 11 files, 3 packages; no callers outside anonimpl touched | Sensitive Paths: `pkg/services/anonymous/**` (auth subsystem), `pkg/setting/setting.go` (global config surface)
AI-Authored Likelihood: LOW
