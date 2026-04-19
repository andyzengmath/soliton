## Summary
4 files changed, 249 lines added, 151 lines deleted. 5 findings (1 critical, 4 improvements, 0 nitpicks).
Adds a `build_occurrence_and_event_data` hook on `StatefulDetectorHandler` and switches `evaluate` to return a dict keyed by `group_key`; the abstract-method addition silently breaks an existing placeholder subclass, and several runtime/observability risks accompany the signature changes.

## Critical

:red_circle: [correctness] `MetricAlertDetectorHandler` becomes uninstantiable in `src/sentry/incidents/grouptype.py`:14 (confidence: 92)
Switching the base from `DetectorHandler[QuerySubscriptionUpdate]` to `StatefulDetectorHandler[QuerySubscriptionUpdate]` without a body causes `MetricAlertDetectorHandler()` to raise `TypeError: Can't instantiate abstract class` because `get_dedupe_value`, `get_group_key_values`, and the newly-abstract `build_occurrence_and_event_data` are all unimplemented. Previously the class was concrete (its `evaluate` returned `[]`), so any production code path that still constructs this handler via `detector.detector_handler` for `metric_alert_fire` detectors will now crash at instantiation rather than silently no-oping.
```suggestion
# TODO: This will be a stateful detector when we build that abstraction
class MetricAlertDetectorHandler(StatefulDetectorHandler[QuerySubscriptionUpdate]):
    def get_dedupe_value(self, data_packet: DataPacket[QuerySubscriptionUpdate]) -> int:
        return 0

    def get_group_key_values(
        self, data_packet: DataPacket[QuerySubscriptionUpdate]
    ) -> dict[str, int]:
        return {}

    def build_occurrence_and_event_data(
        self, group_key: DetectorGroupKey, value: int, new_status: PriorityLevel
    ) -> tuple[IssueOccurrence, dict[str, Any]]:
        raise NotImplementedError
```
<details><summary>More context</summary>

`StatefulDetectorHandler` declares three `@abc.abstractmethod`s (`get_dedupe_value`, `get_group_key_values`, and — added in this PR — `build_occurrence_and_event_data`). Python enforces abstract-method checks at `__init__` time, so the failure mode is a hard `TypeError` at the first caller that invokes `detector.detector_handler(...)`. Grep for `MetricAlertDetectorHandler` / slug `"metric_alert_fire"` in post-detect processing (subscription processor, incident task handlers) to confirm whether a live path constructs it. If no caller exists yet, still consider either (a) leaving the base as plain `DetectorHandler` until the real implementation lands, or (b) supplying stub overrides as shown so the stub remains instantiable during the transition.
</details>

## Improvements

:yellow_circle: [correctness] `PriorityLevel(new_status)` cast can raise `ValueError` in `src/sentry/workflow_engine/processors/detector.py`:296 (confidence: 86)
`new_status` is a `DetectorPriorityLevel`, but `PriorityLevel(new_status)` performs a value-lookup against the `PriorityLevel` enum and raises `ValueError` if the int value isn't a member. If `DetectorPriorityLevel` and `PriorityLevel` ever drift (new detector-only priority tiers, or renumbered members), every non-OK evaluation blows up inside `evaluate_group_key_value`.
```suggestion
            else:
                result, event_data = self.build_occurrence_and_event_data(
                    group_key, value, PriorityLevel(new_status.value)
                )
```
<details><summary>More context</summary>

Even today, passing an IntEnum instance to another IntEnum constructor only works because `DetectorPriorityLevel` subclasses `int` and the numeric value happens to match a `PriorityLevel` member. An explicit `.value` conversion plus a mapping table (or a `try/except ValueError` that maps to `PriorityLevel.HIGH`) documents the coupling and makes test failures localized instead of surfacing from `evaluate_group_key_value`. A short unit test that round-trips every `DetectorPriorityLevel` member through `PriorityLevel(...)` would prevent future enum drift from reaching prod.
</details>

:yellow_circle: [correctness] Duplicate-group-key error log silently dropped in `src/sentry/workflow_engine/processors/detector.py`:58 (confidence: 78)
The old `process_detectors` loop tracked `detector_group_keys` and `logger.error("Duplicate detector state group keys found", …)` when a handler returned two results for the same key; the new dict-based return type removes the check entirely and `results[result.group_key] = result` silently overwrites. A buggy handler that yields multiple evaluations for one key now loses one result and emits no signal, and the covering test `test_state_results_multi_group_dupe` was removed rather than migrated.
```suggestion
        detector_results = handler.evaluate(data_packet)
        for result in detector_results.values():
            if result.result is not None:
                create_issue_occurrence_from_result(result)
```
<details><summary>More context</summary>

Structurally, a `dict[DetectorGroupKey, DetectorEvaluationResult]` makes duplicates impossible at the boundary, so the check is redundant there — but `StatefulDetectorHandler.evaluate` builds the dict from `group_values.items()` via `results[result.group_key] = result`. If `evaluate_group_key_value` ever returns a result whose `group_key` differs from the iteration key (e.g. a handler overrides it to collapse keys), the last-write-wins semantics silently drop data. Either (a) assert `result.group_key == group_key` in `evaluate`, or (b) re-add a `logger.warning` when `result.group_key` already exists in `results`. Also consider migrating the deleted `_dupe` test to cover whatever invariant is meant to hold in the new world.
</details>

:yellow_circle: [testing] Expected `value=6` mismatches data-packet value `10` in `tests/sentry/workflow_engine/processors/test_detector.py`:383 (confidence: 88)
`test_state_results_multi_group` sends `"group_vals": {"group_1": 6, "group_2": 10}` but then computes the expected occurrence for `group_2` with `build_mock_occurrence_and_event(..., "group_2", 6, PriorityLevel.HIGH)`. The assertion still passes today only because `build_mock_occurrence_and_event` ignores the `value` argument when constructing the `IssueOccurrence`, so this is a ticking time-bomb the moment `value` starts flowing into `evidence_data` / `fingerprint` / `subtitle` — exactly the point of adding the hook.
```suggestion
        occurrence_2, event_data_2 = build_mock_occurrence_and_event(
            detector.detector_handler, "group_2", 10, PriorityLevel.HIGH
        )
```
<details><summary>More context</summary>

The same issue exists in `TestEvaluateGroupKeyValue.test_dedupe` (line ~692), where `build_mock_occurrence_and_event(handler, "val1", 6, ...)` is asserted against a path that never passed "val1"/6 explicitly. Once a real handler (e.g. the forthcoming `MetricAlertDetectorHandler`) uses `value` to populate occurrence fields, these assertions will pass against incorrect expectations. Fix by threading the real `group_value` through the test helper — ideally by deriving the expected occurrence from the actual `group_vals` dict instead of hard-coding literals.
</details>

:yellow_circle: [correctness] Bare `result` annotation drops the safe default in `src/sentry/workflow_engine/processors/detector.py`:289 (confidence: 70)
Replacing `result = None` with an annotation-only statement `result: StatusChangeMessage | IssueOccurrence` removes the initializer: if a future edit adds a third `elif` branch (or the if/else structure is refactored), any path that doesn't assign `result` will raise `UnboundLocalError` at the `return DetectorEvaluationResult(..., result=result, ...)` call. The typing is also now wrong — `DetectorEvaluationResult.result` is documented as `Optional`, and when `new_status` is OK-then-resolved the code still passes a non-None `StatusChangeMessage`, but nothing else in the type prevents None from being returned at the call site.
```suggestion
            event_data = None
            result: StatusChangeMessage | IssueOccurrence | None = None
```
<details><summary>More context</summary>

Annotation-only statements in Python 3 don't bind the name — they only register the annotation in `__annotations__`. So `result: StatusChangeMessage | IssueOccurrence` produces `NameError` / `UnboundLocalError` if read before assignment. Today, both branches (`if new_status == OK` and `else`) assign, so it's safe. The defensive initializer costs nothing and matches the existing `event_data = None` pattern one line above. If the intent is to assert that `result` is always set, prefer `typing.assert_never`-style exhaustiveness or a pre-return `assert result is not None`.
</details>

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: core detector processing pipeline + abstract handler interface; touches subclasses in `incidents/grouptype.py` | Sensitive Paths: none hit
AI-Authored Likelihood: LOW
