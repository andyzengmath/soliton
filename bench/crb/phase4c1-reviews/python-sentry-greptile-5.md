## Summary
102 files changed, ~1650 lines added, ~890 lines deleted. 3 findings (2 critical, 1 improvement, 0 nitpicks).
A critical `NameError` in `commit_context.py` will break every merged-PR comment posted by the GitHub and GitLab integrations, and a sparse-dict ordering bug in the replay breadcrumb summarizer can silently pair error IDs with the wrong event payloads.

## Critical

:red_circle: [cross-file-impact] Static method `get_merged_pr_single_issue_template` references undefined `PRCommentWorkflow` — raises NameError at runtime for both GitHub and GitLab PR comments in src/sentry/integrations/source_code_management/commit_context.py:288 (confidence: 95)
The new `@staticmethod get_merged_pr_single_issue_template` is defined on `CommitContextIntegration`, but its body calls `PRCommentWorkflow._truncate_title(title)`. `PRCommentWorkflow` is not imported in `commit_context.py` and is a separate subclass hierarchy, so this raises `NameError: name 'PRCommentWorkflow' is not defined` the first time it executes. Both `GitHubPRCommentWorkflow.get_comment_body` and `GitlabPRCommentWorkflow.get_comment_body` were updated in this same PR to call `self.get_merged_pr_single_issue_template(...)`, so every merged-PR comment path in both integrations is broken. `_truncate_title` is already a `@staticmethod` on `CommitContextIntegration` itself, so the correct call is a sibling-class reference.
```suggestion
@staticmethod
def get_merged_pr_single_issue_template(title: str, url: str, environment: str) -> str:
    truncated_title = CommitContextIntegration._truncate_title(title)
    return MERGED_PR_SINGLE_ISSUE_TEMPLATE.format(
        title=truncated_title,
        url=url,
        environment=environment,
    )
```

:red_circle: [correctness] `zip(error_ids, events.values())` misaligns error IDs with nodestore payloads when any node is missing in src/sentry/replays/endpoints/project_replay_summarize_breadcrumbs.py:729 (confidence: 92)
In `fetch_error_details`, `node_ids` is built from `error_ids` in order, then `nodestore.backend.get_multi(node_ids)` is called. `get_multi` returns only found entries — missing keys are absent, not `None`-valued — and the dict insertion order is not part of its contract. The code then `zip`s the original `error_ids` list against `events.values()`, assuming positional 1:1 correspondence. If `error_ids = ["a","b","c"]` and nodestore returns `{"a": data_a, "c": data_c}`, `zip` yields `("a", data_a)` and `("b", data_c)` — error `b` gets `c`'s title, timestamp, and message. The `if data is not None` guard does not help because the sparse dict never contains a `None` entry for the missing key. This silently corrupts the error context attached to replay summaries.
```suggestion
node_ids = [Event.generate_node_id(project_id, event_id=eid) for eid in error_ids]
events = nodestore.backend.get_multi(node_ids)

result = []
for eid in error_ids:
    node_id = Event.generate_node_id(project_id, event_id=eid)
    data = events.get(node_id)
    if data is not None:
        result.append(ErrorEvent(
            category="error",
            id=eid,
            title=data.get("title", ""),
            timestamp=data.get("timestamp", 0.0),
            message=data.get("message", ""),
        ))
return result
```

## Improvements

:yellow_circle: [testing] Null `max_segment_id` fix lacks a direct unit test for `_make_recording_filenames` in src/sentry/replays/usecases/delete.py:817 (confidence: 85)
The null guard added to `_make_recording_filenames` is logically correct, but the only test change is a fixture tweak in the higher-level `test_delete_replays_bulk.py` integration test — it verifies the bulk-delete plumbing doesn't crash, but never asserts the exact empty-list return for the null-`max_segment_id` branch. A direct unit test locks in the contract and prevents regression if the early return is later rewritten.
```suggestion
def test_make_recording_filenames_null_max_segment_id():
    row = {"retention_days": 90, "replay_id": "abc123", "max_segment_id": None, "platform": "javascript"}
    assert _make_recording_filenames(project_id=1, row=row) == []
```

## Risk Metadata
Risk Score: 65/100 (HIGH) | Blast Radius: ~30-40 downstream files (Visualize yAxes→yAxis refactor + shared commit_context base class + workflow processor change); 2 migration files + 1 auth/ file match sensitive-path patterns | Sensitive Paths: src/sentry/tasks/auth/check_auth.py, src/sentry/migrations/0917_convert_org_saved_searches_to_views.py, src/sentry/migrations/0920_convert_org_saved_searches_to_views_revised.py
AI-Authored Likelihood: LOW

(6 additional findings below confidence threshold 85 — covers: browser_reporting_collector serializer mutual-exclusion gap, delayed_workflow `dcg_to_slow_conditions` scope risk, TableWidgetVisualization empty-stub behind feature flag, asymmetric `isVisualize`/`isBaseVisualize` in pageParamsContext, missing tests for delayed_workflow no-slow-condition-groups branch, and the deleted 0917 migration test with no replacement smoke-test.)
