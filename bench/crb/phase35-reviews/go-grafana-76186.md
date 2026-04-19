## Summary
8 files changed, 137 lines added, 78 lines deleted. 3 findings (0 critical, 3 improvements, 0 nitpicks).
Clean middleware rename/split refactor; observability wiring relies on implicit middleware ordering and the infra logger preserving trace context.

## Improvements
:yellow_circle: [correctness] TestLogger.FromContext discards captured logs in assertions in pkg/plugins/log/fake.go:44 (confidence: 90)
`TestLogger.FromContext(ctx)` returns `NewTestLogger()` — a brand-new, independent `TestLogger` — so any log calls made via `m.logger.FromContext(ctx).Info(...)` (which is exactly the new call site in `LoggerMiddleware.logRequest`) are written to a throwaway logger instead of the original. Existing or future unit tests that pass a `*TestLogger` into `LoggerMiddleware` and later assert on `testLogger.InfoLogs.Message` / `.Ctx` will silently see empty logs. This defeats the purpose of the test double and can mask real regressions in the plugin request logging (the exact behavior this PR is reorganizing).
```suggestion
func (f *TestLogger) FromContext(_ context.Context) Logger {
	return f
}
```

:yellow_circle: [correctness] Trace ID no longer attached explicitly to "Plugin Request Completed" log in pkg/services/pluginsintegration/clientmiddleware/logger_middleware.go:49 (confidence: 65)
The old `logRequest` explicitly extracted the trace ID with `tracing.TraceIDFromContext(ctx, false)` and appended it to `logParams`. The new version removes this entirely and relies on `m.logger.FromContext(ctx).Info(...)` to carry it. The new `ContextualLoggerMiddleware` only injects `endpoint`, `pluginId`, `dsName`, `dsUID`, `uname` via `log.WithContextualAttributes` — it does **not** add `traceID`. So preservation of `traceID` in the final log now depends on `grafanaInfraLogWrapper.FromContext` → `infra/log.ConcreteLogger.FromContext` extracting the span/trace ID from the context on its own. If the infra logger does not do that (or does so under a different key than the prior `"traceID"`), this PR silently regresses trace-to-log correlation for plugin-request telemetry, which is a high-value observability signal. Please verify by tailing a request log under a real trace and confirming the trace ID is still present, or re-add the explicit `traceID` param.
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

:yellow_circle: [consistency] Implicit ordering contract between ContextualLoggerMiddleware and LoggerMiddleware is undocumented in pkg/services/pluginsintegration/clientmiddleware/contextual_logger_middleware.go:10 (confidence: 75)
`LoggerMiddleware` now produces meaningful structured logs only if `ContextualLoggerMiddleware` ran earlier in the chain (to populate `endpoint`/`pluginId`/`dsName`/`dsUID`/`uname` via `log.WithContextualAttributes`). This ordering is enforced solely by the slice order in `CreateMiddlewares`, with no comment, test, or runtime guard. A future contributor reordering the middleware list (or removing `ContextualLoggerMiddleware` under the mistaken impression that it's a no-op for non-logging paths) would silently strip the identifying fields from every `grafana-data-egress` log, making them useless for per-plugin/per-datasource filtering in Loki/ES. Add a short package- or constructor-level comment stating that `ContextualLoggerMiddleware` must appear before `LoggerMiddleware`, and mirror the comment on `NewLoggerMiddleware`.
```suggestion
// NewContextualLoggerMiddleware creates a new plugins.ClientMiddleware that adds
// a contextual logger to the request context. It MUST be registered before
// LoggerMiddleware in the middleware chain; LoggerMiddleware relies on the
// contextual attributes (pluginId, endpoint, dsName, dsUID, uname) set here.
func NewContextualLoggerMiddleware() plugins.ClientMiddleware {
```

## Risk Metadata
Risk Score: 30/100 (LOW) | Blast Radius: Plugin middleware stack — every plugin request (QueryData, CallResource, CheckHealth, CollectMetrics) flows through these middlewares, so observability regressions would be broad but not data-integrity impacting | Sensitive Paths: none
AI-Authored Likelihood: LOW (multi-round human review conversation with `marefr`, Italian-author commit style, incremental fix in response to "need to make sure `instrumentContext` is always included" feedback)
