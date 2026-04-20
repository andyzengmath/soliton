## Summary
8 files changed, 137 lines added, 78 lines deleted. 4 findings (0 critical, 4 improvements, 0 nitpicks).
Refactor renames `InstrumentationMiddleware` → `MetricsMiddleware` and extracts a new `ContextualLoggerMiddleware`; the split is clean, but the `LoggerMiddleware` now depends entirely on contextual-attribute propagation, and the `TestLogger.FromContext` implementation silently drops inspection state.

## Improvements
:yellow_circle: [correctness] `TestLogger.FromContext` returns a fresh logger, breaking log inspection in tests in pkg/plugins/log/fake.go:46 (confidence: 92)
`TestLogger` records calls into fields such as `DebugLogs`, `InfoLogs`, `WarnLogs`, `ErrorLogs` so that tests can assert on what was logged. The new `FromContext` implementation returns `NewTestLogger()` — a brand-new `TestLogger` with empty state — instead of `f`. Any caller that logs via `logger.FromContext(ctx).Info(...)` (which is precisely what the updated `LoggerMiddleware.logRequest` now does on every request) will write into a discarded logger, so tests inspecting the original `TestLogger` will see zero calls. This is a latent regression: no test in this PR exercises the path, but once a test plugs a `TestLogger` into a component that uses `FromContext`, assertions will silently fail to fire. Return the receiver and let it record as before.
```suggestion
func (f *TestLogger) FromContext(_ context.Context) Logger {
	return f
}
```

:yellow_circle: [testing] Observability fields removed from `logRequest` log line with no test asserting they still appear in pkg/services/pluginsintegration/clientmiddleware/logger_middleware.go:50 (confidence: 88)
The previous `logRequest` wrote `pluginId`, `endpoint`, `uname`, `traceID`, `dsName`, `dsUID` directly into `logParams`. The new version drops all six and instead relies on `m.logger.FromContext(ctx).Info(...)` picking them up from contextual attributes set by `ContextualLoggerMiddleware` (plus traceID being auto-injected by the underlying `pkg/infra/log`). That *should* produce an equivalent log line, but the contract is invisible: it depends on (a) `ContextualLoggerMiddleware` running before `LoggerMiddleware` in the chain, (b) `log.WithContextualAttributes` keys being surfaced by `ConcreteLogger.FromContext`, and (c) traceID still being picked up from OTel context at emission time. `traceID` in particular is no longer added explicitly — if the infra logger's automatic trace-ID injection is not wired (or is formatter-dependent), downstream dashboards and alerts keyed on these fields will silently lose data. Add a test in `logger_middleware_test.go` (or an integration test for the middleware stack) that captures the emitted log line and asserts all six keys are present with the expected values.

:yellow_circle: [testing] New `ContextualLoggerMiddleware` ships without any unit tests in pkg/services/pluginsintegration/clientmiddleware/contextual_logger_middleware.go:1 (confidence: 85)
This file introduces 69 lines of new production code — the middleware that all contextual log enrichment now depends on — and no test file is added. The rename of `instrumentation_middleware_test.go` → `metrics_middleware_test.go` only swaps the constructor symbol; it does not cover the new behavior. Minimum coverage should assert: (1) each of `QueryData`, `CallResource`, `CheckHealth`, `CollectMetrics` adds the expected attributes (`endpoint`, `pluginId`, and — when non-nil — `dsName`, `dsUID`, `uname`) to the context passed to `next`; (2) `SubscribeStream`/`PublishStream`/`RunStream` pass context through unmodified (current behavior, but should be pinned); (3) `pCtx.DataSourceInstanceSettings == nil` and `pCtx.User == nil` are handled without panics.

:yellow_circle: [correctness] `grafanaInfraLogWrapper.FromContext` silently drops context when the type assertion fails in pkg/plugins/log/logger.go:48 (confidence: 78)
On a failed type assertion (`d.l.FromContext(ctx).(*log.ConcreteLogger)` returns `ok == false`) the fallback is `d.New()`, which constructs a fresh logger with *no* context at all. That means an unexpected wrapper around `log.Logger` would cause every contextual log line in the plugin stack to quietly lose pluginId/endpoint/traceID — the exact fields this PR is redesigning to preserve. If the type assertion is truly unreachable under the current infra-log contract, replace the branch with a panic/assert to make the invariant load-bearing; otherwise wrap the returned `log.Logger` in a new `grafanaInfraLogWrapper` (by storing `log.Logger` rather than `*log.ConcreteLogger`) so the chain survives an implementation swap.
```suggestion
func (d *grafanaInfraLogWrapper) FromContext(ctx context.Context) Logger {
	concreteInfraLogger, ok := d.l.FromContext(ctx).(*log.ConcreteLogger)
	if !ok {
		// Fail loudly: the infra logger contract is that FromContext returns a *ConcreteLogger.
		// A silent fallback here would drop pluginId/endpoint/traceID from every plugin log line.
		panic("plugins/log: infra log.FromContext did not return *log.ConcreteLogger")
	}
	return &grafanaInfraLogWrapper{
		l: concreteInfraLogger,
	}
}
```

## Risk Metadata
Risk Score: 40/100 (MEDIUM) | Blast Radius: `Logger` interface gains `FromContext` (internal to `pkg/plugins/log`, 2 known implementers both updated); middleware chain order now load-bearing for log-field preservation | Sensitive Paths: none
AI-Authored Likelihood: LOW
