## Summary
4 files changed, 249 lines added, 151 lines deleted. 4 findings (1 critical, 3 improvements, 0 nitpicks).
Adds a `build_occurrence_and_event_data` hook on `StatefulDetectorHandler` and converts `evaluate()` to return a `dict[DetectorGroupKey, DetectorEvaluationResult]`; well-scoped change but introduces an abstract-instantiation regression in `MetricAlertDetectorHandler` and a fragile enum coercion.

## Critical
:red_circle: [correctness] `MetricAlertDetectorHandler` becomes abstract and cannot be instantiated in src/sentry/incidents/grouptype.py:12 (confidence: 95)
The old class inherited from `DetectorHandler` and provided a concrete (stub) `evaluate` that returned `[]`. The new class inherits from `StatefulDetectorHandler[QuerySubscriptionUpdate]` with only `pass` — it inherits three `@abc.abstractmethod`s (`get_dedupe_value`, `get_group_key_values`, and the newly-added `build_occurrence_and_event_data`) and implements none of them. Any code path that resolves `Detector.detector_handler` for a metric-alert group type now calls `group_type.detector_handler(self)` → `MetricAlertDetectorHandler(detector)`, which raises `TypeError: Can't instantiate abstract class MetricAlertDetectorHandler with abstract methods build_occurrence_and_event_data, get_dedupe_value, get_group_key_values`. This is a live regression if any production caller ever resolves the metric-alert detector handler (the previous stub silently returned `[]`).
```suggestion
class MetricAlertDetectorHandler(StatefulDetectorHandler[QuerySubscriptionUpdate]):
    def get_dedupe_value(self, data_packet: DataPacket[QuerySubscriptionUpdate]) -> int:
        raise NotImplementedError  # TODO: implement in a follow-up PR

    def get_group_key_values(
        self, data_packet: DataPacket[QuerySubscriptionUpdate]
    ) -> dict[str, int]:
        raise NotImplementedError  # TODO: implement in a follow-up PR

    def build_occurrence_and_event_data(
        self, group_key, value, new_status
    ):
        raise NotImplementedError  # TODO: implement in a follow-up PR
```

## Improvements
:yellow_circle: [correctness] `PriorityLevel(new_status)` silently assumes value-space compatibility with `DetectorPriorityLevel` in src/sentry/workflow_engine/processors/detector.py:299 (confidence: 80)
`new_status` is a `DetectorPriorityLevel`; `PriorityLevel(new_status)` coerces by integer value. This works only while every non-OK `DetectorPriorityLevel` member has a matching `PriorityLevel` value. If the two enums ever diverge (e.g. `DetectorPriorityLevel` adds a new bucket like `MEDIUM_HIGH`) the call raises `ValueError` at runtime inside the happy path of every stateful detector firing. Prefer an explicit mapping (or a shared superclass / `IntEnum` with a documented invariant) rather than trusting numeric coincidence.
```suggestion
            else:
                # DetectorPriorityLevel.value is guaranteed to map onto PriorityLevel.
                # If this invariant is ever broken, PriorityLevel(new_status) will raise.
                priority = PriorityLevel(new_status.value)
                result, event_data = self.build_occurrence_and_event_data(
                    group_key, value, priority
                )
```

:yellow_circle: [correctness] Silent loss of the "Duplicate detector state group keys found" signal in src/sentry/workflow_engine/processors/detector.py:59 (confidence: 70)
The old loop explicitly logged when a handler produced two results with the same `group_key` and skipped the duplicate. The new code drops that telemetry because the dict construction in `StatefulDetectorHandler.evaluate` — `results[result.group_key] = result` — uses `result.group_key` (not the input `group_key` from `group_values.items()`), which silently overwrites if a subclass returns a result whose `group_key` differs from its input key, or collides with another key. Consider asserting `result.group_key == group_key` (or logging when `result.group_key in results`) so handler bugs don't get masked by the dict structure.
```suggestion
        for group_key, group_value in group_values.items():
            result = self.evaluate_group_key_value(
                group_key, group_value, all_state_data[group_key], dedupe_value
            )
            if result:
                if result.group_key in results:
                    logger.error(
                        "Duplicate detector state group keys found",
                        extra={"detector_id": self.detector.id, "group_key": result.group_key},
                    )
                    continue
                results[result.group_key] = result
```

:yellow_circle: [testing] `test_state_results_multi_group` builds mock occurrences with the wrong `value` for `group_2` in tests/sentry/workflow_engine/processors/test_detector.py:155 (confidence: 65)
The data packet sends `{"group_1": 6, "group_2": 10}` but the second expected occurrence is built with `value=6` (`build_mock_occurrence_and_event(detector.detector_handler, "group_2", 6, PriorityLevel.HIGH)`). The test passes only because `build_mock_occurrence_and_event` never uses `value` meaningfully, so any integer round-trips through the mock. This hides any future regression where the real `build_occurrence_and_event_data` starts caring about `value`, and it misleads readers about what the system-under-test is asserting.
```suggestion
        occurrence_2, event_data_2 = build_mock_occurrence_and_event(
            detector.detector_handler, "group_2", 10, PriorityLevel.HIGH
        )
```

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: 4 files, core workflow-engine processor + model + one consumer (`incidents/grouptype.py`) + tests | Sensitive Paths: none matched
AI-Authored Likelihood: LOW
