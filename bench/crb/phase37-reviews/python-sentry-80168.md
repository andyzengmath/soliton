## Summary
4 files changed, 249 lines added, 151 lines deleted. 4 findings (1 critical, 3 improvements).
Adds `build_occurrence_and_event_data` hook on `StatefulDetectorHandler` and switches `evaluate` return type from list to dict keyed by group_key; the `MetricAlertDetectorHandler` rebase onto the stateful base leaves abstract methods unimplemented.

## Critical
:red_circle: [correctness] `MetricAlertDetectorHandler` cannot be instantiated — abstract methods left unimplemented in src/sentry/incidents/grouptype.py:15 (confidence: 92)
After the rebase, `MetricAlertDetectorHandler` now inherits from `StatefulDetectorHandler` with an empty `pass` body, but `StatefulDetectorHandler` still declares three abstract methods: `get_dedupe_value`, `get_group_key_values`, and the newly-added `build_occurrence_and_event_data`. Attempting to construct this handler via `detector.detector_handler` will raise `TypeError: Can't instantiate abstract class MetricAlertDetectorHandler with abstract methods ...`. The prior implementation provided a concrete stub `evaluate` that returned `[]`, so the class was instantiable even though it produced no results. The new form regresses that contract. If the metric-alert codepath is not yet exercised this is latent, but it is a sharp edge that will bite the first caller. Either keep the class abstract (mark it with `abstract = True` / a different base) or provide stub implementations that raise `NotImplementedError` with a clear TODO.
```suggestion
class MetricAlertDetectorHandler(StatefulDetectorHandler[QuerySubscriptionUpdate]):
    def get_dedupe_value(self, data_packet: DataPacket[QuerySubscriptionUpdate]) -> int:
        raise NotImplementedError("MetricAlertDetectorHandler is not yet wired up")

    def get_group_key_values(
        self, data_packet: DataPacket[QuerySubscriptionUpdate]
    ) -> dict[str, int]:
        raise NotImplementedError("MetricAlertDetectorHandler is not yet wired up")

    def build_occurrence_and_event_data(
        self, group_key: DetectorGroupKey, value: int, new_status: PriorityLevel
    ) -> tuple[IssueOccurrence, dict[str, Any]]:
        raise NotImplementedError("MetricAlertDetectorHandler is not yet wired up")
```

## Improvements
:yellow_circle: [correctness] `PriorityLevel(new_status)` relies on enum-value parity with `DetectorPriorityLevel` in src/sentry/workflow_engine/processors/detector.py:297 (confidence: 78)
`new_status` is a `DetectorPriorityLevel`, and the new else-branch coerces it to `PriorityLevel` by value via `PriorityLevel(new_status)`. This works only if every non-OK `DetectorPriorityLevel` member has a numerically matching `PriorityLevel` member. A future divergence between the two enums (e.g. adding a detector-only severity) will surface here as a `ValueError` at runtime deep inside detector evaluation — far from where someone editing the enum would think to look. Prefer an explicit map (or a `classmethod` on one of the enums) so the failure mode is a type-check error at definition time, not a Kafka-producing runtime path.
```suggestion
_DETECTOR_TO_PRIORITY = {
    DetectorPriorityLevel.HIGH: PriorityLevel.HIGH,
    DetectorPriorityLevel.MEDIUM: PriorityLevel.MEDIUM,
    DetectorPriorityLevel.LOW: PriorityLevel.LOW,
}
# ...
result, event_data = self.build_occurrence_and_event_data(
    group_key, value, _DETECTOR_TO_PRIORITY[new_status]
)
```

:yellow_circle: [correctness] `result` is declared without an initializer; a future branch addition would leak an `UnboundLocalError` in src/sentry/workflow_engine/processors/detector.py:289 (confidence: 72)
The line `result: StatusChangeMessage | IssueOccurrence` is a bare annotation with no default. The two current branches (`if new_status == OK` / `else`) happen to cover the domain, so `result` is always bound before it is read. But the previous code defensively used `result = None` for exactly this reason. If anyone later adds a third branch (e.g. handling a transitional "muted" state) and forgets to assign `result`, Python will raise `UnboundLocalError` rather than a clean `None` passthrough, and the test that surfaces it will be hard to read. Restore the explicit default or make the assignment exhaustive via an early-return style.
```suggestion
result: StatusChangeMessage | IssueOccurrence | None = None
```

:yellow_circle: [testing] `build_mock_occurrence_and_event` ignores its `value` argument, masking drift between test packets and asserted occurrences in tests/sentry/workflow_engine/processors/test_detector.py:458 (confidence: 81)
The helper takes `value: int` but never references it when constructing the `IssueOccurrence`. Several call sites pass values that do not match the data packet (e.g. `test_state_results_multi_group` sends `group_2: 10` but builds the expected occurrence with `value=6`; `test_results_on_change` sends `val1: 100` but passes `6`). Today this silently passes because the mock occurrence does not encode `value`, but it makes the tests lie about what they are asserting, and the first time a real `build_occurrence_and_event_data` implementation folds `value` into the occurrence payload (e.g. into `evidence_data` or `subtitle`), these tests will flip red for reasons unrelated to the code under test. Either thread `value` into the mock occurrence (so the assertion is honest) or drop the parameter to prevent further misleading callers.
```suggestion
def build_mock_occurrence_and_event(
    handler: StatefulDetectorHandler,
    group_key: DetectorGroupKey,
    value: int,
    new_status: PriorityLevel,
) -> tuple[IssueOccurrence, dict[str, Any]]:
    assert handler.detector.group_type is not None
    occurrence = IssueOccurrence(
        id="eb4b0acffadb4d098d48cb14165ab578",
        project_id=handler.detector.project_id,
        event_id="43878ab4419f4ab181f6379ac376d5aa",
        fingerprint=handler.build_fingerprint(group_key),
        issue_title="Some Issue",
        subtitle=f"Some subtitle {value}",
        resource_id=None,
        evidence_data={"value": value},
        evidence_display=[],
        type=handler.detector.group_type,
        detection_time=datetime.now(timezone.utc),
        level="error",
        culprit="Some culprit",
        initial_issue_priority=new_status.value,
    )
    # ...
```

## Risk Metadata
Risk Score: 42/100 (MEDIUM) | Blast Radius: 4 files, 2 production modules (detector processor + model); MetricAlertDetectorHandler is an external entry point for metric-alert ingestion | Sensitive Paths: none (no auth/secret/migration paths touched)
AI-Authored Likelihood: LOW
