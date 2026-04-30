## Summary
8 files changed, 137 lines added, 78 lines deleted. 1 finding (0 critical, 1 improvement).
Clean rename of `InstrumentationMiddleware` → `MetricsMiddleware` and extraction of the contextual logger into a dedicated `ContextualLoggerMiddleware`. Middleware ordering in `pluginsintegration.go` keeps the contextual attributes available to `LoggerMiddleware` downstream, so the refactor is functionally sound. One test-fixture regression risk in the new `Logger.FromContext` implementation on `TestLogger`.

## Improvements
:yellow_circle: [test-quality] `TestLogger.FromContext` returns a fresh logger and drops captured log calls in pkg/plugins/log/fake.go:46 (confidence: 85)
`func (f *TestLogger) FromContext(_ context.Context) Logger { return NewTestLogger() }` discards the receiver `f` and returns a brand-new `TestLogger`. Any test that wires a `TestLogger` into a middleware and then asserts on `f.DebugLogs` / `f.InfoLogs` / `f.WarnLogs` / `f.ErrorLogs` will silently fail to observe log calls that flow through `m.logger.FromContext(ctx).Info(...)` — exactly the new code path now taken by `LoggerMiddleware.logRequest` after this PR. The test-double's capture buffers become disconnected from the production caller, so existing assertions on logged params (status, duration, error, eventName) will see zero calls. The fix is to return the same instance so writes accumulate on the original fixture.
```suggestion
func (f *TestLogger) FromContext(_ context.Context) Logger {
	return f
}
```

## Risk Metadata
Risk Score: 30/100 (LOW) | Blast Radius: localized — plugins client middleware chain only (8 files, no callers outside `pluginsintegration`) | Sensitive Paths: none
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold)
