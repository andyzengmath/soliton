## Summary
4 files changed, 249 lines added, 151 lines deleted. 2 findings (1 critical, 1 improvement, 0 nitpicks).
4 files changed. MetricAlertDetectorHandler is uninstantiable due to unimplemented abstract methods after base class swap.

## Critical
:red_circle: [correctness] MetricAlertDetectorHandler is an uninstantiable abstract class — TypeError at runtime in src/sentry/incidents/grouptype.py:21 (confidence: 97)
MetricAlertDetectorHandler now extends StatefulDetectorHandler but has only `pass` in its body. StatefulDetectorHandler declares three @abc.abstractmethod methods: get_dedupe_value, get_group_key_values, and the newly-added build_occurrence_and_event_data. None of these are implemented in MetricAlertDetectorHandler. Python's ABCMeta will raise TypeError("Can't instantiate abstract class MetricAlertDetectorHandler with abstract methods build_occurrence_and_event_data, get_dedupe_value, get_group_key_values") the moment any code tries to instantiate it. The old implementation was safe because it provided a concrete evaluate() stub returning []. This PR introduced the regression by (a) adding build_occurrence_and_event_data as abstract and (b) changing the base class to StatefulDetectorHandler without supplying implementations. No test currently exercises instantiation of this class, so the failure is invisible until production runtime.
```suggestion
class MetricAlertDetectorHandler(StatefulDetectorHandler[QuerySubscriptionUpdate]):
    def get_dedupe_value(self, data_packet: DataPacket[QuerySubscriptionUpdate]) -> int:
        # TODO: Implement
        return 0

    def get_group_key_values(self, data_packet: DataPacket[QuerySubscriptionUpdate]) -> dict[str, int]:
        # TODO: Implement
        return {}

    def build_occurrence_and_event_data(
        self, group_key: DetectorGroupKey, value: int, new_status: PriorityLevel
    ) -> tuple[IssueOccurrence, dict[str, Any]]:
        raise NotImplementedError
```

## Improvements
:yellow_circle: [testing] No test covers dict-overwrite semantics when evaluate() produces duplicate group_keys in tests/sentry/workflow_engine/processors/test_detector.py:1 (confidence: 90)
The removed test_state_results_multi_group_dupe previously verified behavior when evaluate_group_key_value emits the same group_key more than once. The new implementation switches from a list to a dict keyed by group_key, so subsequent entries with the same key silently overwrite earlier ones. No test exercises this edge case, meaning a bug in deduplication logic (dropped occurrences, wrong priority selected) is invisible. This is particularly relevant because the prior code explicitly logged a warning for this condition — that observability was removed when the dict approach was adopted.
```suggestion
def test_evaluate_duplicate_group_key_overwrites(self):
    data_packet = DataPacket(source_id=1, packet={"group_vals": {"g1": 5}, "dedupe": 1})
    with mock.patch.object(
        MockDetectorStateHandler,
        "evaluate_group_key_value",
        side_effect=[
            DetectorEvaluationResult(group_key="g1", is_active=True, priority=DetectorPriorityLevel.HIGH),
            DetectorEvaluationResult(group_key="g1", is_active=True, priority=DetectorPriorityLevel.MEDIUM),
        ],
    ):
        result = self.handler.evaluate(data_packet)
    assert len(result) == 1
    assert result["g1"].priority == DetectorPriorityLevel.MEDIUM
```

## Risk Metadata
Risk Score: 45/100 (MEDIUM) | Blast Radius: 3 core workflow_engine modules with public signature changes (score 80) | Sensitive Paths: none
AI-Authored Likelihood: LOW

(6 additional findings below confidence threshold)
