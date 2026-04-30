## PR Review — Soliton Automated Analysis

**Summary:** 100 files changed, ~1900 lines added, ~1000 lines deleted. 7 critical findings, 3 improvements, 0 nitpicks (per Phase 3.5 config). Most urgent: hardcoded empty data rendered through a feature-flagged table widget path; several breaking API-surface changes in `explore/` that will silently corrupt state or cause runtime failures at unpatched call sites.

Warning: 1/5 agents failed. The correctness agent timed out before producing findings. This review may be missing additional correctness issues, particularly in the new endpoint validators and explore-view logic changes flagged by the risk assessment.

## Critical

:red_circle: [cross-file-impact] Hardcoded empty columns and data in TableWidgetVisualization feature-flag branch in static/app/views/dashboards/widgetCard/chart.tsx:161 (confidence: 95)
The new `organization.features.includes('use-table-widget-visualization')` branch renders `<TableWidgetVisualization columns={[]} tableData={{ data: [], meta: { fields: {}, units: {} } }} />` with hardcoded empty values. The real `result.data`, `result.meta`, `fieldAliases`, and `eventView` are already in scope but are not threaded through. Any org or user with this feature flag enabled will see every table widget render as blank — a silent data-loss regression. Do not ship the flag enabled until corrected.
```suggestion
// Pass actual data/meta/columns from the surrounding scope:
// <TableWidgetVisualization
//   columns={derivedColumns}
//   tableData={{ data: result.data, meta: result.meta }}
//   eventView={eventView}
//   fieldAliases={fieldAliases}
// />
```

:red_circle: [cross-file-impact] Hook renamed to useGetTraceItemAttributeValues — old import path will runtime-fail; removed `enabled` prop also unleashes unconditional fetches in static/app/views/explore/hooks/useGetTraceItemAttributeValues.tsx:1 (confidence: 95)
`useTraceItemAttributeValues.tsx` was renamed to `useGetTraceItemAttributeValues.tsx` and its exported function renamed. The props `attributeKey`, `enabled`, and `search` were also removed from `UseGetTraceItemAttributeValuesProps`. Two known callers were updated, but any import site outside the diff referencing the old name will fail with a module-not-found error at runtime. The removal of `enabled` is particularly dangerous: call sites that previously gated execution via `enabled: false` will now begin issuing unintended network requests as soon as the component mounts. Audit every existing call site, update the import path/name, and replace `enabled` with conditional rendering at the caller.
```suggestion
// Replace each old call site:
// const data = useTraceItemAttributeValues({ attributeKey, enabled: shouldFetch, search });
// with:
// const data = shouldFetch
//   ? useGetTraceItemAttributeValues({ /* new props */ })
//   : undefined;
```

:red_circle: [cross-file-impact] Visualize constructor changed from yAxes array to single yAxis string — array callers silently corrupt state in static/app/views/explore/contexts/pageParamsContext/visualizes.tsx:48 (confidence: 95)
`new Visualize(yAxes: readonly string[], ...)` was changed to `new Visualize(yAxis: string, ...)`. Any call site still passing an array literal will assign the array object to `this.yAxis` instead of a string. TypeScript catches this at compile time, but a missed import alias or a `// @ts-ignore` will let it through to runtime, producing corrupt visualize state silently. Search the repo for `new Visualize([` — each match is a broken caller that must be updated to pass a single string.
```suggestion
// Each broken call site:
// new Visualize(['count(span.duration)'])  // BROKEN — passes array
// must become:
// new Visualize('count(span.duration)')   // OK — single string
```

:red_circle: [cross-file-impact] Visualize.fromJSON() return type changed from Visualize to Visualize[] — single-item access now operates on an array in static/app/views/explore/contexts/pageParamsContext/visualizes.tsx:197 (confidence: 92)
`static fromJSON(json: BaseVisualize): Visualize` now returns `Visualize[]`. Call sites doing `const v = Visualize.fromJSON(json); v.yAxis` are now accessing `.yAxis` on an array, returning `undefined` at runtime. Internal usages were updated; any external consumer not in the diff will silently break. Search for `Visualize.fromJSON(` and update every site to handle the returned array.
```suggestion
// Old: const v = Visualize.fromJSON(json); v.yAxis
// New: const visualizes = Visualize.fromJSON(json); visualizes.flatMap(v => v.yAxis)
```

:red_circle: [cross-file-impact] Visualize.replace() parameter renamed from yAxes to yAxis — old callers silently ignored in static/app/views/explore/contexts/pageParamsContext/visualizes.tsx:178 (confidence: 90)
`replace()` previously accepted `{ yAxes?: string[] }` and now accepts `{ yAxis?: string }`. Callers still passing `{ yAxes: ... }` will have the parameter silently ignored in JavaScript with no runtime error, leaving the visualize with its stale `yAxis` value. There is no error signal for this regression, making it likely to slip into production. Search for `\.replace\(\{.*yAxes` — update each to `yAxis` (singular string).
```suggestion
// Old: visualize.replace({ yAxes: ['count(span.duration)'] })
// New: visualize.replace({ yAxis: 'count(span.duration)' })
```

:red_circle: [cross-file-impact] UseTraceItemAttributeBaseProps moved to types.tsx — old import path fails to compile in static/app/views/explore/hooks/useTraceItemAttributeKeys.tsx:1 (confidence: 88)
`UseTraceItemAttributeBaseProps` was previously exported from `useTraceItemAttributeKeys.tsx` and has been moved to `static/app/views/explore/types.tsx`. Any file importing the type from the old path gets a TypeScript error and will fail to compile. Search for imports of `UseTraceItemAttributeBaseProps` from `'sentry/views/explore/hooks/useTraceItemAttributeKeys'` and update them to `'sentry/views/explore/types'`.
```suggestion
// Old:
// import type {UseTraceItemAttributeBaseProps} from 'sentry/views/explore/hooks/useTraceItemAttributeKeys';
// New:
// import type {UseTraceItemAttributeBaseProps} from 'sentry/views/explore/types';
```

:red_circle: [cross-file-impact] Cross-class static call: get_merged_pr_single_issue_template references PRCommentWorkflow._truncate_title which may not exist on that class in src/sentry/integrations/source_code_management/commit_context.py:287 (confidence: 87)
`get_merged_pr_single_issue_template` is added as a `@staticmethod` on `CommitContextIntegration` but calls `PRCommentWorkflow._truncate_title(title)`. `_truncate_title` is also defined as a staticmethod on `CommitContextIntegration` in this diff. If `PRCommentWorkflow` is a sibling class (not a subclass of `CommitContextIntegration`) — which the diff suggests via `GitHubPRCommentWorkflow(PRCommentWorkflow)` and `GitlabPRCommentWorkflow(PRCommentWorkflow)` defined as separate hierarchies — this call will raise `AttributeError` at runtime. The hallucination and cross-file-impact agents both flagged this independently. Either call `CommitContextIntegration._truncate_title(title)` (or just `cls._truncate_title(title)`), or move this template method onto `PRCommentWorkflow` where it logically belongs. Verify by running the PR comment code path in tests before merging.
```suggestion
# Replace:
#     return MERGED_PR_SINGLE_ISSUE_TEMPLATE.format(
#         title=PRCommentWorkflow._truncate_title(title),
#         ...
#     )
# with:
#     return MERGED_PR_SINGLE_ISSUE_TEMPLATE.format(
#         title=CommitContextIntegration._truncate_title(title),
#         ...
#     )
# or move both methods onto PRCommentWorkflow.
```

## Improvements

:yellow_circle: [correctness] fetch_error_details swallows nodestore errors — empty list is indistinguishable from legitimate no-errors in src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:717 (confidence: 85)
`fetch_error_details` catches all exceptions with a bare `except Exception` and returns `[]`. The caller passes this directly to `analyze_recording_segments`. A transient nodestore outage silently produces the same AI summary as a clean replay with no indication that error context was missing. `sentry_sdk.capture_exception` fires, but the HTTP response is still 200 with a degraded payload. Return a sentinel value or raise a typed exception so the caller can distinguish "no errors" from "fetch failed". At minimum, change the log level to `warning` and propagate a flag in the response so consumers can annotate the summary as incomplete.
```suggestion
# class ErrorFetchException(Exception): ...
# try:
#     ...
# except Exception as e:
#     sentry_sdk.capture_exception(e)
#     logger.warning("fetch_error_details.failed", exc_info=True)
#     raise ErrorFetchException("nodestore unavailable") from e
```

:yellow_circle: [cross-file-impact] Migration 0920 replaced with no-op — split-state environments require 0921 to be idempotent in src/sentry/migrations/0920_convert_org_saved_searches_to_views_revised.py:1 (confidence: 85)
Migrations 0917 and 0920 had their entire bodies replaced with `return` (no-op), with a comment that the correct logic lives in 0921. Environments where 0917/0920 were previously applied with their original bodies will have partially migrated data; environments that have not yet run them will skip the logic entirely. Migration 0921 must therefore be fully idempotent (e.g. using `update_or_create`) to safely handle both populations. Verify 0921 uses `update_or_create` or equivalent idempotent semantics, confirm it does not assume 0920 performed real work, and add a comment to 0921 documenting this dependency explicitly.
```suggestion
# In 0921: use update_or_create or get_or_create rather than create();
# add a docstring noting that 0917 and 0920 are no-ops and that 0921
# must safely cover both partially-migrated and never-migrated rows.
```

:yellow_circle: [correctness] get_environment_info logs at INFO level on any exception — failures invisible in production alerting in src/sentry/integrations/source_code_management/commit_context.py:271 (confidence: 85)
`get_environment_info` catches all exceptions with bare `except Exception`, logs at `logger.info`, and returns `""`. INFO-level logs are typically not routed to error dashboards, so an unexpected DB error or ORM failure is invisible in production monitoring. Callers building PR comment templates receive a silently incomplete string. Change the log level to `logger.warning` (or `logger.exception`). Narrow caught types where possible (`DatabaseError`, `AttributeError`) to avoid masking programming errors.
```suggestion
# except (DatabaseError, AttributeError) as e:
#     logger.warning(
#         "get_environment_info.error",
#         extra={"issue_id": issue.id},
#         exc_info=True,
#     )
#     return ""
```

## Risk Metadata
Risk Score: ~70/100 (MEDIUM-HIGH) | Blast Radius: 100 files; broad TS API-surface refactor in static/app/views/explore + new Replay/Feedback/PR-comment endpoints + 2 neutered migrations | Sensitive Paths: src/sentry/migrations/, src/sentry/integrations/, src/sentry/replays/endpoints/
AI-Authored Likelihood: N/A

(1 additional finding below confidence threshold)
