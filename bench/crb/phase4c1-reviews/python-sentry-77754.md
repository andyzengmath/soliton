## Summary
7 files changed, 212 lines added, 15 lines deleted. 5 findings (2 critical, 3 improvements).
Cycle-breaking context-object approach is sound, but the new `AssignmentSource` dataclass has a default-factory bug and the outbound-sync call site pushes a new `assignment_source` kwarg into an abstract contract that wasn't updated for it.

## Critical
:red_circle: [correctness] `queued` default evaluated once at class-definition time in `src/sentry/integrations/services/assignment_source.py:17` (confidence: 95)
`queued: datetime = timezone.now()` is not a per-instance default — Python evaluates the right-hand side exactly once when the dataclass is constructed (at module import). Every `AssignmentSource` built without an explicit `queued` value will share the same frozen timestamp for the life of the process. If `queued` is ever used for staleness/ordering logic (either now or when this context grows), it will silently return the process-start time instead of the actual enqueue time. Use `dataclasses.field(default_factory=timezone.now)` so the callable runs per instance. The existing `test_to_dict` passes only because it checks `is not None`, not time-of-call semantics.
```suggestion
from dataclasses import asdict, dataclass, field
...
@dataclass(frozen=True)
class AssignmentSource:
    source_name: str
    integration_id: int
    queued: datetime = field(default_factory=timezone.now)
```

:red_circle: [cross-file-impact] Outbound sync call site passes `assignment_source=` to `installation.sync_assignee_outbound` without updating the abstract contract in `src/sentry/integrations/tasks/sync_assignee_outbound.py:60` (confidence: 80)
In `sync_assignee_outbound.py` the concrete installation is invoked as `installation.sync_assignee_outbound(external_issue, user, assign=assign, assignment_source=parsed_assignment_source)`. However, the abstract declaration in `src/sentry/integrations/mixins/issues.py` (around the `IssueSyncIntegration.sync_assignee_outbound` abstractmethod — not in this diff) was not amended to accept `assignment_source`, while the sibling `sync_status_outbound` abstract method *was* updated in the same diff. Any concrete integration (`JiraIntegration`, `GitHubIntegration`, `GitLabIntegration`, `VSTSIntegration`, etc.) whose `sync_assignee_outbound` signature does not already accept `**kwargs` or an explicit `assignment_source` parameter will raise `TypeError: sync_assignee_outbound() got an unexpected keyword argument 'assignment_source'` the first time this code path runs in production. Please either (a) update the abstract method signature in `mixins/issues.py` to include `assignment_source: AssignmentSource | None = None`, matching what was already done for `sync_status_outbound`, and audit every concrete override, or (b) drop the `assignment_source=` keyword from this call site since the cycle-break already happens inside `should_sync` above. Only mocked `ExampleIntegration.sync_assignee_outbound` is exercised in the new test, so the production implementations are not covered.
```suggestion
    if installation.should_sync("outbound_assignee", parsed_assignment_source):
        # Assume unassign if None.
        user = user_service.get_user(user_id) if user_id else None
        installation.sync_assignee_outbound(external_issue, user, assign=assign)
```

## Improvements
:yellow_circle: [correctness] Celery kwargs contain a `datetime` object that must survive JSON serialization in `src/sentry/integrations/utils/sync.py:135` (confidence: 70)
`assignment_source.to_dict()` returns `asdict(self)` which includes `queued` as a `datetime` instance. The returned dict is then passed as `kwargs` to `sync_assignee_outbound.apply_async(...)`. Sentry's Celery/Kombu default task serializer is `json`, and stock `json.dumps` cannot serialize a raw `datetime` — this will raise `TypeError: Object of type datetime is not JSON serializable` at enqueue time unless a custom encoder/serializer is installed for this task. After a successful round-trip via any JSON-aware encoder, `queued` will arrive as an ISO string on the consumer side, where `AssignmentSource.from_dict({**dict_with_str_queued})` will happily accept it (dataclass init does not runtime-check types), producing an `AssignmentSource` whose `queued` is a `str` — a latent type lie if anything ever starts comparing it as a datetime. Suggest calling `queued.isoformat()` in `to_dict()` and reparsing in `from_dict()`, or dropping `queued` from the serialized payload entirely since it isn't consulted by `should_sync`.
```suggestion
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["queued"] = self.queued.isoformat()
        return data

    @classmethod
    def from_dict(cls, input_dict: dict[str, Any]) -> AssignmentSource | None:
        try:
            data = dict(input_dict)
            if isinstance(data.get("queued"), str):
                data["queued"] = datetime.fromisoformat(data["queued"])
            return cls(**data)
        except (ValueError, TypeError):
            return None
```

:yellow_circle: [consistency] `sync_status_outbound` gains an unused `assignment_source` parameter in `src/sentry/integrations/mixins/issues.py:411` (confidence: 75)
The abstract `sync_status_outbound` signature is widened to accept `assignment_source: AssignmentSource | None = None`, but no call site in this PR passes the argument and no `should_sync` branch consumes it — status syncing does not participate in the assign/deassign cycle the PR is fixing. Adding an unused abstract parameter forces every concrete override to update or silently swallow it via `**kwargs`, and leaves reviewers of downstream PRs guessing whether status-cycle protection was intended and omitted, or is outright dead weight. Either wire it through the status-outbound task (and add a test for status-source cycle-break), or drop it from this PR and add it in the follow-up that actually uses it.

:yellow_circle: [testing] No negative-case test verifying that a *different* integration still triggers outbound sync in `tests/sentry/models/test_groupassignee.py:193` (confidence: 80)
The new `test_assignee_sync_outbound_assign_with_matching_source_integration` proves the positive cycle-break (same integration => `sync_assignee_outbound` not called), but there is no complementary test asserting that an `AssignmentSource` built from a *different* integration still routes through to `sync_assignee_outbound`. Without it, a future regression that makes `should_sync` return `False` for *any* non-`None` `sync_source` — the easiest way to accidentally break this code — would pass CI. Please add a mirror test that creates two integrations, calls `assign(..., assignment_source=AssignmentSource.from_integration(other_integration))`, and asserts `mock_sync_assignee_outbound.assert_called_once`.

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: 7 files across integrations mixins, services, tasks, utils, and the core `GroupAssignee` manager — any assignment path org-wide | Sensitive Paths: `src/sentry/integrations/`, `src/sentry/models/groupassignee.py`
AI-Authored Likelihood: LOW
