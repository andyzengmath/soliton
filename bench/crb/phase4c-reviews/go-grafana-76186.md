## Summary
8 files changed, 137 lines added, 78 lines deleted. 3 findings (0 critical, 2 improvements, 1 nitpick).
Low-risk refactor that renames `InstrumentationMiddleware` → `MetricsMiddleware` and extracts contextual-logger enrichment into its own middleware; a couple of consistency/correctness follow-ups below.

## Improvements

:yellow_circle: [correctness] `traceID` silently dropped from plugin-request log line in `pkg/services/pluginsintegration/clientmiddleware/logger_middleware.go:48` (confidence: 86)
The previous `logRequest` explicitly pulled the trace ID via `tracing.TraceIDFromContext(ctx, false)` and appended it to `logParams`. The new code deletes that block and relies on `m.logger.FromContext(ctx).Info(...)` to surface it. `plog.Logger.FromContext` in `pkg/plugins/log/logger.go:48` forwards to the grafana infra logger's `FromContext`, which attaches *contextual attributes* (set via `log.WithContextualAttributes`) — and `instrumentContext` in the new `ContextualLoggerMiddleware` does **not** add `traceID` to those attributes. Net effect: the `grafana-data-egress` log line loses the `traceID` field that downstream log pipelines likely key on. Either re-emit `traceID` explicitly in `logRequest`, or add it in `instrumentContext` alongside `endpoint`/`pluginId`.
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

:yellow_circle: [consistency] Test function still named `TestInstrumentationMiddleware` in `pkg/services/pluginsintegration/clientmiddleware/metrics_middleware_test.go:1` (confidence: 90)
The file was renamed from `instrumentation_middleware_test.go` to `metrics_middleware_test.go` and the sole constructor call was updated to `newMetricsMiddleware`, but the top-level test function is still `TestInstrumentationMiddleware`. Since the whole point of this PR is the rename, leaving the test identifier on the old name defeats grep-ability and will produce a second follow-up commit. Rename to `TestMetricsMiddleware` (and update any doc-link aliases) in the same changeset.
```suggestion
func TestMetricsMiddleware(t *testing.T) {
```

## Risk Metadata
Risk Score: 22/100 (LOW) | Blast Radius: 8 files across `pkg/plugins/log` (interface change) and `pkg/services/pluginsintegration/clientmiddleware` (middleware chain) | Sensitive Paths: none hit
AI-Authored Likelihood: LOW

(1 additional finding below confidence threshold: `grafanaInfraLogWrapper.FromContext` fallback on `!ok` type assertion returns `d.New()` rather than `d`, silently discarding the wrapper's existing logger state if the infra `FromContext` ever returns a non-`*log.ConcreteLogger` — low likelihood in practice, but the safer fallback is `return d`.)
