## Summary
4 files changed, 249 lines added, 151 lines deleted. 4 findings (2 critical, 2 improvements, 0 nitpicks).
PR adds an abstract `build_occurrence_and_event_data` hook to `StatefulDetectorHandler` and switches `evaluate` to return `dict[DetectorGroupKey, DetectorEvaluationResult]`. Two regressions surface: `MetricAlertDetectorHandler` becomes uninstantiable, and `PriorityLevel(new_status)` silently couples two enums by integer value.

## Critical

:red_circle: [correctness] `MetricAlertDetectorHandler` is no longer instantiable in src/sentry/incidents/grouptype.py:13 (confidence: 92)
This PR rebases `MetricAlertDetectorHandler` from `DetectorHandler[QuerySubscriptionUpdate]` (with a concrete `evaluate` returning `[]`) onto `StatefulDetectorHandler[QuerySubscriptionUpdate]` with only `pass` in the body. `StatefulDetectorHandler` has three abstract methods — `get_dedupe_value`, `get_group_key_values`, and the newly-abstract `build_occurrence_and_event_data` — none of which the subclass implements. Any call site that materializes the handler (notably `Detector.detector_handler`, which calls `handler(self)` for every detector whose grouptype slug resolves to this class) will raise `TypeError: Can't instantiate abstract class MetricAlertDetectorHandler with abstract methods build_occurrence_and_event_data, get_dedupe_value, get_group_key_values`. The pre-PR version was a working no-op stub; the post-PR version is a latent runtime crash whose blast radius scales with however many `MetricAlertDetector` rows exist in production.
```suggestion
class MetricAlertDetectorHandler(StatefulDetectorHandler[QuerySubscriptionUpdate]):
    def get_dedupe_value(self, data_packet: DataPacket[QuerySubscriptionUpdate]) -> int:
        raise NotImplementedError

    def get_group_key_values(self, data_packet: DataPacket[QuerySubscriptionUpdate]) -> dict[str, int]:
        raise NotImplementedError

    def build_occurrence_and_event_data(self, group_key, value, new_status):
        raise NotImplementedError
```

:red_circle: [correctness] Implicit cross-enum int coupling via `PriorityLevel(new_status)` in src/sentry/workflow_engine/processors/detector.py:297 (confidence: 78)
The `else` branch of the active-issue path constructs `PriorityLevel(new_status)` where `new_status` is a `DetectorPriorityLevel`. The cast only succeeds because the integer values of the two enums currently coincide (`LOW=25`, `MEDIUM=50`, `HIGH=75`). There is no static enforcement of this invariant: introducing a new `DetectorPriorityLevel` value (or remapping any existing one) without a matching `PriorityLevel` member will raise `ValueError` inside every stateful detector's hot path. The two enums live in different modules with no cross-reference, so the coupling is invisible to anyone editing only one side. Make the conversion explicit through a mapping table.
```suggestion
            else:
                priority_level = _DETECTOR_TO_PRIORITY[new_status]
                result, event_data = self.build_occurrence_and_event_data(
                    group_key, value, priority_level
                )
```

## Improvements

:yellow_circle: [correctness] Silent loss of duplicate-group-key diagnostics in src/sentry/workflow_engine/processors/detector.py:58 (confidence: 75)
The old `process_detectors` logged `"Duplicate detector state group keys found"` whenever an evaluator returned two results sharing a group key. Switching the return type to `dict[DetectorGroupKey, DetectorEvaluationResult]` makes the structural duplicate impossible *along the `StatefulDetectorHandler.evaluate` path* — but custom `DetectorHandler` subclasses still build the dict themselves, and `StatefulDetectorHandler.evaluate` will silently overwrite when `evaluate_group_key_value` returns a result whose `group_key` differs from the loop key (e.g., a subclass that mutates it). The deleted `test_state_results_multi_group_dupe` was the only place that exercised the duplicate-detection invariant. Consider keeping a defensive metric/log when `len(results) < len(group_values)` or when an inserted key collides with an existing entry.

:yellow_circle: [testing] Mock value mismatch in tests/sentry/workflow_engine/processors/test_detector.py:160 (confidence: 70)
In `test_state_results_multi_group`, the data packet declares `group_vals={"group_1": 6, "group_2": 10}` but the call to `build_mock_occurrence_and_event` for `group_2` passes `value=6` rather than `10`. The test passes today only because `build_mock_occurrence_and_event` ignores its `value` parameter, so the bug is silent — but it will trip up the next reader who adds value-dependent assertions or extends the helper.
```suggestion
        occurrence_2, event_data_2 = build_mock_occurrence_and_event(
            detector.detector_handler, "group_2", 10, PriorityLevel.HIGH
        )
```

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: `workflow_engine.processors.detector` is imported by `incidents/grouptype.py` and is the entry point for every detector flow; the abstract-method regression is latent until a `MetricAlert`-typed detector row is materialized. | Sensitive Paths: none
AI-Authored Likelihood: LOW
