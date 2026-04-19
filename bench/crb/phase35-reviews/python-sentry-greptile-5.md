## Summary
103 files changed, ~1700 lines added, ~900 lines deleted. 10 findings (4 critical, 6 improvements, 0 nitpicks).
Most urgent: `zip(error_ids, events.values())` in `fetch_error_details` misaligns event IDs with data whenever nodestore returns a partial result; several explore-hook refactors (`Visualize.fromJSON`, `useTraceItemAttributeKeys`, attribute-key scoping) change public contracts with broad blast radius.

## Critical
:red_circle: [correctness] zip(error_ids, events.values()) assigns wrong event IDs when nodestore returns a partial result in src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:715 (confidence: 92)
`nodestore.backend.get_multi(node_ids)` returns a dict keyed by node_id containing only the entries that exist in the store. When some events are missing, `events` has fewer entries than `error_ids`. The code then does `zip(error_ids, events.values())`, which silently truncates to the shorter iterable. The `if data is not None` guard never fires because missing events are simply absent from the dict — they do not appear as `None` values. As a result, every `ErrorEvent` after the first missing event gets its `id` field paired with the wrong event's data (shifted by however many earlier entries were absent). This produces subtly corrupted error context in the replay breadcrumb summary. Additionally, even when all events are present, `events.values()` iterates in the insertion order of the dict returned by `get_multi`, which is keyed by node_id (not event_id).
```suggestion
def fetch_error_details(project_id: int, error_ids: list[str]) -> list[ErrorEvent]:
    try:
        node_id_to_event_id = {
            Event.generate_node_id(project_id, event_id=eid): eid for eid in error_ids
        }
        node_ids = list(node_id_to_event_id.keys())
        events = nodestore.backend.get_multi(node_ids)
        return [
            ErrorEvent(
                category="error",
                id=node_id_to_event_id[node_id],
                title=data.get("title", ""),
                timestamp=data.get("timestamp", 0.0),
                message=data.get("message", ""),
            )
            for node_id, data in events.items()
            if data is not None
        ]
    except Exception as e:
        sentry_sdk.capture_exception(e)
        return []
```

:red_circle: [cross-file-impact] UseTraceItemAttributeBaseProps moved out of this file — any external import breaks in static/app/views/explore/hooks/useTraceItemAttributeKeys.tsx:19 (confidence: 95)
`UseTraceItemAttributeBaseProps` was exported from this file and is now moved to `static/app/views/explore/types.tsx`. Any file importing it from the old path (other than the ones updated in this PR) will produce a TypeScript compile error at build time. The PR updates the consumer at `useGetTraceItemAttributeValues.tsx`, but the symbol was part of the public surface of the old hook module and other call sites may still reference it.
```suggestion
// In any remaining file that still imports from the old path, change:
//   import type {UseTraceItemAttributeBaseProps} from 'sentry/views/explore/hooks/useTraceItemAttributeKeys';
// to:
import type {UseTraceItemAttributeBaseProps} from 'sentry/views/explore/types';
```

:red_circle: [cross-file-impact] useTraceItemAttributeKeys now returns `attributes: undefined` on first render instead of `{}`, breaking callers in static/app/views/explore/hooks/useTraceItemAttributeKeys.tsx:21 (confidence: 92)
The refactored hook returns `{ attributes: isFetching ? previous : data, error, isLoading }` where `data` comes from `useQuery<TagCollection>`. On the first render before any data is fetched, `data` is `undefined` and `previous` (from `usePrevious`) is also `undefined`, so `attributes` will be `undefined`. Previously the hook always returned a `TagCollection` object (defaulted to `{}` via useMemo). Any caller doing `attributes[key]` or iterating over `attributes` without a null-check will throw at runtime.
```suggestion
return {
  attributes: (isFetching ? previous : data) ?? {},
  error,
  isLoading,
};
```

:red_circle: [cross-file-impact] Visualize.fromJSON() return-type change from Visualize to Visualize[] may break unmigrated callers in static/app/views/explore/contexts/pageParamsContext/index.tsx:375 (confidence: 88)
`Visualize.fromJSON()` now returns `Visualize[]` instead of a single `Visualize`. The PR updates several call sites (for example `aggregateFields.tsx` already uses `.flatMap` to handle this), but the refactor also changes the `Visualize` constructor from `(yAxes: readonly string[], ...)` to `(yAxis: string, ...)` and renames the `yAxes` property to `yAxis`. Any remaining call site that assigns the result of `fromJSON` to a single `Visualize` variable or accesses `.yAxis`, `.clone()`, or `.replace()` on the result will fail — either at type-check time or at runtime when methods are invoked on the array.
```suggestion
// At each Visualize.fromJSON call site, either:
const visualizes: Visualize[] = Visualize.fromJSON(aggregateField);
// or, when a single value is expected:
const [visualize] = Visualize.fromJSON(aggregateField);
```

:red_circle: [cross-file-impact] Old useTraceItemAttributeValues.attributeKey scoping removed — TransactionNameSearchBar may now suggest non-transaction attribute values in static/app/views/insights/pages/transactionNameSearchBar.tsx:51 (confidence: 85)
The old `useTraceItemAttributeValues` took an `attributeKey` parameter (e.g. `'transaction'`) that scoped value fetching to a specific attribute. The new `useGetTraceItemAttributeValues` exposes a generic `getTraceItemAttributeValues(queryString?)` function with no attribute-key parameter. If the underlying `/organizations/${org}/trace-items/attributes/values/` endpoint returns unscoped values, the search bar will display span-attribute values that are not transaction names, changing product behavior silently.
```suggestion
// Confirm the new hook still scopes to the transaction attribute, e.g.:
const getTraceItemAttributeValues = useGetTraceItemAttributeValues({
  traceItemType: TraceItemDataset.SPANS,
  projectIds,
  attributeKey: 'transaction',
});
```

## Improvements
:yellow_circle: [cross-file-impact] Feature-flagged TableWidgetVisualization renders hardcoded empty columns and empty tableData in static/app/views/dashboards/widgetCard/chart.tsx:2035 (confidence: 82)
When `organization.features.includes('use-table-widget-visualization')` is true, `TableWidgetVisualization` is rendered with `columns={[]}` and `tableData={{ data: [], meta: { fields: {}, units: {} } }}` instead of the real `result.data`, `result.meta`, and `fields` that are available in scope. Any org with this flag enabled will see a blank table in production rather than the existing `StyledSimpleTableChart` output.
```suggestion
<TableWidgetVisualization
  columns={fields}
  tableData={{data: result.data, meta: result.meta}}
/>
```

:yellow_circle: [test-quality] _truncate_title missing edge-case tests (empty, boundary, unicode) in src/sentry/integrations/source_code_management/commit_context.py:265 (confidence: 85)
The new `_truncate_title` static method is only exercised through end-to-end PR-comment tests. Empty input, exact-boundary input (length == `ISSUE_TITLE_MAX_LENGTH`), over-boundary input, and multi-byte unicode truncation are not directly tested, so subtle regressions in the truncation contract cannot be caught.
```suggestion
def test_truncate_title_short():
    assert PRCommentWorkflow._truncate_title("short") == "short"

def test_truncate_title_exact_boundary():
    title = "a" * ISSUE_TITLE_MAX_LENGTH
    assert PRCommentWorkflow._truncate_title(title) == title

def test_truncate_title_over_boundary():
    title = "a" * (ISSUE_TITLE_MAX_LENGTH + 1)
    result = PRCommentWorkflow._truncate_title(title)
    assert result.endswith("...")

def test_truncate_title_empty():
    assert PRCommentWorkflow._truncate_title("") == ""
```

:yellow_circle: [cross-file-impact] Environment filtering dropped from useTraceItemAttributeKeys — scope change is a silent behavioral regression in static/app/views/explore/hooks/useTraceItemAttributeKeys.tsx:62 (confidence: 80)
The old implementation included `environment: selection.environments` in the endpoint options. The refactored `makeTraceItemAttributeKeysQueryOptions` omits environment with an inline comment "environment left out intentionally as it's not supported". Consumers that previously received environment-scoped keys (for example the spans explore search query builder) will now see a broader set that includes keys from environments the user is not currently viewing.
```suggestion
// In the hook's JSDoc, document the contract change:
/**
 * Returns attribute keys for the current selection. Note: environment filtering
 * is intentionally not applied by this hook because the backend endpoint does
 * not yet support it. Callers that require environment scoping must filter
 * downstream or use a more specific query.
 */
```

:yellow_circle: [cross-file-impact] ParameterizationRegexExperiment removed + grouping.experiments.parameterization.traceparent option deregistered — external readers will break in src/sentry/grouping/parameterization.py:147 (confidence: 78)
`ParameterizationRegexExperiment` is deleted and `ParameterizationExperiment` is narrowed to `ParameterizationCallableExperiment`. External imports of `ParameterizationRegexExperiment` will raise `ImportError`. Any call to `options.get("grouping.experiments.parameterization.traceparent")` will raise `UnknownOption` since the option was removed from `options/defaults.py`.
```suggestion
# Audit the repo for remaining references, then remove them:
#   grep -rn "ParameterizationRegexExperiment" src/ tests/
#   grep -rn "grouping.experiments.parameterization.traceparent" src/ tests/
```

:yellow_circle: [consistency] Exception handler calls sentry_sdk.capture_exception without an accompanying logger call in src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:732 (confidence: 75)
Elsewhere in the same file (lines 280–283) the established pattern combines a `logger.*` call with `sentry_sdk.capture_exception`. The new `fetch_error_details` handler only calls `capture_exception(e)`, so the error is reported to Sentry but no local log line is emitted. This makes debugging from application logs harder and is inconsistent with the rest of the file.
```suggestion
except Exception as e:
    logger.exception("Failed to fetch error details")
    sentry_sdk.capture_exception(e)
    return []
```

## Risk Metadata
Risk Score: 69/100 (HIGH) | Blast Radius: ~25 importer files across the changeset (capped at 100; Visualize yAxes→yAxis touches 15+ explore files; commit_context base imported by github+gitlab integrations) | Sensitive Paths: migrations/0917, migrations/0920, src/sentry/tasks/auth/check_auth.py
AI-Authored Likelihood: LOW

(6 additional findings below confidence threshold)
