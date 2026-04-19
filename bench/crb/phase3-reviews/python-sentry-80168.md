## Summary
4 files changed, 249 lines added, 151 lines deleted. 8 findings (2 critical, 5 improvements, 1 nitpick).
Hook for per-detector occurrence production is well-scoped, but the new `MetricAlertDetectorHandler: pass` can't instantiate under ABCMeta and the `evaluate`/`process_detectors` return-type change from list to dict is a breaking contract shift without visible caller updates.

## Critical

:red_circle: [correctness] `MetricAlertDetectorHandler` will fail to instantiate in `src/sentry/incidents/grouptype.py:11` (confidence: 95)
Changing the parent from `DetectorHandler` (where a concrete `evaluate` was supplied) to `StatefulDetectorHandler` with only `pass` leaves three abstract methods unimplemented: `get_dedupe_value`, `get_group_key_values`, and the newly-added `build_occurrence_and_event_data`. `DetectorHandler` uses `abc.ABC`, so any code resolving `Detector.detector_handler` for a metric-alert group type — including `Detector.detector_handler` at `src/sentry/workflow_engine/models/detector.py:63` which instantiates the class — will raise `TypeError: Can't instantiate abstract class MetricAlertDetectorHandler with abstract methods build_occurrence_and_event_data, get_dedupe_value, get_group_key_values`. Previously this class was a functional no-op returning `[]`; now it is a latent crash. Either keep it on `DetectorHandler` with a stub `evaluate` until the stateful implementation lands, or provide concrete implementations of the three abstracts (even raising `NotImplementedError` in each would at least be explicit).
```suggestion
class MetricAlertDetectorHandler(StatefulDetectorHandler[QuerySubscriptionUpdate]):
    def get_dedupe_value(self, data_packet):
        raise NotImplementedError
    def get_group_key_values(self, data_packet):
        raise NotImplementedError
    def build_occurrence_and_event_data(self, group_key, value, new_status):
        raise NotImplementedError
```

:red_circle: [cross-file-impact] Breaking return-type change without caller audit in `src/sentry/workflow_engine/processors/detector.py:48` (confidence: 85)
Both `process_detectors` (returns `list[tuple[Detector, dict[...]]]`) and `DetectorHandler.evaluate` (returns `dict[DetectorGroupKey, DetectorEvaluationResult]`) changed from list-shaped to dict-shaped, but this PR only updates call sites inside the test file. Any external caller doing `for r in handler.evaluate(...)` now iterates over group-key strings and `r.priority` / `r.result` will raise `AttributeError: 'str' object has no attribute ...`. Subscript-style access (`results[0]`) now raises `KeyError: 0`. Emptiness checks (`if not results`) still work, so the regression is silent in happy-path cases and only fires when a detector produces results. Audit required: grep the repo for `.evaluate(` on `DetectorHandler` / `StatefulDetectorHandler` subclasses and for iteration over `process_detectors(...)` results in any consumer/task module, and update to `.values()` iteration.

## Improvements

:yellow_circle: [correctness] Lost diagnostic for duplicate group keys in `src/sentry/workflow_engine/processors/detector.py:58` (confidence: 80)
The removed block logged `"Duplicate detector state group keys found"` when a handler produced the same `group_key` twice. The new dict-keyed return silently overwrites duplicates (last-wins, insertion-order dependent), hiding handler bugs. The corresponding `test_state_results_multi_group_dupe` test was also deleted. If duplicates can't happen by construction (dict input in `get_group_key_values`), that's fine — but `evaluate_group_key_value` returns a `DetectorEvaluationResult(group_key=group_key, ...)` where group_key is the iteration key, so duplicates truly are impossible today. However, the defensive log caught class-of-bug problems for future handler implementers. Consider retaining a cheap `assert` or debug-level log when building the dict to preserve the signal.

:yellow_circle: [correctness] Bare annotation removes defensive initializer in `src/sentry/workflow_engine/processors/detector.py:289` (confidence: 70)
Replacing `result = None` with `result: StatusChangeMessage | IssueOccurrence` (no assignment) is fine today because the if/else is exhaustive, but it removes defense against future refactors that introduce a third branch or early return. Also, `DetectorEvaluationResult.result` was previously accepting `None`; the new annotation narrows the local type and may diverge from the dataclass field type. Either restore the default or tighten the field type explicitly.
```suggestion
            event_data: dict[str, Any] | None = None
            result: StatusChangeMessage | IssueOccurrence | None = None
```

:yellow_circle: [testing] `build_mock_occurrence_and_event` ignores its `value` parameter in `tests/sentry/workflow_engine/processors/test_detector.py:458` (confidence: 85)
The helper accepts `value: int` but never references it — only `new_status.value` is used (for `initial_issue_priority`). Call sites like line 382 pass `value=6` when the data packet actually contains `{"group_2": 10}`. Tests pass because the helper is consulted for both expected and actual objects, but this silently masks any future production divergence where `value` feeds evidence or fingerprint data. Either thread `value` into `evidence_data` / `subtitle` / etc. or drop the parameter from the helper signature.

:yellow_circle: [consistency] Redundant `GroupType` import in `src/sentry/workflow_engine/models/detector.py:13` (confidence: 90)
`from sentry.issues import grouptype` is already imported on the next line and used as `grouptype.registry`. Adding `from sentry.issues.grouptype import GroupType` for a single type hint is inconsistent — use `grouptype.GroupType` instead to keep the access pattern uniform.
```suggestion
-from sentry.issues.grouptype import GroupType
 from sentry.models.owner_base import OwnerModel
 ...
-    def group_type(self) -> builtins.type[GroupType] | None:
+    def group_type(self) -> builtins.type[grouptype.GroupType] | None:
```

:yellow_circle: [consistency] Unusual `builtins.type[...]` annotation in `src/sentry/workflow_engine/models/detector.py:58` (confidence: 75)
`builtins.type[GroupType]` is used because the `Detector` model has a `self.type` string attribute shadowing the built-in. The codebase convention for class-reference annotations elsewhere in Sentry is `type[GroupType]` at module level (with an alias like `_type = type` or `from typing import Type`). `builtins` is imported just for this one spot. Consider `from typing import Type` and annotating as `Type[GroupType] | None`; it avoids the one-off `builtins` import and reads more naturally.

## Nitpicks

:white_circle: [testing] Hardcoded UUIDs in `tests/sentry/workflow_engine/processors/test_detector.py:465-467` (confidence: 60)
`id="eb4b0acffadb4d098d48cb14165ab578"` and `event_id="43878ab4419f4ab181f6379ac376d5aa"` are fine for isolated unit tests but will collide if two occurrences are built inside one test and persisted with unique constraints. Prefer `uuid.uuid4().hex` if any test later stores these or emits them to Kafka for inspection.

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: core detector abstraction touched; 4 files, ~580 diff lines; breaking API shape change to `evaluate`/`process_detectors`; new abstract method forces all existing subclasses to update | Sensitive Paths: none (no auth/secrets/migrations)
AI-Authored Likelihood: LOW — idiomatic Sentry refactor style, mixed test patch hunks, explicit `TODO`-driven motivation match a human contributor.

Metadata: 4 agents dispatched (risk-scorer, correctness, cross-file-impact, consistency), 4 completed, 0 failed. Review compiled from diff-only (shim repo) — caller audit for the breaking signature change could not be run against the live repo and is called out as an action item in Finding 2.
