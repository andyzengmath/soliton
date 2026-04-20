## Summary
4 files changed, 249 lines added, 151 lines deleted. 4 findings (2 critical, 2 improvements).
Hook for occurrence production added to StatefulDetectorHandler; return signature changed list→dict, but a subclass is left non-instantiable and a type-narrowing bug can raise `UnboundLocalError`.

## Critical
:red_circle: [correctness] `MetricAlertDetectorHandler` becomes abstract/uninstantiable in src/sentry/incidents/grouptype.py:12 (confidence: 92)
Before this PR, `MetricAlertDetectorHandler` extended `DetectorHandler` and provided a concrete `evaluate` that returned `[]`, so it could be instantiated (albeit as a no-op). After this PR it extends `StatefulDetectorHandler` with only `pass` as its body. `StatefulDetectorHandler` declares three `@abc.abstractmethod`s (`get_dedupe_value`, `get_group_key_values`, and the newly added `build_occurrence_and_event_data`), plus `evaluate_group_key_value` via abstract condition wiring. Any code path that reaches `Detector.detector_handler` for a metric-alert group type will now raise `TypeError: Can't instantiate abstract class MetricAlertDetectorHandler with abstract methods build_occurrence_and_event_data, get_dedupe_value, get_group_key_values` instead of the previous graceful empty-result behavior. Either keep the handler concrete (return `{}` / no-op impls) until the stateful abstraction is finished, or temporarily skip registering it.
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
        raise NotImplementedError("MetricAlertDetectorHandler occurrence building not yet implemented")
```

:red_circle: [correctness] `result` may be unbound in `evaluate_group_key_value` in src/sentry/workflow_engine/processors/detector.py:289 (confidence: 88)
The previous code initialized `result = None` before the `if new_status == DetectorPriorityLevel.OK` branch. The new code replaces the initializer with a bare type annotation:
```
result: StatusChangeMessage | IssueOccurrence
if new_status == DetectorPriorityLevel.OK:
    result = StatusChangeMessage(...)
else:
    result, event_data = self.build_occurrence_and_event_data(...)
```
A bare annotation does NOT bind the name. If `new_status` ever falls outside `{OK, HIGH, MEDIUM, LOW}` — e.g. a future priority level or a defensive path where condition evaluation returns something unexpected — `result` is referenced below in `DetectorEvaluationResult(..., result=result, ...)` while unbound, raising `UnboundLocalError`. Also, the `Optional[...]` handling on `DetectorEvaluationResult.result` is lost: callers downstream can no longer distinguish "no message produced" from "produced". Initialize explicitly and keep `Optional`.
```suggestion
event_data = None
result: StatusChangeMessage | IssueOccurrence | None = None
if new_status == DetectorPriorityLevel.OK:
    result = StatusChangeMessage(
        ...
    )
else:
    result, event_data = self.build_occurrence_and_event_data(
        group_key, value, PriorityLevel(new_status)
    )
```

## Improvements
:yellow_circle: [correctness] `PriorityLevel(new_status)` relies on implicit enum value compatibility in src/sentry/workflow_engine/processors/detector.py:300 (confidence: 80)
`new_status` is a `DetectorPriorityLevel`, and `PriorityLevel(new_status)` calls the `PriorityLevel` enum with the `DetectorPriorityLevel` member as input. This only works if the integer values align 1:1 between the two enums. There's no comment or assertion guaranteeing this invariant, and a future re-numbering of `DetectorPriorityLevel` (e.g. inserting a new level) would silently produce wrong `PriorityLevel` values or raise `ValueError` at runtime for unmapped values. Add an explicit mapping or assertion, or use `.value`:
```suggestion
            else:
                result, event_data = self.build_occurrence_and_event_data(
                    group_key, value, PriorityLevel(new_status.value)
                )
```
and add a unit test pinning the invariant `PriorityLevel(DetectorPriorityLevel.HIGH.value) == PriorityLevel.HIGH` for every level.

:yellow_circle: [testing] Mock occurrence ignores `value`, masking real bugs in tests/sentry/workflow_engine/processors/test_detector.py:266 (confidence: 82)
`build_mock_occurrence_and_event` accepts a `value` parameter but never uses it when constructing the `IssueOccurrence` or `event_data`. Combined with the fact that `test_state_results_multi_group` asserts the mock with `value=6` for `group_2` even though the data packet has `{"group_2": 10}`, the tests would pass even if `evaluate_group_key_value` accidentally passed the wrong value to `build_occurrence_and_event_data`. Either incorporate `value` into `evidence_data` / `subtitle` so equality checks exercise the wiring, or assert on `build_occurrence_and_event_data.call_args_list` with the correct expected values.
```suggestion
    occurrence = IssueOccurrence(
        id="eb4b0acffadb4d098d48cb14165ab578",
        project_id=handler.detector.project_id,
        event_id="43878ab4419f4ab181f6379ac376d5aa",
        fingerprint=handler.build_fingerprint(group_key),
        issue_title="Some Issue",
        subtitle=f"Value: {value}",
        resource_id=None,
        evidence_data={"value": value},
        evidence_display=[],
        type=handler.detector.group_type,
        detection_time=datetime.now(timezone.utc),
        level="error",
        culprit="Some culprit",
        initial_issue_priority=new_status.value,
    )
```

## Risk Metadata
Risk Score: 45/100 (MEDIUM) | Blast Radius: 4 files, 2 of which are core workflow_engine types consumed by detector registration | Sensitive Paths: none
AI-Authored Likelihood: LOW
