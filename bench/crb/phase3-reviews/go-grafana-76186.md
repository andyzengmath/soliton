## Summary
8 files changed, 137 lines added, 78 lines deleted. 4 findings (0 critical, 1 improvement, 3 nitpicks).
Mechanical rename of `InstrumentationMiddleware` → `MetricsMiddleware` plus extraction of contextual-logger wiring into a new `ContextualLoggerMiddleware`; behavior-preserving except for a minor log-field regression around `traceID`/pluginCtx attributes that now depend on correct middleware ordering and on `infra/log.FromContext` auto-attaching trace info.

## Improvements
:yellow_circle: [correctness] `traceID` may silently drop from "Plugin Request Completed" logs in `pkg/services/pluginsintegration/clientmiddleware/logger_middleware.go`:49 (confidence: 82)
The old `LoggerMiddleware.logRequest` explicitly called `tracing.TraceIDFromContext(ctx, false)` and appended `"traceID"` to `logParams`. The new code removes that append and relies on `m.logger.FromContext(ctx).Info(...)` to surface the trace ID. This only works if `grafanaInfraLogWrapper.FromContext` → `infra/log.ConcreteLogger.FromContext` auto-attaches trace fields from the context. If it does not (or if the type assertion in `grafanaInfraLogWrapper.FromContext` falls through to `d.New()`), the `traceID` field is lost from plugin request logs — a silent observability regression. Verify with a test that asserts `traceID` is still emitted, or re-add explicit extraction in `logRequest`.
```suggestion
	logParams := []any{
		"status", status,
		"duration", time.Since(start),
		"eventName", "grafana-data-egress",
		"time_before_plugin_request", timeBeforePluginRequest,
	}
	if traceID := tracing.TraceIDFromContext(ctx, false); traceID != "" {
		logParams = append(logParams, "traceID", traceID)
	}
	if status == statusError {
		logParams = append(logParams, "error", err)
	}
	m.logger.FromContext(ctx).Info("Plugin Request Completed", logParams...)
```

## Nitpicks
:white_circle: [correctness] `grafanaInfraLogWrapper.FromContext` silently discards context on type-assertion failure in `pkg/plugins/log/logger.go`:48 (confidence: 70)
```go
concreteInfraLogger, ok := d.l.FromContext(ctx).(*log.ConcreteLogger)
if !ok {
    return d.New()
}
```
If `d.l.FromContext(ctx)` ever returns a different concrete type (e.g. a test double or a wrapped logger), the fallback returns `d.New()` — a fresh logger with no context attributes. That means contextual attrs added by `ContextualLoggerMiddleware` (pluginId, endpoint, dsName, dsUID, uname) would vanish from the log line with no diagnostic. Consider at minimum a debug/warn log on the fallback, or return `d` unchanged so the caller retains a usable logger — either is safer than a silently context-less logger.

:white_circle: [testing] No dedicated test for the new `ContextualLoggerMiddleware` in `pkg/services/pluginsintegration/clientmiddleware/contextual_logger_middleware.go`:1 (confidence: 75)
The new middleware is load-bearing: it is the sole place where plugin-context attributes (pluginId, endpoint, dsName, dsUID, uname) are attached to the request context, and the `LoggerMiddleware`/plugin logs depend on it. There is no unit test verifying that `instrumentContext` actually puts the expected key/values into the context, nor that `QueryData`/`CallResource`/`CheckHealth`/`CollectMetrics` each call it (and that the stream methods do not). Adding a focused test would lock in the contract the `LoggerMiddleware` now implicitly depends on.

:white_circle: [consistency] Stream methods silently skip contextual attribution in `pkg/services/pluginsintegration/clientmiddleware/contextual_logger_middleware.go`:51 (confidence: 55)
`SubscribeStream`, `PublishStream`, and `RunStream` pass through without calling `instrumentContext`. That matches prior behavior of `LoggerMiddleware` (which also did not log stream endpoints), so it is intentional, but a one-line comment on each — or a single comment on the type — would help future readers understand that streams are deliberately excluded rather than an oversight.

## Risk Metadata
Risk Score: 18/100 (LOW) | Blast Radius: internal only — `pkg/plugins/log` (interface addition) and `pkg/services/pluginsintegration/clientmiddleware` (rename + new middleware), no cross-module API surface, no callers outside repo; existing test file renamed with updated constructor reference | Sensitive Paths: none hit (no auth/security/payment/migration/secrets)
AI-Authored Likelihood: LOW — idiomatic Go, consistent style with surrounding middleware files, no hallucinated APIs, patterns match the rest of `clientmiddleware/`

## Recommendation
Approve with minor follow-ups. The `traceID` regression is the only non-trivial concern; everything else is a nit. The PR is a well-scoped refactor, already has three upstream approvals, and correctly sequences the new `ContextualLoggerMiddleware` before `LoggerMiddleware` in `pluginsintegration.go:159`.

---
_Metadata_: synthesized review, 8 files / 215 lines / 4 findings · risk 18/100 LOW · review duration ≈ 109s · no comments posted upstream (local CRB benchmark run).
